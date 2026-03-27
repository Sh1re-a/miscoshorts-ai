from __future__ import annotations

from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUT_JOBS_DIR = OUTPUTS_DIR / "jobs"
OUTPUT_CACHE_DIR = OUTPUTS_DIR / "cache"
ENV_FILE = PROJECT_ROOT / ".env"
