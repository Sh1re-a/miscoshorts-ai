from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from app.paths import LOGS_DIR, MODEL_CACHE_DIR, OUTPUT_CACHE_DIR, OUTPUT_JOBS_DIR, OUTPUT_TEMP_DIR

TEMP_RETENTION_HOURS = max(1, int(os.getenv("TEMP_RETENTION_HOURS", "12")))
CACHE_RETENTION_DAYS = max(1, int(os.getenv("CACHE_RETENTION_DAYS", "30")))
JOB_OUTPUT_RETENTION_DAYS = max(1, int(os.getenv("JOB_OUTPUT_RETENTION_DAYS", "30")))


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, indent=2))
        temp_path = Path(handle.name)
    temp_path.replace(path)


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        return 0


def storage_summary() -> dict[str, dict[str, int | str]]:
    return {
        "jobs": {"path": str(OUTPUT_JOBS_DIR), "bytes": path_size(OUTPUT_JOBS_DIR)},
        "cache": {"path": str(OUTPUT_CACHE_DIR), "bytes": path_size(OUTPUT_CACHE_DIR)},
        "temp": {"path": str(OUTPUT_TEMP_DIR), "bytes": path_size(OUTPUT_TEMP_DIR)},
        "logs": {"path": str(LOGS_DIR), "bytes": path_size(LOGS_DIR)},
        "modelCache": {"path": str(MODEL_CACHE_DIR), "bytes": path_size(MODEL_CACHE_DIR)},
    }


def _age_seconds(path: Path) -> float:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 0.0


def _result_last_used(path: Path) -> float | None:
    manifest_path = path / "meta" / "result.json"
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return float(payload.get("lastUsedAt") or payload.get("generatedAt") or 0.0)
    except (TypeError, ValueError):
        return None


def _remove_path(path: Path, dry_run: bool) -> int:
    removed_bytes = path_size(path)
    if dry_run:
        return removed_bytes
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    return removed_bytes


def _prune_children(root: Path, *, max_age_seconds: float, dry_run: bool, last_used_reader=None) -> dict[str, int]:
    removed_items = 0
    removed_bytes = 0
    if not root.exists():
        return {"removedItems": 0, "removedBytes": 0}

    for child in root.iterdir():
        last_used_at = last_used_reader(child) if last_used_reader else None
        age_seconds = max(0.0, time.time() - last_used_at) if last_used_at else _age_seconds(child)
        if age_seconds < max_age_seconds:
            continue
        removed_items += 1
        removed_bytes += _remove_path(child, dry_run)

    return {"removedItems": removed_items, "removedBytes": removed_bytes}


def prune_runtime_storage(*, dry_run: bool = False) -> dict:
    temp_stats = _prune_children(
        OUTPUT_TEMP_DIR,
        max_age_seconds=TEMP_RETENTION_HOURS * 3600,
        dry_run=dry_run,
    )
    cache_stats = _prune_children(
        OUTPUT_CACHE_DIR,
        max_age_seconds=CACHE_RETENTION_DAYS * 86400,
        dry_run=dry_run,
    )
    job_stats = _prune_children(
        OUTPUT_JOBS_DIR,
        max_age_seconds=JOB_OUTPUT_RETENTION_DAYS * 86400,
        dry_run=dry_run,
        last_used_reader=_result_last_used,
    )
    return {
        "dryRun": dry_run,
        "temp": temp_stats,
        "cache": cache_stats,
        "jobs": job_stats,
        "summary": storage_summary(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or prune MicroShorts AI storage.")
    parser.add_argument("--prune", action="store_true", help="Prune stale temp/cache/output folders.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without deleting anything.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    args = parser.parse_args(argv)

    report = prune_runtime_storage(dry_run=args.dry_run) if args.prune else {"summary": storage_summary(), "dryRun": args.dry_run}
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        for key, payload in report["summary"].items():
            print(f"{key}: {payload['path']} ({payload['bytes']} bytes)")
        if args.prune:
            print("")
            for bucket in ("temp", "cache", "jobs"):
                stats = report[bucket]
                print(f"{bucket}: removed {stats['removedItems']} item(s), reclaimed {stats['removedBytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
