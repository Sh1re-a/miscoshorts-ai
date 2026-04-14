from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from app.paths import OUTPUT_CACHE_DIR
from app.runtime import pipeline_compat_signature
from app.storage import atomic_write_json

CACHE_ENABLED = os.getenv("LOCAL_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def video_cache_key(video_url: str) -> str:
    return hashlib.sha1(video_url.encode("utf-8")).hexdigest()[:16]


def cache_dir_for_url(video_url: str) -> Path:
    return OUTPUT_CACHE_DIR / pipeline_compat_signature() / video_cache_key(video_url)


def is_valid_transcript_payload(payload: dict | None) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("text"), str)
        and isinstance(payload.get("segments"), list)
    )


def find_cached_video(video_url: str) -> Path | None:
    if not CACHE_ENABLED:
        return None

    cache_dir = cache_dir_for_url(video_url)
    matches = sorted(cache_dir.glob("source.*"))
    for match in matches:
        if match.is_file() and match.stat().st_size > 1024:
            return match
    return None


def restore_cached_video(video_url: str, destination_base: Path) -> Path | None:
    cached_video = find_cached_video(video_url)
    if cached_video is None:
        return None

    destination_path = destination_base.with_suffix(cached_video.suffix)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached_video, destination_path)
    return destination_path


def store_cached_video(video_url: str, video_path: Path) -> None:
    if not CACHE_ENABLED or not video_path.exists():
        return

    cache_dir = cache_dir_for_url(video_url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for existing in cache_dir.glob("source.*"):
        existing.unlink(missing_ok=True)
    shutil.copy2(video_path, cache_dir / f"source{video_path.suffix.lower()}")


def load_cached_transcript(video_url: str) -> dict | None:
    if not CACHE_ENABLED:
        return None

    transcript_path = cache_dir_for_url(video_url) / "transcript.json"
    if not transcript_path.exists():
        return None

    try:
        payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if is_valid_transcript_payload(payload) else None


def store_cached_transcript(video_url: str, transcript: dict) -> None:
    if not CACHE_ENABLED or not is_valid_transcript_payload(transcript):
        return

    atomic_write_json(cache_dir_for_url(video_url) / "transcript.json", transcript)


def _clip_analysis_cache_path(video_url: str, clip_count: int) -> Path:
    return cache_dir_for_url(video_url) / f"clip_analysis_v1_{clip_count}.json"


def _is_valid_clip_candidates(payload: object) -> bool:
    if not isinstance(payload, list):
        return False
    for item in payload:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("start"), (int, float)) or not isinstance(item.get("end"), (int, float)):
            return False
    return True


def load_cached_clip_candidates(video_url: str, clip_count: int) -> list[dict] | None:
    if not CACHE_ENABLED:
        return None
    cache_path = _clip_analysis_cache_path(video_url, clip_count)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    candidates = payload.get("candidates")
    return candidates if _is_valid_clip_candidates(candidates) else None


def store_cached_clip_candidates(video_url: str, clip_count: int, candidates: list[dict]) -> None:
    if not CACHE_ENABLED or not _is_valid_clip_candidates(candidates):
        return
    atomic_write_json(
        _clip_analysis_cache_path(video_url, clip_count),
        {
            "clipCount": clip_count,
            "candidates": candidates,
        },
    )
