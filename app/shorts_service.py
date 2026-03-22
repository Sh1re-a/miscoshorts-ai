from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Callable

import whisper
import yt_dlp
from moviepy import VideoFileClip

from app import gemini_analyzer, subtitles
from app.paths import OUTPUTS_DIR


ProgressCallback = Callable[[str, str], None]
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
TARGET_ASPECT_RATIO = OUTPUT_WIDTH / OUTPUT_HEIGHT
VIDEO_CRF = os.getenv("VIDEO_CRF", "15")
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "medium")
VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "12M")
VIDEO_MAXRATE = os.getenv("VIDEO_MAXRATE", "18M")
VIDEO_BUFSIZE = os.getenv("VIDEO_BUFSIZE", "24M")
VIDEO_AUDIO_BITRATE = os.getenv("VIDEO_AUDIO_BITRATE", "192k")
DOWNLOAD_FORMAT = os.getenv(
    "YTDLP_FORMAT",
    "bestvideo*[height<=2160]+bestaudio/best[height<=2160]/best",
)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "turbo,base")
RENDER_THREADS = max(4, min(12, os.cpu_count() or 4))
DEFAULT_CLIP_COUNT = 3
RENDER_PROFILE_LABEL = "Studio HQ 1080x1920 MP4"

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
        bitrate=VIDEO_BITRATE,
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
    return transcribe_media(video_path, word_timestamps=False)


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


def download_video(url: str, destination_base: Path) -> Path:
    ydl_opts = {
        "format": DOWNLOAD_FORMAT,
        "outtmpl": str(destination_base.with_suffix(".%(ext)s")),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return _resolve_downloaded_video_path(destination_base)


def parse_gemini_response(text: str) -> dict:
    data = {}
    for line in text.split("\n"):
        if "TITLE:" in line:
            data["title"] = line.split("TITLE:")[1].strip()
        if "START:" in line:
            data["start"] = float(line.split("START:")[1].strip())
        if "END:" in line:
            data["end"] = float(line.split("END:")[1].strip())
        if "REASON:" in line:
            data["reason"] = line.split("REASON:")[1].strip()
    return data


def parse_gemini_responses(text: str) -> list[dict]:
    clips: list[dict] = []
    current: dict = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.upper().startswith("CLIP "):
            if current:
                clips.append(current)
            current = {}
            continue

        if line.startswith("TITLE:"):
            if current.get("title") and current.get("start") is not None and current.get("end") is not None:
                clips.append(current)
                current = {}
            current["title"] = line.split("TITLE:", 1)[1].strip()
        elif line.startswith("START:"):
            current["start"] = float(line.split("START:", 1)[1].strip())
        elif line.startswith("END:"):
            current["end"] = float(line.split("END:", 1)[1].strip())
        elif line.startswith("REASON:"):
            current["reason"] = line.split("REASON:", 1)[1].strip()

    if current:
        clips.append(current)

    normalized: list[dict] = []
    seen_ranges: set[tuple[int, int]] = set()
    for clip in clips:
        start = clip.get("start")
        end = clip.get("end")
        if start is None or end is None or end <= start:
            continue

        key = (round(start * 10), round(end * 10))
        if key in seen_ranges:
            continue

        seen_ranges.add(key)
        normalized.append(clip)

    return normalized


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
    ensure_dependencies()
    _emit(progress_callback, "validating", "Checking subtitle rendering compatibility...")
    subtitles.assert_subtitle_rendering_ready(subtitle_style)

    job_id, output_dir = create_output_dir(base_dir=base_dir, job_id=job_id)
    transcript_path = output_dir / "full_transcript.txt"
    temp_base = output_dir / "video_temp"

    video_path = None
    clip = None
    clip_vertical = None
    clip_final = None

    try:
        _emit(progress_callback, "downloading", "Downloading the highest-quality source video and audio from YouTube...")
        video_path = download_video(video_url, temp_base)

        _emit(progress_callback, "transcribing", f"Fast transcribing full video with Whisper ({_get_whisper_model_candidates()[0]})...")
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
            single_clip = parse_gemini_response(analysis)
            if "start" not in single_clip or "end" not in single_clip:
                raise ValueError("Gemini did not return any valid clip intervals.")
            clip_candidates = [single_clip]

        clip_candidates = clip_candidates[:clip_count]
        clips_output = []

        for index, clip_data in enumerate(clip_candidates, start=1):
            start = clip_data["start"]
            end = clip_data["end"]
            current_filename = build_clip_filename(output_filename, index, len(clip_candidates))
            output_path = output_dir / current_filename

            _emit(progress_callback, "rendering", f"Rendering clip {index} of {len(clip_candidates)}...")
            clip = VideoFileClip(str(video_path)).subclipped(start, end)

            clip_vertical = build_vertical_master_clip(clip)
            if clip.audio is not None:
                clip_vertical = clip_vertical.with_audio(clip.audio.with_duration(clip_vertical.duration))

            _emit(progress_callback, "rendering", f"Preparing subtitle timing for clip {index}...")
            clip_transcript = transcribe_clip_for_subtitles(clip, output_dir, index)

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