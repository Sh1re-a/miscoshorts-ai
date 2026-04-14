from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.paths import DOCTOR_REPORT_PATH, ENV_FILE, INTERNAL_DIR, LOGS_DIR, MODEL_CACHE_DIR, OUTPUT_CACHE_DIR, OUTPUT_LOCKS_DIR, OUTPUTS_DIR, OUTPUT_TEMP_DIR, PROJECT_ROOT, RUNTIME_DIR, SETUP_DIR

_ENV_LOADED = False
_CONFIGURED_LOGGERS: set[str] = set()
_BACKEND_SIGNATURE_FILES = (
    "app/doctor.py",
    "app/server.py",
    "app/shorts_service.py",
    "app/runtime_recovery.py",
    "app/run_report.py",
    "app/subtitles.py",
    "app/source_pipeline.py",
    "app/render_session.py",
    "app/video_render.py",
    "app/transcription.py",
    "app/gemini_analyzer.py",
)


def load_local_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    _ENV_LOADED = True


def is_debug_enabled() -> bool:
    load_local_env()
    return os.getenv("MISCOSHORTS_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}


def ensure_runtime_dirs() -> None:
    for directory in (INTERNAL_DIR, RUNTIME_DIR, LOGS_DIR, SETUP_DIR, OUTPUTS_DIR, OUTPUT_CACHE_DIR, OUTPUT_TEMP_DIR, OUTPUT_LOCKS_DIR, MODEL_CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def get_log_path(name: str) -> Path:
    ensure_runtime_dirs()
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name).strip("_") or "app"
    return LOGS_DIR / f"{safe_name}.log"


def configure_logging(name: str) -> tuple[logging.Logger, Path]:
    ensure_runtime_dirs()
    logger_name = f"miscoshorts.{name}"
    logger = logging.getLogger(logger_name)
    log_path = get_log_path(name)

    if logger_name not in _CONFIGURED_LOGGERS:
        logger.setLevel(logging.DEBUG if is_debug_enabled() else logging.INFO)
        logger.propagate = False
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(file_handler)
        _CONFIGURED_LOGGERS.add(logger_name)

    return logger, log_path


def runtime_summary() -> dict[str, str]:
    ensure_runtime_dirs()
    return {
        "internalDir": str(INTERNAL_DIR),
        "runtimeDir": str(RUNTIME_DIR),
        "logsDir": str(LOGS_DIR),
        "outputsDir": str(OUTPUTS_DIR),
        "cacheDir": str(OUTPUT_CACHE_DIR),
        "tempDir": str(OUTPUT_TEMP_DIR),
        "locksDir": str(OUTPUT_LOCKS_DIR),
        "modelCacheDir": str(MODEL_CACHE_DIR),
        "doctorReportPath": str(DOCTOR_REPORT_PATH),
        "envFile": str(ENV_FILE),
    }


def managed_runtime_python() -> Path | None:
    candidates = (
        RUNTIME_DIR / "venv" / "bin" / "python3",
        RUNTIME_DIR / "venv" / "bin" / "python",
        RUNTIME_DIR / "venv" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def runtime_identity() -> dict[str, str | bool | None]:
    managed_python = managed_runtime_python()
    current_executable = Path(sys.executable)
    managed_runtime_dir = RUNTIME_DIR / "venv"
    current_prefix = Path(sys.prefix)
    current_base_prefix = Path(getattr(sys, "base_prefix", sys.prefix))
    using_managed_runtime = False

    if current_prefix == managed_runtime_dir:
        using_managed_runtime = True
    elif os.getenv("VIRTUAL_ENV"):
        using_managed_runtime = Path(os.getenv("VIRTUAL_ENV", "")).resolve() == managed_runtime_dir.resolve()
    elif managed_python is not None and current_executable == managed_python:
        using_managed_runtime = True

    return {
        "currentExecutable": str(current_executable),
        "managedExecutable": str(managed_python) if managed_python is not None else None,
        "currentPrefix": str(current_prefix),
        "currentBasePrefix": str(current_base_prefix),
        "usingManagedRuntime": using_managed_runtime,
    }


def backend_code_signature() -> str:
    digest = hashlib.sha1()
    for relative_path in _BACKEND_SIGNATURE_FILES:
        file_path = PROJECT_ROOT / relative_path
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(file_path.read_bytes())
        except OSError:
            digest.update(b"missing")
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def pipeline_compat_signature() -> str:
    """Version the reusable local artifacts against code + critical model config."""
    load_local_env()
    digest = hashlib.sha1()
    for value in (
        backend_code_signature(),
        os.getenv("WHISPER_MODEL", "distil-large-v3,large-v3"),
        os.getenv("WHISPER_BACKEND", "auto"),
        os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        os.getenv("SPEAKER_DIARIZATION_MODE", "auto"),
    ):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]
