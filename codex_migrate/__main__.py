import argparse, json, sqlite3, sys
from pathlib import Path
from .common import MigrationError, default_home, dump, load, sqlite_root
from . import cleanup, exporter, importer, transport


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Codex session migration / chat history backup, restore, image repair and selected sync"
    )
    parser.add_argument("--version", action="version", version="0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)

    def home_options(p):
        p.add_argument(
            "--home",
            type=Path,
            default=default_home(),
            help="Actual CODEX_HOME (default: environment or ~/.codex)",
        )
        p.add_argument(
            "--sqlite-home",
            type=Path,
            help="Actual SQLite root; overrides configured sqlite_home for this tool",
        )

    p = commands.add_parser(
        "inventory", help="Read-only database versions and thread IDs"
    )
    home_options(p)
    p.add_argument("--output", type=Path)
    p = commands.add_parser(
        "export", help="Read-only selected conversations plus user image bytes"
    )
    home_options(p)
    p.add_argument(
        "--thread",
        action="append",
        default=[],
        help="Thread UUID; repeat to select more than one",
    )
    p.add_argument(
        "--under", help="Select threads whose cwd is under this exact project root"
    )
    p.add_argument(
        "--map",
        action="append",
        required=True,
        help="Source project root=recipient project root; repeat as needed",
    )
    p.add_argument("--output", required=True, type=Path)
    p.add_argument(
        "--baseline",
        type=Path,
        help="Previous successful export stage or export.sqlite for refresh conflict detection",
    )
    p.add_argument(
        "--image-choices",
        type=Path,
        help="Private JSON mapping ambiguous image source paths to recovered SHA256",
    )
    p = commands.add_parser(
        "check-package", help="Read-only full payload SHA and scope check"
    )
    p.add_argument("--stage", required=True, type=Path)
    p = commands.add_parser(
        "prepare", help="Build and validate recipient copies; never updates live files"
    )
    home_options(p)
    p.add_argument("--stage", required=True, type=Path)
    p.add_argument("--image-root", type=Path)
    p = commands.add_parser(
        "apply",
        help="Offline only: backup, guarded publication and automatic rollback on error",
    )
    home_options(p)
    p.add_argument("--prepared", required=True, type=Path)
    p.add_argument(
        "--confirm",
        action="store_true",
        required=True,
        help="Required explicit acknowledgement; still checks Codex is offline",
    )
    p = commands.add_parser(
        "verify",
        help="Read-only post-apply data checks; GUI acceptance remains separate",
    )
    p.add_argument("--stage", required=True, type=Path)
    p = commands.add_parser(
        "rollback",
        help="Offline only: restore a backup only if later work would not be overwritten",
    )
    home_options(p)
    p.add_argument("--backup", required=True, type=Path)
    p.add_argument("--confirm", action="store_true", required=True)
    p = commands.add_parser(
        "serve", help="Optional, peer-restricted temporary LAN HTTP transfer"
    )
    p.add_argument("--stage", required=True, type=Path)
    p.add_argument("--bind", required=True)
    p.add_argument("--peer", required=True)
    p.add_argument("--port", type=int, default=49473)
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--connection", required=True, type=Path)
    p = commands.add_parser(
        "receive", help="Download/resume, verify and extract; does not update live data"
    )
    p.add_argument("--connection", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = commands.add_parser(
        "cleanup-plan",
        help="List staging disk use and backup roles without deleting anything",
    )
    p.add_argument("--stage", required=True, type=Path)
    p = commands.add_parser(
        "cleanup",
        help="Delete only named, unchanged staging entries selected by the user",
    )
    p.add_argument("--plan", required=True, type=Path)
    p.add_argument("--select", action="append", required=True)
    p.add_argument(
        "--delete-backups",
        action="store_true",
        help="Explicitly allow a selected rollback backup to be deleted",
    )
    p.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "home"):
            args.home = args.home.expanduser().resolve()
            dbroot = sqlite_root(args.home, args.sqlite_home)
        if args.command == "inventory":
            result = exporter.inventory(args.home, dbroot)
            if args.output:
                dump(args.output, result)
        elif args.command == "export":
            maps = []
            for pair in args.map:
                if "=" not in pair:
                    raise MigrationError("--map needs source=target")
                source, target = pair.split("=", 1)
                if not source or not target:
                    raise MigrationError("Empty path mapping")
                maps.append([source, target])
            result = exporter.export(
                args.home,
                dbroot,
                args.thread,
                args.output,
                maps,
                args.under,
                args.baseline,
                load(args.image_choices) if args.image_choices else None,
            )
        elif args.command == "check-package":
            plan = exporter.verify_package(args.stage)
            result = {
                "status": "PACKAGE_VERIFIED",
                "run_id": plan["run_id"],
                "threads": len(plan["ids"]),
            }
        elif args.command == "prepare":
            result = importer.prepare(args.stage, args.home, dbroot, args.image_root)
            result = {
                k: v
                for k, v in result.items()
                if k not in ("targets", "database_digests")
            }
        elif args.command == "apply":
            result = importer.apply(args.prepared, args.home, dbroot)
        elif args.command == "verify":
            result = importer.verify(args.stage)
        elif args.command == "rollback":
            result = importer.rollback(args.backup, args.home, dbroot)
        elif args.command == "serve":
            transport.serve(
                args.stage,
                args.bind,
                args.peer,
                args.port,
                args.minutes,
                args.connection,
            )
            return 0
        elif args.command == "receive":
            result = transport.receive(args.connection, args.output)
        elif args.command == "cleanup-plan":
            result = cleanup.cleanup_plan(args.stage)
            result = {
                **result,
                "entries": [
                    {k: v for k, v in e.items() if k != "signature"}
                    | {
                        "bytes": e["signature"]["bytes"],
                        "files": len(e["signature"]["files"]),
                    }
                    for e in result["entries"]
                ],
            }
        elif args.command == "cleanup":
            result = cleanup.cleanup(args.plan, args.select, args.delete_backups)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") == "DATA_VERIFICATION_FAILED" else 0
    except (MigrationError, OSError, ValueError, sqlite3.Error) as exc:
        print(
            json.dumps({"status": "STOPPED", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
