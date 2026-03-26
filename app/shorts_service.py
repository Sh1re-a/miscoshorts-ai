from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import whisper
import yt_dlp
from moviepy import VideoFileClip

from app import gemini_analyzer, subtitles
from app.paths import OUTPUTS_DIR


ProgressCallback = Callable[[str, str], None]
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
TARGET_ASPECT_RATIO = OUTPUT_WIDTH / OUTPUT_HEIGHT
VIDEO_CRF = os.getenv("VIDEO_CRF", "13")
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "medium")
VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "14M")
VIDEO_MAXRATE = os.getenv("VIDEO_MAXRATE", "20M")
VIDEO_BUFSIZE = os.getenv("VIDEO_BUFSIZE", "28M")
VIDEO_AUDIO_BITRATE = os.getenv("VIDEO_AUDIO_BITRATE", "192k")
DOWNLOAD_FORMAT = os.getenv(
    "YTDLP_FORMAT",
    "bestvideo*[height<=2160]+bestaudio/best[height<=2160]/best",
)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "turbo,base")
RENDER_THREADS = max(4, min(12, os.cpu_count() or 4))
DEFAULT_CLIP_COUNT = 3
RENDER_PROFILE_LABEL = "Studio HQ 1080x1920 MP4"
ALLOWED_VIDEO_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}
GEMINI_FIELD_PATTERN = re.compile(r"^(TITLE|START|END|REASON)\s*:\s*(.+?)\s*$", re.IGNORECASE)
GEMINI_CLIP_PATTERN = re.compile(r"^CLIP\s+\d+\s*:?\s*$", re.IGNORECASE)

_whisper_model_cache: dict[str, object] = {}
_whisper_model_lock = threading.Lock()


def _emit(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback is not None:
        callback(stage, message)


def _make_even(value: float) -> int:
    return max(2, int(round(value / 2) * 2))


def _resolve_downloaded_video_path(destination_base: Path) -> Path:
    preferred_extensions = (".mp4", ".mkv", ".mov", ".webm")
    for extension in preferred_extensions:
        candidate = destination_base.with_suffix(extension)
        if candidate.exists():
            return candidate

    matches = sorted(destination_base.parent.glob(f"{destination_base.name}.*"))
    if matches:
        return matches[0]

    raise FileNotFoundError("yt-dlp completed without producing a video file.")


def validate_video_url(url: str) -> str:
    normalized_url = (url or "").strip()
    if not normalized_url:
        raise ValueError("videoUrl is required")

    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Video URL must start with http:// or https://")

    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_VIDEO_HOSTS and not hostname.endswith(".youtube.com"):
        raise ValueError("Only YouTube video URLs are supported.")

    if not parsed.path or parsed.path == "/":
        raise ValueError("The YouTube URL appears to be incomplete.")

    return normalized_url


def sanitize_output_filename(output_filename: str) -> str:
    raw_value = (output_filename or "").strip() or "short_con_subs.mp4"
    filename = Path(raw_value).name.strip().strip(".")
    filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", filename)
    filename = re.sub(r"\s+", " ", filename).strip()

    if not filename:
        filename = "short_con_subs.mp4"

    path = Path(filename)
    stem = (path.stem or "short_con_subs").strip(" .") or "short_con_subs"
    suffix = path.suffix.lower() or ".mp4"

    if suffix != ".mp4":
        suffix = ".mp4"

    if stem.upper() in WINDOWS_RESERVED_FILENAMES:
        stem = f"{stem}_video"

    return f"{stem}{suffix}"


def normalize_requested_subtitle_style(subtitle_style: dict | None) -> dict:
    if subtitle_style is None:
        return subtitles.normalize_subtitle_style(None)

    if not isinstance(subtitle_style, dict):
        raise ValueError("subtitleStyle must be a JSON object.")

    allowed_keys = {"fontPreset", "colorPreset"}
    unknown_keys = sorted(set(subtitle_style) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"subtitleStyle contains unsupported keys: {', '.join(unknown_keys)}")

    for key, value in subtitle_style.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(f"subtitleStyle field '{key}' must be a string.")

    return subtitles.normalize_subtitle_style(subtitle_style)


def get_render_fps(clip: VideoFileClip) -> int:
    return max(24, round(clip.fps or 24))


def build_vertical_master_clip(clip: VideoFileClip) -> VideoFileClip:
    width, height = clip.size
    source_ratio = width / height

    if abs(source_ratio - TARGET_ASPECT_RATIO) < 0.001:
        return clip.resized(new_size=(OUTPUT_WIDTH, OUTPUT_HEIGHT))

    if source_ratio > TARGET_ASPECT_RATIO:
        crop_width = min(width, _make_even(height * TARGET_ASPECT_RATIO))
        x1 = max(0, int(round((width - crop_width) / 2)))
        x2 = min(width, x1 + crop_width)
        cropped_clip = clip.cropped(x1=x1, y1=0, x2=x2, y2=height)
    else:
        crop_height = min(height, _make_even(width / TARGET_ASPECT_RATIO))
        y1 = max(0, int(round((height - crop_height) / 2)))
        y2 = min(height, y1 + crop_height)
        cropped_clip = clip.cropped(x1=0, y1=y1, x2=width, y2=y2)

    return cropped_clip.resized(new_size=(OUTPUT_WIDTH, OUTPUT_HEIGHT))


def write_high_quality_video(clip: VideoFileClip, output_path: str | Path) -> None:
    clip.write_videofile(
        str(output_path),
        codec="libx264",
        audio_codec="aac",
        fps=get_render_fps(clip),
        audio_bitrate=VIDEO_AUDIO_BITRATE,
        threads=RENDER_THREADS,
        preset=VIDEO_PRESET,
        ffmpeg_params=[
            "-crf",
            VIDEO_CRF,
            "-maxrate",
            VIDEO_MAXRATE,
            "-bufsize",
            VIDEO_BUFSIZE,
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-level:v",
            "4.2",
        ],
        logger=None,
    )


def ensure_dependencies() -> None:
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg is not installed or not available in PATH. On Windows, install it with 'winget install Gyan.FFmpeg'."
    )


def _get_whisper_model_candidates() -> list[str]:
    return [candidate.strip() for candidate in WHISPER_MODEL.split(",") if candidate.strip()] or ["base"]


def load_whisper_model() -> tuple[str, object]:
    last_error = None
    with _whisper_model_lock:
        for model_name in _get_whisper_model_candidates():
            cached_model = _whisper_model_cache.get(model_name)
            if cached_model is not None:
                return model_name, cached_model

            try:
                model = whisper.load_model(model_name)
                _whisper_model_cache[model_name] = model
                return model_name, model
            except Exception as error:
                last_error = error

    raise RuntimeError("Could not load any configured Whisper model.") from last_error


def transcribe_media(media_path: Path, *, word_timestamps: bool) -> dict:
    model_name, model = load_whisper_model()
    transcribe_options = {
        "fp16": False,
        "verbose": False,
        "condition_on_previous_text": False,
        "temperature": 0.0,
    }
    if word_timestamps:
        transcribe_options["word_timestamps"] = True

    try:
        return model.transcribe(str(media_path), **transcribe_options)
    except TypeError:
        fallback_options = dict(transcribe_options)
        fallback_options.pop("word_timestamps", None)
        return model.transcribe(str(media_path), **fallback_options)
    except Exception as error:
        if model_name != "base":
            with _whisper_model_lock:
                _whisper_model_cache.pop(model_name, None)
                base_model = _whisper_model_cache.get("base")
                if base_model is None:
                    base_model = whisper.load_model("base")
                    _whisper_model_cache["base"] = base_model
            fallback_options = dict(transcribe_options)
            try:
                return base_model.transcribe(str(media_path), **fallback_options)
            except TypeError:
                fallback_options.pop("word_timestamps", None)
                return base_model.transcribe(str(media_path), **fallback_options)
        raise RuntimeError("Whisper transcription failed.") from error


def transcribe_video_fast(video_path: Path) -> dict:
    return transcribe_media(video_path, word_timestamps=True)


def transcribe_clip_for_subtitles(clip: VideoFileClip, output_dir: Path, clip_index: int) -> dict:
    if clip.audio is None:
        return {"text": "", "segments": []}

    audio_path = output_dir / f"clip_audio_{clip_index:02d}.wav"
    try:
        clip.audio.write_audiofile(
            str(audio_path),
            fps=16000,
            nbytes=2,
            ffmpeg_params=["-ac", "1"],
            logger=None,
        )
        return transcribe_media(audio_path, word_timestamps=True)
    finally:
        if audio_path.exists():
            audio_path.unlink()


def _slice_segment_words(words: list[dict], clip_start_time: float, clip_end_time: float) -> list[dict]:
    clip_duration = max(0.0, clip_end_time - clip_start_time)
    sliced_words: list[dict] = []

    for word in words:
        raw_text = (word.get("word") or "").strip()
        raw_start = word.get("start")
        raw_end = word.get("end")
        if not raw_text or raw_start is None or raw_end is None:
            continue

        try:
            word_start = float(raw_start)
            word_end = float(raw_end)
        except (TypeError, ValueError):
            continue

        if word_end <= clip_start_time or word_start >= clip_end_time:
            continue

        relative_start = max(0.0, word_start - clip_start_time)
        relative_end = min(clip_duration, word_end - clip_start_time)
        if relative_end <= relative_start:
            relative_end = min(clip_duration, relative_start + 0.12)

        sliced_words.append(
            {
                "word": raw_text,
                "start": relative_start,
                "end": relative_end,
            }
        )

    return sliced_words


def extract_clip_transcript_from_full(full_transcript: dict, clip_start_time: float, clip_end_time: float) -> tuple[dict, bool]:
    clip_duration = max(0.0, clip_end_time - clip_start_time)
    clip_segments: list[dict] = []
    transcript_text_parts: list[str] = []
    requires_precise_fallback = False

    for raw_segment in full_transcript.get("segments") or []:
        raw_start = raw_segment.get("start")
        raw_end = raw_segment.get("end")
        if raw_start is None or raw_end is None:
            continue

        try:
            segment_start = float(raw_start)
            segment_end = float(raw_end)
        except (TypeError, ValueError):
            continue

        if segment_end <= clip_start_time or segment_start >= clip_end_time:
            continue

        relative_start = max(0.0, segment_start - clip_start_time)
        relative_end = min(clip_duration, segment_end - clip_start_time)
        if relative_end <= relative_start:
            continue

        sliced_words = _slice_segment_words(raw_segment.get("words") or [], clip_start_time, clip_end_time)
        if sliced_words:
            segment_text = " ".join(word["word"] for word in sliced_words)
        else:
            segment_text = (raw_segment.get("text") or "").strip()
            if segment_start < clip_start_time or segment_end > clip_end_time:
                requires_precise_fallback = True

        if not segment_text and not sliced_words:
            continue

        clipped_segment = {
            "start": relative_start,
            "end": relative_end,
            "text": segment_text,
        }
        if sliced_words:
            clipped_segment["words"] = sliced_words

        clip_segments.append(clipped_segment)
        if segment_text:
            transcript_text_parts.append(segment_text)

    return {
        "text": " ".join(transcript_text_parts).strip(),
        "segments": clip_segments,
    }, requires_precise_fallback


def download_video(url: str, destination_base: Path, progress_callback: ProgressCallback | None = None) -> Path:
    last_reported: dict[str, int] = {"pct": -1}

    def _ydl_progress(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes") or 0
        if total > 0:
            pct = int(downloaded / total * 100)
            # Report at most every 10%
            if pct >= last_reported["pct"] + 10:
                last_reported["pct"] = pct
                speed = d.get("speed") or 0
                speed_mb = speed / 1_048_576 if speed else 0
                eta = d.get("eta") or 0
                msg = f"Downloading... {pct}%"
                if speed_mb >= 0.1:
                    msg += f"  ({speed_mb:.1f} MB/s"
                    if eta:
                        msg += f", ~{eta}s left"
                    msg += ")"
                _emit(progress_callback, "downloading", msg)
        elif d.get("info_dict"):
            # No size info yet — just confirm it started
            if last_reported["pct"] < 0:
                last_reported["pct"] = 0
                _emit(progress_callback, "downloading", "Downloading video… (size unknown)")

    ydl_opts = {
        "format": DOWNLOAD_FORMAT,
        "outtmpl": str(destination_base.with_suffix(".%(ext)s")),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [_ydl_progress],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    _emit(progress_callback, "downloading", "Download complete.")
    return _resolve_downloaded_video_path(destination_base)


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
    return {
        "title": title,
        "start": start,
        "end": end,
        "reason": reason,
    }


def parse_gemini_response(text: str) -> dict:
    clips = parse_gemini_responses(text)
    if not clips:
        raise ValueError("Gemini did not return any valid clip intervals.")
    return clips[0]


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


def create_output_dir(base_dir: str | Path = OUTPUTS_DIR, job_id: str | None = None) -> tuple[str, Path]:
    job_id = job_id or uuid.uuid4().hex[:10]
    output_dir = Path(base_dir) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return job_id, output_dir


def create_short_from_url(
    video_url: str,
    api_key: str,
    output_filename: str = "short_con_subs.mp4",
    base_dir: str | Path = OUTPUTS_DIR,
    job_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    subtitle_style: dict | None = None,
    clip_count: int = DEFAULT_CLIP_COUNT,
) -> dict:
    video_url = validate_video_url(video_url)
    output_filename = sanitize_output_filename(output_filename)
    subtitle_style = normalize_requested_subtitle_style(subtitle_style)

    ensure_dependencies()
    _emit(progress_callback, "validating", "Checking subtitle rendering compatibility...")
    subtitles.assert_subtitle_rendering_ready(subtitle_style)

    job_id, output_dir = create_output_dir(base_dir=base_dir, job_id=job_id)
    transcript_path = output_dir / "full_transcript.txt"
    temp_base = output_dir / "video_temp"

    video_path = None
    source_video = None
    clip = None
    clip_vertical = None
    clip_final = None

    try:
        _emit(progress_callback, "downloading", "Downloading the highest-quality source video and audio from YouTube...")
        video_path = download_video(video_url, temp_base)

        _emit(progress_callback, "transcribing", f"Transcribing full video once with Whisper ({_get_whisper_model_candidates()[0]}) for analysis and subtitles...")
        result = transcribe_video_fast(video_path)
        _emit(progress_callback, "transcribing", f"Transcription complete. Found {len(result.get('segments') or [])} segments.")

        transcript_path.write_text(
            f"URL: {video_url}\n{result['text']}", encoding="utf-8"
        )

        clip_count = max(1, min(5, int(clip_count)))
        _emit(progress_callback, "analyzing", f"Asking Gemini for the best {clip_count} clips...")
        analysis = gemini_analyzer.find_viral_clips(result["segments"], api_key, clip_count=clip_count)
        clip_candidates = parse_gemini_responses(analysis)
        if not clip_candidates:
            clip_candidates = [parse_gemini_response(analysis)]

        clip_candidates = clip_candidates[:clip_count]
        clips_output = []
        source_video = VideoFileClip(str(video_path))

        for index, clip_data in enumerate(clip_candidates, start=1):
            start = clip_data["start"]
            end = clip_data["end"]
            current_filename = build_clip_filename(output_filename, index, len(clip_candidates))
            output_path = output_dir / current_filename

            _emit(progress_callback, "rendering", f"Rendering clip {index} of {len(clip_candidates)}...")
            clip = source_video.subclipped(start, end)

            clip_vertical = build_vertical_master_clip(clip)
            if clip.audio is not None:
                clip_vertical = clip_vertical.with_audio(clip.audio.with_duration(clip_vertical.duration))

            _emit(progress_callback, "rendering", f"Preparing subtitle timing for clip {index}...")
            clip_transcript, requires_precise_fallback = extract_clip_transcript_from_full(result, start, end)
            if requires_precise_fallback:
                _emit(progress_callback, "rendering", f"Refining subtitle timing for clip {index}...")
                clip_transcript = transcribe_clip_for_subtitles(clip, output_dir, index)

            subtitle_plan = subtitles.build_subtitle_plan(
                clip_transcript.get("segments") or [],
                0,
                clip_vertical.duration,
            )
            subtitle_plan_path = output_dir / f"clip_{index:02d}_subtitles.json"
            subtitle_plan_path.write_text(
                json.dumps(subtitles.export_subtitle_plan(subtitle_plan), ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            subtitle_preflight = subtitles.validate_subtitle_plan_renderability(
                clip_vertical.size,
                subtitle_plan,
                subtitle_style,
            )
            subtitle_preflight_path = output_dir / f"clip_{index:02d}_subtitle_preflight.json"
            subtitle_preflight_path.write_text(
                json.dumps(subtitle_preflight, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            _emit(progress_callback, "rendering", f"Subtitle preflight passed for clip {index}.")

            clip_final = subtitles.create_subtitles(
                clip_vertical,
                clip_transcript.get("segments") or [],
                0,
                subtitle_style,
                clip_title=clip_data.get("title"),
                clip_reason=clip_data.get("reason"),
            )
            write_high_quality_video(clip_final, output_path)

            clips_output.append(
                {
                    "index": index,
                    "title": clip_data.get("title"),
                    "reason": clip_data.get("reason"),
                    "start": start,
                    "end": end,
                    "outputFilename": current_filename,
                    "outputPath": str(output_path),
                    "subtitlePlanPath": str(subtitle_plan_path),
                    "subtitlePlan": subtitles.export_subtitle_plan(subtitle_plan),
                    "subtitlePreflightPath": str(subtitle_preflight_path),
                    "subtitlePreflight": subtitle_preflight,
                }
            )

            clip_final.close()
            clip_final = None
            clip_vertical.close()
            clip_vertical = None
            clip.close()
            clip = None

        _emit(progress_callback, "completed", f"{len(clips_output)} high-quality clips are ready to download.")
        first_clip = clips_output[0]
        return {
            "jobId": job_id,
            "videoUrl": video_url,
            "title": first_clip.get("title"),
            "reason": first_clip.get("reason"),
            "start": first_clip["start"],
            "end": first_clip["end"],
            "outputFilename": first_clip["outputFilename"],
            "subtitleStyle": subtitle_style,
            "outputPath": first_clip["outputPath"],
            "transcriptPath": str(transcript_path),
            "outputDir": str(output_dir),
            "renderProfile": RENDER_PROFILE_LABEL,
            "clips": clips_output,
            "clipCount": len(clips_output),
        }
    finally:
        if clip_final is not None:
            clip_final.close()
        if source_video is not None:
            source_video.close()
        if clip_vertical is not None:
            clip_vertical.close()
        if clip is not None:
            clip.close()
        if video_path and os.path.exists(video_path):
            os.remove(video_path)


def build_clip_filename(output_filename: str, clip_index: int, total_clips: int) -> str:
    if total_clips <= 1:
        return output_filename

    path = Path(output_filename)
    stem = path.stem or "short_con_subs"
    suffix = path.suffix or ".mp4"
    return f"{stem}_{clip_index:02d}{suffix}"