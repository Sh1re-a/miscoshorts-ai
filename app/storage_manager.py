from __future__ import annotations

import json
import time
from pathlib import Path

from app.media_cache import cache_dir_for_url
from app.paths import OUTPUT_CACHE_DIR, OUTPUTS_DIR, OUTPUT_JOBS_DIR, OUTPUT_TEMP_DIR
from app.render_session import list_active_fingerprint_locks
from app.storage import atomic_write_json, path_size, prune_runtime_storage, storage_summary

TERMINAL_JOB_STATUSES = {"completed", "failed"}
ACTIVE_JOB_STATUSES = {"queued", "validating", "downloading", "transcribing", "analyzing", "rendering"}
JOB_STATE_DIR = OUTPUTS_DIR / "_job_state"


def _cache_breakdown() -> dict[str, int | str]:
    source_media_bytes = 0
    transcript_bytes = 0
    clip_analysis_bytes = 0
    other_bytes = 0
    source_media_files = 0
    transcript_files = 0
    clip_analysis_files = 0

    if OUTPUT_CACHE_DIR.exists():
        for path in OUTPUT_CACHE_DIR.rglob("*"):
            if not path.is_file():
                continue
            size = path_size(path)
            if path.name.startswith("source."):
                source_media_bytes += size
                source_media_files += 1
            elif path.name == "transcript.json":
                transcript_bytes += size
                transcript_files += 1
            elif path.name.startswith("clip_analysis_") and path.suffix == ".json":
                clip_analysis_bytes += size
                clip_analysis_files += 1
            else:
                other_bytes += size

    return {
        "path": str(OUTPUT_CACHE_DIR),
        "bytes": path_size(OUTPUT_CACHE_DIR),
        "sourceMediaBytes": source_media_bytes,
        "sourceMediaFiles": source_media_files,
        "transcriptBytes": transcript_bytes,
        "transcriptFiles": transcript_files,
        "clipAnalysisBytes": clip_analysis_bytes,
        "clipAnalysisFiles": clip_analysis_files,
        "otherBytes": other_bytes,
    }


def _job_state_counts(jobs_by_id: dict[str, dict]) -> dict[str, int]:
    counts = {
        "active": 0,
        "queued": 0,
        "completed": 0,
        "failed": 0,
    }
    for job in jobs_by_id.values():
        status = job.get("status")
        if status in ACTIVE_JOB_STATUSES:
            counts["active"] += 1
        elif status == "queued":
            counts["queued"] += 1
        elif status == "completed":
            counts["completed"] += 1
        elif status == "failed":
            counts["failed"] += 1
    return counts


def _normalize_output_dir(raw: str) -> str:
    """Normalize output directory path for consistent cross-platform comparison."""
    stripped = raw.strip()
    if not stripped:
        return ""
    return str(Path(stripped))


def _output_dir_reference_counts(jobs_by_id: dict[str, dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs_by_id.values():
        output_dir = _normalize_output_dir(str((job.get("result") or {}).get("outputDir") or ""))
        if not output_dir:
            continue
        counts[output_dir] = counts.get(output_dir, 0) + 1
    return counts


def _job_storage_entry(job_id: str, job: dict, output_dir_reference_counts: dict[str, int]) -> dict | None:
    status = str(job.get("status") or "")
    if status not in TERMINAL_JOB_STATUSES:
        return None

    result = job.get("result") or {}
    output_dir_raw = str(result.get("outputDir") or "").strip()
    output_dir = Path(output_dir_raw) if output_dir_raw else None
    output_exists = bool(output_dir and output_dir.exists())
    output_dir_key = str(output_dir) if output_dir is not None else ""
    source_dir = output_dir / "source" if output_dir is not None else None
    source_media_bytes = path_size(source_dir) if source_dir is not None else 0
    clips_dir = output_dir / "clips" if output_dir is not None else None
    clip_bytes = path_size(clips_dir) if clips_dir is not None else 0
    diagnostics_dir = output_dir / "diagnostics" if output_dir is not None else None
    diagnostics_bytes = path_size(diagnostics_dir) if diagnostics_dir is not None else 0
    meta_dir = output_dir / "meta" if output_dir is not None else None
    metadata_bytes = path_size(meta_dir) if meta_dir is not None else 0
    source_cache_dir = None
    source_cache_bytes = 0
    video_url = str(result.get("videoUrl") or job.get("videoUrl") or "").strip()
    if video_url:
        source_cache_dir = cache_dir_for_url(video_url)
        source_cache_bytes = sum(
            path_size(path)
            for path in source_cache_dir.glob("source.*")
            if path.is_file()
        )

    shared_output_refs = output_dir_reference_counts.get(output_dir_key, 0) if output_dir_key else 0
    return {
        "jobId": job_id,
        "status": status,
        "updatedAt": job.get("updatedAt"),
        "videoUrl": video_url or None,
        "jobFingerprint": result.get("jobFingerprint") or job.get("jobFingerprint"),
        "outputDir": output_dir_key or None,
        "outputExists": output_exists,
        "sharedOutputRefs": shared_output_refs,
        "canDeleteJob": bool(output_dir_key) and output_exists and shared_output_refs <= 1,
        "canDeleteSourceMedia": bool(source_dir and source_dir.exists() and source_media_bytes > 0),
        "canDeleteStateOnly": True,
        "storage": {
            "clipsBytes": clip_bytes,
            "sourceMediaBytes": source_media_bytes,
            "diagnosticsBytes": diagnostics_bytes,
            "metadataBytes": metadata_bytes,
            "outputBytes": path_size(output_dir) if output_dir is not None else 0,
            "sourceCacheBytes": source_cache_bytes,
        },
    }


def build_storage_report(jobs_by_id: dict[str, dict]) -> dict[str, object]:
    output_dir_reference_counts = _output_dir_reference_counts(jobs_by_id)
    jobs_summary = storage_summary()
    cache_summary = _cache_breakdown()
    manageable_jobs = [
        entry
        for job_id, job in sorted(
            jobs_by_id.items(),
            key=lambda item: (float(item[1].get("updatedAt") or item[1].get("createdAt") or 0), item[0]),
            reverse=True,
        )
        if (entry := _job_storage_entry(job_id, job, output_dir_reference_counts)) is not None
    ]

    return {
        "summary": {
            **jobs_summary,
            "cache": cache_summary,
        },
        "jobStateCounts": _job_state_counts(jobs_by_id),
        "manageableJobs": manageable_jobs,
        "recommendations": {
            "canPruneTemp": jobs_summary["temp"]["bytes"] > 0,
            "canPruneCache": cache_summary["bytes"] > 0,
            "canCleanFinishedJobs": any(job["canDeleteJob"] for job in manageable_jobs),
            "canDeleteJobSourceMedia": any(job["canDeleteSourceMedia"] for job in manageable_jobs),
        },
    }


def _job_state_path(job_id: str) -> Path:
    return JOB_STATE_DIR / f"{job_id}.json"


def _job_output_dir(job: dict) -> Path | None:
    output_dir = str((job.get("result") or {}).get("outputDir") or "").strip()
    return Path(output_dir) if output_dir else None


def _active_resource_protection(jobs_by_id: dict[str, dict]) -> dict[str, set[str]]:
    protected_temp_paths: set[str] = set()
    protected_cache_paths: set[str] = set()
    protected_job_paths: set[str] = set()

    active_job_ids = {
        job_id
        for job_id, job in jobs_by_id.items()
        if str(job.get("status") or "") in ACTIVE_JOB_STATUSES
    }
    active_fingerprints = {
        str(job.get("jobFingerprint"))
        for job in jobs_by_id.values()
        if str(job.get("status") or "") in ACTIVE_JOB_STATUSES and job.get("jobFingerprint")
    }

    for job_id, job in jobs_by_id.items():
        status = str(job.get("status") or "")
        if status not in ACTIVE_JOB_STATUSES:
            continue

        video_url = str(job.get("videoUrl") or (job.get("result") or {}).get("videoUrl") or "").strip()
        if video_url:
            protected_cache_paths.add(str(cache_dir_for_url(video_url)))

        output_dir = _job_output_dir(job)
        if output_dir is not None:
            protected_job_paths.add(str(output_dir))

        fingerprint = str(job.get("jobFingerprint") or "").strip()
        if fingerprint:
            protected_job_paths.add(str(OUTPUT_JOBS_DIR / fingerprint))

    for lock in list_active_fingerprint_locks():
        fingerprint = str(lock.get("fingerprint") or "").strip()
        if fingerprint:
            active_fingerprints.add(fingerprint)
            protected_job_paths.add(str(OUTPUT_JOBS_DIR / fingerprint))

    if OUTPUT_TEMP_DIR.exists():
        for child in OUTPUT_TEMP_DIR.iterdir():
            name = child.name
            if any(name.startswith(f"{fingerprint}-") for fingerprint in active_fingerprints):
                protected_temp_paths.add(str(child))
                continue
            if name.startswith("render-") and any(job_id[:8] in name for job_id in active_job_ids):
                protected_temp_paths.add(str(child))

    return {
        "temp": protected_temp_paths,
        "cache": protected_cache_paths,
        "jobs": protected_job_paths,
    }


def _remove_path(path: Path, *, dry_run: bool) -> dict[str, int]:
    existed = path.exists()
    removed_bytes = path_size(path)
    if not dry_run:
        if path.is_dir():
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    return {"removedItems": 1 if existed else 0, "removedBytes": removed_bytes}


def delete_job_storage(jobs_by_id: dict[str, dict], job_id: str, *, mode: str, dry_run: bool = False) -> dict[str, object]:
    job = jobs_by_id.get(job_id)
    if job is None:
        raise ValueError("Job not found.")

    status = str(job.get("status") or "")
    if status in ACTIVE_JOB_STATUSES:
        raise ValueError("Cannot delete storage for a job that is still queued or rendering.")
    if status not in TERMINAL_JOB_STATUSES:
        raise ValueError("Only completed or failed jobs can be cleaned up.")

    output_dir_reference_counts = _output_dir_reference_counts(jobs_by_id)
    output_dir = _job_output_dir(job)
    result = job.get("result") or {}

    if mode == "source_media":
        if output_dir is None:
            raise ValueError("This job has no finished output folder to clean.")
        source_dir = output_dir / "source"
        if not source_dir.exists():
            return {"jobId": job_id, "mode": mode, "removedItems": 0, "removedBytes": 0}
        stats = _remove_path(source_dir, dry_run=dry_run)
        manifest_path = output_dir / "meta" / "result.json"
        if not dry_run and manifest_path.exists():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                payload["sourceMediaPresent"] = False
                payload["sourceMediaDeletedAt"] = payload.get("sourceMediaDeletedAt") or time.time()
                atomic_write_json(manifest_path, payload)
        return {"jobId": job_id, "mode": mode, **stats}

    if mode == "job":
        output_dir_key = str(output_dir) if output_dir is not None else ""
        if output_dir_key and output_dir_reference_counts.get(output_dir_key, 0) > 1:
            raise ValueError("Cannot delete this finished render because another saved job still points to the same output.")

        removed_items = 0
        removed_bytes = 0
        if output_dir is not None and output_dir.exists():
            output_stats = _remove_path(output_dir, dry_run=dry_run)
            removed_items += output_stats["removedItems"]
            removed_bytes += output_stats["removedBytes"]
        job_state = _job_state_path(job_id)
        if job_state.exists():
            state_stats = _remove_path(job_state, dry_run=dry_run)
            removed_items += state_stats["removedItems"]
            removed_bytes += state_stats["removedBytes"]
        return {"jobId": job_id, "mode": mode, "removedItems": removed_items, "removedBytes": removed_bytes}

    raise ValueError("Unknown cleanup mode.")


def prune_storage(jobs_by_id: dict[str, dict], *, prune_temp: bool, prune_cache: bool, prune_jobs: bool, prune_failed_jobs: bool, dry_run: bool = False) -> dict[str, object]:
    protected_paths = _active_resource_protection(jobs_by_id)
    report = prune_runtime_storage(
        dry_run=dry_run,
        prune_temp=prune_temp,
        prune_cache=prune_cache,
        prune_jobs=prune_jobs,
        protected_temp_paths=protected_paths["temp"],
        protected_cache_paths=protected_paths["cache"],
        protected_job_paths=protected_paths["jobs"],
    )

    removed_failed_job_ids: list[str] = []
    removed_failed_job_bytes = 0
    if prune_failed_jobs:
        for job_id, job in jobs_by_id.items():
            if str(job.get("status") or "") != "failed":
                continue
            job_state = _job_state_path(job_id)
            if not job_state.exists():
                continue
            removed_failed_job_ids.append(job_id)
            removed_failed_job_bytes += path_size(job_state)
            if not dry_run:
                job_state.unlink(missing_ok=True)

    report["failedJobs"] = {
        "removedItems": len(removed_failed_job_ids),
        "removedBytes": removed_failed_job_bytes,
        "removedJobIds": removed_failed_job_ids,
    }
    report["protectedPaths"] = {
        "temp": sorted(protected_paths["temp"]),
        "cache": sorted(protected_paths["cache"]),
        "jobs": sorted(protected_paths["jobs"]),
    }
    return report
