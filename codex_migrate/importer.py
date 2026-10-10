import copy, datetime, json, ntpath, os, shutil, time
from contextlib import ExitStack, closing
from pathlib import Path
from .common import (
    check,
    clone,
    columns,
    connect,
    digests,
    dump,
    layout,
    load,
    migrations,
    norm,
    offline,
    quote,
    require,
    safe_child,
    sha,
)
from .exporter import TABLES, verify_package
from .images import canonical, image_paths, prose_digest, rewrite_log, transform

KEYS = {
    "thread_turns": ("thread_id", "turn_id"),
    "thread_items": ("thread_id", "turn_id", "item_id"),
    "thread_history_projection_state": ("thread_id",),
    "thread_realtime_items": ("thread_id", "turn_id", "item_id"),
}


def compatible_columns(c, meta):
    source = json.loads(meta["migrations_json"])
    target = migrations(c)
    cols = json.loads(meta["columns_json"])
    actual = columns(c, meta["table_name"])
    compat = None
    cutoff = {"state": 59, "history": 8}[meta["kind"]]
    require(
        all(0 < r["version"] <= cutoff and r["success"] == 1 for r in source + target),
        "Unsupported database migration version; update compatibility support before importing",
    )
    if source != target:
        require(
            meta["kind"] == "history"
            and source[:-1] == target
            and source[-1]
            == {"version": 8, "description": "thread turn root", "success": 1},
            "Unsupported schema migration difference; align application versions",
        )
        compat = "root_turn_id self-link omitted for history schema 7"
    if cols != actual:
        require(
            bool(compat)
            and meta["table_name"] == "thread_turns"
            and cols == actual + ["root_turn_id"],
            "Unsupported schema columns",
        )
    return actual, compat


def selected(c, table, ids, field="thread_id"):
    return [
        dict(x)
        for x in c.execute(
            "select * from "
            + quote(table)
            + " where "
            + quote(field)
            + " in ("
            + ",".join("?" for _ in ids)
            + ") order by "
            + ",".join(str(i + 1) for i in range(len(columns(c, table)))),
            ids,
        )
    ]


def baseline_guard(current, baseline, ids):
    has_images = baseline.execute(
        "select 1 from sqlite_master where name='image_baseline'"
    ).fetchone()
    image_digests = (
        dict(baseline.execute("select source_path,sha256 from image_baseline"))
        if has_images
        else {}
    )
    for table, key in list(KEYS.items())[:3]:
        old = {
            tuple(r[k] for k in key): r
            for r in selected(baseline, "history__" + table, ids)
        }
        now = {tuple(r[k] for k in key): r for r in selected(current, table, ids)}
        require(
            set(old) == set(now),
            "Recipient added or removed records; do not overwrite a diverged chat",
        )
        for k, a in old.items():
            b = now[k]
            for field in (
                "rollout_byte_offset",
                "rollout_end_byte_offset",
                "next_rollout_byte_offset",
            ):
                a.pop(field, None)
                b.pop(field, None)
            # These two known schema-8 additions are metadata, not dialogue content.
            if "root_turn_id" not in a and b.get("root_turn_id") in (
                None,
                b.get("turn_id"),
            ):
                b.pop("root_turn_id", None)
            if "root_turn_id" not in b and a.get("root_turn_id") in (
                None,
                a.get("turn_id"),
            ):
                a.pop("root_turn_id", None)
            if "item_json" in a:
                old_json = json.loads(a["item_json"])
                new_json = json.loads(b["item_json"])
                if a.get("item_type") == "userMessage":
                    old_paths = image_paths(old_json)
                    new_paths = image_paths(new_json)
                    require(
                        len(old_paths) == len(new_paths),
                        "Recipient image references changed",
                    )
                    for old_path, new_path in zip(old_paths, new_paths):
                        require(
                            norm(old_path) in image_digests,
                            "Baseline image digest missing; do not guess image equivalence",
                        )
                        require(
                            Path(new_path).is_file()
                            and sha(new_path) == image_digests[norm(old_path)],
                            "Recipient local image content changed; manual reconciliation is required",
                        )
                a["item_json"] = canonical(old_json)
                b["item_json"] = canonical(new_json)
            require(
                a == b,
                "Recipient local changes detected; manual reconciliation is required",
            )
    require(
        not selected(current, "thread_realtime_items", ids),
        "Recipient has an active realtime stream",
    )


def mapped(value, plan, home):
    if not isinstance(value, str):
        return value
    clean = ntpath.normpath(value.removeprefix("\\\\?\\"))
    pairs = sorted(
        plan["path_maps"] + [[plan["source_home"], str(home)]],
        key=lambda x: len(x[0]),
        reverse=True,
    )
    for src, dst in pairs:
        if norm(clean) == norm(src):
            return dst
        if norm(clean).startswith(norm(src) + "\\"):
            return ntpath.join(dst, clean[len(ntpath.normpath(src)) + 1 :])
    return value


def sandbox(value, plan, home):
    obj = json.loads(value)
    if obj.get("type") == "managed":
        fs = obj.get("file_system", {})
        entries = []
        for e in fs.get("entries", []):
            p = e.get("path", {})
            if isinstance(p, dict) and p.get("type") == "path":
                old = p["path"]
                new = mapped(old, plan, home)
                if new == old and ntpath.isabs(old):
                    continue
                p["path"] = new
            entries.append(e)
        fs["entries"] = entries
    elif isinstance(obj.get("writable_roots"), list):
        obj["writable_roots"] = [
            mapped(p, plan, home)
            for p in obj["writable_roots"]
            if mapped(p, plan, home) != p
        ]
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def close_snapshot(c):
    c.commit()
    check(c)
    c.execute("pragma wal_checkpoint(truncate)")
    c.execute("pragma journal_mode=delete")
    c.close()


def prepare(stage, home, dbroot, image_root=None):
    with ExitStack() as stack:
        return _prepare(stage, home, dbroot, image_root, stack)


def _prepare(stage, home, dbroot, image_root, stack):
    stage = Path(stage).resolve()
    home = Path(home).resolve()
    dbroot = Path(dbroot).resolve()
    plan = verify_package(stage)
    ids = plan["ids"]
    require(
        not home.is_relative_to(stage) and not dbroot.is_relative_to(stage),
        "Staging must not contain the live home/database roots",
    )
    work = stage / (
        "prepared-"
        + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        + "-"
        + os.urandom(3).hex()
    )
    work.mkdir()
    target_paths = layout(home, dbroot)
    clones = {}
    before = {}
    keep = {}
    compatibility = set()
    counts = {}
    image_root = (
        Path(image_root).resolve()
        if image_root
        else home / "migration-assets" / plan["run_id"]
    )
    require(
        not image_root.is_relative_to(stage),
        "Live images must be outside disposable staging",
    )
    mapping = {
        norm(r["source_path"]): str(image_root / r["name"]) for r in plan["images"]
    }
    for kind, path in target_paths.items():
        if not path.exists():
            require(
                kind == "desktop" and not plan["catalog"],
                "Recipient database is missing: " + kind,
            )
            continue
        dest = work / "databases" / (kind + ".sqlite")
        clone(path, dest)
        clones[kind] = dest
        with closing(connect(dest, True)) as c:
            before[kind] = digests(c)
            keep[kind] = digests(
                c, ids, TABLES.get(kind, ("local_thread_catalog",)), kind == "desktop"
            )
    require(
        "state" in clones and "history" in clones,
        "State and history databases are required",
    )
    exported = connect(stage / "payload/export.sqlite", True)
    history = connect(clones["history"])
    state = connect(clones["state"])
    for connection in (exported, history, state):
        stack.callback(connection.close)
    existing = {x["id"] for x in selected(state, "threads", ids, "id")}
    require(
        not existing or existing == set(ids),
        "Mixed new/existing selections are not supported; split the batch",
    )
    if existing:
        base = stage / "payload/baseline.sqlite"
        require(
            base.is_file(),
            "Existing chats require --baseline from the previous successful export",
        )
        with closing(connect(base, True)) as baseline:
            baseline_guard(history, baseline, ids)
    source_threads = {
        r["id"]: r for r in exported.execute("select * from state__threads")
    }
    require(set(source_threads) == set(ids), "Selected state rows mismatch")
    incoming = {}
    for meta in exported.execute("select * from metadata"):
        c = state if meta["kind"] == "state" else history
        cols, compat = compatible_columns(c, meta)
        if compat:
            compatibility.add(compat)
        rows = [
            dict(r)
            for r in exported.execute("select * from " + quote(meta["export_name"]))
        ]
        require(len(rows) == meta["row_count"], "Export row count mismatch")
        if meta["table_name"] == "thread_turns" and "root_turn_id" not in cols:
            require(
                all(
                    r["root_turn_id"] is None or r["root_turn_id"] == r["turn_id"]
                    for r in rows
                ),
                "A nontrivial root-turn relation cannot be omitted",
            )
            for r in rows:
                r.pop("root_turn_id")
        incoming[(meta["kind"], meta["table_name"])] = (cols, rows)
        counts[meta["kind"] + "." + meta["table_name"]] = len(rows)
    for row in incoming[("history", "thread_items")][1]:
        if row["item_type"] == "userMessage":
            obj = json.loads(row["item_json"])
            old = prose_digest(obj)
            changed = transform(obj, mapping)
            require(prose_digest(obj) == old, "Prose changed")
            if changed:
                row["item_json"] = json.dumps(
                    obj, ensure_ascii=False, separators=(",", ":")
                )
    log_targets = []
    for log in plan["logs"]:
        tid = log["thread_id"]
        relative = log["relative"]
        points = set()
        if relative == plan["anchors"][tid]:
            for r in incoming[("history", "thread_turns")][1]:
                if r["thread_id"] == tid:
                    points.update(
                        r[k]
                        for k in ("rollout_byte_offset", "rollout_end_byte_offset")
                        if r[k] is not None
                    )
            points.update(
                r["next_rollout_byte_offset"]
                for r in incoming[("history", "thread_history_projection_state")][1]
                if r["thread_id"] == tid
            )
        src = safe_child(stage / "payload/home", relative)
        dest = safe_child(work / "home", relative)
        report = rewrite_log(src, dest, mapping, points)
        if points:
            remap = report["offset_map"]
            for r in incoming[("history", "thread_turns")][1]:
                if r["thread_id"] == tid:
                    for k in ("rollout_byte_offset", "rollout_end_byte_offset"):
                        if r[k] is not None:
                            r[k] = remap[r[k]]
            for r in incoming[("history", "thread_history_projection_state")][1]:
                if r["thread_id"] == tid:
                    r["next_rollout_byte_offset"] = remap[r["next_rollout_byte_offset"]]
        live = safe_child(home, relative)
        oldsha = sha(live) if live.exists() else None
        require(
            existing or oldsha is None,
            "A destination log already exists for a new chat",
        )
        log_targets.append(
            {
                "destination": str(live),
                "prepared": str(dest),
                "before_sha256": oldsha,
                "after_sha256": report["sha256"],
                "role": "session-log",
            }
        )
    project_ids = {}
    target_roots = {}
    state_tables = {
        x[0] for x in state.execute("select name from sqlite_master where type='table'")
    }
    for tid, row in source_threads.items():
        cwd = mapped(row["cwd"], plan, home)
        require(
            cwd != row["cwd"]
            or any(
                norm(row["cwd"]) == norm(p[0]) and norm(p[0]) == norm(p[1])
                for p in plan["path_maps"]
            ),
            "Unmapped cwd",
        )
        roots = [
            p[1]
            for p in plan["path_maps"]
            if norm(cwd) == norm(p[1]) or norm(cwd).startswith(norm(p[1]) + "\\")
        ]
        require(len(set(roots)) == 1, "Ambiguous target project root")
        root = roots[0]
        target_roots[tid] = root
        old = state.execute("select * from threads where id=?", (tid,)).fetchone()
        if old:
            require(
                norm(old["cwd"]) == norm(cwd),
                "Existing thread belongs to a different project",
            )
            project_ids[tid] = old["project_id"]
        elif "project_roots" in state_tables:
            found = [
                r["project_id"]
                for r in state.execute("select project_id,path from project_roots")
                if norm(r["path"]) == norm(root)
            ]
            require(
                len(found) == 1,
                "Add the target project in Codex before importing; project root must be unique",
            )
            project_ids[tid] = found[0]
        else:
            require(not plan["catalog"], "Desktop target project registry is missing")
            project_ids[tid] = None
    for (kind, table), (cols, rows) in incoming.items():
        c = state if kind == "state" else history
        if table == "threads":
            for row in rows:
                old = c.execute(
                    "select * from threads where id=?", (row["id"],)
                ).fetchone()
                row["cwd"] = mapped(row["cwd"], plan, home)
                relative = ntpath.relpath(
                    row["rollout_path"].removeprefix("\\\\?\\"), plan["source_home"]
                ).replace("\\", "/")
                row["rollout_path"] = str(safe_child(home, relative))
                row["sandbox_policy"] = sandbox(row["sandbox_policy"], plan, home)
                row["project_id"] = project_ids[row["id"]]
                require(
                    relative in {x["relative"] for x in plan["logs"]},
                    "Canonical log is not packaged",
                )
                if old:
                    for key in ("archived", "archived_at"):
                        row[key] = old[key]
                    c.execute(
                        "update threads set "
                        + ",".join(quote(k) + "=?" for k in cols if k != "id")
                        + " where id=?",
                        [row[k] for k in cols if k != "id"] + [row["id"]],
                    )
                else:
                    c.execute(
                        "insert into threads ("
                        + ",".join(quote(k) for k in cols)
                        + ") values ("
                        + ",".join("?" for _ in cols)
                        + ")",
                        [row[k] for k in cols],
                    )
        else:
            if existing:
                c.execute(
                    "delete from "
                    + quote(table)
                    + " where thread_id in ("
                    + ",".join("?" for _ in ids)
                    + ")",
                    ids,
                )
            if rows:
                c.executemany(
                    "insert into "
                    + quote(table)
                    + " ("
                    + ",".join(quote(k) for k in cols)
                    + ") values ("
                    + ",".join("?" for _ in cols)
                    + ")",
                    [tuple(row[k] for k in cols) for row in rows],
                )
    for kind, c in [("state", state), ("history", history)]:
        c.commit()
        check(c)
        require(
            digests(c, ids, TABLES[kind]) == keep[kind],
            "Unrelated database rows changed",
        )
        close_snapshot(c)
    if "desktop" in clones:
        c = connect(clones["desktop"])
        stack.callback(c.close)
        cols = columns(c, "local_thread_catalog")
        for row in plan["catalog"]:
            row = dict(row)
            require(set(row) == set(cols), "Desktop catalog schema mismatch")
            tid = row["thread_id"]
            old = c.execute(
                "select * from local_thread_catalog where host_id='local' and thread_id=?",
                (tid,),
            ).fetchone()
            if old:
                c.execute(
                    "update local_thread_catalog set display_title=?,source_updated_at=?,source_recency_at=? where host_id='local' and thread_id=?",
                    (
                        row["display_title"],
                        row["source_updated_at"],
                        row["source_recency_at"],
                        tid,
                    ),
                )
            else:
                row["cwd"] = mapped(row["cwd"], plan, home)
                row["project_id"] = project_ids[tid]
                row["missing_candidate"] = 0
                c.execute(
                    "insert into local_thread_catalog ("
                    + ",".join(quote(k) for k in cols)
                    + ") values ("
                    + ",".join("?" for _ in cols)
                    + ")",
                    [row[k] for k in cols],
                )
        c.commit()
        require(
            digests(c, ids, ("local_thread_catalog",), True) == keep["desktop"],
            "Unrelated desktop rows changed",
        )
        close_snapshot(c)
    targets = [
        {
            "destination": str(target_paths[kind]),
            "prepared": str(p),
            "after_sha256": sha(p),
            "kind": kind,
            "role": "database",
        }
        for kind, p in clones.items()
    ] + log_targets
    global_path = home / ".codex-global-state.json"
    if plan["catalog"]:
        require(global_path.is_file(), "Desktop global state missing")
        obj = load(global_path)
        old = copy.deepcopy(obj)
        assignments = obj.setdefault("thread-project-assignments", {})

        def values(x):
            if isinstance(x, dict):
                return [v for item in x.values() for v in values(item)]
            if isinstance(x, list):
                return [v for item in x for v in values(item)]
            return [x]

        for row in plan["catalog"]:
            tid = row["thread_id"]
            matches = [
                pid
                for pid, data in obj.get("local-projects", {}).items()
                if any(
                    isinstance(v, str) and norm(v) == norm(target_roots[tid])
                    for v in values(data)
                )
            ]
            require(
                len(matches) == 1,
                "Target desktop project must be registered exactly once",
            )
            assignments[tid] = {"projectKind": "local", "projectId": matches[0]}
        comparison = copy.deepcopy(obj)
        for tid in ids:
            for x in (old, comparison):
                x.get("thread-project-assignments", {}).pop(tid, None)
        for x in (old, comparison):
            if not x.get("thread-project-assignments"):
                x.pop("thread-project-assignments", None)
        require(old == comparison, "Unrelated global state changed")
        dest = work / "global-state.json"
        dump(dest, obj)
        targets.append(
            {
                "destination": str(global_path),
                "prepared": str(dest),
                "before_sha256": sha(global_path),
                "after_sha256": sha(dest),
                "role": "global-state",
            }
        )
    index = home / "session_index.jsonl"
    old = index.read_bytes() if index.exists() else b""
    lines = []
    seen = set()
    for line in old.splitlines(keepends=True):
        if not line.strip():
            lines.append(line)
            continue
        row = json.loads(line)
        if row.get("id") in ids:
            row["thread_name"] = source_threads[row["id"]]["name"]
            seen.add(row["id"])
            line = (
                json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode()
                + b"\n"
            )
        lines.append(line)
    for tid in ids:
        if tid not in seen:
            if lines and not lines[-1].endswith(b"\n"):
                lines[-1] += b"\n"
            lines.append(
                json.dumps(
                    {
                        "id": tid,
                        "thread_name": source_threads[tid]["name"],
                        "updated_at": datetime.datetime.fromtimestamp(
                            source_threads[tid]["updated_at"], datetime.timezone.utc
                        ).isoformat(),
                    },
                    ensure_ascii=False,
                ).encode()
                + b"\n"
            )
    dest = work / "session_index.jsonl"
    dest.write_bytes(b"".join(lines))
    targets.append(
        {
            "destination": str(index),
            "prepared": str(dest),
            "before_sha256": sha(index) if index.exists() else None,
            "after_sha256": sha(dest),
            "role": "session-index",
        }
    )
    images = []
    for row in {x["name"]: x for x in plan["images"]}.values():
        live = safe_child(image_root, row["name"])
        require(
            not live.exists() or sha(live) == row["sha256"],
            "Image destination collision",
        )
        images.append(
            {
                "destination": str(live),
                "prepared": str(stage / "payload/images" / row["name"]),
                "before_sha256": sha(live) if live.exists() else None,
                "after_sha256": row["sha256"],
                "role": "user-image",
            }
        )
    targets += images
    exported.close()
    require(
        len({norm(x["destination"]) for x in targets}) == len(targets),
        "Duplicate publish targets",
    )
    result = {
        "status": "PREPARED_NOT_APPLIED",
        "format_version": 1,
        "run_id": plan["run_id"],
        "stage": str(stage),
        "prepared_root": str(work),
        "home": str(home),
        "sqlite_home": str(dbroot),
        "image_root": str(image_root),
        "ids": ids,
        "mode": "refresh" if existing else "initial",
        "counts": counts,
        "compatibility": sorted(compatibility),
        "schema_modified": False,
        "unrelated_rows_preserved": True,
        "database_paths": {k: str(v) for k, v in target_paths.items() if k in clones},
        "database_digests": before,
        "targets": targets,
        "created_at": datetime.datetime.now().astimezone().isoformat(),
    }
    dump(work / "validation.json", result)
    return result


def validate_prepared(work, home, dbroot):
    work = Path(work).resolve()
    r = load(work / "validation.json")
    require(
        r["format_version"] == 1 and r["status"] == "PREPARED_NOT_APPLIED",
        "Wrong prepared state",
    )
    require(
        norm(r["prepared_root"]) == norm(work)
        and norm(r["home"]) == norm(home)
        and norm(r["sqlite_home"]) == norm(dbroot),
        "Prepared/live roots mismatch",
    )
    expected = layout(home, dbroot)
    allowed_roots = [Path(home).resolve(), Path(r["image_root"]).resolve()]
    require(
        not any(
            root.is_relative_to(Path(r["stage"]).resolve()) for root in allowed_roots
        ),
        "Live destination is inside staging",
    )
    for t in r["targets"]:
        destination = Path(t["destination"]).resolve()
        src = Path(t["prepared"]).resolve()
        require(
            src.is_relative_to(work)
            or src.is_relative_to(Path(r["stage"]) / "payload/images"),
            "Prepared source outside stage",
        )
        if t["role"] == "database":
            require(
                norm(destination) == norm(expected[t["kind"]]),
                "Unexpected database target",
            )
        else:
            require(
                any(destination.is_relative_to(root) for root in allowed_roots),
                "Unexpected live destination",
            )
        for p in (destination, *destination.parents):
            require(
                not p.is_symlink()
                and not (hasattr(p, "is_junction") and p.is_junction()),
                "Linked publication target",
            )
        require(src.is_file(), "Prepared source missing")
        require(sha(src) == t["after_sha256"], "Prepared file changed")
        if t["role"] == "database":
            with closing(connect(src, True)) as c:
                check(c)
    return r


def apply(work, home, dbroot):
    r = validate_prepared(work, home, dbroot)
    work = Path(work).resolve()
    stage = Path(r["stage"])
    require(
        not (stage / "apply-result.json").exists(), "This batch was already applied"
    )
    offline(r["database_paths"].values())
    for kind, p in r["database_paths"].items():
        with closing(connect(p, True)) as c:
            require(
                digests(c) == r["database_digests"][kind],
                "Recipient database changed since prepare; prepare again",
            )
    for t in r["targets"]:
        if t["role"] != "database":
            require(
                (sha(t["destination"]) if Path(t["destination"]).exists() else None)
                == t["before_sha256"],
                "Recipient file changed since prepare",
            )
    backup = stage / (
        "backup-"
        + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        + "-"
        + os.urandom(3).hex()
    )
    backup.mkdir()
    entries = []
    for i, t in enumerate(r["targets"]):
        p = Path(t["destination"])
        saved = backup / (str(i) + ".bin")
        exists = p.exists()
        if exists:
            shutil.copy2(p, saved)
        entry = {
            **t,
            "backup_file": str(saved),
            "existed": exists,
            "before_file_sha256": sha(p) if exists else None,
        }
        if t["role"] == "database":
            for suffix in ("-wal", "-shm"):
                side = Path(str(p) + suffix)
                if side.exists():
                    shutil.copy2(side, Path(str(saved) + suffix))
        entries.append(entry)
    manifest = {
        "format_version": 1,
        "run_id": r["run_id"],
        "home": str(Path(home).resolve()),
        "sqlite_home": str(Path(dbroot).resolve()),
        "image_root": r["image_root"],
        "database_paths": r["database_paths"],
        "entries": entries,
        "rollback_used": False,
    }
    dump(backup / "backup-manifest.json", manifest)
    published = []
    try:
        offline(r["database_paths"].values())
        for t in entries:
            live = Path(t["destination"])
            live.parent.mkdir(parents=True, exist_ok=True)
            published.append(t)
            if t["role"] == "database":
                for suffix in ("-wal", "-shm"):
                    Path(str(live) + suffix).unlink(missing_ok=True)
            temp = live.with_name(".mig-" + os.urandom(6).hex())
            try:
                shutil.copy2(t["prepared"], temp)
                os.replace(temp, live)
            finally:
                temp.unlink(missing_ok=True)
            require(sha(live) == sha(t["prepared"]), "Publication SHA mismatch")
            t["published_sha256"] = sha(live)
        manifest["entries"] = entries
        dump(backup / "backup-manifest.json", manifest)
        for p in r["database_paths"].values():
            with closing(connect(p, True)) as c:
                check(c)
        manifest["published_database_digests"] = {}
        for kind, p in r["database_paths"].items():
            with closing(connect(p, True)) as c:
                manifest["published_database_digests"][kind] = digests(c)
        dump(backup / "backup-manifest.json", manifest)
    except BaseException:
        restore_entries(published)
        dump(
            stage / "rollback-result.json",
            {
                "status": "ROLLED_BACK_AFTER_ERROR",
                "backup": str(backup),
                "run_id": r["run_id"],
            },
        )
        raise
    result = {
        **r,
        "status": "APPLIED_RESTART_VERIFICATION_PENDING",
        "backup": str(backup),
        "completed_at": datetime.datetime.now().astimezone().isoformat(),
    }
    dump(stage / "apply-result.json", result)
    return {k: v for k, v in result.items() if k not in ("targets", "database_digests")}


def restore_entries(entries):
    for t in reversed(entries):
        live = Path(t["destination"])
        saved = Path(t["backup_file"])
        if t["existed"]:
            require(
                saved.is_file() and sha(saved) == t["before_file_sha256"],
                "Backup is missing or corrupt",
            )
            shutil.copy2(saved, live)
        else:
            live.unlink(missing_ok=True)
        if t["role"] == "database":
            for suffix in ("-wal", "-shm"):
                src = Path(str(saved) + suffix)
                dest = Path(str(live) + suffix)
                if src.exists():
                    shutil.copy2(src, dest)
                else:
                    dest.unlink(missing_ok=True)


def rollback(backup, home, dbroot):
    backup = Path(backup).resolve()
    r = load(backup / "backup-manifest.json")
    require(
        norm(r["home"]) == norm(home) and norm(r["sqlite_home"]) == norm(dbroot),
        "Backup/live roots mismatch",
    )
    require(not r["rollback_used"], "Backup already rolled back")
    offline(r["database_paths"].values())
    require(
        "published_database_digests" in r,
        "Backup did not record a successful publication",
    )
    for kind, p in r["database_paths"].items():
        require(
            norm(p) == norm(layout(home, dbroot)[kind]),
            "Unexpected rollback database root",
        )
        with closing(connect(p, True)) as c:
            require(
                digests(c) == r["published_database_digests"][kind],
                "Live data changed after migration, including WAL; do not overwrite later work",
            )
    for t in r["entries"]:
        require(
            Path(t["backup_file"]).resolve().is_relative_to(backup),
            "Backup path escapes backup root",
        )
        live = Path(t["destination"]).resolve()
        if t["role"] == "database":
            require(
                norm(live) == norm(layout(home, dbroot)[t["kind"]]),
                "Unexpected rollback database",
            )
        else:
            require(
                live.is_relative_to(Path(home).resolve())
                or live.is_relative_to(Path(r["image_root"]).resolve()),
                "Rollback destination escapes live roots",
            )
        require(
            t.get("published_sha256")
            and Path(t["destination"]).is_file()
            and sha(t["destination"]) == t["published_sha256"],
            "Live data changed after migration; do not overwrite later work",
        )
        if t["existed"]:
            require(
                sha(t["backup_file"]) == t["before_file_sha256"], "Backup SHA mismatch"
            )
    restore_entries(r["entries"])
    r["rollback_used"] = True
    dump(backup / "backup-manifest.json", r)
    return {"status": "ROLLED_BACK", "backup": str(backup)}


def verify(stage):
    stage = Path(stage).resolve()
    r = load(stage / "apply-result.json")
    issues = []
    for t in r["targets"]:
        if t["role"] == "database":
            with (
                closing(connect(t["destination"], True)) as actual,
                closing(connect(t["prepared"], True)) as expected,
            ):
                check(actual)
                for table in TABLES.get(t["kind"], ("local_thread_catalog",)):
                    field = "id" if table == "threads" else "thread_id"
                    a = selected(actual, table, r["ids"], field)
                    b = selected(expected, table, r["ids"], field)
                    if a != b:
                        issues.append(
                            {
                                "database": t["kind"],
                                "table": table,
                                "reason": "selected rows differ from prepared rows",
                            }
                        )
        elif t["role"] == "global-state":
            if not Path(t["destination"]).is_file():
                issues.append(
                    {"role": "global-state", "reason": "missing desktop state"}
                )
            else:
                actual = load(t["destination"]).get("thread-project-assignments", {})
                expected = load(t["prepared"]).get("thread-project-assignments", {})
                if any(actual.get(tid) != expected.get(tid) for tid in r["ids"]):
                    issues.append(
                        {
                            "role": "global-state",
                            "reason": "selected project assignments differ",
                        }
                    )
        elif (
            not Path(t["destination"]).is_file()
            or sha(t["destination"]) != t["after_sha256"]
        ):
            issues.append({"role": t["role"], "reason": "missing or SHA differs"})
    result = {
        "status": (
            "DATA_VERIFICATION_PASSED" if not issues else "DATA_VERIFICATION_FAILED"
        ),
        "run_id": r["run_id"],
        "issues": issues,
        "counts": r["counts"],
        "unrelated_rows_preserved_at_apply": r["unrelated_rows_preserved"],
        "backup": r["backup"],
        "gui_pixel_verification_performed": False,
        "completed_at": datetime.datetime.now().astimezone().isoformat(),
    }
    dump(stage / "verify-result.json", result)
    return result
