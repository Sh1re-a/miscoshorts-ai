from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

import whisper
import yt_dlp
from moviepy import VideoFileClip

import gemini_analyzer
import subtitles


ProgressCallback = Callable[[str, str], None]
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
VIDEO_CRF = "18"
VIDEO_BITRATE = "10M"
VIDEO_AUDIO_BITRATE = "192k"
DEFAULT_CLIP_COUNT = 3


def _emit(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback is not None:
        callback(stage, message)


def ensure_dependencies() -> None:
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg is not installed or not available in PATH. On Windows, install it with 'winget install Gyan.FFmpeg'."
    )


def download_video(url: str, destination_base: Path) -> Path:
    ydl_opts = {
        "format": "best[ext=mp4]",
        "outtmpl": str(destination_base.with_suffix(".%(ext)s")),
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return destination_base.with_suffix(".mp4")


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


def create_output_dir(base_dir: str | Path = "outputs", job_id: str | None = None) -> tuple[str, Path]:
    job_id = job_id or uuid.uuid4().hex[:10]
    output_dir = Path(base_dir) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return job_id, output_dir


def create_short_from_url(
    video_url: str,
    api_key: str,
    output_filename: str = "short_con_subs.mp4",
    base_dir: str | Path = "outputs",
    job_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    subtitle_style: dict | None = None,
    clip_count: int = DEFAULT_CLIP_COUNT,
) -> dict:
    ensure_dependencies()

    job_id, output_dir = create_output_dir(base_dir=base_dir, job_id=job_id)
    transcript_path = output_dir / "full_transcript.txt"
    temp_base = output_dir / "video_temp"

    video_path = None
    clip = None
    clip_final = None

    try:
        _emit(progress_callback, "downloading", "Downloading video from YouTube...")
        video_path = download_video(video_url, temp_base)

        _emit(progress_callback, "transcribing", "Transcribing audio with Whisper...")
        model = whisper.load_model("base")
        result = model.transcribe(str(video_path))

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

            width, height = clip.size
            new_width = height * (9 / 16)
            clip_vertical = clip.cropped(
                x1=width / 2 - new_width / 2,
                y1=0,
                x2=width / 2 + new_width / 2,
                y2=height,
            )
            clip_vertical = clip_vertical.resized(new_size=(OUTPUT_WIDTH, OUTPUT_HEIGHT))

            clip_final = subtitles.create_subtitles(clip_vertical, result["segments"], start, subtitle_style)
            clip_final.write_videofile(
                str(output_path),
                codec="libx264",
                audio_codec="aac",
                fps=max(24, round(clip.fps or 24)),
                bitrate=VIDEO_BITRATE,
                audio_bitrate=VIDEO_AUDIO_BITRATE,
                threads=4,
                preset="slow",
                ffmpeg_params=["-crf", VIDEO_CRF, "-movflags", "+faststart", "-pix_fmt", "yuv420p"],
                logger=None,
            )

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
            clip.close()
            clip = None

        _emit(progress_callback, "completed", f"{len(clips_output)} clips are ready to download.")
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
            "clips": clips_output,
            "clipCount": len(clips_output),
        }
    finally:
        if clip_final is not None:
            clip_final.close()
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