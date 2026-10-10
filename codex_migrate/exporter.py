import datetime, json, os, shutil, sqlite3, uuid, zipfile
from contextlib import ExitStack, closing
from pathlib import Path
from .common import (
    check,
    columns,
    connect,
    dump,
    layout,
    load,
    migrations,
    norm,
    quote,
    require,
    safe_child,
    sha,
)
from .images import image_paths, recover, rewrite_log, prose_digest, transform

TABLES = {
    "state": ("threads", "thread_dynamic_tools", "thread_attachments"),
    "history": (
        "thread_turns",
        "thread_items",
        "thread_history_projection_state",
        "thread_realtime_items",
    ),
}


def inventory(home, dbroot):
    result = {
        "home": str(home),
        "sqlite_home": str(dbroot),
        "databases": {},
        "threads": [],
    }
    for kind, path in layout(home, dbroot).items():
        if not path.exists():
            continue
        with closing(connect(path, True)) as c:
            check(c)
            result["databases"][kind] = {
                "path": str(path),
                "migrations": (
                    migrations(c)
                    if "_sqlx_migrations"
                    in {
                        x[0]
                        for x in c.execute(
                            "select name from sqlite_master where type='table'"
                        )
                    }
                    else []
                ),
            }
            if kind == "state":
                result["threads"] = [
                    dict(x)
                    for x in c.execute(
                        "select id,name,cwd,rollout_path,archived from threads order by updated_at desc"
                    )
                ]
    return result


def snapshot(source, dest, points):
    dest.parent.mkdir(parents=True, exist_ok=True)
    limit = source.stat().st_size
    pos = 0
    bounds = {0}
    with source.open("rb") as src, dest.open("xb") as out:
        while pos < limit:
            line = src.readline(limit - pos)
            if not line.endswith(b"\n"):
                break
            out.write(line)
            pos += len(line)
            if pos in points:
                bounds.add(pos)
    require(pos > 0, "Empty or incomplete rollout")
    return set(points).issubset(bounds)


def export(home, dbroot, ids, output, maps, under=None, baseline=None, choices=None):
    with ExitStack() as stack:
        return _export(home, dbroot, ids, output, maps, under, baseline, choices, stack)


def _export(home, dbroot, ids, output, maps, under, baseline, choices, stack):
    home = Path(home).resolve()
    output = Path(output).resolve()
    require(not output.exists(), "Output already exists; use a fresh staging directory")
    require(
        not output.is_relative_to(home), "Output cannot be inside source CODEX_HOME"
    )
    require(maps, "At least one source=target project path mapping is required")
    paths = layout(home, dbroot)
    output.mkdir(parents=True)
    payload = output / "payload"
    payload.mkdir()
    out = connect(payload / "export.sqlite")
    stack.callback(out.close)
    out.execute(
        "create table metadata(kind TEXT,table_name TEXT,export_name TEXT PRIMARY KEY,columns_json TEXT,ddl TEXT,migrations_json TEXT,row_count INTEGER)"
    )
    with closing(connect(paths["state"], True)) as c:
        if under:
            ids.extend(
                x[0]
                for x in c.execute("select id,cwd from threads")
                if norm(x[1]) == norm(under)
                or norm(x[1]).startswith(norm(under) + "\\")
            )
    ids = list(dict.fromkeys(ids))
    require(ids, "No selected threads")
    require(
        all(str(uuid.UUID(t)) == t for t in ids), "Thread IDs must be canonical UUIDs"
    )
    markers = ",".join("?" for _ in ids)
    counts = {}
    metadata = []
    for kind, names in TABLES.items():
        with closing(connect(paths[kind], True)) as c:
            c.execute("begin")
            for table in names:
                if not c.execute(
                    "select 1 from sqlite_master where type='table' and name=?",
                    (table,),
                ).fetchone():
                    continue
                cols = columns(c, table)
                ddl = c.execute(
                    "select sql from sqlite_master where name=?", (table,)
                ).fetchone()[0]
                dest = kind + "__" + table
                out.execute("create table " + quote(dest) + " " + ddl[ddl.index("(") :])
                field = "id" if table == "threads" else "thread_id"
                rows = c.execute(
                    "select * from "
                    + quote(table)
                    + " where "
                    + quote(field)
                    + " in ("
                    + markers
                    + ")",
                    ids,
                ).fetchall()
                if rows:
                    out.executemany(
                        "insert into "
                        + quote(dest)
                        + " values ("
                        + ",".join("?" for _ in cols)
                        + ")",
                        [tuple(x) for x in rows],
                    )
                versions = migrations(c)
                out.execute(
                    "insert into metadata values(?,?,?,?,?,?,?)",
                    (
                        kind,
                        table,
                        dest,
                        json.dumps(cols),
                        ddl,
                        json.dumps(versions),
                        len(rows),
                    ),
                )
                counts[dest] = len(rows)
    threads = {r["id"]: dict(r) for r in out.execute("select * from state__threads")}
    require(set(threads) == set(ids), "Some selected threads are absent")
    require(
        counts.get("state__thread_attachments", 0) == 0,
        "Non-image thread attachments require a separate attachment-aware workflow; export stopped",
    )
    require(
        counts.get("history__thread_realtime_items", 0) == 0,
        "Selected realtime stream is active; wait for it to finish",
    )
    active = out.execute(
        "select 1 from history__thread_turns where lower(status) in ('inprogress','in_progress','running') limit 1"
    ).fetchone()
    require(not active, "A selected turn is still running; wait before exporting")
    catalog = []
    if paths["desktop"].exists():
        with closing(connect(paths["desktop"], True)) as c:
            catalog = [
                dict(r)
                for r in c.execute(
                    "select * from local_thread_catalog where host_id=? and thread_id in ("
                    + markers
                    + ")",
                    ["local"] + ids,
                )
            ]
    points = {t: set() for t in ids}
    for r in out.execute("select * from history__thread_turns"):
        points[r["thread_id"]].update(
            r[k]
            for k in ("rollout_byte_offset", "rollout_end_byte_offset")
            if r[k] is not None
        )
    for r in out.execute("select * from history__thread_history_projection_state"):
        points[r["thread_id"]].add(r["next_rollout_byte_offset"])
    found = {}
    for folder in ("sessions", "archived_sessions"):
        for current, dirs, files in os.walk(home / folder, followlinks=False):
            dirs[:] = [
                d
                for d in dirs
                if not (Path(current) / d).is_symlink()
                and not (
                    hasattr(Path(current) / d, "is_junction")
                    and (Path(current) / d).is_junction()
                )
            ]
            for name in files:
                owners = [t for t in ids if t in name]
                if name.endswith(".jsonl") and owners:
                    p = Path(current) / name
                    require(not p.is_symlink(), "Linked rollout")
                    require(len(owners) == 1, "Ambiguous rollout filename")
                    found[norm(p)] = (p, owners[0])
    for tid, row in threads.items():
        p = Path(row["rollout_path"].removeprefix("\\\\?\\")).resolve()
        require(p.is_relative_to(home), "Rollout is outside CODEX_HOME")
        require(not p.is_symlink(), "Linked canonical rollout")
        found[norm(p)] = (p, tid)
    snapshots = output / "snapshots"
    logs = []
    for source, tid in found.values():
        relative = source.relative_to(home).as_posix()
        dest = safe_child(snapshots, relative)
        aligned = snapshot(source, dest, points[tid])
        logs.append(
            {
                "relative": relative,
                "thread_id": tid,
                "snapshot": str(dest),
                "source_path": str(source),
                "offsets_align": aligned,
            }
        )
    anchors = {}
    for tid, row in threads.items():
        owned = [x for x in logs if x["thread_id"] == tid]
        canonical = next(
            x for x in owned if norm(x["source_path"]) == norm(row["rollout_path"])
        )
        if canonical["offsets_align"]:
            anchors[tid] = canonical["relative"]
        else:
            compatible = [x for x in owned if x["offsets_align"]]
            require(len(compatible) == 1, "Cannot identify a unique history anchor log")
            anchors[tid] = compatible[0]["relative"]
    history = [dict(x) for x in out.execute("select * from history__thread_items")]
    images = recover(
        history, [x["snapshot"] for x in logs], payload / "images", choices
    )
    out.execute("create table image_baseline(source_path TEXT PRIMARY KEY,sha256 TEXT)")
    out.executemany(
        "insert into image_baseline values(?,?)",
        [(norm(r["source_path"]), r["sha256"]) for r in images],
    )
    # Paths are finalized on the receiver; source payload records retain original paths.
    if baseline:
        prior = Path(baseline)
        prior = prior / "payload/export.sqlite" if prior.is_dir() else prior
        require(prior.is_file(), "Baseline export.sqlite missing")
        shutil.copy2(prior, payload / "baseline.sqlite")
    plan = {
        "format_version": 1,
        "run_id": str(uuid.uuid4()),
        "source_home": str(home),
        "source_sqlite_home": str(dbroot),
        "ids": ids,
        "path_maps": maps,
        "counts": counts,
        "catalog": catalog,
        "anchors": anchors,
        "logs": [
            {"relative": x["relative"], "thread_id": x["thread_id"]} for x in logs
        ],
        "images": images,
        "created_at": datetime.datetime.now().astimezone().isoformat(),
        "scope": "SELECTED_SESSION_RECORDS_AND_USER_IMAGES_ONLY",
        "project_files_included": False,
        "credentials_included": False,
    }
    out.commit()
    require(
        [r[0] for r in out.execute("pragma quick_check")] == ["ok"],
        "Export SQLite integrity check failed",
    )
    out.close()
    for row in logs:
        dest = safe_child(payload / "home", row["relative"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(row["snapshot"], dest)
    dump(payload / "plan.json", plan)
    files = []
    for p in sorted(payload.rglob("*")):
        if p.is_file():
            files.append(
                {
                    "path": p.relative_to(payload).as_posix(),
                    "bytes": p.stat().st_size,
                    "sha256": sha(p),
                }
            )
    manifest = {"format_version": 1, "run_id": plan["run_id"], "files": files}
    dump(output / "manifest.json", manifest)
    with zipfile.ZipFile(
        output / "bundle.zip",
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=1,
        allowZip64=True,
    ) as z:
        for row in files:
            z.write(payload / row["path"], row["path"])
    with zipfile.ZipFile(output / "bundle.zip") as z:
        require(z.testzip() is None, "Archive CRC failed")
    result = {
        "status": "EXPORTED",
        "run_id": plan["run_id"],
        "threads": len(ids),
        "counts": counts,
        "user_image_paths": len(images),
        "unique_image_assets": len({x["name"] for x in images}),
        "raw_logs": len(logs),
        "archive_bytes": (output / "bundle.zip").stat().st_size,
        "archive_sha256": sha(output / "bundle.zip"),
        "project_files": 0,
        "credentials": 0,
        "source_unchanged": True,
    }
    dump(output / "export-result.json", result)
    return result


def verify_package(stage):
    stage = Path(stage)
    manifest = load(stage / "manifest.json")
    require(manifest["format_version"] == 1, "Unsupported bundle format")
    seen = set()
    for row in manifest["files"]:
        require(row["path"] not in seen, "Duplicate manifest path")
        seen.add(row["path"])
        p = safe_child(stage / "payload", row["path"])
        require(
            row["path"]
            in (
                "export.sqlite",
                "baseline.sqlite",
                "plan.json",
                "image-recovery-audit.json",
            )
            or row["path"].startswith(
                ("home/sessions/", "home/archived_sessions/", "images/")
            ),
            "Unexpected file scope",
        )
        require(
            p.is_file()
            and p.stat().st_size == row["bytes"]
            and sha(p) == row["sha256"],
            "Payload SHA or size mismatch: " + row["path"],
        )
    actual = {
        p.relative_to(stage / "payload").as_posix()
        for p in (stage / "payload").rglob("*")
        if p.is_file()
    }
    require(actual == seen, "Unlisted payload files")
    plan = load(stage / "payload/plan.json")
    require(plan["run_id"] == manifest["run_id"], "Run ID mismatch")
    return plan
