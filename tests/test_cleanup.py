"""Tests for server cleanup thread, orphan cleanup, temp dir cleanup, and graceful shutdown.

Fully mocked — no real subprocesses, no real disk outside tmpdir.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import server


class CleanupTempDirsTests(unittest.TestCase):
    """_cleanup_stale_temp_dirs removes old dirs but keeps fresh ones."""

    def test_removes_old_temp_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp) / "temp"
            temp_dir.mkdir()
            old_dir = temp_dir / "old-workspace"
            old_dir.mkdir()
            (old_dir / "scratch.txt").write_text("data")
            # Set mtime to 2 hours ago
            old_mtime = time.time() - 7200
            os.utime(old_dir, (old_mtime, old_mtime))

            fresh_dir = temp_dir / "fresh-workspace"
            fresh_dir.mkdir()
            (fresh_dir / "scratch.txt").write_text("data")

            with patch("app.server.OUTPUTS_DIR", Path(tmp)):
                server._cleanup_stale_temp_dirs()

            self.assertFalse(old_dir.exists(), "Old temp dir should have been removed")
            self.assertTrue(fresh_dir.exists(), "Fresh temp dir should be preserved")

    def test_no_crash_when_temp_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.server.OUTPUTS_DIR", Path(tmp) / "nonexistent"):
                # Should not raise
                server._cleanup_stale_temp_dirs()


class CleanupOrphanProcessesTests(unittest.TestCase):
    """_cleanup_orphan_processes removes dead process refs from tracking."""

    def setUp(self) -> None:
        # Save original state
        with server._active_worker_procs_lock:
            self._saved_procs = list(server._active_worker_procs)
            self._saved_map = dict(server._job_worker_map)
            self._saved_flags = dict(server._job_cancel_flags)

    def tearDown(self) -> None:
        with server._active_worker_procs_lock:
            server._active_worker_procs.clear()
            server._active_worker_procs.extend(self._saved_procs)
            server._job_worker_map.clear()
            server._job_worker_map.update(self._saved_map)
            server._job_cancel_flags.clear()
            server._job_cancel_flags.update(self._saved_flags)

    def test_removes_dead_process_references(self) -> None:
        dead_proc = MagicMock()
        dead_proc.poll.return_value = 0  # process exited

        alive_proc = MagicMock()
        alive_proc.poll.return_value = None  # still running

        with server._active_worker_procs_lock:
            server._active_worker_procs.clear()
            server._active_worker_procs.extend([dead_proc, alive_proc])
            server._job_worker_map.clear()
            server._job_worker_map["dead-job"] = dead_proc
            server._job_worker_map["alive-job"] = alive_proc

        server._cleanup_orphan_processes()

        with server._active_worker_procs_lock:
            self.assertNotIn(dead_proc, server._active_worker_procs)
            self.assertIn(alive_proc, server._active_worker_procs)
            self.assertNotIn("dead-job", server._job_worker_map)
            self.assertIn("alive-job", server._job_worker_map)

    def test_no_crash_on_empty_state(self) -> None:
        with server._active_worker_procs_lock:
            server._active_worker_procs.clear()
            server._job_worker_map.clear()

        # Should not raise
        server._cleanup_orphan_processes()


class ShutdownWorkersTests(unittest.TestCase):
    """_shutdown_workers terminates all tracked subprocess refs."""

    def setUp(self) -> None:
        with server._active_worker_procs_lock:
            self._saved_procs = list(server._active_worker_procs)
            self._saved_map = dict(server._job_worker_map)
            self._saved_flags = dict(server._job_cancel_flags)
        self._saved_shutdown = server._shutdown_requested.is_set()

    def tearDown(self) -> None:
        if not self._saved_shutdown:
            server._shutdown_requested.clear()
        with server._active_worker_procs_lock:
            server._active_worker_procs.clear()
            server._active_worker_procs.extend(self._saved_procs)
            server._job_worker_map.clear()
            server._job_worker_map.update(self._saved_map)
            server._job_cancel_flags.clear()
            server._job_cancel_flags.update(self._saved_flags)

    def test_kills_tracked_processes(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = None  # still alive
        proc.pid = 55555
        proc.wait.return_value = 0

        with server._active_worker_procs_lock:
            server._active_worker_procs.clear()
            server._active_worker_procs.append(proc)
            server._job_worker_map.clear()
            server._job_cancel_flags.clear()

        with patch("app.server._cleanup_stale_temp_dirs"):
            server._shutdown_workers()

        proc.terminate.assert_called_once()

    def test_clears_tracking_maps(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = 0  # already dead
        proc.pid = 44444

        with server._active_worker_procs_lock:
            server._active_worker_procs.clear()
            server._active_worker_procs.append(proc)
            server._job_worker_map["j"] = proc
            server._job_cancel_flags["j"] = True

        with patch("app.server._cleanup_stale_temp_dirs"):
            server._shutdown_workers()

        with server._active_worker_procs_lock:
            self.assertEqual(len(server._active_worker_procs), 0)
            self.assertEqual(len(server._job_worker_map), 0)
            self.assertEqual(len(server._job_cancel_flags), 0)


class CleanupExpiredJobsTests(unittest.TestCase):
    """_cleanup_expired_jobs removes jobs older than retention hours."""

    def setUp(self) -> None:
        self.client = server.app.test_client()
        with server.jobs_lock:
            self._saved_jobs = {k: dict(v) for k, v in server.jobs.items()}

    def tearDown(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs.update(self._saved_jobs)

    def test_removes_old_terminal_jobs(self) -> None:
        old_time = time.time() - (server.JOB_RETENTION_HOURS * 3600 + 100)
        with server.jobs_lock:
            server.jobs["j-old-completed"] = {
                "status": "completed",
                "createdAt": old_time,
                "updatedAt": old_time,
            }
            server.jobs["j-old-failed"] = {
                "status": "failed",
                "createdAt": old_time,
                "updatedAt": old_time,
            }
            server.jobs["j-fresh"] = {
                "status": "completed",
                "createdAt": time.time(),
                "updatedAt": time.time(),
            }

        server._cleanup_expired_jobs()

        with server.jobs_lock:
            self.assertNotIn("j-old-completed", server.jobs)
            self.assertNotIn("j-old-failed", server.jobs)
            self.assertIn("j-fresh", server.jobs)

    def test_keeps_active_jobs_regardless_of_age(self) -> None:
        old_time = time.time() - 999999
        with server.jobs_lock:
            server.jobs["j-active-old"] = {
                "status": "rendering",
                "createdAt": old_time,
                "updatedAt": old_time,
            }

        server._cleanup_expired_jobs()

        with server.jobs_lock:
            self.assertIn("j-active-old", server.jobs)

    def test_removes_old_recovered_failures_on_shorter_retention(self) -> None:
        old_time = time.time() - (server.RECOVERED_JOB_RETENTION_SECONDS + 60)
        fresh_time = time.time()
        with server.jobs_lock:
            server.jobs["j-recovered-old"] = {
                "status": "failed",
                "recoveredByRestart": True,
                "createdAt": old_time,
                "updatedAt": old_time,
            }
            server.jobs["j-failed-normal"] = {
                "status": "failed",
                "createdAt": fresh_time,
                "updatedAt": fresh_time,
            }

        server._cleanup_expired_jobs()

        with server.jobs_lock:
            self.assertNotIn("j-recovered-old", server.jobs)
            self.assertIn("j-failed-normal", server.jobs)

    def test_removes_old_placeholder_terminal_jobs(self) -> None:
        old_time = time.time() - (server.PLACEHOLDER_JOB_RETENTION_SECONDS + 60)
        with server.jobs_lock:
            server.jobs["j-placeholder"] = {
                "status": "completed",
                "createdAt": old_time,
                "updatedAt": old_time,
                "runtimeSessionId": "previous-session",
            }

        server._cleanup_expired_jobs()

        with server.jobs_lock:
            self.assertNotIn("j-placeholder", server.jobs)


class CleanupFailureLogTests(unittest.TestCase):
    def test_cleanup_old_failure_logs_removes_stale_worker_and_run_files(self) -> None:
        import tempfile
        old_time = time.time() - (server.RENDER_FAILURE_LOG_RETENTION_SECONDS + 60)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            stale_run = root / "run-old.json"
            stale_worker = root / "render-worker-old.log"
            fresh_run = root / "run-fresh.json"
            stale_run.write_text("{}", encoding="utf-8")
            stale_worker.write_text("{}", encoding="utf-8")
            fresh_run.write_text("{}", encoding="utf-8")
            os.utime(stale_run, (old_time, old_time))
            os.utime(stale_worker, (old_time, old_time))

            with patch("app.server.LOGS_DIR", root):
                removed = server._cleanup_old_failure_logs()

            self.assertEqual(removed, 2)
            self.assertFalse(stale_run.exists())
            self.assertFalse(stale_worker.exists())
            self.assertTrue(fresh_run.exists())


if __name__ == "__main__":
    unittest.main()
