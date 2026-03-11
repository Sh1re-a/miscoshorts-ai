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

        _emit(progress_callback, "analyzing", "Asking Gemini for the best clip...")
        analysis = gemini_analyzer.find_viral_clip(result["segments"], api_key)
        clip_data = parse_gemini_response(analysis)

        if "start" not in clip_data or "end" not in clip_data:
            raise ValueError("Gemini did not return a valid START and END interval.")

        start = clip_data["start"]
        end = clip_data["end"]
        output_path = output_dir / output_filename

        _emit(progress_callback, "rendering", "Rendering vertical short with subtitles...")
        clip = VideoFileClip(str(video_path)).subclipped(start, end)

        width, height = clip.size
        new_width = height * (9 / 16)
        clip_vertical = clip.cropped(
            x1=width / 2 - new_width / 2,
            y1=0,
            x2=width / 2 + new_width / 2,
            y2=height,
        )

        clip_final = subtitles.create_subtitles(clip_vertical, result["segments"], start, subtitle_style)
        clip_final.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            fps=24,
            threads=4,
            logger=None,
        )

        _emit(progress_callback, "completed", "The short is ready to download.")
        return {
            "jobId": job_id,
            "videoUrl": video_url,
            "title": clip_data.get("title"),
            "reason": clip_data.get("reason"),
            "start": start,
            "end": end,
            "outputFilename": output_filename,
            "subtitleStyle": subtitle_style,
            "outputPath": str(output_path),
            "transcriptPath": str(transcript_path),
            "outputDir": str(output_dir),
        }
    finally:
        if clip_final is not None:
            clip_final.close()
        if clip is not None:
            clip.close()
        if video_path and os.path.exists(video_path):
            os.remove(video_path)