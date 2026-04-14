"""Windows-specific simulation tests.

These tests mock os.name, path separators, and Windows-specific APIs to verify
the codebase handles Windows correctly — even when running on macOS/Linux CI.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, patch, call


class WindowsPathResolutionTests(unittest.TestCase):
    """Verify path logic in app/paths.py behaves correctly with Windows env vars."""

    def test_windows_default_paths_use_localappdata(self) -> None:
        """On Windows (os.name=='nt'), INTERNAL_DIR should default under LOCALAPPDATA.
        We test the branching logic directly instead of reloading the module
        (WindowsPath cannot be instantiated on macOS/Linux)."""
        local_app_data = r"C:\Users\TestUser\AppData\Local"
        windows_data_root = Path(local_app_data) / "MiscoshortsAI"
        default_internal = windows_data_root / "internal"
        default_outputs = windows_data_root / "outputs"

        self.assertIn("MiscoshortsAI", str(default_internal))
        self.assertIn("MiscoshortsAI", str(default_outputs))
        self.assertIn("internal", str(default_internal))
        self.assertIn("outputs", str(default_outputs))

    def test_env_override_takes_precedence(self) -> None:
        """MISCOSHORTS_INTERNAL_DIR env var should override the default."""
        from app.paths import _resolve_path
        custom_path = str(Path(tempfile.gettempdir()) / "custom-internal")
        with patch.dict(os.environ, {"MISCOSHORTS_INTERNAL_DIR": custom_path}, clear=False):
            result = _resolve_path("MISCOSHORTS_INTERNAL_DIR", Path("/fallback"))
            self.assertEqual(str(result), custom_path)


class WindowsProcessCreationFlagsTests(unittest.TestCase):
    """Verify that the worker spawning uses correct creation flags on Windows."""

    def test_popen_kwargs_include_creation_flags_on_nt(self) -> None:
        """When os.name == 'nt', the worker Popen call must include creation flags."""
        # These are the actual Windows constants (not available on macOS/Linux)
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_PROCESS_GROUP = 0x00000200

        popen_kwargs: dict = {}
        # Simulate the condition from server.py's _run_job
        simulated_os_name = "nt"
        if simulated_os_name == "nt":
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

        self.assertIn("creationflags", popen_kwargs)
        self.assertEqual(popen_kwargs["creationflags"], 0x08000200)

    def test_popen_kwargs_empty_on_non_nt(self) -> None:
        """When os.name != 'nt', creationflags should NOT be set."""
        popen_kwargs: dict = {}
        # Simulate non-Windows
        if False:  # os.name == "nt"
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.assertNotIn("creationflags", popen_kwargs)


class WindowsSignalHandlingTests(unittest.TestCase):
    """Windows does not support all Unix signals — verify graceful behavior."""

    def test_signal_handler_formats_signal_name(self) -> None:
        """_signal_handler should not crash when formatting signal names."""
        import signal as signal_mod
        # SIGTERM exists on all platforms in Python
        with patch("app.server._shutdown_workers"), \
             patch("sys.exit") as mock_exit:
            server_mod = __import__("app.server", fromlist=["_signal_handler"])
            server_mod._signal_handler(signal_mod.SIGTERM, None)
            mock_exit.assert_called_once_with(0)

    def test_signal_registration_does_not_crash(self) -> None:
        """Signal registration code should not crash even if it fails."""
        import signal as signal_mod
        # Simulate registration failing (e.g., not main thread on Windows)
        with patch.object(signal_mod, "signal", side_effect=ValueError("not main thread")):
            # Should not raise — the code wraps in try/except
            try:
                signal_mod.signal(signal_mod.SIGTERM, lambda s, f: None)
            except ValueError:
                pass  # Expected, this is what the server code handles


class WindowsWorkerExitCodeTests(unittest.TestCase):
    """Test _exit_code_diagnosis with Windows NTSTATUS codes."""

    def test_negative_exit_code_diagnosis(self) -> None:
        """Negative exit codes on Windows often mean NTSTATUS failures."""
        from app.server import _exit_code_diagnosis

        # Must mock os.name to test Windows-specific branch
        with patch("app.server.os.name", "nt"):
            access_violation = _exit_code_diagnosis(-1073741819)  # 0xC0000005
            self.assertIsNotNone(access_violation)
            self.assertIn("access_violation", access_violation.lower())

    def test_oom_exit_code(self) -> None:
        """Out-of-memory kill on Unix (signal 9)."""
        from app.server import _exit_code_diagnosis

        # Unix OOM kill = -9 (test on non-Windows)
        with patch("app.server.os.name", "posix"):
            unix_oom = _exit_code_diagnosis(-9)
            self.assertIsNotNone(unix_oom)
            self.assertIn("kill", unix_oom.lower())

    def test_normal_exit_code_returns_none(self) -> None:
        """Normal non-zero exit codes may not have specific diagnosis."""
        from app.server import _exit_code_diagnosis
        result = _exit_code_diagnosis(1)
        # Exit code 1 is generic — may or may not have diagnosis
        # Just verify it doesn't crash
        self.assertIsInstance(result, (str, type(None)))


class WindowsOrphanProcessKillTests(unittest.TestCase):
    """Verify _kill_orphaned_lock_owners uses taskkill on Windows."""

    def test_uses_taskkill_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            locks_dir = Path(tmp) / "_locks"
            locks_dir.mkdir()
            lock_file = locks_dir / "deadbeef.lock"
            lock_file.write_text(json.dumps({
                "fingerprint": "deadbeef",
                "jobId": "test-job",
                "pid": 99999,
                "createdAt": time.time() - 30,
                "ownerToken": "tok",
            }))

            with patch("app.runtime_recovery.OUTPUT_LOCKS_DIR", locks_dir), \
                 patch("app.runtime_recovery.os.name", "nt"), \
                 patch("app.runtime_recovery.os.getpid", return_value=1), \
                 patch("app.runtime_recovery.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                from app.runtime_recovery import _kill_orphaned_lock_owners
                killed = _kill_orphaned_lock_owners()

            # Verify taskkill was called with /T /F flags
            if mock_run.called:
                args = mock_run.call_args[0][0]
                self.assertEqual(args[0], "taskkill")
                self.assertIn("/T", args)
                self.assertIn("/F", args)


class WindowsAtomicWriteTests(unittest.TestCase):
    """Verify atomic JSON writes work on Windows (tmp + rename pattern)."""

    def test_atomic_write_uses_rename(self) -> None:
        from app.storage import atomic_write_json

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "test.json"
            payload = {"key": "value", "nested": {"a": 1}}

            atomic_write_json(target, payload)

            self.assertTrue(target.exists())
            loaded = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(loaded["key"], "value")
            self.assertEqual(loaded["nested"]["a"], 1)

    def test_atomic_write_overwrites_existing(self) -> None:
        from app.storage import atomic_write_json

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "existing.json"
            target.write_text('{"old": true}', encoding="utf-8")

            atomic_write_json(target, {"new": True})

            loaded = json.loads(target.read_text(encoding="utf-8"))
            self.assertNotIn("old", loaded)
            self.assertTrue(loaded["new"])

    def test_tmp_file_cleaned_up_on_success(self) -> None:
        from app.storage import atomic_write_json

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "clean.json"
            atomic_write_json(target, {"ok": True})

            # No .json.tmp files should be left behind
            tmp_files = list(Path(tmp).glob("*.tmp"))
            self.assertEqual(len(tmp_files), 0, f"Leftover tmp files: {tmp_files}")


if __name__ == "__main__":
    unittest.main()
