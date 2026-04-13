from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from app import analytics, storage_manager, subtitle_preview
from app.doctor import run_doctor
from app.errors import explain_exception
from app.paths import FRONTEND_DIST_DIR, LOGS_DIR, OUTPUTS_DIR, PROJECT_ROOT
import atexit

from app.render_session import cleanup_stale_fingerprint_locks, job_fingerprint, list_active_fingerprint_locks
from app.runtime import backend_code_signature, configure_logging, is_debug_enabled, load_local_env, managed_runtime_python, runtime_identity, runtime_summary
from app.runtime_recovery import recover_runtime_state
from app.storage import prune_runtime_storage
from app.shorts_service import (
    normalize_requested_render_profile,
    normalize_requested_subtitle_style,
    RENDER_PROFILES,
    sanitize_output_filename,
    validate_video_url,
)

load_local_env()
logger, SERVER_LOG_PATH = configure_logging("server")
DEBUG_MODE = is_debug_enabled()

# Suppress werkzeug's per-request HTTP logs — they spam the terminal during
# normal polling (the frontend polls /api/jobs every 1.2 s and /api/runtime
# every 2 s). Errors and warnings are still shown.
if not DEBUG_MODE:
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.ERROR)


app = Flask(__name__, static_folder=str(FRONTEND_DIST_DIR), static_url_path="/")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
_shutdown_requested = threading.Event()
JOB_STATE_DIR = OUTPUTS_DIR / "_job_state"
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "1")))
MAX_QUEUED_JOBS = max(0, int(os.getenv("MAX_QUEUED_JOBS", "2")))
JOB_RETENTION_HOURS = max(1, int(os.getenv("JOB_RETENTION_HOURS", "24")))
ACTIVE_JOB_STATUSES = {"validating", "downloading", "transcribing", "analyzing", "rendering"}
TERMINAL_JOB_STATUSES = {"completed", "failed"}
RUNTIME_SESSION_ID = secrets.token_hex(8)
SERVER_STARTED_AT = time.time()
_active_jobs = 0
_job_slots = threading.Semaphore(MAX_CONCURRENT_JOBS)
_active_worker_procs: list[subprocess.Popen] = []
_active_worker_procs_lock = threading.Lock()
_job_worker_map: dict[str, subprocess.Popen] = {}  # job_id -> worker process
_job_cancel_flags: dict[str, bool] = {}  # job_id -> True if cancellation requested
_cleanup_thread: threading.Thread | None = None
_CLEANUP_INTERVAL = 60  # seconds between cleanup runs


def _shutdown_workers() -> None:
    """Kill all active render worker subprocesses. Called via atexit."""
    logger.info("Shutting down: signaling cleanup thread to stop...")
    _shutdown_requested.set()
    
    # Wait for cleanup thread to finish
    if _cleanup_thread and _cleanup_thread.is_alive():
        _cleanup_thread.join(timeout=2)
    
    with _active_worker_procs_lock:
        procs = list(_active_worker_procs)
        # Clear all tracking maps
        _active_worker_procs.clear()
        _job_worker_map.clear()
        _job_cancel_flags.clear()
    
    for proc in procs:
        try:
            if proc.poll() is None:
                logger.info("Killing worker process pid=%s", proc.pid)
                proc.terminate()  # Try graceful termination first
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()  # Force kill if needed
                    proc.wait(timeout=2)
        except Exception as e:
            logger.warning("Error killing worker: %s", e)
    
    if procs:
        logger.info("atexit: killed %d render worker process(es).", len(procs))
    
    # Clean up temp directories
    _cleanup_stale_temp_dirs()


def _cleanup_stale_temp_dirs() -> None:
    """Remove any temp directories that might have been left behind."""
    temp_dir = OUTPUTS_DIR / "temp"
    if not temp_dir.exists():
        return
    
    cutoff = time.time() - 3600  # 1 hour old
    removed = 0
    for item in temp_dir.iterdir():
        try:
            if item.is_dir() and item.stat().st_mtime < cutoff:
                shutil.rmtree(item, ignore_errors=True)
                removed += 1
        except Exception:
            pass
    
    if removed:
        logger.info("Cleaned up %d stale temp directories.", removed)


def _cleanup_orphan_processes() -> None:
    """Check for and clean up any orphaned worker processes."""
    with _active_worker_procs_lock:
        orphaned = []
        for proc in _active_worker_procs:
            if proc.poll() is not None:
                orphaned.append(proc)
        
        for proc in orphaned:
            _active_worker_procs.remove(proc)
        
        # Also clean up job worker map for dead processes
        dead_jobs = [
            job_id for job_id, proc in _job_worker_map.items()
            if proc.poll() is not None
        ]
        for job_id in dead_jobs:
            _job_worker_map.pop(job_id, None)
            _job_cancel_flags.pop(job_id, None)
    
    if orphaned:
        logger.debug("Cleaned up %d orphaned process references.", len(orphaned))


def _cleanup_thread_func() -> None:
    """Background thread that periodically cleans up resources."""
    logger.info("Cleanup thread started.")
    while not _shutdown_requested.wait(timeout=_CLEANUP_INTERVAL):
        try:
            _cleanup_orphan_processes()
            _cleanup_stale_fingerprint_locks()
            _cleanup_stale_temp_dirs()
            _cleanup_expired_jobs()
        except Exception as e:
            logger.warning("Error in cleanup thread: %s", e)
    logger.info("Cleanup thread stopped.")


def _start_cleanup_thread() -> None:
    """Start the background cleanup thread."""
    global _cleanup_thread
    if _cleanup_thread is None or not _cleanup_thread.is_alive():
        _cleanup_thread = threading.Thread(target=_cleanup_thread_func, daemon=True, name="cleanup-thread")
        _cleanup_thread.start()


def _signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    logger.info("Received %s signal, initiating graceful shutdown...", sig_name)
    _shutdown_workers()
    sys.exit(0)


# Register signal handlers
if threading.current_thread() is threading.main_thread():
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
    except Exception as e:
        logger.debug("Could not register signal handlers: %s", e)

atexit.register(_shutdown_workers)
_DOWNLOAD_PCT_PATTERN = re.compile(r"Downloading\.\.\.\s+(\d+)%")
_DOWNLOAD_ETA_PATTERN = re.compile(r"~(\d+)s\s+left")
_RENDER_CLIP_PATTERN = re.compile(r"clip\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)
_LOCK_WAIT_PATTERN = re.compile(
    r"^LOCK_WAIT \| fingerprint=(?P<fingerprint>[0-9a-f]+) \| ownerJobId=(?P<owner_job_id>[a-zA-Z0-9_-]+) \| ownerPid=(?P<owner_pid>[0-9]+|unknown) \| ",
    re.IGNORECASE,
)
STARTUP_RECOVERY = recover_runtime_state()

# Start cleanup thread
_start_cleanup_thread()


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
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload.setdefault("runtimeSessionId", "previous-session")
        loaded_jobs[path.stem] = payload

    with jobs_lock:
        jobs.update(loaded_jobs)
        _refresh_queue_positions_locked()
        for job_id in loaded_jobs:
            _persist_job_locked(job_id)


def _get_job(job_id: str) -> dict | None:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return None
        return dict(job)


def _count_jobs_by_status() -> dict[str, int]:
    counts = {"queued": 0, "active": 0}

    with jobs_lock:
        for job in jobs.values():
            status = job.get("status")
            if status == "queued":
                counts["queued"] += 1
            elif status in ACTIVE_JOB_STATUSES:
                counts["active"] += 1

    return counts


def _derive_queue_state(stage: str, message: str, job: dict) -> dict[str, object | None]:
    queue_state: str | None = job.get("queueState") if isinstance(job, dict) else None
    waiting_on_job_id = None
    waiting_on_fingerprint = None
    lock_match = _LOCK_WAIT_PATTERN.match(message or "")

    if stage == "queued":
        if lock_match:
            queue_state = "waiting_for_identical_render"
            owner_job_id = lock_match.group("owner_job_id")
            waiting_on_job_id = None if owner_job_id == "unknown" else owner_job_id
            waiting_on_fingerprint = lock_match.group("fingerprint")
        elif "available render worker" in (message or "").lower():
            queue_state = "waiting_for_worker"
        else:
            queue_state = "queued"
    elif stage in ACTIVE_JOB_STATUSES:
        queue_state = "running"
    elif stage == "completed":
        queue_state = "completed"
    elif stage == "failed":
        queue_state = "failed"

    return {
        "queueState": queue_state,
        "waitingOnJobId": waiting_on_job_id,
        "waitingOnFingerprint": waiting_on_fingerprint,
    }


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

    if removed_job_ids:
        _refresh_queue_positions()
        prune_runtime_storage(dry_run=False)

    return len(removed_job_ids)


def _resolve_job_artifact_path(job_id: str, artifact_path: str | None) -> Path | None:
    if not artifact_path:
        return None

    resolved_path = Path(artifact_path).resolve()
    try:
        resolved_path.relative_to(OUTPUTS_DIR.resolve())
    except ValueError:
        return None

    if not resolved_path.exists():
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
        queue_state = str(job.get("queueState") or "")
        if queue_state == "waiting_for_identical_render":
            stage_progress = 22.0
            eta_seconds = max(90.0, clip_count * 110.0)
        elif queue_state == "waiting_for_worker":
            stage_progress = 12.0 if queue_position == 0 else max(8.0, 48.0 - queue_position * 10.0)
            eta_seconds = max(60.0, max(1, queue_position) * max(75.0, clip_count * 95.0))
        else:
            stage_progress = 100.0 if queue_position == 0 else max(5.0, 100.0 - queue_position * 18.0)
            eta_seconds = queue_position * max(75.0, clip_count * 95.0)
    elif stage == "downloading":
        match = _DOWNLOAD_PCT_PATTERN.search(message)
        pct = float(match.group(1)) if match else 20.0
        stage_progress = pct
        overall = 10.0 + pct * 0.20
        # Try to extract actual ETA from message (e.g., "~1219s left")
        eta_match = _DOWNLOAD_ETA_PATTERN.search(message)
        if eta_match:
            eta_seconds = float(eta_match.group(1))
        else:
            # Fallback estimate if ETA not in message
            eta_seconds = max(30.0, (100.0 - pct) * 3.0)
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
    state_fields = _derive_queue_state(stage, message, job)
    _set_job(job_id, status=stage, message=message, updatedAt=time.time(), **progress_fields, **state_fields)


def _matching_live_job_for_fingerprint(fingerprint: str, *, exclude_job_id: str | None = None) -> dict | None:
    with jobs_lock:
        ranked_jobs = sorted(
            (
                (job_id, job)
                for job_id, job in jobs.items()
                if job.get("jobFingerprint") == fingerprint and job_id != exclude_job_id and job.get("status") not in TERMINAL_JOB_STATUSES
            ),
            key=lambda item: (0 if item[1].get("status") in ACTIVE_JOB_STATUSES else 1, float(item[1].get("createdAt") or 0)),
        )
    if not ranked_jobs:
        return None
    job_id, job = ranked_jobs[0]
    snapshot = dict(job)
    snapshot["jobId"] = job_id
    return snapshot


def _queue_snapshot() -> dict[str, object]:
    with jobs_lock:
        job_snapshots = {job_id: dict(job) for job_id, job in jobs.items()}
        active_jobs = [
            {"jobId": job_id, **job}
            for job_id, job in job_snapshots.items()
            if job.get("status") in ACTIVE_JOB_STATUSES
        ]
        queued_jobs = [
            {"jobId": job_id, **job}
            for job_id, job in job_snapshots.items()
            if job.get("status") == "queued"
        ]
        recent_jobs = sorted(
            ({"jobId": job_id, **job} for job_id, job in job_snapshots.items()),
            key=lambda item: (float(item.get("updatedAt") or item.get("createdAt") or 0), item["jobId"]),
            reverse=True,
        )[:12]

    active_jobs.sort(key=lambda item: (float(item.get("createdAt") or 0), item["jobId"]))
    queued_jobs.sort(
        key=lambda item: (
            float(item.get("queuePosition") or 0),
            float(item.get("createdAt") or 0),
            item["jobId"],
        )
    )
    lock_details = list_active_fingerprint_locks()
    issues: list[str] = []
    if _active_jobs != len(active_jobs):
        issues.append(f"Active worker count mismatch: semaphore tracks {_active_jobs}, jobs track {len(active_jobs)}.")
    # Detect locks pointing at finished jobs and auto-remove them.
    stale_lock_fingerprints: list[str] = []
    for lock in lock_details:
        lock_job_id = lock.get("jobId")
        if lock_job_id and job_snapshots.get(str(lock_job_id), {}).get("status") in TERMINAL_JOB_STATUSES:
            stale_lock_fingerprints.append(str(lock.get("fingerprint", "")))
            issues.append(f"Lock for fingerprint {lock.get('fingerprint')} still points at terminal job {lock_job_id} (auto-removing).")
    if stale_lock_fingerprints:
        # Gather terminal job IDs and run cleanup that will remove the stale locks.
        terminal_ids = {
            job_id for job_id, job in job_snapshots.items()
            if job.get("status") in TERMINAL_JOB_STATUSES
        }
        cleaned = cleanup_stale_fingerprint_locks(terminal_job_ids=terminal_ids)
        if cleaned["removedLocks"]:
            # Refresh lock_details after cleanup.
            lock_details = list_active_fingerprint_locks()
    for queued_job in queued_jobs:
        if queued_job.get("queueState") == "waiting_for_identical_render" and queued_job.get("waitingOnFingerprint"):
            if not any(lock.get("fingerprint") == queued_job.get("waitingOnFingerprint") for lock in lock_details):
                issues.append(
                    f"Job {queued_job['jobId']} says it is waiting on fingerprint {queued_job.get('waitingOnFingerprint')} but no live lock exists."
                )

    return {
        "runtimeSessionId": RUNTIME_SESSION_ID,
        "serverPid": os.getpid(),
        "serverStartedAt": SERVER_STARTED_AT,
        "queue": {
            "activeCount": len(active_jobs),
            "queuedCount": len(queued_jobs),
            "waitingForWorkerCount": sum(1 for job in queued_jobs if job.get("queueState") == "waiting_for_worker"),
            "waitingForIdenticalRenderCount": sum(1 for job in queued_jobs if job.get("queueState") == "waiting_for_identical_render"),
            "activeJobs": active_jobs,
            "queuedJobs": queued_jobs,
        },
        "locks": lock_details,
        "recovery": STARTUP_RECOVERY,
        "recentJobs": recent_jobs,
        "consistency": {
            "status": "ok" if not issues else "degraded",
            "issues": issues,
        },
    }


# ---------------------------------------------------------------------------
# Subprocess-isolated render worker
# ---------------------------------------------------------------------------
# The heavy render pipeline (moviepy / ffmpeg / PIL / OpenCV) runs in a child
# process.  If the worker segfaults, gets OOM-killed, or triggers any
# unrecoverable native crash, the Flask backend survives and reports the
# failure cleanly.  Communication uses JSON files in a temp directory.
# ---------------------------------------------------------------------------

_WORKER_POLL_INTERVAL = 0.8  # seconds between progress reads


def _read_worker_progress(progress_path: Path, last_offset: int) -> tuple[list[dict], int]:
    """Read new JSONL lines from the progress file written by the worker."""
    entries: list[dict] = []
    try:
        with open(progress_path, "r", encoding="utf-8") as f:
            f.seek(last_offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            new_offset = f.tell()
    except FileNotFoundError:
        return entries, last_offset
    except OSError:
        return entries, last_offset
    return entries, new_offset


def _read_worker_result(result_path: Path) -> dict | None:
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def _exit_code_diagnosis(rc: int) -> str:
    """Translate common process exit codes to human-readable causes."""
    if os.name == "nt":
        # Windows: negative codes are unsigned 32-bit NTSTATUS or signal codes.
        codes: dict[int, str] = {
            -1073741819: "memory access violation (segfault / STATUS_ACCESS_VIOLATION)",
            -1073741571: "stack overflow (STATUS_STACK_OVERFLOW)",
            -1073741510: "process terminated by Ctrl+C (STATUS_CONTROL_C_EXIT)",
            -1073740791: "heap corruption (STATUS_HEAP_CORRUPTION)",
            -1073740940: "process killed (STATUS_STACK_BUFFER_OVERRUN / fail-fast)",
        }
        description = codes.get(rc)
        if description:
            return description
        if rc < 0:
            return f"native crash (exit code {rc:#010x})"
    else:
        import signal as _signal
        if rc < 0:
            sig = -rc
            name = _signal.Signals(sig).name if sig in _signal.valid_signals() else f"signal {sig}"
            if sig == 9:
                return f"killed by OS — likely out of memory ({name})"
            if sig == 11:
                return f"segmentation fault ({name})"
            return f"killed by {name}"
    if rc != 0:
        return f"exited with code {rc}"
    return ""


def _run_job(job_id: str, video_url: str, api_key: str, output_filename: str, clip_count: int) -> None:
    """Spawn the render pipeline in an isolated child process and relay progress."""
    global _active_jobs
    _job_progress(job_id, "queued", "The job is queued.")
    work_dir: Path | None = None
    worker_proc: subprocess.Popen | None = None
    log_handle = None

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

        # --- prepare worker directory and params ---
        work_dir = Path(tempfile.mkdtemp(prefix=f"render-{job_id[:8]}-", dir=str(OUTPUTS_DIR / "temp")))
        params_path = work_dir / "params.json"
        progress_path = work_dir / "progress.jsonl"
        result_path = work_dir / "result.json"
        worker_log_path = LOGS_DIR / f"render-worker-{job_id[:12]}.log"
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        params = {
            "jobId": job_id,
            "videoUrl": video_url,
            "apiKey": api_key,
            "outputFilename": output_filename,
            "clipCount": clip_count,
            "subtitleStyle": job.get("subtitleStyle"),
            "renderProfile": job.get("renderProfile") or normalize_requested_render_profile(None),
        }
        params_path.write_text(json.dumps(params, ensure_ascii=True, indent=2), encoding="utf-8")

        # --- spawn isolated worker ---
        worker_python = managed_runtime_python() or Path(sys.executable)
        worker_env = dict(os.environ)
        worker_env.setdefault("PYTHONUTF8", "1")
        worker_env.setdefault("PYTHONIOENCODING", "utf-8")

        # On Windows, CREATE_NO_WINDOW prevents a console flash and
        # CREATE_NEW_PROCESS_GROUP isolates the worker from Ctrl+C in the
        # launcher terminal (which would kill both the server and the worker).
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

        (OUTPUTS_DIR / "temp").mkdir(parents=True, exist_ok=True)
        log_handle = open(worker_log_path, "w", encoding="utf-8")
        try:
            worker_proc = subprocess.Popen(
                [str(worker_python), "-m", "app.render_worker", str(params_path)],
                cwd=str(PROJECT_ROOT),
                env=worker_env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                **popen_kwargs,
            )
        except Exception:
            log_handle.close()
            raise

        with _active_worker_procs_lock:
            _active_worker_procs.append(worker_proc)
            _job_worker_map[job_id] = worker_proc
            _job_cancel_flags.pop(job_id, None)  # Clear any stale cancel flag

        _job_progress(job_id, "queued", f"Render worker started (pid {worker_proc.pid}).")
        logger.info("Render worker pid=%d for job %s, log=%s", worker_proc.pid, job_id, worker_log_path)

        # --- monitor worker: relay progress and wait for completion ---
        progress_offset = 0
        last_progress_time = time.time()

        while True:
            # Check for cancellation
            if _job_cancel_flags.get(job_id):
                logger.info("Cancellation requested for job %s, killing worker...", job_id)
                worker_proc.kill()
                worker_proc.wait(timeout=10)
                raise Exception("Job cancelled by user")
            
            rc = worker_proc.poll()

            # Read any new progress lines
            entries, progress_offset = _read_worker_progress(progress_path, progress_offset)
            for entry in entries:
                stage = entry.get("stage", "rendering")
                message = entry.get("message", "")
                _job_progress(job_id, stage, message)
                last_progress_time = time.time()

            if rc is not None:
                # Process exited — read any remaining progress
                entries, progress_offset = _read_worker_progress(progress_path, progress_offset)
                for entry in entries:
                    _job_progress(job_id, entry.get("stage", "rendering"), entry.get("message", ""))
                break

            time.sleep(_WORKER_POLL_INTERVAL)

        log_handle.close()

        # --- interpret result ---
        if rc == 0:
            result_payload = _read_worker_result(result_path)
            if result_payload and result_payload.get("ok"):
                _append_job_log(job_id, "completed", "Render finished successfully.")
                _set_job(
                    job_id,
                    status="completed",
                    result=result_payload["result"],
                    updatedAt=time.time(),
                    overallProgress=100.0,
                    stageProgress=100.0,
                    etaSeconds=0.0,
                )
            else:
                error_msg = (result_payload or {}).get("error", "Worker exited cleanly but produced no result.")
                _append_job_log(job_id, "failed", error_msg)
                _set_job(
                    job_id,
                    status="failed",
                    error=error_msg,
                    errorHelp="Check the render worker log for details.",
                    errorCategory="worker-error",
                    errorId=f"worker-error-{job_id[:8]}",
                    logPath=str(worker_log_path),
                    updatedAt=time.time(),
                    etaSeconds=None,
                )
        else:
            # Worker process crashed (segfault, OOM-kill, unhandled exception).
            result_payload = _read_worker_result(result_path)
            if result_payload and not result_payload.get("ok"):
                # Worker wrote an error before dying — use it.
                error_msg = result_payload.get("error", "Render failed.")
                error_help = "Check the render worker log for details."
                error_category = "worker-error"
            else:
                # No result file — the process died without Python-level handling.
                diagnosis = _exit_code_diagnosis(rc)
                error_msg = f"The render worker crashed ({diagnosis})." if diagnosis else f"The render worker crashed (exit code {rc})."
                error_help = (
                    "The render process was killed — this is often caused by running out of memory. "
                    "Try a shorter video or fewer clips. "
                    f"Worker log: {worker_log_path}"
                )
                error_category = "crash"

            friendly_error_id = f"{error_category}-{job_id[:8]}"
            logger.error("Render worker for job %s exited with code %d: %s", job_id, rc, error_msg)
            _append_job_log(job_id, "failed", f"{error_msg} [{friendly_error_id}]")
            _set_job(
                job_id,
                status="failed",
                error=error_msg,
                errorHelp=error_help,
                errorCategory=error_category,
                errorId=friendly_error_id,
                workerExitCode=rc,
                logPath=str(worker_log_path),
                updatedAt=time.time(),
                etaSeconds=None,
            )

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
        if log_handle is not None and not log_handle.closed:
            log_handle.close()
        if worker_proc is not None:
            with _active_worker_procs_lock:
                try:
                    _active_worker_procs.remove(worker_proc)
                except ValueError:
                    pass
                _job_worker_map.pop(job_id, None)
                _job_cancel_flags.pop(job_id, None)
            if worker_proc.poll() is None:
                try:
                    worker_proc.kill()
                    worker_proc.wait(timeout=10)
                except Exception:
                    logger.warning("Could not cleanly kill worker pid=%s", getattr(worker_proc, 'pid', '?'))
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)
        with jobs_lock:
            if _active_jobs > 0:
                _active_jobs -= 1
            _refresh_queue_positions_locked()
            for queued_job_id in jobs:
                _persist_job_locked(queued_job_id)
        _job_slots.release()
        _cleanup_expired_jobs()


# ── Global error handlers ────────────────────────────────────────────

@app.errorhandler(Exception)
def handle_exception(e):
    """Catch-all error handler for unhandled exceptions."""
    logger.exception("Unhandled exception in request: %s", e)
    return jsonify({
        "error": "An unexpected error occurred.",
        "errorHelp": "Check the server logs for details.",
        "errorCategory": "internal-error",
    }), 500


@app.errorhandler(404)
def handle_not_found(e):
    """Handle 404 errors."""
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(500)
def handle_internal_error(e):
    """Handle 500 errors."""
    logger.exception("Internal server error: %s", e)
    return jsonify({
        "error": "Internal server error.",
        "errorHelp": "The server encountered an unexpected condition.",
    }), 500


# ── Health & Bootstrap endpoints ─────────────────────────────────────

@app.get("/api/health")
def healthcheck():
    counts = _count_jobs_by_status()
    runtime_snapshot = _queue_snapshot()
    return jsonify(
        {
            "status": runtime_snapshot["consistency"]["status"],
            "limits": {
                "maxConcurrentJobs": MAX_CONCURRENT_JOBS,
                "maxQueuedJobs": MAX_QUEUED_JOBS,
                "jobRetentionHours": JOB_RETENTION_HOURS,
            },
            "jobs": counts,
            "queueDepth": counts["queued"],
            "runtimeSessionId": RUNTIME_SESSION_ID,
            "serverStartedAt": SERVER_STARTED_AT,
            "consistency": runtime_snapshot["consistency"],
        }
    )


@app.get("/api/bootstrap")
def bootstrap():
    doctor_report = run_doctor(prepare_whisper=False)
    runtime_snapshot = _queue_snapshot()
    return jsonify(
        {
            "hasConfiguredApiKey": bool((os.getenv("GEMINI_API_KEY") or "").strip()),
            "frontendBuilt": FRONTEND_DIST_DIR.exists(),
            "defaultRenderProfile": normalize_requested_render_profile(None),
            "renderProfiles": {key: profile["label"] for key, profile in RENDER_PROFILES.items()},
            "backendSignature": backend_code_signature(),
            "runtimeSessionId": RUNTIME_SESSION_ID,
            "serverPid": os.getpid(),
            "serverStartedAt": SERVER_STARTED_AT,
            "speakerDiarizationMode": os.getenv("SPEAKER_DIARIZATION_MODE", "auto").strip().lower() or "auto",
            "hasPyannoteToken": bool(
                (os.getenv("PYANNOTE_AUTH_TOKEN") or os.getenv("HUGGINGFACE_ACCESS_TOKEN") or os.getenv("HF_TOKEN") or "").strip()
            ),
            "doctorStatus": doctor_report["status"],
            "renderReady": doctor_report.get("renderReady"),
            "runtime": runtime_summary(),
            "python": runtime_identity(),
            "logPath": str(SERVER_LOG_PATH),
            "doctorReportPath": doctor_report.get("reportPath"),
            "queue": runtime_snapshot["queue"],
            "recovery": STARTUP_RECOVERY,
            "consistency": runtime_snapshot["consistency"],
        }
    )


@app.get("/api/doctor")
def doctor_report():
    report = run_doctor(prepare_whisper=False)
    report["logPath"] = str(SERVER_LOG_PATH)
    return jsonify(report)


@app.get("/api/runtime")
def runtime_status():
    global STARTUP_RECOVERY
    lock_cleanup = cleanup_stale_fingerprint_locks()
    if lock_cleanup["removedLocks"]:
        STARTUP_RECOVERY = {
            **STARTUP_RECOVERY,
            "clearedLocks": list(STARTUP_RECOVERY.get("clearedLocks") or []) + lock_cleanup["removedLocks"],
        }
    snapshot = _queue_snapshot()
    snapshot["backendSignature"] = backend_code_signature()
    snapshot["logPath"] = str(SERVER_LOG_PATH)
    snapshot["runtime"] = runtime_summary()
    return jsonify(snapshot)


@app.post("/api/process")
def process_video():
    global STARTUP_RECOVERY
    lock_cleanup = cleanup_stale_fingerprint_locks()
    if lock_cleanup["removedLocks"]:
        STARTUP_RECOVERY = {
            **STARTUP_RECOVERY,
            "clearedLocks": list(STARTUP_RECOVERY.get("clearedLocks") or []) + lock_cleanup["removedLocks"],
        }
    _cleanup_expired_jobs()
    doctor_report = run_doctor(prepare_whisper=False)
    blocking_checks = doctor_report.get("blockingChecks") or [
        check for check in doctor_report.get("checks", []) if check.get("blocks_rendering")
    ]
    if blocking_checks:
        blocking_summary = "; ".join(
            f"{check.get('name')}: {check.get('message')}" for check in blocking_checks[:3]
        )
        return (
            jsonify(
                {
                    "error": "This machine is not ready for rendering yet. Fix the blocking setup checks first.",
                    "errorHelp": "Run the launcher again so it can repair the managed runtime, then reopen the app and retry.",
                    "doctorStatus": doctor_report.get("status"),
                    "renderReady": doctor_report.get("renderReady"),
                    "doctorReportPath": doctor_report.get("reportPath"),
                    "blockingChecks": blocking_checks,
                    "details": blocking_summary,
                }
            ),
            503,
        )

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
    pipeline_fingerprint = job_fingerprint(
        video_url=video_url,
        output_filename=output_filename,
        clip_count=clip_count,
        render_profile=render_profile,
        subtitle_style=subtitle_style,
    )
    matching_job = _matching_live_job_for_fingerprint(pipeline_fingerprint)
    _set_job(
        job_id,
        status="queued",
        subtitleStyle=subtitle_style,
        renderProfile=render_profile,
        jobFingerprint=pipeline_fingerprint,
        clipCount=clip_count,
        createdAt=time.time(),
        updatedAt=time.time(),
        overallProgress=4.0,
        stageProgress=5.0,
        runtimeSessionId=RUNTIME_SESSION_ID,
        queueState="waiting_for_identical_render" if matching_job is not None else "waiting_for_worker",
        waitingOnJobId=matching_job.get("jobId") if matching_job is not None else None,
        waitingOnFingerprint=pipeline_fingerprint if matching_job is not None else None,
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
            "jobFingerprint": pipeline_fingerprint,
            "queueState": job_snapshot.get("queueState"),
            "waitingOnJobId": job_snapshot.get("waitingOnJobId"),
            "runtimeSessionId": RUNTIME_SESSION_ID,
        }
    ), 202


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    job = _get_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    """Cancel an in-progress job and clean up all associated files."""
    job = _get_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    
    status = job.get("status", "")
    
    # Can't cancel completed or already failed jobs
    if status in TERMINAL_JOB_STATUSES:
        return jsonify({"error": f"Cannot cancel a job that is already {status}"}), 400
    
    logger.info("Cancelling job %s (current status: %s)", job_id, status)
    
    # Set the cancel flag so the worker monitoring loop picks it up
    with _active_worker_procs_lock:
        worker = _job_worker_map.get(job_id)
        _job_cancel_flags[job_id] = True
        
        # If we have a direct handle to the worker, kill it immediately
        if worker is not None and worker.poll() is None:
            try:
                worker.terminate()  # Try graceful termination first
                try:
                    worker.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    worker.kill()  # Force kill if needed
                    worker.wait(timeout=2)
                logger.info("Killed worker process for job %s (pid %s)", job_id, worker.pid)
            except Exception as e:
                logger.warning("Failed to kill worker for job %s: %s", job_id, e)
            finally:
                # Clean up from tracking maps
                _job_worker_map.pop(job_id, None)
                try:
                    _active_worker_procs.remove(worker)
                except ValueError:
                    pass
    
    # Update job status to failed/cancelled
    _set_job(
        job_id,
        status="failed",
        error="Job cancelled by user.",
        errorHelp="Start a new render when you're ready.",
        errorCategory="cancelled",
        updatedAt=time.time(),
        etaSeconds=None,
    )
    _append_job_log(job_id, "failed", "Job cancelled by user.")
    
    # Clean up via the storage cleanup logic
    try:
        # Re-fetch job so the dict reflects the "failed" status we just set
        updated_job = _get_job(job_id) or job
        storage_manager.delete_job_storage({job_id: updated_job}, job_id, mode="job")
    except Exception as e:
        logger.warning("Error during cancel cleanup for job %s: %s", job_id, e)
    
    # Remove job from memory and disk state
    with jobs_lock:
        jobs.pop(job_id, None)
    _job_state_path(job_id).unlink(missing_ok=True)
    
    # Clean up any cancel flag
    _job_cancel_flags.pop(job_id, None)
    
    logger.info("Job %s cancelled and cleaned up.", job_id)
    return jsonify({"ok": True, "message": "Job cancelled and cleaned up."})


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


@app.get("/api/jobs/<job_id>/preview/video/<int:clip_index>")
def preview_video_clip(job_id: str, clip_index: int):
    """Serve a clip for inline playback (no Content-Disposition: attachment)."""
    job = _get_job(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "Video not ready"}), 404

    clips = job.get("result", {}).get("clips") or []
    if clip_index < 1 or clip_index > len(clips):
        return jsonify({"error": "Clip not found"}), 404

    output_path = _resolve_job_artifact_path(job_id, clips[clip_index - 1].get("outputPath"))
    if output_path is None:
        return jsonify({"error": "Clip file is unavailable"}), 404

    return send_file(output_path, mimetype="video/mp4", conditional=True)


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


@app.get("/api/storage")
def get_storage_report():
    with jobs_lock:
        jobs_snapshot = dict(jobs)
    return jsonify(storage_manager.build_storage_report(jobs_snapshot))


@app.post("/api/storage/jobs/<job_id>/cleanup")
def cleanup_job_storage(job_id: str):
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "job"))
    if mode not in ("job", "source_media"):
        return jsonify({"error": "Invalid mode. Use 'job' or 'source_media'."}), 400
    dry_run = bool(payload.get("dryRun", False))
    with jobs_lock:
        jobs_snapshot = dict(jobs)
    try:
        result = storage_manager.delete_job_storage(jobs_snapshot, job_id, mode=mode, dry_run=dry_run)
    except ValueError as err:
        msg = str(err)
        # Active/queued job conflicts are a known safe guard — surface as 409.
        status_code = 409 if "still queued or rendering" in msg else 400
        return jsonify({"error": msg}), status_code
    if not dry_run and mode == "job":
        with jobs_lock:
            jobs.pop(job_id, None)
        _job_state_path(job_id).unlink(missing_ok=True)
    elif not dry_run and mode == "source_media":
        # Keep the job in memory but reflect the deletion so subsequent polls
        # return accurate sourceMediaPresent state without a disk re-read.
        with jobs_lock:
            job_state = jobs.get(job_id)
            if job_state and isinstance(job_state.get("result"), dict):
                job_state["result"]["sourceMediaPresent"] = False
    return jsonify(result)


@app.post("/api/storage/prune")
def prune_storage_endpoint():
    payload = request.get_json(silent=True) or {}
    dry_run = bool(payload.get("dryRun", False))
    prune_temp = bool(payload.get("pruneTemp", False))
    prune_cache = bool(payload.get("pruneCache", False))
    prune_jobs = bool(payload.get("pruneJobs", False))
    prune_failed_jobs = bool(payload.get("pruneFailedJobs", False))
    if not any([prune_temp, prune_cache, prune_jobs, prune_failed_jobs]):
        return jsonify({"error": "Select at least one category to prune."}), 400
    with jobs_lock:
        jobs_snapshot = dict(jobs)
    result = storage_manager.prune_storage(
        jobs_snapshot,
        prune_temp=prune_temp,
        prune_cache=prune_cache,
        prune_jobs=prune_jobs,
        prune_failed_jobs=prune_failed_jobs,
        dry_run=dry_run,
    )
    return jsonify(result)


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
