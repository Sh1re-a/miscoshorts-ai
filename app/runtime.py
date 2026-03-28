from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from app.paths import DOCTOR_REPORT_PATH, ENV_FILE, INTERNAL_DIR, LOGS_DIR, MODEL_CACHE_DIR, OUTPUT_CACHE_DIR, OUTPUT_LOCKS_DIR, OUTPUTS_DIR, OUTPUT_TEMP_DIR, RUNTIME_DIR, SETUP_DIR

_ENV_LOADED = False
_CONFIGURED_LOGGERS: set[str] = set()


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
