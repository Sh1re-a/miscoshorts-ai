from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from app.paths import OUTPUT_JOBS_DIR, OUTPUT_LOCKS_DIR, OUTPUT_TEMP_DIR
from app.runtime import configure_logging
from app.storage import atomic_write_json

logger, _LOG_PATH = configure_logging("render-session")

ProgressCallback = Callable[[str, str], None]
PIPELINE_FINGERPRINT_VERSION = "v3"
LOCK_WAIT_TIMEOUT_SECONDS = max(60, int(os.getenv("FINGERPRINT_LOCK_WAIT_TIMEOUT_SECONDS", "1800")))
LOCK_STALE_SECONDS = max(300, int(os.getenv("FINGERPRINT_LOCK_STALE_SECONDS", "900")))


def _emit(progress_callback: ProgressCallback | None, stage: str, message: str) -> None:
    if progress_callback is not None:
        progress_callback(stage, message)


def job_fingerprint(
    *,
    video_url: str,
    output_filename: str,
    clip_count: int,
    render_profile: str,
    subtitle_style: dict,
) -> str:
    payload = {
        "version": PIPELINE_FINGERPRINT_VERSION,
        "videoUrl": video_url,
        "outputFilename": output_filename,
        "clipCount": clip_count,
        "renderProfile": render_profile,
        "subtitleStyle": subtitle_style,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def result_manifest_path(output_dir: Path) -> Path:
    return output_dir / "meta" / "result.json"


def result_paths_exist(result: dict) -> bool:
    transcript_path = result.get("transcriptPath")
    if transcript_path and not Path(transcript_path).exists():
        return False
    for clip in result.get("clips") or []:
        output_path = clip.get("outputPath")
        if not output_path or not Path(output_path).exists():
            return False
    output_path = result.get("outputPath")
    if output_path and not Path(output_path).exists():
        return False
    return True


def load_existing_result(output_dir: Path, fingerprint: str) -> dict | None:
    manifest_path = result_manifest_path(output_dir)
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("jobFingerprint") != fingerprint:
        return None
    if not result_paths_exist(payload):
        return None
    payload["lastUsedAt"] = time.time()
    atomic_write_json(manifest_path, payload)
    return payload


def write_result_manifest(output_dir: Path, payload: dict) -> None:
    atomic_write_json(result_manifest_path(output_dir), payload)


@dataclass
class RenderWorkspace:
    job_id: str
    fingerprint: str
    workspace_dir: Path
    final_output_dir: Path
    promoted: bool = False

    @classmethod
    def create(cls, *, fingerprint: str, job_id: str, base_dir: str | Path = OUTPUT_JOBS_DIR) -> "RenderWorkspace":
        final_output_dir = Path(base_dir) / fingerprint
        workspace_dir = OUTPUT_TEMP_DIR / f"{fingerprint}-{job_id}-{uuid.uuid4().hex[:6]}"
        workspace = cls(
            job_id=job_id,
            fingerprint=fingerprint,
            workspace_dir=workspace_dir,
            final_output_dir=final_output_dir,
        )
        workspace._bind(workspace_dir)
        for directory in (
            workspace.workspace_dir,
            workspace.source_dir,
            workspace.audio_dir,
            workspace.clips_dir,
            workspace.meta_dir,
            workspace.diagnostics_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return workspace

    def _bind(self, root: Path) -> None:
        self.root_dir = root
        self.source_dir = root / "source"
        self.audio_dir = root / "audio"
        self.clips_dir = root / "clips"
        self.meta_dir = root / "meta"
        self.diagnostics_dir = root / "diagnostics"

    def promote(self) -> Path:
        self.final_output_dir.parent.mkdir(parents=True, exist_ok=True)
        backup_dir: Path | None = None
        if self.final_output_dir.exists():
            backup_dir = self.final_output_dir.parent / f".{self.final_output_dir.name}.backup-{uuid.uuid4().hex[:6]}"
            shutil.move(str(self.final_output_dir), str(backup_dir))
        try:
            shutil.move(str(self.workspace_dir), str(self.final_output_dir))
        except Exception:
            if self.final_output_dir.exists():
                shutil.rmtree(self.final_output_dir, ignore_errors=True)
            if backup_dir is not None and backup_dir.exists():
                shutil.move(str(backup_dir), str(self.final_output_dir))
            raise
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        self.promoted = True
        self._bind(self.final_output_dir)
        return self.final_output_dir

    def cleanup(self) -> None:
        target = self.workspace_dir if not self.promoted else None
        if target is not None and target.exists():
            shutil.rmtree(target, ignore_errors=True)


def _lock_path(fingerprint: str) -> Path:
    OUTPUT_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_LOCKS_DIR / f"{fingerprint}.lock"


def _safe_unlink_lock(lock_path: Path) -> bool:
    """Delete a lock file, tolerating Windows PermissionError (WinError 32).

    On Windows a lock file may still be held open by a dead-but-not-yet-reaped
    process.  Crashing because of that would bring down the /api/runtime
    endpoint which is polled every 2 seconds.
    """
    try:
        lock_path.unlink(missing_ok=True)
        return True
    except PermissionError:
        logger.warning("Cannot delete lock %s (file held by another process) — will retry later.", lock_path)
        return False
    except OSError as exc:
        logger.warning("Cannot delete lock %s: %s", lock_path, exc)
        return False


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists but we lack permission to signal it.
    except OSError:
        return False
    return True


def _read_lock_payload(lock_path: Path) -> dict | None:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def describe_fingerprint_lock(lock_path: Path) -> dict[str, object]:
    payload = _read_lock_payload(lock_path)
    try:
        age_seconds = max(0.0, time.time() - lock_path.stat().st_mtime)
    except OSError:
        age_seconds = 0.0
    pid_raw = payload.get("pid") if isinstance(payload, dict) else None
    try:
        pid = int(pid_raw) if pid_raw is not None else None
    except (TypeError, ValueError):
        pid = None
    return {
        "path": str(lock_path),
        "fingerprint": (payload.get("fingerprint") if isinstance(payload, dict) else None) or lock_path.stem,
        "jobId": payload.get("jobId") if isinstance(payload, dict) else None,
        "pid": pid,
        "createdAt": payload.get("createdAt") if isinstance(payload, dict) else None,
        "ownerToken": payload.get("ownerToken") if isinstance(payload, dict) else None,
        "alive": _pid_is_alive(pid),
        "ageSeconds": round(age_seconds, 1),
        "payloadValid": isinstance(payload, dict),
    }


def list_active_fingerprint_locks() -> list[dict[str, object]]:
    if not OUTPUT_LOCKS_DIR.exists():
        return []
    return [describe_fingerprint_lock(path) for path in sorted(OUTPUT_LOCKS_DIR.glob("*.lock"))]


def cleanup_stale_fingerprint_locks() -> dict[str, list[dict[str, object]]]:
    removed_locks: list[dict[str, object]] = []
    active_locks: list[dict[str, object]] = []
    if not OUTPUT_LOCKS_DIR.exists():
        return {"removedLocks": removed_locks, "activeLocks": active_locks}

    for lock_path in sorted(OUTPUT_LOCKS_DIR.glob("*.lock")):
        details = describe_fingerprint_lock(lock_path)
        should_remove = False
        reason = None
        if not details["payloadValid"] and float(details["ageSeconds"]) >= LOCK_STALE_SECONDS:
            should_remove = True
            reason = "invalid-payload"
        elif not details["alive"]:
            should_remove = True
            reason = "dead-owner"

        if should_remove:
            if _safe_unlink_lock(lock_path):
                details["reason"] = reason
                removed_locks.append(details)
                logger.warning(
                    "Removed orphan fingerprint lock %s (fingerprint=%s, jobId=%s, pid=%s, reason=%s)",
                    details["path"],
                    details["fingerprint"],
                    details["jobId"],
                    details["pid"],
                    reason,
                )
            else:
                # Could not delete (Windows file lock) — treat as still active.
                active_locks.append(details)
        else:
            active_locks.append(details)

    return {"removedLocks": removed_locks, "activeLocks": active_locks}


@contextlib.contextmanager
def acquire_fingerprint_lock(
    fingerprint: str,
    *,
    job_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Iterator[None]:
    lock_path = _lock_path(fingerprint)
    wait_started_at = time.time()
    fd: int | None = None
    wait_message_sent = False
    owner_token = uuid.uuid4().hex

    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(
                fd,
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "jobId": job_id,
                        "pid": os.getpid(),
                        "createdAt": time.time(),
                        "ownerToken": owner_token,
                    }
                ).encode("utf-8"),
            )
        except FileExistsError:
            details = describe_fingerprint_lock(lock_path)
            if not details["alive"]:
                logger.warning(
                    "Removing orphan fingerprint lock %s (fingerprint=%s, jobId=%s, pid=%s)",
                    lock_path,
                    details["fingerprint"],
                    details["jobId"],
                    details["pid"],
                )
                if _safe_unlink_lock(lock_path):
                    continue
                # Windows: file still locked — fall through to wait.
            if not details["payloadValid"] and float(details["ageSeconds"]) > LOCK_STALE_SECONDS:
                logger.warning("Removing stale unreadable fingerprint lock %s after %.1fs", lock_path, float(details["ageSeconds"]))
                if _safe_unlink_lock(lock_path):
                    continue
                # Windows: file still locked — fall through to wait.
            if not wait_message_sent:
                owner_job_id = details["jobId"] or "unknown"
                owner_pid = details["pid"] if details["pid"] is not None else "unknown"
                _emit(
                    progress_callback,
                    "queued",
                    f"LOCK_WAIT | fingerprint={fingerprint} | ownerJobId={owner_job_id} | ownerPid={owner_pid} | Another identical render is already running. Waiting so this request can safely reuse the finished result instead of generating duplicate clips.",
                )
                wait_message_sent = True
            if time.time() - wait_started_at > LOCK_WAIT_TIMEOUT_SECONDS:
                raise TimeoutError("Timed out while waiting for another identical render to finish.")
            time.sleep(1.0)

    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        current_details = describe_fingerprint_lock(lock_path) if lock_path.exists() else None
        if current_details and current_details.get("ownerToken") != owner_token:
            logger.warning(
                "Lock release skipped because ownership changed for %s (expected token=%s, current token=%s)",
                lock_path,
                owner_token,
                current_details.get("ownerToken"),
            )
        else:
            _safe_unlink_lock(lock_path)
