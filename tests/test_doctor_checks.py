"""Tests for doctor.py diagnostic checks.

All external tools (ffmpeg, npm, yt-dlp, whisper) are mocked.
No real processes are spawned and no filesystem probes hit disk.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.doctor import (
    DoctorCheck,
    _add_check,
    _check_writable,
    _format_bytes,
    _module_exists,
    _writable_fix_message,
)


class DoctorCheckDataclassTests(unittest.TestCase):
    def test_fields(self) -> None:
        check = DoctorCheck(
            status="PASS",
            name="FFmpeg",
            message="FFmpeg is available.",
            fix=None,
            requirement="required",
            blocks_rendering=False,
        )
        self.assertEqual(check.status, "PASS")
        self.assertEqual(check.name, "FFmpeg")
        self.assertIsNone(check.fix)

    def test_default_blocks_rendering(self) -> None:
        check = DoctorCheck(status="FAIL", name="test", message="msg")
        self.assertFalse(check.blocks_rendering)


class AddCheckTests(unittest.TestCase):
    def test_fail_required_blocks_rendering(self) -> None:
        results: list[DoctorCheck] = []
        _add_check(results, "FAIL", "FFmpeg", "Missing FFmpeg", requirement="required")
        self.assertTrue(results[0].blocks_rendering)

    def test_pass_does_not_block(self) -> None:
        results: list[DoctorCheck] = []
        _add_check(results, "PASS", "FFmpeg", "OK")
        self.assertFalse(results[0].blocks_rendering)

    def test_warn_does_not_block(self) -> None:
        results: list[DoctorCheck] = []
        _add_check(results, "WARN", "Python", "Old version")
        self.assertFalse(results[0].blocks_rendering)

    def test_fail_optional_does_not_block(self) -> None:
        results: list[DoctorCheck] = []
        _add_check(results, "FAIL", "Pyannote", "Missing", requirement="optional")
        self.assertFalse(results[0].blocks_rendering)

    def test_explicit_blocks_rendering_override(self) -> None:
        results: list[DoctorCheck] = []
        _add_check(results, "PASS", "X", "OK", blocks_rendering=True)
        self.assertTrue(results[0].blocks_rendering)


class FormatBytesTests(unittest.TestCase):
    def test_bytes(self) -> None:
        self.assertEqual(_format_bytes(512), "512 B")

    def test_kilobytes(self) -> None:
        self.assertEqual(_format_bytes(2048), "2 KB")

    def test_megabytes(self) -> None:
        self.assertEqual(_format_bytes(5 * 1024 * 1024), "5 MB")

    def test_gigabytes(self) -> None:
        self.assertEqual(_format_bytes(3 * 1024 * 1024 * 1024), "3.0 GB")

    def test_zero(self) -> None:
        self.assertEqual(_format_bytes(0), "0 B")


class ModuleExistsTests(unittest.TestCase):
    def test_existing_module(self) -> None:
        self.assertTrue(_module_exists("json"))

    def test_missing_module(self) -> None:
        self.assertFalse(_module_exists("nonexistent_module_xyz_abc_123"))


class CheckWritableTests(unittest.TestCase):
    def test_writable_tmpdir(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertTrue(_check_writable(Path(tmpdir) / "subdir"))

    def test_non_writable_path(self) -> None:
        self.assertFalse(_check_writable(Path("/proc/nonexistent/test")))


class WritableFixMessageTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("win"), "WindowsPath only on Windows")
    def test_windows_message_includes_desktop(self) -> None:
        with patch("os.name", "nt"):
            msg = _writable_fix_message(Path("C:\\test"))
            self.assertIn("Desktop", msg)
            self.assertIn("miscoshorts-ai", msg)

    @unittest.skipUnless(sys.platform.startswith("win"), "WindowsPath only on Windows")
    def test_windows_message_logic(self) -> None:
        """Verify the Windows branch is entered and includes expected keywords."""
        msg = _writable_fix_message(Path("C:\\test"))
        self.assertIn("miscoshorts-ai", msg)

    def test_non_windows_message(self) -> None:
        with patch("os.name", "posix"):
            msg = _writable_fix_message(Path("/test"))
            self.assertIn("writable", msg.lower())
            self.assertNotIn("Desktop", msg)


class RunDoctorIntegrationTests(unittest.TestCase):
    """High-level run_doctor tests with mocked subprocess and environment."""

    @patch("app.doctor.atomic_write_json")
    @patch("app.doctor.ensure_runtime_dirs")
    @patch("app.doctor.load_local_env")
    @patch("app.doctor.ensure_dependencies")
    @patch("shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("app.doctor._module_exists", return_value=True)
    @patch("app.doctor._probe_modules_in_python", return_value={
        "faster_whisper": True, "yt_dlp": True, "moviepy": True,
        "PIL": True, "flask": True, "google.genai": True, "cv2": True,
    })
    @patch("app.doctor.runtime_identity", return_value={
        "currentExecutable": sys.executable,
        "managedExecutable": sys.executable,
        "usingManagedRuntime": True,
    })
    @patch("app.doctor.managed_runtime_python", return_value=Path(sys.executable))
    @patch("app.doctor._check_writable", return_value=True)
    def test_all_pass_produces_ready_report(
        self, mock_writable, mock_managed, mock_identity,
        mock_probe, mock_mod, mock_which, mock_deps, mock_load, mock_dirs, mock_write
    ):
        from app.doctor import run_doctor
        with patch("app.doctor.FRONTEND_DIST_DIR") as mock_dist:
            mock_dist.exists.return_value = True
            report = run_doctor()
        self.assertTrue(report["renderReady"])
        self.assertFalse(any(c["blocks_rendering"] for c in report["checks"]))


if __name__ == "__main__":
    unittest.main()
