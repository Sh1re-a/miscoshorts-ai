from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from app.paths import OUTPUTS_DIR, OUTPUT_LOCKS_DIR, OUTPUT_TEMP_DIR, PROJECT_ROOT
from app.render_session import force_remove_all_locks
from app.runtime import configure_logging
from app.storage import atomic_write_json

logger, _LOG_PATH = configure_logging("runtime-recovery")

JOB_STATE_DIR = OUTPUTS_DIR / "_job_state"
RECOVERABLE_JOB_STATUSES = {"queued", "validating", "downloading", "transcribing", "analyzing", "rendering"}
STARTUP_TEMP_STALE_SECONDS = max(3600, int(os.getenv("STARTUP_TEMP_STALE_SECONDS", str(12 * 3600))))


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


def _extract_temp_workspace_job_id(path: Path) -> str | None:
    if not path.is_dir():
        return None

    params_path = path / "params.json"
    if params_path.exists():
        try:
            payload = json.loads(params_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            job_id = payload.get("jobId")
            if isinstance(job_id, str) and job_id.strip():
                return job_id.strip()

    stem = path.name
    if stem.startswith("render-"):
        return None

    parts = stem.split("-")
    if len(parts) >= 3:
        return parts[1]
    return None


def _should_remove_temp_workspace(
    path: Path,
    *,
    recovered_job_ids: set[str],
    cleared_lock_fingerprints: set[str],
) -> bool:
    try:
        age_seconds = max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        age_seconds = 0.0

    if age_seconds >= STARTUP_TEMP_STALE_SECONDS:
        return True

    if not path.is_dir():
        return age_seconds >= STARTUP_TEMP_STALE_SECONDS

    workspace_job_id = _extract_temp_workspace_job_id(path)
    if workspace_job_id and workspace_job_id in recovered_job_ids:
        return True

    workspace_name = path.name
    if any(workspace_name.startswith(f"{fingerprint}-") for fingerprint in cleared_lock_fingerprints):
        return True

    return False


def cleanup_temp_workspaces(
    *,
    recovered_job_ids: set[str] | None = None,
    cleared_lock_fingerprints: set[str] | None = None,
) -> dict[str, object]:
    cleared_paths: list[str] = []
    recovered_job_ids = recovered_job_ids or set()
    cleared_lock_fingerprints = cleared_lock_fingerprints or set()

    if not OUTPUT_TEMP_DIR.exists():
        return {"clearedTempWorkspacePaths": cleared_paths}

    for child in sorted(OUTPUT_TEMP_DIR.iterdir()):
        if not child.exists():
            continue
        if not _should_remove_temp_workspace(
            child,
            recovered_job_ids=recovered_job_ids,
            cleared_lock_fingerprints=cleared_lock_fingerprints,
        ):
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
        cleared_paths.append(str(child))

    if cleared_paths:
        logger.warning("Removed %s stale temp workspace item(s) during startup recovery.", len(cleared_paths))

    return {"clearedTempWorkspacePaths": cleared_paths}


def _pid_matches_miscoshorts_worker(pid: int, expected_project_root: str | None) -> bool:
    expected_root = (expected_project_root or str(PROJECT_ROOT)).strip()
    if not expected_root:
        return False

    try:
        if os.name == "nt":
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\").CommandLine",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            command_line = completed.stdout.strip()
        else:
            completed = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            command_line = completed.stdout.strip()
    except Exception:
        return False

    if not command_line:
        return False

    lowered = command_line.lower()
    return "app.render_worker" in lowered and expected_root.lower() in lowered


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
        expected_project_root = payload.get("projectRoot") if isinstance(payload, dict) else None
        if not _pid_matches_miscoshorts_worker(pid, str(expected_project_root or "")):
            logger.warning(
                "Skipping orphan kill for pid=%s from lock %s because the process could not be verified as a Miscoshorts render worker.",
                pid,
                lock_path.name,
            )
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
    temp_report = cleanup_temp_workspaces(
        recovered_job_ids=set(jobs_report["recoveredJobIds"]),
        cleared_lock_fingerprints={
            str(lock.get("fingerprint"))
            for lock in lock_report["removedLocks"]
            if lock.get("fingerprint")
        },
    )
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
