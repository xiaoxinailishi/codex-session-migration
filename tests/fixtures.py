import base64, json, sqlite3
from pathlib import Path

THREAD = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZ1kAAAAASUVORK5CYII="
)


def schema(home, root_version=8):
    home.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(home / "state_5.sqlite")
    c.executescript(
        """CREATE TABLE _sqlx_migrations(version INTEGER PRIMARY KEY,description TEXT,success INTEGER);
    INSERT INTO _sqlx_migrations VALUES(1,'fixture state',1);
    CREATE TABLE threads(id TEXT PRIMARY KEY,name TEXT,cwd TEXT,rollout_path TEXT,updated_at INTEGER,sandbox_policy TEXT,project_id TEXT,archived INTEGER,archived_at INTEGER);
    CREATE TABLE project_roots(project_id TEXT,position INTEGER,path TEXT);
    CREATE TABLE thread_dynamic_tools(thread_id TEXT,name TEXT);
    CREATE TABLE thread_attachments(thread_id TEXT,path TEXT);"""
    )
    c.commit()
    c.close()
    c = sqlite3.connect(home / "thread_history_1.sqlite")
    extra = ",root_turn_id TEXT" if root_version == 8 else ""
    c.executescript(
        """CREATE TABLE _sqlx_migrations(version INTEGER PRIMARY KEY,description TEXT,success INTEGER);
    INSERT INTO _sqlx_migrations VALUES(7,'thread item lifecycle timestamps',1);
    CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,item_id TEXT,item_type TEXT,item_json TEXT,PRIMARY KEY(thread_id,turn_id,item_id));
    CREATE TABLE thread_history_projection_state(thread_id TEXT PRIMARY KEY,next_rollout_byte_offset INTEGER);
    CREATE TABLE thread_realtime_items(thread_id TEXT,turn_id TEXT,item_id TEXT);
    CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,status TEXT,rollout_byte_offset INTEGER,rollout_end_byte_offset INTEGER"""
        + extra
        + ",PRIMARY KEY(thread_id,turn_id));"
    )
    if root_version == 8:
        c.execute("insert into _sqlx_migrations values(8,?,1)", ("thread turn root",))
    c.commit()
    c.close()


def add_thread(home, project, tid=THREAD, title="Synthetic chat", with_image=True):
    project.mkdir(parents=True, exist_ok=True)
    log = home / "sessions" / ("rollout-" + tid + ".jsonl")
    log.parent.mkdir(exist_ok=True)
    missing = home / "expired-upload.png"
    parts = [
        {
            "type": "input_text",
            "text": "Synthetic user message; prose must be preserved.",
        }
    ]
    if with_image:
        parts += [
            {"type": "input_text", "text": '<image path="' + str(missing) + '">'},
            {
                "type": "input_image",
                "image_url": "data:image/png;base64," + base64.b64encode(PNG).decode(),
            },
            {"type": "input_text", "text": "</image>"},
        ]
    record = {
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": parts},
    }
    data = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    log.write_bytes(data)
    c = sqlite3.connect(home / "state_5.sqlite")
    c.execute(
        "insert into threads values(?,?,?,?,?,?,?,?,?)",
        (
            tid,
            title,
            str(project),
            str(log),
            1,
            json.dumps({"type": "workspace-write", "writable_roots": [str(project)]}),
            "synthetic-project",
            0,
            None,
        ),
    )
    c.commit()
    c.close()
    c = sqlite3.connect(home / "thread_history_1.sqlite")
    hasroot = any(
        r[1] == "root_turn_id" for r in c.execute("pragma table_info(thread_turns)")
    )
    values = [tid, "turn-1", "completed", 0, len(data)]
    if hasroot:
        values.append("turn-1")
    c.execute(
        "insert into thread_turns values(" + ",".join("?" for _ in values) + ")", values
    )
    body = {
        "type": "userMessage",
        "content": [
            {"type": "text", "text": "Synthetic user message; prose must be preserved."}
        ],
    }
    if with_image:
        body["content"].append({"type": "localImage", "path": str(missing)})
    c.execute(
        "insert into thread_items values(?,?,?,?,?)",
        (tid, "turn-1", "item-1", "userMessage", json.dumps(body)),
    )
    c.execute(
        "insert into thread_history_projection_state values(?,?)", (tid, len(data))
    )
    c.commit()
    c.close()
    return log
