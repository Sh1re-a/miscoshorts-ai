from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from app import analytics, subtitle_preview
from app.doctor import run_doctor
from app.errors import explain_exception
from app.paths import FRONTEND_DIST_DIR, OUTPUTS_DIR, OUTPUT_JOBS_DIR
from app.runtime import configure_logging, is_debug_enabled, load_local_env, runtime_summary
from app.shorts_service import (
    create_short_from_url,
    normalize_requested_render_profile,
    normalize_requested_subtitle_style,
    RENDER_PROFILES,
    sanitize_output_filename,
    validate_video_url,
)

load_local_env()
logger, SERVER_LOG_PATH = configure_logging("server")
DEBUG_MODE = is_debug_enabled()


app = Flask(__name__, static_folder=str(FRONTEND_DIST_DIR), static_url_path="/")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
JOB_STATE_DIR = OUTPUTS_DIR / "_job_state"
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "1")))
MAX_QUEUED_JOBS = max(0, int(os.getenv("MAX_QUEUED_JOBS", "2")))
JOB_RETENTION_HOURS = max(1, int(os.getenv("JOB_RETENTION_HOURS", "24")))
_active_jobs = 0
_job_slots = threading.Semaphore(MAX_CONCURRENT_JOBS)
_DOWNLOAD_PCT_PATTERN = re.compile(r"Downloading\.\.\.\s+(\d+)%")
_RENDER_CLIP_PATTERN = re.compile(r"clip\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


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


def _count_jobs_by_status() -> dict[str, int]:
    counts = {"queued": 0, "active": 0}
    active_statuses = {"validating", "downloading", "transcribing", "analyzing", "rendering"}

    with jobs_lock:
        for job in jobs.values():
            status = job.get("status")
            if status == "queued":
                counts["queued"] += 1
            elif status in active_statuses:
                counts["active"] += 1

    return counts


def _refresh_queue_positions_locked() -> None:
    queued_jobs = sorted(
        (
            (job_id, job)
            for job_id, job in jobs.items()
            if job.get("status") == "queued"
        ),
        key=lambda item: (float(item[1].get("createdAt") or 0), item[0]),
    )

    for position, (job_id, job) in enumerate(queued_jobs, start=1):
        job["queuePosition"] = position

    for job_id, job in jobs.items():
        if job.get("status") != "queued":
            job["queuePosition"] = 0


def _refresh_queue_positions() -> None:
    with jobs_lock:
        _refresh_queue_positions_locked()
        for job_id in jobs:
            _persist_job_locked(job_id)


def _cleanup_expired_jobs() -> int:
    cutoff = time.time() - (JOB_RETENTION_HOURS * 3600)
    removed_job_ids: list[str] = []

    with jobs_lock:
        for job_id, job in list(jobs.items()):
            updated_at = float(job.get("updatedAt") or job.get("createdAt") or 0)
            if updated_at and updated_at < cutoff:
                removed_job_ids.append(job_id)
                jobs.pop(job_id, None)

    for job_id in removed_job_ids:
        _job_state_path(job_id).unlink(missing_ok=True)
        shutil.rmtree(OUTPUT_JOBS_DIR / job_id, ignore_errors=True)

    if removed_job_ids:
        _refresh_queue_positions()

    return len(removed_job_ids)


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


def _derive_progress_fields(job: dict, stage: str, message: str) -> dict[str, float | int | None]:
    clip_count = int(job.get("clipCount") or 1)
    overall = {
        "queued": 4.0,
        "validating": 10.0,
        "downloading": 18.0,
        "transcribing": 42.0,
        "analyzing": 62.0,
        "rendering": 78.0,
        "completed": 100.0,
        "failed": float(job.get("overallProgress") or 100.0),
    }.get(stage, 0.0)
    stage_progress = 0.0
    eta_seconds: float | None = None

    if stage == "queued":
        queue_position = int(job.get("queuePosition") or 0)
        stage_progress = 100.0 if queue_position == 0 else max(5.0, 100.0 - queue_position * 18.0)
        eta_seconds = queue_position * max(75.0, clip_count * 95.0)
    elif stage == "downloading":
        match = _DOWNLOAD_PCT_PATTERN.search(message)
        pct = float(match.group(1)) if match else 20.0
        stage_progress = pct
        overall = 10.0 + pct * 0.20
        eta_seconds = max(8.0, (100.0 - pct) * 1.2)
    elif stage == "transcribing":
        lowered_message = message.lower()
        if "preparing the local speech model" in lowered_message:
            stage_progress = 12.0
            eta_seconds = max(90.0, clip_count * 150.0)
        else:
            stage_progress = 35.0 if "whisper" in lowered_message else 100.0 if "complete" in lowered_message else 55.0
            eta_seconds = max(20.0, clip_count * (120.0 if stage_progress < 100.0 else 8.0))
        overall = 18.0 + stage_progress * 0.24
    elif stage == "analyzing":
        stage_progress = 45.0 if "Asking Gemini" in message else 100.0
        overall = 42.0 + stage_progress * 0.20
        eta_seconds = 45.0 if stage_progress < 100.0 else 5.0
    elif stage == "rendering":
        match = _RENDER_CLIP_PATTERN.search(message)
        if match:
            clip_index = int(match.group(1))
            total = max(1, int(match.group(2)))
            stage_progress = round(((clip_index - 1) / total) * 100.0, 1)
            overall = 62.0 + ((clip_index - 1) / total) * 36.0
            eta_seconds = max(20.0, (total - clip_index + 1) * 70.0)
        elif "Subtitle preflight passed" in message:
            stage_progress = min(98.0, float(job.get("stageProgress") or 0.0) + 12.0)
        else:
            stage_progress = min(95.0, float(job.get("stageProgress") or 0.0) + 6.0)
    elif stage == "completed":
        stage_progress = 100.0
        eta_seconds = 0.0
    elif stage == "failed":
        stage_progress = 100.0

    return {
        "overallProgress": round(min(100.0, overall), 1),
        "stageProgress": round(min(100.0, stage_progress), 1),
        "etaSeconds": None if eta_seconds is None else round(max(0.0, eta_seconds), 1),
    }


def _set_job(job_id: str, **fields) -> None:
    with jobs_lock:
        jobs.setdefault(job_id, {}).update(fields)
        _persist_job_locked(job_id)


def _job_progress(job_id: str, stage: str, message: str) -> None:
    print(f"[{stage}] {message}", flush=True)
    logger.info("[%s] %s", stage, message)
    _append_job_log(job_id, stage, message)
    job = _get_job(job_id) or {}
    progress_fields = _derive_progress_fields(job, stage, message)
    _set_job(job_id, status=stage, message=message, updatedAt=time.time(), **progress_fields)


def _run_job(job_id: str, video_url: str, api_key: str, output_filename: str, clip_count: int) -> None:
    global _active_jobs
    _job_progress(job_id, "queued", "The job is queued.")
    try:
        _job_progress(job_id, "queued", "Waiting for an available render worker...")
        _job_slots.acquire()
        with jobs_lock:
            _active_jobs += 1
            queued_job = jobs.get(job_id)
            if queued_job is not None:
                queued_job["queuePosition"] = 0
            _refresh_queue_positions_locked()
            for queued_job_id in jobs:
                _persist_job_locked(queued_job_id)

        job = _get_job(job_id) or {}
        result = create_short_from_url(
            video_url=video_url,
            api_key=api_key,
            output_filename=output_filename,
            clip_count=clip_count,
            job_id=job_id,
            progress_callback=lambda stage, message: _job_progress(job_id, stage, message),
            subtitle_style=job.get("subtitleStyle"),
            render_profile=job.get("renderProfile") or normalize_requested_render_profile(None),
        )
        _append_job_log(job_id, "completed", "Render finished successfully.")
        _set_job(job_id, status="completed", result=result, updatedAt=time.time(), overallProgress=100.0, stageProgress=100.0, etaSeconds=0.0)
    except Exception as error:
        friendly = explain_exception(error)
        error_id = f"{friendly.category}-{job_id[:8]}"
        logger.exception("Job %s failed", job_id)
        if DEBUG_MODE:
            print(traceback.format_exc(), flush=True)
        _append_job_log(job_id, "failed", f"{friendly.summary} [{error_id}]")
        _set_job(
            job_id,
            status="failed",
            error=friendly.summary,
            errorHelp=friendly.hint,
            errorCategory=friendly.category,
            errorId=error_id,
            traceback=traceback.format_exc() if DEBUG_MODE else None,
            technicalError=str(error) if DEBUG_MODE else None,
            logPath=str(SERVER_LOG_PATH),
            updatedAt=time.time(),
            etaSeconds=None,
        )
    finally:
        with jobs_lock:
            if _active_jobs > 0:
                _active_jobs -= 1
            _refresh_queue_positions_locked()
            for queued_job_id in jobs:
                _persist_job_locked(queued_job_id)
        _job_slots.release()
        _cleanup_expired_jobs()


@app.get("/api/health")
def healthcheck():
    counts = _count_jobs_by_status()
    return jsonify(
        {
            "status": "ok",
            "limits": {
                "maxConcurrentJobs": MAX_CONCURRENT_JOBS,
                "maxQueuedJobs": MAX_QUEUED_JOBS,
                "jobRetentionHours": JOB_RETENTION_HOURS,
            },
            "jobs": counts,
            "queueDepth": counts["queued"],
        }
    )


@app.get("/api/bootstrap")
def bootstrap():
    doctor_report = run_doctor(prepare_whisper=False)
    return jsonify(
        {
            "hasConfiguredApiKey": bool((os.getenv("GEMINI_API_KEY") or "").strip()),
            "frontendBuilt": FRONTEND_DIST_DIR.exists(),
            "defaultRenderProfile": normalize_requested_render_profile(None),
            "renderProfiles": {key: profile["label"] for key, profile in RENDER_PROFILES.items()},
            "speakerDiarizationMode": os.getenv("SPEAKER_DIARIZATION_MODE", "auto").strip().lower() or "auto",
            "hasPyannoteToken": bool(
                (os.getenv("PYANNOTE_AUTH_TOKEN") or os.getenv("HUGGINGFACE_ACCESS_TOKEN") or os.getenv("HF_TOKEN") or "").strip()
            ),
            "doctorStatus": doctor_report["status"],
            "runtime": runtime_summary(),
            "logPath": str(SERVER_LOG_PATH),
            "doctorReportPath": doctor_report.get("reportPath"),
        }
    )


@app.get("/api/doctor")
def doctor_report():
    report = run_doctor(prepare_whisper=False)
    report["logPath"] = str(SERVER_LOG_PATH)
    return jsonify(report)


@app.post("/api/process")
def process_video():
    _cleanup_expired_jobs()
    payload = request.get_json(silent=True) or {}
    video_url = (payload.get("videoUrl") or "").strip()
    api_key = (payload.get("apiKey") or "").strip()
    output_filename = (payload.get("outputFilename") or "short_con_subs.mp4").strip() or "short_con_subs.mp4"
    subtitle_style = payload.get("subtitleStyle")
    render_profile = payload.get("renderProfile")
    clip_count = payload.get("clipCount") or 3

    if not api_key and not (os.getenv("GEMINI_API_KEY") or "").strip():
        return jsonify({"error": "Gemini API key is required. Paste it in the app or add GEMINI_API_KEY to .env."}), 400

    try:
        video_url = validate_video_url(video_url)
        output_filename = sanitize_output_filename(output_filename)
        subtitle_style = normalize_requested_subtitle_style(subtitle_style)
        render_profile = normalize_requested_render_profile(render_profile)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    try:
        clip_count = max(1, min(5, int(clip_count)))
    except (TypeError, ValueError):
        return jsonify({"error": "clipCount must be a number between 1 and 5"}), 400

    counts = _count_jobs_by_status()
    if counts["active"] >= MAX_CONCURRENT_JOBS and counts["queued"] >= MAX_QUEUED_JOBS:
        return (
            jsonify(
                {
                    "error": "The render queue is full right now. Try again in a few minutes.",
                    "limits": {
                        "maxConcurrentJobs": MAX_CONCURRENT_JOBS,
                        "maxQueuedJobs": MAX_QUEUED_JOBS,
                    },
                    "jobs": counts,
                }
            ),
            503,
        )

    job_id = secrets.token_hex(12)
    _set_job(
        job_id,
        status="queued",
        subtitleStyle=subtitle_style,
        renderProfile=render_profile,
        clipCount=clip_count,
        createdAt=time.time(),
        updatedAt=time.time(),
        overallProgress=4.0,
        stageProgress=5.0,
        )
    _refresh_queue_positions()
    _append_job_log(job_id, "queued", "The job was created and is waiting for the worker thread.")

    job_snapshot = _get_job(job_id) or {}

    worker = threading.Thread(
        target=_run_job,
        args=(job_id, video_url, api_key, output_filename, clip_count),
        daemon=True,
    )
    worker.start()

    return jsonify(
        {
            "jobId": job_id,
            "status": "queued",
            "clipCount": clip_count,
            "queuePosition": job_snapshot.get("queuePosition", 0),
            "renderProfile": render_profile,
        }
    ), 202


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


@app.post("/api/subtitle-preview")
def generate_subtitle_preview():
    payload = request.get_json(silent=True) or {}
    subtitle_style = payload.get("subtitleStyle")
    title = (payload.get("title") or "").strip()
    reason = (payload.get("reason") or "").strip()

    try:
        subtitle_style = normalize_requested_subtitle_style(subtitle_style)
        preview = subtitle_preview.generate_preview_bundle(
            subtitle_style=subtitle_style,
            title=title,
            reason=reason,
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    preview_id = preview["previewId"]
    preview["headerImages"] = [f"/api/subtitle-preview/{preview_id}/{filename}" for filename in preview["headerImages"]]
    for cue in preview["subtitleFrames"]:
        cue["frames"] = {
            name: f"/api/subtitle-preview/{preview_id}/{filename}"
            for name, filename in cue["frames"].items()
        }
    return jsonify(preview)


@app.get("/api/subtitle-preview/<preview_id>/<path:filename>")
def serve_subtitle_preview_asset(preview_id: str, filename: str):
    preview_dir = subtitle_preview.PREVIEW_ROOT / preview_id
    asset_path = (preview_dir / filename).resolve()
    try:
        asset_path.relative_to(preview_dir.resolve())
    except ValueError:
        return jsonify({"error": "Preview asset not found"}), 404
    if not asset_path.exists():
        return jsonify({"error": "Preview asset not found"}), 404
    return send_from_directory(preview_dir, filename)


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
_cleanup_expired_jobs()


if __name__ == "__main__":
    app.run(
        host=os.getenv("MISCOSHORTS_HOST", "127.0.0.1"),
        port=int(os.getenv("MISCOSHORTS_PORT", "5001")),
        debug=False,
    )
