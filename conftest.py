"""Root conftest — configure pytest for the project."""
from __future__ import annotations

import os
import sys

# Ensure project root is on sys.path so `import app.*` works in CI
sys.path.insert(0, os.path.dirname(__file__))
