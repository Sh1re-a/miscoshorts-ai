from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from moviepy import VideoFileClip

DEFAULT_RENDER_THREADS = max(4, min(12, os.cpu_count() or 4))
DEFAULT_RENDER_SETTINGS = {
    "video_crf": os.getenv("VIDEO_CRF", "13"),
    "video_preset": os.getenv("VIDEO_PRESET", "slow"),
    "video_bitrate": os.getenv("VIDEO_BITRATE", "12M"),
    "video_maxrate": os.getenv("VIDEO_MAXRATE", "18M"),
    "video_bufsize": os.getenv("VIDEO_BUFSIZE", "24M"),
    "audio_bitrate": os.getenv("VIDEO_AUDIO_BITRATE", "320k"),
    "x264_params": os.getenv(
        "VIDEO_X264_PARAMS",
        "aq-mode=3:aq-strength=0.9:deblock=-1,-1",
    ),
}


def get_render_fps(clip: VideoFileClip) -> int:
    return max(24, round(clip.fps or 24))


def write_high_quality_video(
    clip: VideoFileClip,
    output_path: str | Path,
    *,
    audio_path: Path | None = None,
    render_settings: dict[str, str] | None = None,
    render_threads: int | None = None,
) -> dict[str, int | float | bool]:
    """Write a finished clip to disk with an FFmpeg-safe two-pass audio path."""
    fps = get_render_fps(clip)
    keyint = str(fps * 1)
    keyint_min = str(fps)
    output_path = Path(output_path)
    settings = dict(DEFAULT_RENDER_SETTINGS)
    if render_settings:
        settings.update(render_settings)
    threads = render_threads or DEFAULT_RENDER_THREADS

    base_ffmpeg_params = [
        "-crf", settings["video_crf"],
        "-b:v", settings["video_bitrate"],
        "-maxrate", settings["video_maxrate"],
        "-bufsize", settings["video_bufsize"],
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level:v", "4.2",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-g", keyint,
        "-keyint_min", keyint_min,
        "-sc_threshold", "0",
        "-sws_flags", "lanczos+accurate_rnd+full_chroma_int",
        "-x264-params", settings.get("x264_params", DEFAULT_RENDER_SETTINGS["x264_params"]),
    ]
    metrics: dict[str, int | float | bool] = {
        "usedExternalAudioMux": bool(audio_path is not None and Path(audio_path).exists()),
    }

    if audio_path is not None and Path(audio_path).exists():
        video_only_path = output_path.with_suffix(".videoonly.mp4")
        try:
            encode_started_at = time.time()
            clip.write_videofile(
                str(video_only_path),
                codec="libx264",
                audio=False,
                fps=fps,
                threads=threads,
                preset=settings["video_preset"],
                ffmpeg_params=base_ffmpeg_params,
                logger=None,
            )
            metrics["videoEncodeSeconds"] = round(time.time() - encode_started_at, 2)
            if video_only_path.exists():
                metrics["videoOnlyBytes"] = video_only_path.stat().st_size
            ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
            mux_started_at = time.time()
            mux_proc = subprocess.run(
                [
                    ffmpeg_bin, "-y",
                    "-i", str(video_only_path),
                    "-i", str(audio_path),
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", settings["audio_bitrate"],
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-movflags", "+faststart",
                    "-shortest",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            if mux_proc.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg audio mux failed (rc={mux_proc.returncode}): {mux_proc.stderr[:500]}"
                )
            metrics["audioMuxSeconds"] = round(time.time() - mux_started_at, 2)
        finally:
            if video_only_path.exists():
                video_only_path.unlink(missing_ok=True)
    else:
        encode_started_at = time.time()
        clip.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac" if clip.audio is not None else None,
            fps=fps,
            audio_bitrate=settings["audio_bitrate"],
            threads=threads,
            preset=settings["video_preset"],
            ffmpeg_params=base_ffmpeg_params,
            logger=None,
        )
        metrics["videoEncodeSeconds"] = round(time.time() - encode_started_at, 2)

    if output_path.exists():
        metrics["outputBytes"] = output_path.stat().st_size
    return metrics


def extract_audio_segment(
    video_path: Path,
    start: float,
    end: float,
    temp_dir: Path,
) -> Path | None:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return None

    out_path = temp_dir / f"audio_tmp_{start:.3f}_{end:.3f}.wav"
    duration = end - start
    try:
        proc = subprocess.run(
            [
                ffmpeg_bin, "-y",
                "-ss", f"{start:.6f}",
                "-t", f"{duration:.6f}",
                "-i", str(video_path),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "44100",
                "-ac", "2",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            return None
        return out_path
    except Exception:
        return None
