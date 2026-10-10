import hmac, http.server, ipaddress, json, os, re, secrets, shutil, time, urllib.request, zipfile
from pathlib import Path
from .common import dump, load, require, safe_child, sha
from .exporter import verify_package


def serve(stage, bind, peer, port, minutes, connection):
    stage = Path(stage).resolve()
    plan = verify_package(stage)
    require(1 <= minutes <= 120, "Server lifetime must be 1..120 minutes")
    ipaddress.ip_address(bind)
    ipaddress.ip_address(peer)
    require(
        ":" not in bind and ":" not in peer,
        "This transport currently supports IPv4 only",
    )
    require(1 <= port <= 65535, "Invalid port")
    info = {
        "format_version": 1,
        "host": bind,
        "port": port,
        "peer": peer,
        "token": secrets.token_urlsafe(32),
        "run_id": plan["run_id"],
        "archive_sha256": sha(stage / "bundle.zip"),
        "manifest_sha256": sha(stage / "manifest.json"),
        "archive_bytes": (stage / "bundle.zip").stat().st_size,
    }
    connection = Path(connection).resolve()
    require(
        not connection.exists(), "Connection file already exists; choose a fresh file"
    )
    connection.parent.mkdir(parents=True, exist_ok=True)
    dump(connection, info)
    try:
        os.chmod(connection, 0o600)
    except OSError:
        pass
    allowed = {
        "/bundle.zip": stage / "bundle.zip",
        "/manifest.json": stage / "manifest.json",
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.client_address[0] != peer or not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + info["token"]
            ):
                self.send_error(403)
                return
            p = allowed.get(self.path)
            if p is None:
                self.send_error(404)
                return
            size = p.stat().st_size
            start = 0
            header = self.headers.get("Range")
            if header:
                match = re.fullmatch(r"bytes=(\d+)-", header)
                if not match or int(match.group(1)) >= size:
                    self.send_error(416)
                    return
                start = int(match.group(1))
            self.send_response(206 if header else 200)
            self.send_header("Content-Length", str(size - start))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            if header:
                self.send_header("Content-Range", f"bytes {start}-{size-1}/{size}")
            self.end_headers()
            try:
                with p.open("rb") as f:
                    f.seek(start)
                    for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
                        self.wfile.write(block)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = None
    try:
        server = http.server.ThreadingHTTPServer((bind, port), Handler)
        server.timeout = 1
        server.daemon_threads = True
        print(
            json.dumps(
                {
                    "status": "SERVING",
                    "connection_file": str(connection),
                    "allowed_peer": peer,
                    "port": port,
                    "expires_in_minutes": minutes,
                }
            ),
            flush=True,
        )
        deadline = time.monotonic() + minutes * 60
        while time.monotonic() < deadline:
            server.handle_request()
    finally:
        if server is not None:
            server.server_close()
        info["status"] = "REVOKED"
        info.pop("token", None)
        dump(connection, info)


def receive(connection, output):
    info = load(connection)
    require(
        info.get("format_version") == 1 and info.get("token"),
        "Missing or revoked private connection file",
    )
    ipaddress.ip_address(info["host"])
    require(":" not in info["host"], "This transport currently supports IPv4 only")
    require(1 <= int(info["port"]) <= 65535, "Invalid port")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(
        not (output / "payload").exists(),
        "Receiver payload already exists; use another output directory",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for name, expected in [
        ("manifest.json", info["manifest_sha256"]),
        ("bundle.zip", info["archive_sha256"]),
    ]:
        target = output / name
        part = output / (name + ".part")
        if target.exists():
            require(sha(target) == expected, "Existing download SHA mismatch")
            continue
        if part.exists() and sha(part) == expected:
            os.replace(part, target)
            continue
        start = part.stat().st_size if part.exists() else 0
        headers = {"Authorization": "Bearer " + info["token"]}
        if start:
            headers["Range"] = "bytes=" + str(start) + "-"
        request = urllib.request.Request(
            "http://" + info["host"] + ":" + str(info["port"]) + "/" + name,
            headers=headers,
        )
        with opener.open(request, timeout=90) as response:
            require(not start or response.status == 206, "Server did not honor resume")
            with part.open("ab" if start else "wb") as out:
                for block in iter(lambda: response.read(4 * 1024 * 1024), b""):
                    out.write(block)
        require(sha(part) == expected, "Downloaded SHA mismatch")
        os.replace(part, target)
    manifest = load(output / "manifest.json")
    require(manifest["run_id"] == info["run_id"], "Wrong run")
    rows = {x["path"]: x for x in manifest["files"]}
    require(len(rows) == len(manifest["files"]), "Duplicate manifest names")
    with zipfile.ZipFile(output / "bundle.zip") as z:
        require(
            len(z.namelist()) == len(rows) and set(z.namelist()) == set(rows),
            "Archive scope differs from manifest",
        )
        for name, row in rows.items():
            require(
                z.getinfo(name).file_size == row["bytes"],
                "Archive entry size differs from manifest",
            )
            dest = safe_child(output / "payload", name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, 4 * 1024 * 1024)
            require(sha(dest) == row["sha256"], "Extracted SHA mismatch")
    plan = verify_package(output)
    result = {
        "status": "RECEIVED_AND_VERIFIED",
        "run_id": plan["run_id"],
        "files": len(rows),
        "live_databases_modified": False,
    }
    dump(output / "receive-result.json", result)
    return result
