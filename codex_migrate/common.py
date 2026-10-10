import hashlib, json, ntpath, os, sqlite3, subprocess
from contextlib import closing
from pathlib import Path, PurePosixPath


class MigrationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise MigrationError(message)


def norm(value):
    return ntpath.normpath(str(value).removeprefix("\\\\?\\")).casefold()


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dump(path, obj):
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def load(path):
    return json.loads(Path(path).read_text("utf-8-sig"))


def connect(path, readonly=False):
    c = (
        sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=20)
        if readonly
        else sqlite3.connect(path)
    )
    c.row_factory = sqlite3.Row
    if readonly:
        c.execute("pragma query_only=on")
    return c


def check(c):
    require(
        [r[0] for r in c.execute("pragma quick_check")] == ["ok"],
        "SQLite quick_check failed",
    )
    require(
        not c.execute("pragma foreign_key_check").fetchone(),
        "SQLite foreign_key_check failed",
    )


def migrations(c):
    return [
        dict(r)
        for r in c.execute(
            "select version,description,success from _sqlx_migrations order by version"
        )
    ]


def tables(c):
    return [
        r[0]
        for r in c.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        )
    ]


def columns(c, table):
    return [r["name"] for r in c.execute("pragma table_info(" + quote(table) + ")")]


def table_digest(c, table, ids=(), desktop=False):
    cols = columns(c, table)
    args = []
    sql = "select * from " + quote(table)
    field = "id" if table == "threads" else "thread_id" if "thread_id" in cols else None
    if ids and field:
        sql += (
            " where not (" + quote(field) + " in (" + ",".join("?" for _ in ids) + ")"
        )
        args = list(ids)
        if desktop:
            sql += " and host_id=?"
            args.append("local")
        sql += ")"
    sql += " order by " + ",".join(str(i + 1) for i in range(len(cols)))
    h = hashlib.sha256()
    count = 0
    for r in c.execute(sql, args):
        h.update(
            json.dumps(
                [{"blob": v.hex()} if isinstance(v, bytes) else v for v in r],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        h.update(b"\n")
        count += 1
    return {"rows": count, "sha256": h.hexdigest()}


def digests(c, ids=(), touched=(), desktop=False):
    return {
        t: table_digest(
            c, t, ids if t in touched else (), desktop and t == "local_thread_catalog"
        )
        for t in tables(c)
    }


def clone(source, target):
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(source, True)) as src, closing(connect(target)) as dst:
        src.backup(dst)
        check(dst)


def safe_child(root, relative):
    p = PurePosixPath(relative)
    require(
        not p.is_absolute()
        and ".." not in p.parts
        and "\\" not in relative
        and ":" not in relative
        and bool(p.parts),
        "Unsafe relative path",
    )
    root = Path(root).resolve()
    result = root.joinpath(*p.parts)
    require(result.resolve().is_relative_to(root), "Destination escapes its root")
    for parent in (result, *result.parents):
        if parent == root.parent:
            break
        require(
            not parent.is_symlink()
            and not (hasattr(parent, "is_junction") and parent.is_junction()),
            "Linked destination is not supported",
        )
    return result


def offline(paths):
    require(
        os.name == "nt", "Live database publication currently supports Windows only"
    )
    command = "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(Codex|ChatGPT)(\\.exe)?$|^codex-app-server' -or ($_.Name -eq 'codex.exe' -and $_.ExecutablePath -notmatch 'sandbox') } | Select-Object ProcessId,Name) | ConvertTo-Json -Compress"
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
    )
    require(
        r.stdout.strip() in ("", "null", "[]"),
        "Completely quit Codex and its app-server before applying or rolling back",
    )
    values = ",".join(
        "'" + str(Path(p)).replace("'", "''") + "'" for p in paths if Path(p).exists()
    )
    command = (
        "$ErrorActionPreference='Stop'; foreach($taskPath in @("
        + values
        + ")){ $taskHandle=[IO.File]::Open($taskPath,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None); $taskHandle.Dispose() }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
    )


def default_home():
    return (
        Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        .expanduser()
        .resolve()
    )


def sqlite_root(home, override=None):
    if override:
        return Path(override).expanduser().resolve()
    import tomllib

    config = Path(home) / "config.toml"
    settings = tomllib.loads(config.read_text("utf-8-sig")) if config.exists() else {}
    value = settings.get("sqlite_home") or os.environ.get("CODEX_SQLITE_HOME")
    return Path(value).expanduser().resolve() if value else Path(home)


def layout(home, dbroot):
    return {
        "state": Path(dbroot) / "state_5.sqlite",
        "history": Path(dbroot) / "thread_history_1.sqlite",
        "desktop": Path(home) / "sqlite/codex-dev.db",
    }
