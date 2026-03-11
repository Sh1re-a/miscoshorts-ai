from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

import whisper
import yt_dlp
from moviepy import VideoFileClip

import cerebro_gemini as gemini_tjanst
import subtitulos as undertextning


ProgressCallback = Callable[[str, str], None]


def _emit(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback is not None:
        callback(stage, message)


def kontrollera_beroenden() -> None:
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg ar inte installerat eller finns inte i PATH. I Windows kan du installera det med 'winget install Gyan.FFmpeg'."
    )


def ladda_ner_video(url: str, mal_bas: Path) -> Path:
    ydl_opts = {
        "format": "best[ext=mp4]",
        "outtmpl": str(mal_bas.with_suffix(".%(ext)s")),
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return mal_bas.with_suffix(".mp4")


def tolka_gemini_svar(text: str) -> dict:
    data = {}
    for rad in text.split("\n"):
        if "TITEL:" in rad:
            data["titel"] = rad.split("TITEL:")[1].strip()
        if "START:" in rad:
            data["start"] = float(rad.split("START:")[1].strip())
        if "SLUT:" in rad:
            data["slut"] = float(rad.split("SLUT:")[1].strip())
        if "ORSAK:" in rad:
            data["orsak"] = rad.split("ORSAK:")[1].strip()
    return data


def skapa_utdatamapp(base_dir: str | Path = "outputs", job_id: str | None = None) -> tuple[str, Path]:
    job_id = job_id or uuid.uuid4().hex[:10]
    output_dir = Path(base_dir) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return job_id, output_dir


def skapa_short_fran_url(
    video_url: str,
    api_key: str,
    output_filename: str = "short_con_subs.mp4",
    base_dir: str | Path = "outputs",
    job_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    kontrollera_beroenden()

    job_id, output_dir = skapa_utdatamapp(base_dir=base_dir, job_id=job_id)
    transcript_path = output_dir / "fullstandig_transkription.txt"
    temp_base = output_dir / "video_temp"

    video_path = None
    clip = None
    clip_final = None

    try:
        _emit(progress_callback, "downloading", "Laddar ner video fran YouTube...")
        video_path = ladda_ner_video(video_url, temp_base)

        _emit(progress_callback, "transcribing", "Transkriberar ljud med Whisper...")
        model = whisper.load_model("base")
        resultat = model.transcribe(str(video_path))

        transcript_path.write_text(
            f"URL: {video_url}\n{resultat['text']}", encoding="utf-8"
        )

        _emit(progress_callback, "analyzing", "Fragar Gemini efter det basta klippet...")
        analys = gemini_tjanst.hitta_viralt_klipp(resultat["segments"], api_key)
        clip_data = tolka_gemini_svar(analys)

        if "start" not in clip_data or "slut" not in clip_data:
            raise ValueError("Gemini returnerade inget giltigt START- och SLUT-intervall.")

        start = clip_data["start"]
        end = clip_data["slut"]
        output_path = output_dir / output_filename

        _emit(progress_callback, "rendering", "Renderar vertikal short med undertexter...")
        clip = VideoFileClip(str(video_path)).subclipped(start, end)

        width, height = clip.size
        new_width = height * (9 / 16)
        clip_vertical = clip.cropped(
            x1=width / 2 - new_width / 2,
            y1=0,
            x2=width / 2 + new_width / 2,
            y2=height,
        )

        clip_final = undertextning.skapa_undertexter(clip_vertical, resultat["segments"], start)
        clip_final.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            fps=24,
            threads=4,
            logger=None,
        )

        _emit(progress_callback, "completed", "Shorten ar klar for nedladdning.")
        return {
            "jobId": job_id,
            "videoUrl": video_url,
            "title": clip_data.get("titel"),
            "reason": clip_data.get("orsak"),
            "start": start,
            "end": end,
            "outputFilename": output_filename,
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