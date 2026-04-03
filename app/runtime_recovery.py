from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from app.paths import OUTPUTS_DIR, OUTPUT_LOCKS_DIR, OUTPUT_TEMP_DIR
from app.render_session import force_remove_all_locks
from app.runtime import configure_logging
from app.storage import atomic_write_json

logger, _LOG_PATH = configure_logging("runtime-recovery")

JOB_STATE_DIR = OUTPUTS_DIR / "_job_state"
RECOVERABLE_JOB_STATUSES = {"queued", "validating", "downloading", "transcribing", "analyzing", "rendering"}


def recover_interrupted_job_states() -> dict[str, object]:
    recovered_at = time.time()
    recovered_job_ids: list[str] = []

    if not JOB_STATE_DIR.exists():
        return {"recoveredAt": recovered_at, "recoveredJobIds": recovered_job_ids}

    for path in sorted(JOB_STATE_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Skipping unreadable job state file during recovery: %s", path)
            continue

        if payload.get("status") not in RECOVERABLE_JOB_STATUSES:
            continue

        previous_status = str(payload.get("status") or "unknown")
        payload["status"] = "failed"
        payload["queuePosition"] = 0
        payload["error"] = "This older job was interrupted by an app restart before it finished."
        payload["errorHelp"] = "Start the render again. The recovered job state was cleared so the queue can continue."
        payload["errorCategory"] = "recovered"
        payload["message"] = "Recovered interrupted job state after restart."
        payload["updatedAt"] = recovered_at
        payload["etaSeconds"] = None
        payload["recoveredByRestart"] = True
        payload["recoveredFromStatus"] = previous_status
        logs = list(payload.get("logs") or [])
        logs.append(
            {
                "time": recovered_at,
                "stage": "failed",
                "message": f"Runtime recovery marked the interrupted {previous_status} job as failed after restart.",
            }
        )
        payload["logs"] = logs[-120:]
        atomic_write_json(path, payload)
        recovered_job_ids.append(path.stem)

    if recovered_job_ids:
        logger.warning(
            "Recovered %s interrupted job(s) from disk after restart: %s",
            len(recovered_job_ids),
            ", ".join(recovered_job_ids),
        )

    return {"recoveredAt": recovered_at, "recoveredJobIds": recovered_job_ids}


def cleanup_temp_workspaces() -> dict[str, object]:
    cleared_paths: list[str] = []

    if not OUTPUT_TEMP_DIR.exists():
        return {"clearedTempWorkspacePaths": cleared_paths}

    for child in sorted(OUTPUT_TEMP_DIR.iterdir()):
        if not child.exists():
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
        cleared_paths.append(str(child))

    if cleared_paths:
        logger.warning("Removed %s stale temp workspace item(s) during startup recovery.", len(cleared_paths))

    return {"clearedTempWorkspacePaths": cleared_paths}


def _kill_orphaned_lock_owners() -> list[int]:
    """Kill processes listed in lock files.  At startup these are always orphans."""
    killed_pids: list[int] = []
    if not OUTPUT_LOCKS_DIR.exists():
        return killed_pids

    for lock_path in OUTPUT_LOCKS_DIR.glob("*.lock"):
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = payload.get("pid") if isinstance(payload, dict) else None
        if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
            continue
        try:
            if os.name == "nt":
                # /T = kill child tree, /F = force
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=10,
                )
            else:
                os.kill(pid, 9)
            killed_pids.append(pid)
            logger.warning("Killed orphaned render worker pid=%d from lock %s", pid, lock_path.name)
        except (ProcessLookupError, PermissionError):
            pass  # Already dead or not ours — fine.
        except Exception as exc:
            logger.debug("Could not kill pid=%d: %s", pid, exc)

    if killed_pids:
        # Give Windows a moment to release file handles after killing processes.
        time.sleep(0.5)

    return killed_pids


def recover_runtime_state() -> dict[str, object]:
    # At startup nothing is running — kill any orphaned render workers from a
    # previous session, then force-remove ALL lock files unconditionally.
    killed_pids = _kill_orphaned_lock_owners()
    lock_report = force_remove_all_locks()
    jobs_report = recover_interrupted_job_states()
    temp_report = cleanup_temp_workspaces()
    summary = {
        "recoveredAt": jobs_report["recoveredAt"],
        "recoveredJobIds": jobs_report["recoveredJobIds"],
        "clearedLocks": lock_report["removedLocks"],
        "activeLocks": lock_report["activeLocks"],
        "killedOrphanPids": killed_pids,
        "clearedTempWorkspacePaths": temp_report["clearedTempWorkspacePaths"],
    }
    if summary["recoveredJobIds"] or summary["clearedLocks"] or summary["clearedTempWorkspacePaths"] or killed_pids:
        logger.warning("Startup runtime recovery summary: %s", summary)
    else:
        logger.info("Startup runtime recovery found no interrupted jobs, orphan locks, or stale temp workspaces.")
    return summary
