from __future__ import annotations

import sys
from pathlib import Path

from app.shorts_service import (
    WHISPER_MODEL_CACHE_DIR,
    _get_whisper_model_candidates,
    ensure_dependencies,
    load_whisper_model,
)


def _format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024 * 1024):.1f} GB"
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.0f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes} B"


def _directory_size(path: Path) -> int:
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        return 0


def main() -> int:
    print("Whisper preflight")
    print(f"  Cache directory: {WHISPER_MODEL_CACHE_DIR}")
    print(f"  Requested model order: {', '.join(_get_whisper_model_candidates())}")
    print("  Checking FFmpeg ...")
    ensure_dependencies()
    WHISPER_MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print("  Preparing local transcription backend ...")
    backend, model_name, _model = load_whisper_model()
    cache_size = _directory_size(WHISPER_MODEL_CACHE_DIR)
    print(f"  Ready: {backend} / {model_name}")
    print(f"  Local model cache size: {_format_bytes(cache_size)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Whisper preflight failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
