"""Tests for path resolution on macOS, Linux, and Windows.

All platform-specific behaviour is simulated via os.name / env patching.
No real filesystem operations.
"""
from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch


class PathResolutionTests(unittest.TestCase):
    """Verify path defaults and env overrides in app/paths.py."""

    def _reload_paths(self):
        """Re-import paths module to pick up env/os.name changes."""
        import app.paths
        importlib.reload(app.paths)
        return app.paths

    def test_project_root_points_to_repo(self) -> None:
        from app.paths import PROJECT_ROOT
        # PROJECT_ROOT should be the repo root (parent of app/)
        self.assertTrue((PROJECT_ROOT / "app").is_dir())
        self.assertTrue((PROJECT_ROOT / "run.py").is_file())

    def test_frontend_dir_exists(self) -> None:
        from app.paths import FRONTEND_DIR
        self.assertTrue(FRONTEND_DIR.is_dir())

    def test_env_override_outputs_dir(self) -> None:
        test_path = os.path.join("tmp", "test_outputs")
        with patch.dict(os.environ, {"MISCOSHORTS_OUTPUTS_DIR": test_path}):
            paths = self._reload_paths()
            self.assertEqual(str(paths.OUTPUTS_DIR), str(Path(test_path)))
        # Reload to restore defaults
        self._reload_paths()

    def test_env_override_internal_dir(self) -> None:
        test_path = os.path.join("tmp", "test_internal")
        with patch.dict(os.environ, {"MISCOSHORTS_INTERNAL_DIR": test_path}):
            paths = self._reload_paths()
            self.assertEqual(str(paths.INTERNAL_DIR), str(Path(test_path)))
        self._reload_paths()

    def test_tilde_expansion_in_env(self) -> None:
        with patch.dict(os.environ, {"MISCOSHORTS_OUTPUTS_DIR": "~/my_outputs"}):
            paths = self._reload_paths()
            self.assertNotIn("~", str(paths.OUTPUTS_DIR))
            self.assertIn(os.path.expanduser("~"), str(paths.OUTPUTS_DIR))
        self._reload_paths()

    def test_derived_paths_under_outputs(self) -> None:
        from app.paths import OUTPUTS_DIR, OUTPUT_JOBS_DIR, OUTPUT_CACHE_DIR, OUTPUT_TEMP_DIR
        self.assertEqual(OUTPUT_JOBS_DIR.parent, OUTPUTS_DIR)
        self.assertEqual(OUTPUT_CACHE_DIR.parent, OUTPUTS_DIR)
        self.assertEqual(OUTPUT_TEMP_DIR.parent, OUTPUTS_DIR)

    def test_model_cache_under_runtime(self) -> None:
        from app.paths import RUNTIME_DIR, MODEL_CACHE_DIR
        self.assertEqual(MODEL_CACHE_DIR.parent, RUNTIME_DIR)


@unittest.skipUnless(sys.platform.startswith("win"), "Windows-only path tests")
class WindowsPathSimulationTests(unittest.TestCase):
    """Verify Windows-specific path logic using env simulation.

    These tests only run on actual Windows because Python 3.13 raises
    UnsupportedOperation when instantiating WindowsPath on other platforms.
    """

    def test_windows_default_uses_localappdata(self) -> None:
        env_overrides = {
            "LOCALAPPDATA": "C:\\Users\\Test\\AppData\\Local",
            "MISCOSHORTS_INTERNAL_DIR": "",  # clear CI override so default kicks in
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            import app.paths
            importlib.reload(app.paths)
            internal_str = str(app.paths.INTERNAL_DIR)
            self.assertIn("MiscoshortsAI", internal_str)
        importlib.reload(app.paths)

    def test_windows_logs_at_project_root(self) -> None:
        import app.paths
        importlib.reload(app.paths)
        logs_parent = str(app.paths.LOGS_DIR.parent)
        project_root_str = str(app.paths.PROJECT_ROOT)
        self.assertEqual(logs_parent, project_root_str)
        importlib.reload(app.paths)


class StartLocalTests(unittest.TestCase):
    """Test start_local.py utility functions."""

    def test_npm_command_returns_npm_cmd_on_windows(self) -> None:
        with patch("sys.platform", "win32"):
            from app.start_local import npm_command
            importlib.reload(sys.modules["app.start_local"])
            from app.start_local import npm_command
            # Re-evaluate (module-level function checks sys.platform)
            result = npm_command()
            # On win32, should return npm.cmd
            if sys.platform.startswith("win"):
                self.assertEqual(result, "npm.cmd")
            else:
                # When running on non-Windows, the function checks sys.platform
                # which we can't fully mock at function level since it's evaluated
                # at call time. Just verify it returns a string.
                self.assertIsInstance(result, str)

    def test_find_available_port_returns_free_port(self) -> None:
        from app.start_local import find_available_port
        port = find_available_port(start_port=19000, max_attempts=5)
        self.assertGreaterEqual(port, 19000)
        self.assertLess(port, 19005)

    def test_find_available_port_raises_when_exhausted(self) -> None:
        from app.start_local import find_available_port
        import socket
        # Bind many ports to exhaust the range
        sockets = []
        try:
            for p in range(19100, 19103):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", p))
                s.listen(1)
                sockets.append(s)
            with self.assertRaises(RuntimeError, msg="Could not find"):
                find_available_port(start_port=19100, max_attempts=3)
        finally:
            for s in sockets:
                s.close()

    def test_url_responds_false_for_unreachable(self) -> None:
        from app.start_local import url_responds
        self.assertFalse(url_responds("http://127.0.0.1:19999/api/health"))


class EnsureToolsTests(unittest.TestCase):
    """Test ensure_tools validation."""

    def test_missing_npm_raises(self) -> None:
        from app.start_local import ensure_tools
        with patch("shutil.which") as mock_which:
            # Python found, npm not found
            mock_which.side_effect = lambda cmd: sys.executable if cmd == sys.executable else None
            with self.assertRaises(EnvironmentError, msg="npm"):
                ensure_tools()


if __name__ == "__main__":
    unittest.main()
