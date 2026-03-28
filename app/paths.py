from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"


def _resolve_path(env_name: str, default: Path) -> Path:
    raw_value = (os.getenv(env_name) or "").strip()
    return Path(raw_value).expanduser() if raw_value else default


if os.name == "nt":
    _windows_data_root = Path(os.getenv("LOCALAPPDATA") or str(PROJECT_ROOT)) / "MiscoshortsAI"
    _default_internal_dir = _windows_data_root / "internal"
    _default_outputs_dir = _windows_data_root / "outputs"
else:
    _default_internal_dir = PROJECT_ROOT / ".miscoshorts"
    _default_outputs_dir = PROJECT_ROOT / "outputs"


INTERNAL_DIR = _resolve_path("MISCOSHORTS_INTERNAL_DIR", _default_internal_dir)
RUNTIME_DIR = INTERNAL_DIR / "runtime"
LOGS_DIR = INTERNAL_DIR / "logs"
SETUP_DIR = INTERNAL_DIR / "setup"
OUTPUTS_DIR = _resolve_path("MISCOSHORTS_OUTPUTS_DIR", _default_outputs_dir)
OUTPUT_JOBS_DIR = OUTPUTS_DIR / "jobs"
OUTPUT_CACHE_DIR = OUTPUTS_DIR / "cache"
OUTPUT_TEMP_DIR = OUTPUTS_DIR / "temp"
MODEL_CACHE_DIR = RUNTIME_DIR / "model-cache"
DOCTOR_REPORT_PATH = SETUP_DIR / "doctor-report.json"
ENV_FILE = PROJECT_ROOT / ".env"
