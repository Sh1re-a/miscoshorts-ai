from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from app.paths import OUTPUT_CACHE_DIR

CACHE_ENABLED = os.getenv("LOCAL_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def video_cache_key(video_url: str) -> str:
    return hashlib.sha1(video_url.encode("utf-8")).hexdigest()[:16]


def cache_dir_for_url(video_url: str) -> Path:
    return OUTPUT_CACHE_DIR / video_cache_key(video_url)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    temp_path.replace(path)


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
