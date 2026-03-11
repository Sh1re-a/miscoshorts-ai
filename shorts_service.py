from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

import whisper
import yt_dlp
from moviepy import VideoFileClip

import cerebro_gemini as cerebro
import subtitulos


ProgressCallback = Callable[[str, str], None]


def _emit(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback is not None:
        callback(stage, message)


def validar_dependencias() -> None:
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg no esta instalado o no esta disponible en PATH. En Windows puedes instalarlo con 'winget install Gyan.FFmpeg'."
    )


def descargar_video(url: str, destino_base: Path) -> Path:
    ydl_opts = {
        "format": "best[ext=mp4]",
        "outtmpl": str(destino_base.with_suffix(".%(ext)s")),
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return destino_base.with_suffix(".mp4")


def parsear_respuesta_gemini(texto: str) -> dict:
    datos = {}
    for line in texto.split("\n"):
        if "TITULO:" in line:
            datos["titulo"] = line.split("TITULO:")[1].strip()
        if "INICIO:" in line:
            datos["inicio"] = float(line.split("INICIO:")[1].strip())
        if "FIN:" in line:
            datos["fin"] = float(line.split("FIN:")[1].strip())
        if "RAZON:" in line:
            datos["razon"] = line.split("RAZON:")[1].strip()
    return datos


def crear_directorio_salida(base_dir: str | Path = "outputs", job_id: str | None = None) -> tuple[str, Path]:
    job_id = job_id or uuid.uuid4().hex[:10]
    output_dir = Path(base_dir) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return job_id, output_dir


def generar_short_desde_url(
    video_url: str,
    api_key: str,
    output_filename: str = "short_con_subs.mp4",
    base_dir: str | Path = "outputs",
    job_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    validar_dependencias()

    job_id, output_dir = crear_directorio_salida(base_dir=base_dir, job_id=job_id)
    transcript_path = output_dir / "transcripcion_completa.txt"
    temp_base = output_dir / "video_temp"

    video_path = None
    clip = None
    clip_final = None

    try:
        _emit(progress_callback, "downloading", "Descargando video desde YouTube...")
        video_path = descargar_video(video_url, temp_base)

        _emit(progress_callback, "transcribing", "Transcribiendo audio con Whisper...")
        model = whisper.load_model("base")
        resultado = model.transcribe(str(video_path))

        transcript_path.write_text(
            f"URL: {video_url}\n{resultado['text']}", encoding="utf-8"
        )

        _emit(progress_callback, "analyzing", "Consultando a Gemini para elegir el mejor clip...")
        analisis = cerebro.encontrar_clip_viral(resultado["segments"], api_key)
        clip_data = parsear_respuesta_gemini(analisis)

        if "inicio" not in clip_data or "fin" not in clip_data:
            raise ValueError("Gemini no devolvio un rango valido de INICIO y FIN.")

        start = clip_data["inicio"]
        end = clip_data["fin"]
        output_path = output_dir / output_filename

        _emit(progress_callback, "rendering", "Renderizando short vertical con subtitulos...")
        clip = VideoFileClip(str(video_path)).subclipped(start, end)

        width, height = clip.size
        new_width = height * (9 / 16)
        clip_vertical = clip.cropped(
            x1=width / 2 - new_width / 2,
            y1=0,
            x2=width / 2 + new_width / 2,
            y2=height,
        )

        clip_final = subtitulos.generar_subtitulos(clip_vertical, resultado["segments"], start)
        clip_final.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            fps=24,
            threads=4,
            logger=None,
        )

        _emit(progress_callback, "completed", "Short listo para descargar.")
        return {
            "jobId": job_id,
            "videoUrl": video_url,
            "title": clip_data.get("titulo"),
            "reason": clip_data.get("razon"),
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