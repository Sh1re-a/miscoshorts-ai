from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from shorts_service import skapa_short_fran_url


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = ROOT_DIR / "frontend" / "dist"

app = Flask(__name__, static_folder=str(FRONTEND_DIST_DIR), static_url_path="/")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def _set_job(job_id: str, **fields) -> None:
    with jobs_lock:
        jobs.setdefault(job_id, {}).update(fields)


def _job_progress(job_id: str, stage: str, message: str) -> None:
    _set_job(job_id, status=stage, message=message, updatedAt=time.time())


def _run_job(job_id: str, video_url: str, api_key: str, output_filename: str) -> None:
    _set_job(job_id, status="queued", message="Jobbet ligger i ko.", updatedAt=time.time())
    try:
        result = skapa_short_fran_url(
            video_url=video_url,
            api_key=api_key,
            output_filename=output_filename,
            job_id=job_id,
            progress_callback=lambda stage, message: _job_progress(job_id, stage, message),
        )
        _set_job(job_id, status="completed", result=result, updatedAt=time.time())
    except Exception as error:
        _set_job(job_id, status="failed", error=str(error), updatedAt=time.time())


@app.get("/api/health")
def healthcheck():
    return jsonify({"status": "ok"})


@app.post("/api/process")
def process_video():
    payload = request.get_json(silent=True) or {}
    video_url = (payload.get("videoUrl") or "").strip()
    api_key = (payload.get("apiKey") or "").strip()
    output_filename = (payload.get("outputFilename") or "short_con_subs.mp4").strip() or "short_con_subs.mp4"

    if not video_url:
        return jsonify({"error": "videoUrl is required"}), 400
    if not api_key:
        return jsonify({"error": "apiKey is required"}), 400

    job_id = uuid.uuid4().hex[:10]
    _set_job(job_id, status="queued", createdAt=time.time(), updatedAt=time.time())

    worker = threading.Thread(
        target=_run_job,
        args=(job_id, video_url, api_key, output_filename),
        daemon=True,
    )
    worker.start()

    return jsonify({"jobId": job_id, "status": "queued"}), 202


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