from __future__ import annotations

import contextlib
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
LOCK_WAIT_TIMEOUT_SECONDS = max(60, int(os.getenv("FINGERPRINT_LOCK_WAIT_TIMEOUT_SECONDS", "7200")))
LOCK_STALE_SECONDS = max(300, int(os.getenv("FINGERPRINT_LOCK_STALE_SECONDS", "7200")))


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
        if self.final_output_dir.exists():
            shutil.rmtree(self.final_output_dir, ignore_errors=True)
        shutil.move(str(self.workspace_dir), str(self.final_output_dir))
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


@contextlib.contextmanager
def acquire_fingerprint_lock(
    fingerprint: str,
    *,
    progress_callback: ProgressCallback | None = None,
) -> Iterator[None]:
    lock_path = _lock_path(fingerprint)
    wait_started_at = time.time()
    fd: int | None = None
    wait_message_sent = False

    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"fingerprint": fingerprint, "pid": os.getpid(), "createdAt": time.time()}).encode("utf-8"))
        except FileExistsError:
            try:
                age_seconds = max(0.0, time.time() - lock_path.stat().st_mtime)
            except OSError:
                age_seconds = 0.0
            if age_seconds > LOCK_STALE_SECONDS:
                logger.warning("Removing stale fingerprint lock %s after %.1fs", lock_path, age_seconds)
                lock_path.unlink(missing_ok=True)
                continue
            if not wait_message_sent:
                _emit(
                    progress_callback,
                    "queued",
                    "Another identical render is already running. Waiting so this request can safely reuse the finished result instead of generating duplicate clips...",
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
        lock_path.unlink(missing_ok=True)
