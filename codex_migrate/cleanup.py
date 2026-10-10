"""Only the user's explicitly selected, unchanged staging entries can be deleted."""

import os, shutil
from pathlib import Path
from .common import dump, load, require, safe_child, sha


def tree_signature(path):
    p = Path(path)
    items = []
    total = 0
    if p.is_file():
        return {
            "files": [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p)}],
            "bytes": p.stat().st_size,
        }
    for current, dirs, files in os.walk(p, followlinks=False):
        for name in dirs + files:
            q = Path(current) / name
            require(
                not q.is_symlink()
                and not (hasattr(q, "is_junction") and q.is_junction()),
                "Linked cleanup entry is blocked",
            )
        for name in sorted(files):
            q = Path(current) / name
            total += q.stat().st_size
            items.append(
                {
                    "path": q.relative_to(p).as_posix(),
                    "bytes": q.stat().st_size,
                    "sha256": sha(q),
                }
            )
    return {"files": sorted(items, key=lambda x: x["path"]), "bytes": total}


def cleanup_plan(stage):
    stage = Path(stage).resolve()
    require((stage / "manifest.json").is_file(), "Not a migration stage")
    applied = (
        load(stage / "apply-result.json")
        if (stage / "apply-result.json").exists()
        else None
    )
    verified = (
        load(stage / "verify-result.json")
        if (stage / "verify-result.json").exists()
        else None
    )
    entries = []
    protected = (
        [Path(applied[k]).resolve() for k in ("home", "sqlite_home", "image_root")]
        if applied
        else []
    )
    require(
        not any(root.is_relative_to(stage) or stage == root for root in protected),
        "Stage contains live data; cleanup blocked",
    )
    for p in sorted(stage.iterdir()):
        role = "temporary"
        recommendation = "review after data and GUI verification"
        if p.name.startswith("backup-"):
            role = "rollback-backup"
            recommendation = "keep by default; deleting this removes a rollback option"
        elif p.name == "payload":
            role = "export-and-baseline"
            recommendation = (
                "keep export.sqlite separately if needed as the next refresh baseline"
            )
        elif p.name.startswith("prepared-"):
            role = "prepared-snapshot"
            recommendation = "temporary copy; verify-result references it, so keep until verification is finished"
        elif p.name == "snapshots" or p.name == "bundle.zip" or p.suffix == ".part":
            role = "temporary-package"
        else:
            role = "evidence-or-control"
            recommendation = "keep small manifests, validation reports and instructions"
        entries.append(
            {
                "path": p.name,
                "role": role,
                "recommendation": recommendation,
                "delete_selected": False,
                "signature": tree_signature(p),
            }
        )
    result = {
        "format_version": 1,
        "stage": str(stage),
        "status": "USER_DECISION_REQUIRED",
        "automatic_delete": False,
        "data_verification_passed": bool(
            verified and verified["status"] == "DATA_VERIFICATION_PASSED"
        ),
        "backups_selected_by_default": False,
        "protected_live_roots": [str(p) for p in protected],
        "entries": entries,
    }
    dump(stage / "cleanup-plan.json", result)
    return result


def cleanup(plan_path, selections, allow_backups=False):
    plan_path = Path(plan_path).resolve()
    r = load(plan_path)
    stage = Path(r["stage"]).resolve()
    require(
        plan_path.parent == stage and r["format_version"] == 1,
        "Cleanup plan/root mismatch",
    )
    require(r["data_verification_passed"], "Complete data verification before cleanup")
    require(selections, "Select named entries explicitly; no default deletion")
    verified = load(stage / "verify-result.json")
    require(
        verified["status"] == "DATA_VERIFICATION_PASSED",
        "Current verification report no longer passes",
    )
    applied = load(stage / "apply-result.json")
    require(
        verified["run_id"]
        == applied["run_id"]
        == load(stage / "manifest.json")["run_id"],
        "Cleanup run IDs differ",
    )
    protected = [
        Path(applied[k]).resolve() for k in ("home", "sqlite_home", "image_root")
    ]
    require(
        not any(root.is_relative_to(stage) for root in protected),
        "Stage contains live data; cleanup blocked",
    )
    candidates = {x["path"]: x for x in r["entries"]}
    paths = []
    for name in dict.fromkeys(selections):
        require(name in candidates, "Path is not in the cleanup plan")
        entry = candidates[name]
        require(
            allow_backups or entry["role"] != "rollback-backup",
            "Backups are kept unless --delete-backups is explicitly set",
        )
        p = safe_child(stage, name)
        require(
            p != stage and p.exists(), "Cannot delete the stage root or a missing entry"
        )
        require(
            tree_signature(p) == entry["signature"],
            "Entry changed since cleanup planning; create a fresh plan",
        )
        require(
            not any(p == root or root.is_relative_to(p) for root in protected),
            "Selected entry contains live data",
        )
        paths.append(p)
    removed = []
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        removed.append(p.name)
    result = {
        "status": "USER_SELECTED_ENTRIES_DELETED",
        "deleted": removed,
        "backups_deleted": allow_backups,
    }
    dump(stage / "cleanup-result.json", result)
    return result
