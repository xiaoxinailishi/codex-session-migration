"""Recover exact user image bytes and change structured paths, never prose."""

import base64, bisect, hashlib, json, re
from pathlib import Path
from .common import MigrationError, dump, norm, require, sha

IMAGE_TAG = re.compile(r'<image\b[^>]*\bpath="([^"]+)"')


def image_paths(obj):
    found = []

    def walk(x):
        if isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            if x.get("type") in ("localImage", "local_image") and isinstance(
                x.get("path"), str
            ):
                found.append(x["path"])
            if x.get("type") == "user_message":
                found.extend(p for p in x.get("local_images", []) if isinstance(p, str))
            for v in x.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    walk(obj)
    return found


def transform(obj, mapping):
    changed = 0
    if isinstance(obj, list):
        return sum(transform(v, mapping) for v in obj)
    if not isinstance(obj, dict):
        return 0
    if obj.get("type") in ("localImage", "local_image") and isinstance(
        obj.get("path"), str
    ):
        value = mapping.get(norm(obj["path"]))
        if value and value != obj["path"]:
            obj["path"] = value
            changed += 1
    if obj.get("type") == "input_text" and isinstance(obj.get("text"), str):
        match = re.fullmatch(r'(<image\b[^>]*\bpath=")([^"]+)("[^>]*>)', obj["text"])
        if match and norm(match.group(2)) in mapping:
            value = mapping[norm(match.group(2))]
            if value != match.group(2):
                obj["text"] = match.group(1) + value + match.group(3)
                changed += 1
    if obj.get("type") == "user_message" and isinstance(obj.get("local_images"), list):
        for i, p in enumerate(obj["local_images"]):
            if isinstance(p, str) and norm(p) in mapping and p != mapping[norm(p)]:
                obj["local_images"][i] = mapping[norm(p)]
                changed += 1
    for v in obj.values():
        if isinstance(v, (dict, list)):
            changed += transform(v, mapping)
    return changed


def prose_digest(obj):
    h = hashlib.sha256()

    def walk(x):
        if isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            for k, v in x.items():
                if (
                    k in ("text", "message")
                    and isinstance(v, str)
                    and not (
                        x.get("type") == "input_text"
                        and re.fullmatch(r'<image\b[^>]*\bpath="[^"]+"[^>]*>', v)
                    )
                ):
                    h.update(k.encode())
                    h.update(v.encode())
                    h.update(b"\0")
                elif isinstance(v, (list, dict)):
                    walk(v)

    walk(obj)
    return h.hexdigest()


def canonical(obj):
    if isinstance(obj, list):
        return [canonical(v) for v in obj]
    if not isinstance(obj, dict):
        return obj
    obj = {k: canonical(v) for k, v in obj.items()}
    if obj.get("type") in ("localImage", "local_image"):
        obj["path"] = "<image>"
    if (
        obj.get("type") == "input_text"
        and isinstance(obj.get("text"), str)
        and re.fullmatch(r'<image\b[^>]*\bpath="[^"]+"[^>]*>', obj["text"])
    ):
        obj["text"] = IMAGE_TAG.sub(
            lambda m: m.group(0).replace(m.group(1), "<image>"), obj["text"]
        )
    if obj.get("type") == "user_message" and isinstance(obj.get("local_images"), list):
        obj["local_images"] = ["<image>" for _ in obj["local_images"]]
    return obj


def suffix(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise MigrationError(
        "Unsupported referenced image format; PNG/JPEG/GIF/WebP are supported"
    )


def recover(history, logs, assets, choices=None):
    refs = {}
    assets = Path(assets)
    assets.mkdir()
    choices = choices or {}

    def add(path):
        return refs.setdefault(norm(path), {"source_path": path, "candidates": {}})

    def accept(path, data, kind):
        row = add(path)
        digest = hashlib.sha256(data).hexdigest()
        name = digest + suffix(data)
        dest = assets / name
        if not dest.exists():
            dest.write_bytes(data)
        item = row["candidates"].setdefault(
            digest, {"name": name, "sha256": digest, "bytes": len(data), "kinds": []}
        )
        if kind not in item["kinds"]:
            item["kinds"].append(kind)

    for row in history:
        if row["item_type"] == "userMessage":
            for p in image_paths(json.loads(row["item_json"])):
                add(p)
    for log in logs:
        with Path(log).open("r", encoding="utf-8") as f:
            for line in f:
                if '"response_item"' not in line[:250] or '"input_image"' not in line:
                    continue
                record = json.loads(line)
                obj = record.get("payload", {})
                if (
                    record.get("type") != "response_item"
                    or obj.get("type") != "message"
                    or obj.get("role") != "user"
                ):
                    continue
                pending = None
                for part in obj.get("content", []):
                    if part.get("type") == "input_text":
                        m = IMAGE_TAG.search(part.get("text", ""))
                        if m:
                            pending = m.group(1)
                            add(pending)
                        elif part.get("text", "").strip() == "</image>":
                            pending = None
                    elif part.get("type") == "input_image" and pending:
                        uri = part.get("image_url", "")
                        if uri.startswith("data:image/") and ";base64," in uri:
                            accept(
                                pending,
                                base64.b64decode(uri.split(",", 1)[1], validate=True),
                                "transmitted_bitmap",
                            )
                            pending = None
    for row in refs.values():
        p = Path(row["source_path"].removeprefix("\\\\?\\"))
        if p.is_file():
            accept(row["source_path"], p.read_bytes(), "original_file")
    audit = {
        "paths": list(refs.values()),
        "unresolved": [r["source_path"] for r in refs.values() if not r["candidates"]],
    }
    dump(assets.parent / "image-recovery-audit.json", audit)
    require(
        not audit["unresolved"],
        "Some user images cannot be recovered; inspect image-recovery-audit.json",
    )
    selected = []
    for key, row in refs.items():
        options = list(row["candidates"].values())
        original = [c for c in options if "original_file" in c["kinds"]]
        pick = choices.get(row["source_path"]) or choices.get(key)
        if pick:
            require(pick in row["candidates"], "Image choice SHA was not recovered")
            chosen = row["candidates"][pick]
        elif original:
            require(len(original) == 1, "Multiple original images")
            chosen = original[0]
        else:
            require(
                len(options) == 1,
                "Ambiguous image variants; provide --image-choices JSON mapping source path to SHA",
            )
            chosen = options[0]
        selected.append({"source_path": row["source_path"], **chosen})
    chosen_names = {x["name"] for x in selected}
    for p in assets.iterdir():
        if p.name not in chosen_names:
            p.unlink()
    return selected


def rewrite_log(source, dest, mapping, points):
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = Path(source).stat().st_size
    pos = newpos = lines = changes = 0
    boundaries = {0}
    deltas = []
    with Path(source).open("rb") as src, Path(dest).open("wb") as out:
        for line in src:
            pos += len(line)
            lines += 1
            if pos in points:
                boundaries.add(pos)
            new = line
            if any(
                x in line
                for x in (b"localImage", b"local_image", b"local_images", b"<image")
            ):
                obj = json.loads(line)
                prose = prose_digest(obj)
                count = transform(obj, mapping)
                if count:
                    require(prose_digest(obj) == prose, "Historical prose changed")
                    ending = (
                        b"\r\n"
                        if line.endswith(b"\r\n")
                        else b"\n" if line.endswith(b"\n") else b""
                    )
                    new = (
                        json.dumps(
                            obj, ensure_ascii=False, separators=(",", ":")
                        ).encode()
                        + ending
                    )
                    deltas.append((pos, len(new) - len(line)))
                    changes += count
            out.write(new)
            newpos += len(new)
    require(size == Path(source).stat().st_size, "Snapshot changed while rewriting")
    require(
        set(points).issubset(boundaries),
        "History byte offsets do not align with the anchor log",
    )
    ends = [x[0] for x in deltas]
    prefix = [0]
    for _, delta in deltas:
        prefix.append(prefix[-1] + delta)
    return {
        "bytes": newpos,
        "lines": lines,
        "image_reference_changes": changes,
        "sha256": sha(dest),
        "offset_map": {p: p + prefix[bisect.bisect_right(ends, p)] for p in points},
    }
