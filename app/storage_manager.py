from __future__ import annotations

import json
from pathlib import Path

from app.media_cache import cache_dir_for_url
from app.paths import OUTPUT_CACHE_DIR, OUTPUT_JOBS_DIR
from app.storage import path_size, storage_summary

TERMINAL_JOB_STATUSES = {"completed", "failed"}
ACTIVE_JOB_STATUSES = {"queued", "validating", "downloading", "transcribing", "analyzing", "rendering"}


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


def _output_dir_reference_counts(jobs_by_id: dict[str, dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs_by_id.values():
        output_dir = str((job.get("result") or {}).get("outputDir") or "").strip()
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
