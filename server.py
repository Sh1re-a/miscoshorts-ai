from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from shorts_service import create_short_from_url


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = ROOT_DIR / "frontend" / "dist"

app = Flask(__name__, static_folder=str(FRONTEND_DIST_DIR), static_url_path="/")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def _append_job_log(job_id: str, stage: str, message: str) -> None:
    entry = {"time": time.time(), "stage": stage, "message": message}
    with jobs_lock:
        job = jobs.setdefault(job_id, {})
        logs = job.setdefault("logs", [])
        logs.append(entry)
        if len(logs) > 120:
            del logs[:-120]


def _set_job(job_id: str, **fields) -> None:
    with jobs_lock:
        jobs.setdefault(job_id, {}).update(fields)


def _job_progress(job_id: str, stage: str, message: str) -> None:
    print(f"[{stage}] {message}", flush=True)
    _append_job_log(job_id, stage, message)
    _set_job(job_id, status=stage, message=message, updatedAt=time.time())


def _run_job(job_id: str, video_url: str, api_key: str, output_filename: str, clip_count: int) -> None:
    _job_progress(job_id, "queued", "The job is queued.")
    try:
        result = create_short_from_url(
            video_url=video_url,
            api_key=api_key,
            output_filename=output_filename,
            clip_count=clip_count,
            job_id=job_id,
            progress_callback=lambda stage, message: _job_progress(job_id, stage, message),
            subtitle_style=jobs[job_id].get("subtitleStyle"),
        )
        _append_job_log(job_id, "completed", "Render finished successfully.")
        _set_job(job_id, status="completed", result=result, updatedAt=time.time())
    except Exception as error:
        print(traceback.format_exc(), flush=True)
        _append_job_log(job_id, "failed", str(error))
        _set_job(
            job_id,
            status="failed",
            error=str(error),
            traceback=traceback.format_exc(),
            updatedAt=time.time(),
        )


@app.get("/api/health")
def healthcheck():
    return jsonify({"status": "ok"})


@app.get("/api/bootstrap")
def bootstrap():
    return jsonify(
        {
            "hasConfiguredApiKey": bool((os.getenv("GEMINI_API_KEY") or "").strip()),
            "frontendBuilt": FRONTEND_DIST_DIR.exists(),
        }
    )


@app.post("/api/process")
def process_video():
    payload = request.get_json(silent=True) or {}
    video_url = (payload.get("videoUrl") or "").strip()
    api_key = (payload.get("apiKey") or "").strip()
    output_filename = (payload.get("outputFilename") or "short_con_subs.mp4").strip() or "short_con_subs.mp4"
    subtitle_style = payload.get("subtitleStyle") or {}
    clip_count = payload.get("clipCount") or 3

    if not video_url:
        return jsonify({"error": "videoUrl is required"}), 400
    if not api_key and not (os.getenv("GEMINI_API_KEY") or "").strip():
        return jsonify({"error": "Gemini API key is required. Paste it in the app or add GEMINI_API_KEY to .env."}), 400

    try:
        clip_count = max(1, min(5, int(clip_count)))
    except (TypeError, ValueError):
        return jsonify({"error": "clipCount must be a number between 1 and 5"}), 400

    job_id = uuid.uuid4().hex[:10]
    _set_job(job_id, status="queued", subtitleStyle=subtitle_style, clipCount=clip_count, createdAt=time.time(), updatedAt=time.time())
    _append_job_log(job_id, "queued", "The job was created and is waiting for the worker thread.")

    worker = threading.Thread(
        target=_run_job,
        args=(job_id, video_url, api_key, output_filename, clip_count),
        daemon=True,
    )
    worker.start()

    return jsonify({"jobId": job_id, "status": "queued", "clipCount": clip_count}), 202


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.get("/api/jobs/<job_id>/download/video")
def download_video(job_id: str):
    job = jobs.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "Video not ready"}), 404
    return send_file(job["result"]["outputPath"], as_attachment=True)


@app.get("/api/jobs/<job_id>/download/video/<int:clip_index>")
def download_video_clip(job_id: str, clip_index: int):
    job = jobs.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "Video not ready"}), 404

    clips = job.get("result", {}).get("clips") or []
    if clip_index < 1 or clip_index > len(clips):
        return jsonify({"error": "Clip not found"}), 404

    return send_file(clips[clip_index - 1]["outputPath"], as_attachment=True)


@app.get("/api/jobs/<job_id>/download/transcript")
def download_transcript(job_id: str):
    job = jobs.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "Transcript not ready"}), 404
    return send_file(job["result"]["transcriptPath"], as_attachment=True)


@app.get("/")
def serve_index():
    if FRONTEND_DIST_DIR.exists():
        return send_from_directory(FRONTEND_DIST_DIR, "index.html")
    return jsonify(
        {
            "message": "Frontend not built yet. Run npm install && npm run build inside frontend/ or start the Vite dev server.",
        }
    )


@app.get("/<path:path>")
def serve_static(path: str):
    if FRONTEND_DIST_DIR.exists() and (FRONTEND_DIST_DIR / path).exists():
        return send_from_directory(FRONTEND_DIST_DIR, path)
    return serve_index()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)