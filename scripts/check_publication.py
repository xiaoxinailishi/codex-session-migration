"""Guard the public source tree; this is not a general secret detector."""

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ROOT_FILES = {
    ".gitignore",
    ".gitattributes",
    "LICENSE",
    "pyproject.toml",
    "README.md",
    "README.en.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
}
ALLOWED_DIRS = {"codex_migrate", "tests", "scripts", "docs", ".github", "examples"}
ALLOWED_SUFFIXES = {".py", ".ps1", ".md", ".yml", ".yaml", ".toml"}
PATTERNS = {
    "possible credential": re.compile(
        r"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
    ),
    "private Windows profile": re.compile(
        r"[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|Example\b|<)[^\\/\s\"\']+", re.I
    ),
    "private transfer token": re.compile(
        r"[\"\']token[\"\']\s*:\s*[\"\'][A-Za-z0-9_-]{24,}[\"\']"
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked-only", action="store_true")
    args = parser.parse_args()
    if args.tracked_only:
        raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        paths = [ROOT / p.decode("utf-8") for p in raw.split(b"\0") if p]
    else:
        paths = [ROOT / p for p in ALLOWED_ROOT_FILES if (ROOT / p).is_file()]
        for name in ALLOWED_DIRS:
            paths.extend(
                p
                for p in (ROOT / name).rglob("*")
                if p.is_file() and "__pycache__" not in p.parts
            )
    failures = []
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            failures.append((str(relative), "linked public source"))
            continue
        if (len(relative.parts) == 1 and relative.name not in ALLOWED_ROOT_FILES) or (
            len(relative.parts) > 1
            and (
                relative.parts[0] not in ALLOWED_DIRS
                or path.suffix not in ALLOWED_SUFFIXES
            )
        ):
            failures.append((str(relative), "file outside publication allowlist"))
            continue
        try:
            body = path.read_text("utf-8-sig")
        except (UnicodeError, OSError):
            failures.append((str(relative), "non-text or unreadable public file"))
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(body):
                failures.append((str(relative), label))
    if failures:
        for path, label in failures:
            print(f"BLOCKED {path}: {label}")
        return 1
    print(
        f"PASS: {len(paths)} allowlisted text files; no configured private-data pattern found."
    )
    print(
        "Manual review remains required: selected conversation text can contain secrets."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
