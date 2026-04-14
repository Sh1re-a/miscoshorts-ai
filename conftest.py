"""Root conftest — configure pytest for the project."""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
from pathlib import Path

_TEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="miscoshorts-pytest-"))
os.environ.setdefault("MISCOSHORTS_INTERNAL_DIR", str(_TEST_RUNTIME_ROOT / "internal"))
os.environ.setdefault("MISCOSHORTS_OUTPUTS_DIR", str(_TEST_RUNTIME_ROOT / "outputs"))
os.environ.setdefault("MISCOSHORTS_LOGS_DIR", str(_TEST_RUNTIME_ROOT / "logs"))

# Ensure project root is on sys.path so `import app.*` works in CI
sys.path.insert(0, os.path.dirname(__file__))


@atexit.register
def _cleanup_test_runtime_root() -> None:
    try:
        import shutil

        shutil.rmtree(_TEST_RUNTIME_ROOT, ignore_errors=True)
    except Exception:
        pass
