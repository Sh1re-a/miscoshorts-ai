from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable

import yt_dlp
from moviepy import VideoFileClip

from app import gemini_analyzer
from app.media_cache import (
    find_cached_video,
    load_cached_clip_candidates,
    load_cached_transcript,
    store_cached_clip_candidates,
    store_cached_transcript,
    store_cached_video,
)
from app.runtime import configure_logging
from app.transcription import get_whisper_model_candidates, transcribe_video_fast, whisper_cache_contains_files

logger, _LOG_PATH = configure_logging("source-pipeline")

ProgressCallback = Callable[[str, str], None]
YTDLP_MAX_HEIGHT = max(1080, int(os.getenv("YTDLP_MAX_HEIGHT", "4320")))
DOWNLOAD_FORMAT = os.getenv(
    "YTDLP_FORMAT",
    f"bestvideo*[height<={YTDLP_MAX_HEIGHT}]+bestaudio/best[height<={YTDLP_MAX_HEIGHT}]/best",
)
DOWNLOAD_FORMAT_SORT = [
    field.strip()
    for field in os.getenv("YTDLP_FORMAT_SORT", "res,fps,hdr:12,vcodec:h264,acodec:aac").split(",")
    if field.strip()
]
GEMINI_FIELD_PATTERN = re.compile(r"^(TITLE|START|END|REASON)\s*:\s*(.+?)\s*$", re.IGNORECASE)
GEMINI_CLIP_PATTERN = re.compile(r"^CLIP\s+\d+\s*:?\s*$", re.IGNORECASE)
MIN_CLIP_SECONDS = 20.0
YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS = max(1, int(os.getenv("YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS", "1")))
YTDLP_SOCKET_TIMEOUT_SECONDS = max(10, int(os.getenv("YTDLP_SOCKET_TIMEOUT_SECONDS", "30")))
YTDLP_RETRY_ATTEMPTS = max(1, int(os.getenv("YTDLP_RETRY_ATTEMPTS", "3")))
YTDLP_PROGRESSIVE_FALLBACK_FORMAT = os.getenv(
    "YTDLP_PROGRESSIVE_FALLBACK_FORMAT",
    f"best[ext=mp4][height<={YTDLP_MAX_HEIGHT}]/best[height<={YTDLP_MAX_HEIGHT}]/best",
)
_TRANSIENT_DOWNLOAD_ERROR_MARKERS = (
    "can't assign requested address",
    "failed to establish a new connection",
    "connection reset by peer",
    "temporary failure in name resolution",
    "timed out",
    "network is unreachable",
    "connection aborted",
)


def _emit(progress_callback: ProgressCallback | None, stage: str, message: str) -> None:
    if progress_callback is not None:
        progress_callback(stage, message)


def _resolve_downloaded_video_path(destination_base: Path) -> Path:
    destination_dir = destination_base.parent
    stem = destination_base.name
    matches = sorted(destination_dir.glob(f"{stem}.*"))
    for path in matches:
        if path.is_file() and path.stat().st_size > 1024:
            return path
    raise FileNotFoundError("yt-dlp completed without producing a video file.")


def _summarize_download_info(info: dict | None) -> dict:
    if not info:
        return {}

    requested_formats = info.get("requested_formats") or []
    video_format = requested_formats[0] if requested_formats else info
    audio_format = requested_formats[1] if len(requested_formats) > 1 else info

    width = video_format.get("width") or info.get("width")
    height = video_format.get("height") or info.get("height")
    fps = video_format.get("fps") or info.get("fps")
    source_summary = {
        "id": info.get("id"),
        "title": info.get("title"),
        "webpageUrl": info.get("webpage_url") or info.get("original_url"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "container": info.get("ext"),
        "width": width,
        "height": height,
        "fps": fps,
        "videoCodec": video_format.get("vcodec") or info.get("vcodec"),
        "audioCodec": audio_format.get("acodec") or info.get("acodec"),
        "videoBitrate": video_format.get("tbr") or info.get("tbr"),
        "audioBitrate": audio_format.get("abr") or info.get("abr"),
    }
    return {
        key: value
        for key, value in source_summary.items()
        if value is not None and value != "" and value != []
    }


def _format_download_quality_label(download_info: dict) -> str:
    if not download_info:
        return "Source download complete."

    dimensions = "x".join(
        str(int(value))
        for value in (download_info.get("width"), download_info.get("height"))
        if value is not None
    )
    fps = download_info.get("fps")
    codec = download_info.get("videoCodec")
    audio = download_info.get("audioCodec")
    parts = [part for part in [dimensions, f"{fps}fps" if fps else None, codec, audio] if part]
    if not parts:
        return "Source download complete."
    return f"Source download complete: {' / '.join(parts)}."


def _is_transient_download_error(error: Exception) -> bool:
    lowered = str(error).lower()
    return any(marker in lowered for marker in _TRANSIENT_DOWNLOAD_ERROR_MARKERS)


def _retry_sleep_seconds(attempt: int) -> float:
    return min(8.0, float(2 ** max(0, attempt - 1)))


def _download_attempt_profiles() -> list[dict]:
    return [
        {
            "label": "primary",
            "format": DOWNLOAD_FORMAT,
            "source_address": None,
        },
        {
            "label": "ipv4-fallback",
            "format": DOWNLOAD_FORMAT,
            "source_address": "0.0.0.0",
        },
        {
            "label": "progressive-fallback",
            "format": YTDLP_PROGRESSIVE_FALLBACK_FORMAT,
            "source_address": "0.0.0.0",
        },
    ]


def download_video(url: str, destination_base: Path, progress_callback: ProgressCallback | None = None) -> tuple[Path, dict]:
    last_reported: dict[str, int] = {"pct": -1}

    def _ydl_progress(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes") or 0
        if total > 0:
            pct = int(downloaded / total * 100)
            if pct >= last_reported["pct"] + 10:
                last_reported["pct"] = pct
                speed = d.get("speed") or 0
                speed_mb = speed / 1_048_576 if speed else 0
                eta = d.get("eta") or 0
                msg = f"SOURCE | Downloading... {pct}%"
                if speed_mb >= 0.1:
                    msg += f"  ({speed_mb:.1f} MB/s"
                    if eta:
                        msg += f", ~{eta}s left"
                    msg += ")"
                _emit(progress_callback, "downloading", msg)
        elif d.get("info_dict") and last_reported["pct"] < 0:
            last_reported["pct"] = 0
            _emit(progress_callback, "downloading", "SOURCE | Downloading video... (size unknown)")

    attempt_profiles = _download_attempt_profiles()
    last_error: Exception | None = None

    for attempt_index, profile in enumerate(attempt_profiles, start=1):
        if attempt_index > YTDLP_RETRY_ATTEMPTS:
            break

        last_reported["pct"] = -1
        source_address = profile["source_address"]
        ydl_opts = {
            "format": profile["format"],
            "format_sort": DOWNLOAD_FORMAT_SORT,
            "outtmpl": str(destination_base.with_suffix(".%(ext)s")),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "check_formats": False,
            "concurrent_fragment_downloads": YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS,
            "retries": 3,
            "fragment_retries": 3,
            "file_access_retries": 3,
            "socket_timeout": YTDLP_SOCKET_TIMEOUT_SECONDS,
            "progress_hooks": [_ydl_progress],
        }
        if source_address:
            ydl_opts["source_address"] = source_address

        try:
            logger.info(
                "Starting yt-dlp download attempt %s/%s (profile=%s, source_address=%s, concurrent_fragments=%s)",
                attempt_index,
                min(YTDLP_RETRY_ATTEMPTS, len(attempt_profiles)),
                profile["label"],
                source_address or "default",
                YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS,
            )
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            download_info = _summarize_download_info(info)
            _emit(progress_callback, "downloading", f"SOURCE | {_format_download_quality_label(download_info)}")
            return _resolve_downloaded_video_path(destination_base), download_info
        except Exception as error:
            last_error = error
            if not _is_transient_download_error(error):
                logger.exception("yt-dlp download failed with a non-transient error on attempt %s", attempt_index)
                raise

            if attempt_index >= min(YTDLP_RETRY_ATTEMPTS, len(attempt_profiles)):
                logger.exception("yt-dlp download exhausted all transient-network recovery attempts")
                break

            sleep_seconds = _retry_sleep_seconds(attempt_index)
            _emit(
                progress_callback,
                "downloading",
                "SOURCE | Download connection failed locally. Retrying with a safer network profile...",
            )
            logger.warning(
                "Transient yt-dlp download failure on attempt %s/%s (profile=%s): %s. Retrying in %.1fs",
                attempt_index,
                min(YTDLP_RETRY_ATTEMPTS, len(attempt_profiles)),
                profile["label"],
                error,
                sleep_seconds,
            )
            for partial_path in destination_base.parent.glob(f"{destination_base.name}.*"):
                partial_path.unlink(missing_ok=True)
            time.sleep(sleep_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("The source video download did not start.")


def resolve_source_video(video_url: str, destination_base: Path, progress_callback: ProgressCallback | None = None) -> tuple[Path, dict, bool, bool]:
    cached_video = find_cached_video(video_url)
    if cached_video is not None:
        _emit(progress_callback, "downloading", "CACHE | Reusing cached source video from a previous local run...")
        return cached_video, {"webpageUrl": video_url, "localPath": str(cached_video)}, False, True

    _emit(progress_callback, "downloading", "SOURCE | Downloading the highest-quality source video and audio from YouTube...")
    video_path, download_info = download_video(video_url, destination_base, progress_callback)
    store_cached_video(video_url, video_path)
    download_info["localPath"] = str(video_path)
    return video_path, download_info, True, False


def resolve_transcript(video_url: str, video_path: Path, progress_callback: ProgressCallback | None = None) -> tuple[dict, bool]:
    cached = load_cached_transcript(video_url)
    if cached is not None:
        _emit(progress_callback, "transcribing", "CACHE | Reusing cached transcript from a previous local run...")
        return cached, True

    whisper_model = get_whisper_model_candidates()[0]
    if whisper_cache_contains_files():
        _emit(progress_callback, "transcribing", f"TRANSCRIPT | Transcribing full video once with Whisper ({whisper_model}) for analysis and subtitles...")
    else:
        _emit(
            progress_callback,
            "transcribing",
            f"TRANSCRIPT | Preparing the local speech model ({whisper_model}) for first run. This one-time download can take a few minutes, then transcription starts automatically...",
        )
    transcript = transcribe_video_fast(video_path)
    store_cached_transcript(video_url, transcript)
    _emit(progress_callback, "transcribing", f"TRANSCRIPT | Transcription complete. Found {len(transcript.get('segments') or [])} segments.")
    return transcript, False


def _extract_gemini_clip_blocks(text: str) -> list[dict[str, str]]:
    clips: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if GEMINI_CLIP_PATTERN.match(line):
            if current:
                clips.append(current)
            current = {}
            continue
        match = GEMINI_FIELD_PATTERN.match(line)
        if not match:
            continue
        key = match.group(1).lower()
        value = match.group(2).strip()
        if key == "title" and current.get("title") and current.get("start") and current.get("end"):
            clips.append(current)
            current = {}
        current[key] = value

    if current:
        clips.append(current)
    return clips


def _parse_gemini_float(raw_value: str | None, field_name: str) -> float:
    if raw_value is None or not raw_value.strip():
        raise ValueError(f"Gemini response is missing {field_name.upper()}.")
    try:
        return float(raw_value.strip())
    except ValueError as error:
        raise ValueError(f"Gemini returned an invalid {field_name.upper()} value: {raw_value!r}") from error


def _normalize_gemini_clip(raw_clip: dict[str, str]) -> dict:
    start = _parse_gemini_float(raw_clip.get("start"), "start")
    end = _parse_gemini_float(raw_clip.get("end"), "end")
    if end <= start:
        raise ValueError(f"Gemini returned an invalid clip interval: start={start}, end={end}")
    title = (raw_clip.get("title") or "").strip() or None
    reason = (raw_clip.get("reason") or "").strip() or None
    return {"title": title, "start": start, "end": end, "reason": reason}


def parse_gemini_responses(text: str) -> list[dict]:
    raw_clips = _extract_gemini_clip_blocks(text)
    normalized: list[dict] = []
    seen_ranges: set[tuple[int, int]] = set()
    parse_errors: list[str] = []

    for raw_clip in raw_clips:
        try:
            clip = _normalize_gemini_clip(raw_clip)
        except ValueError as error:
            parse_errors.append(str(error))
            continue
        key = (round(clip["start"] * 10), round(clip["end"] * 10))
        if key in seen_ranges:
            continue
        seen_ranges.add(key)
        normalized.append(clip)

    if normalized:
        return normalized
    if parse_errors:
        raise ValueError(parse_errors[0])
    return []


def parse_gemini_response(text: str) -> dict:
    clips = parse_gemini_responses(text)
    if not clips:
        raise ValueError("Gemini did not return any valid clip intervals.")
    return clips[0]


def select_clip_candidates(
    *,
    video_url: str,
    transcript_segments: list[dict],
    api_key: str,
    clip_count: int,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict], bool]:
    cached = load_cached_clip_candidates(video_url, clip_count)
    if cached is not None:
        _emit(progress_callback, "analyzing", f"CACHE | Reusing cached clip selection for {clip_count} clips...")
        return cached, True

    _emit(progress_callback, "analyzing", f"CLIP_SELECTION | Asking Gemini for the best {clip_count} clips...")
    analysis = gemini_analyzer.find_viral_clips(
        transcript_segments,
        api_key,
        clip_count=clip_count,
        progress_callback=progress_callback,
    )
    clip_candidates = parse_gemini_responses(analysis)
    if not clip_candidates:
        clip_candidates = [parse_gemini_response(analysis)]
    clip_candidates = clip_candidates[:clip_count]
    store_cached_clip_candidates(video_url, clip_count, clip_candidates)
    return clip_candidates, False


def probe_video_duration(video_path: Path) -> float:
    source_video = VideoFileClip(str(video_path))
    try:
        return float(source_video.duration or 0.0)
    finally:
        source_video.close()


def validate_clip_candidates(clip_candidates: list[dict], video_duration: float, *, warn_callback: Callable[[str], None] | None = None) -> list[dict]:
    valid_candidates = []
    for candidate in clip_candidates:
        start = max(0.0, min(candidate["start"], video_duration - 1.0))
        end = max(start + 5.0, min(candidate["end"], video_duration))
        if end - start < MIN_CLIP_SECONDS:
            if warn_callback is not None:
                warn_callback(
                    f"Skipping clip {candidate.get('title', 'unknown')}: too short after clamping ({end - start:.1f}s < {MIN_CLIP_SECONDS:.0f} s minimum)."
                )
            continue
        normalized = dict(candidate)
        normalized["start"] = round(start, 2)
        normalized["end"] = round(end, 2)
        valid_candidates.append(normalized)
    return valid_candidates


def estimate_required_free_bytes(video_path: Path, clip_count: int, video_duration: float) -> int:
    try:
        source_size = video_path.stat().st_size
    except OSError:
        source_size = 0
    estimated_render_bytes = clip_count * 700 * 1024 * 1024
    long_video_buffer = 2 * 1024 * 1024 * 1024 if video_duration >= 1800 else 0
    return max(2 * 1024 * 1024 * 1024, source_size * 2 + estimated_render_bytes + long_video_buffer)


def ensure_disk_headroom(target_dir: Path, *, video_path: Path, clip_count: int, video_duration: float) -> int:
    required_free_bytes = estimate_required_free_bytes(video_path, clip_count, video_duration)
    free_bytes = shutil.disk_usage(target_dir).free
    if free_bytes < required_free_bytes:
        required_gb = required_free_bytes / (1024 * 1024 * 1024)
        free_gb = free_bytes / (1024 * 1024 * 1024)
        raise OSError(
            f"Insufficient free disk space for this render. About {required_gb:.1f} GB is recommended, but only {free_gb:.1f} GB is free."
        )
    return free_bytes


def emit_workload_warning(video_duration: float, clip_count: int, progress_callback: ProgressCallback | None = None) -> None:
    if video_duration >= 2700 or clip_count >= 5:
        _emit(
            progress_callback,
            "rendering",
            "SUMMARY | Heavy workload detected. This source is long or requests many clips, so rendering will use more RAM and disk than a normal run.",
        )
