from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from app.paths import FRONTEND_DIST_DIR, OUTPUTS_DIR
from app.shorts_service import (
    create_short_from_url,
    normalize_requested_subtitle_style,
    sanitize_output_filename,
    validate_video_url,
)
from app import analytics


app = Flask(__name__, static_folder=str(FRONTEND_DIST_DIR), static_url_path="/")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
JOB_STATE_DIR = OUTPUTS_DIR / "_job_state"


def _job_state_path(job_id: str) -> Path:
    return JOB_STATE_DIR / f"{job_id}.json"


def _persist_job_locked(job_id: str) -> None:
    JOB_STATE_DIR.mkdir(parents=True, exist_ok=True)
    target_path = _job_state_path(job_id)
    temp_path = target_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(jobs[job_id], ensure_ascii=True, indent=2), encoding="utf-8")
    temp_path.replace(target_path)


def _load_jobs_from_disk() -> None:
    if not JOB_STATE_DIR.exists():
        return

    loaded_jobs: dict[str, dict] = {}
    for path in JOB_STATE_DIR.glob("*.json"):
        try:
            loaded_jobs[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

    with jobs_lock:
        jobs.update(loaded_jobs)


def _get_job(job_id: str) -> dict | None:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return None
        return dict(job)


def _resolve_job_artifact_path(job_id: str, artifact_path: str | None) -> Path | None:
    if not artifact_path:
        return None

    resolved_path = Path(artifact_path).resolve()
    try:
        resolved_path.relative_to(OUTPUTS_DIR.resolve())
    except ValueError:
        return None

    if job_id not in resolved_path.parts or not resolved_path.exists():
        return None

    return resolved_path


def _append_job_log(job_id: str, stage: str, message: str) -> None:
    entry = {"time": time.time(), "stage": stage, "message": message}
    with jobs_lock:
        job = jobs.setdefault(job_id, {})
        logs = job.setdefault("logs", [])
        logs.append(entry)
        if len(logs) > 120:
            del logs[:-120]
        _persist_job_locked(job_id)


def _set_job(job_id: str, **fields) -> None:
    with jobs_lock:
        jobs.setdefault(job_id, {}).update(fields)
        _persist_job_locked(job_id)


def _job_progress(job_id: str, stage: str, message: str) -> None:
    print(f"[{stage}] {message}", flush=True)
    _append_job_log(job_id, stage, message)
    _set_job(job_id, status=stage, message=message, updatedAt=time.time())


def _run_job(job_id: str, video_url: str, api_key: str, output_filename: str, clip_count: int) -> None:
    _job_progress(job_id, "queued", "The job is queued.")
    try:
        job = _get_job(job_id) or {}
        result = create_short_from_url(
            video_url=video_url,
            api_key=api_key,
            output_filename=output_filename,
            clip_count=clip_count,
            job_id=job_id,
            progress_callback=lambda stage, message: _job_progress(job_id, stage, message),
            subtitle_style=job.get("subtitleStyle"),
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
    subtitle_style = payload.get("subtitleStyle")
    clip_count = payload.get("clipCount") or 3

    if not api_key and not (os.getenv("GEMINI_API_KEY") or "").strip():
        return jsonify({"error": "Gemini API key is required. Paste it in the app or add GEMINI_API_KEY to .env."}), 400

    try:
        video_url = validate_video_url(video_url)
        output_filename = sanitize_output_filename(output_filename)
        subtitle_style = normalize_requested_subtitle_style(subtitle_style)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    try:
        clip_count = max(1, min(5, int(clip_count)))
    except (TypeError, ValueError):
        return jsonify({"error": "clipCount must be a number between 1 and 5"}), 400

    job_id = secrets.token_hex(12)
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
    job = _get_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.get("/api/jobs/<job_id>/download/video")
def download_video(job_id: str):
    job = _get_job(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "Video not ready"}), 404

    output_path = _resolve_job_artifact_path(job_id, job.get("result", {}).get("outputPath"))
    if output_path is None:
        return jsonify({"error": "Video file is unavailable"}), 404

    return send_file(output_path, as_attachment=True)


@app.get("/api/jobs/<job_id>/download/video/<int:clip_index>")
def download_video_clip(job_id: str, clip_index: int):
    job = _get_job(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "Video not ready"}), 404

    clips = job.get("result", {}).get("clips") or []
    if clip_index < 1 or clip_index > len(clips):
        return jsonify({"error": "Clip not found"}), 404

    output_path = _resolve_job_artifact_path(job_id, clips[clip_index - 1].get("outputPath"))
    if output_path is None:
        return jsonify({"error": "Clip file is unavailable"}), 404

    return send_file(output_path, as_attachment=True)


@app.get("/api/jobs/<job_id>/download/transcript")
def download_transcript(job_id: str):
    job = _get_job(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "Transcript not ready"}), 404

    transcript_path = _resolve_job_artifact_path(job_id, job.get("result", {}).get("transcriptPath"))
    if transcript_path is None:
        return jsonify({"error": "Transcript file is unavailable"}), 404

    return send_file(transcript_path, as_attachment=True)


# ── Feedback & analytics ─────────────────────────────────────────────

@app.post("/api/jobs/<job_id>/clips/<int:clip_index>/feedback")
def submit_feedback(job_id: str, clip_index: int):
    """Save user feedback (rating + optional tags) for a specific clip."""
    job = _get_job(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "Job not found or not completed"}), 404

    clips = job.get("result", {}).get("clips") or []
    if clip_index < 1 or clip_index > len(clips):
        return jsonify({"error": "Clip not found"}), 404

    payload = request.get_json(silent=True) or {}
    rating = (payload.get("rating") or "").strip().lower()
    tags = payload.get("tags") or []
    note = (payload.get("note") or "").strip()

    if not isinstance(tags, list):
        tags = []

    try:
        fb = analytics.save_feedback(job_id, clip_index, rating, tags, note)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify(fb), 201


@app.get("/api/jobs/<job_id>/clips/<int:clip_index>/feedback")
def get_feedback(job_id: str, clip_index: int):
    """Retrieve existing feedback for a clip."""
    fb = analytics.get_feedback(job_id, clip_index)
    if fb is None:
        return jsonify(None), 200
    return jsonify(fb)


@app.get("/api/analytics")
def get_analytics():
    """Return aggregated insights and threshold suggestions."""
    return jsonify(analytics.get_insights())


@app.post("/api/analytics/refresh")
def refresh_analytics():
    """Force-rebuild insights from all job data."""
    return jsonify(analytics.build_insights())


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


_load_jobs_from_disk()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)