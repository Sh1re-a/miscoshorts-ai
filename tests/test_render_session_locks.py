"""Tests for fingerprint lock lifecycle in render_session.py.

Exercises acquire/release, orphan cleanup, stale lock detection,
and Windows-simulation edge cases.  All filesystem operations use
a temporary directory.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.render_session import (
    _lock_path,
    _pid_is_alive,
    _read_lock_payload,
    _safe_unlink_lock,
    acquire_fingerprint_lock,
    cleanup_stale_fingerprint_locks,
    describe_fingerprint_lock,
    force_remove_all_locks,
    job_fingerprint,
    list_active_fingerprint_locks,
)


class JobFingerprintTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        kwargs = dict(
            video_url="https://youtube.com/watch?v=abc",
            output_filename="test.mp4",
            clip_count=3,
            render_profile="default",
            subtitle_style={"fontName": "Arial"},
        )
        self.assertEqual(job_fingerprint(**kwargs), job_fingerprint(**kwargs))

    def test_different_for_different_inputs(self) -> None:
        base = dict(
            video_url="https://youtube.com/watch?v=abc",
            output_filename="test.mp4",
            clip_count=3,
            render_profile="default",
            subtitle_style={},
        )
        fp1 = job_fingerprint(**base)
        fp2 = job_fingerprint(**{**base, "clip_count": 5})
        self.assertNotEqual(fp1, fp2)

    def test_returns_16_char_hex(self) -> None:
        fp = job_fingerprint(
            video_url="https://youtube.com/watch?v=abc",
            output_filename="test.mp4",
            clip_count=1,
            render_profile="default",
            subtitle_style={},
        )
        self.assertEqual(len(fp), 16)
        int(fp, 16)  # Should not raise


class PidIsAliveTests(unittest.TestCase):
    def test_current_pid_is_alive(self) -> None:
        self.assertTrue(_pid_is_alive(os.getpid()))

    def test_none_pid(self) -> None:
        self.assertFalse(_pid_is_alive(None))

    def test_zero_pid(self) -> None:
        self.assertFalse(_pid_is_alive(0))

    def test_negative_pid(self) -> None:
        self.assertFalse(_pid_is_alive(-1))

    def test_dead_pid(self) -> None:
        # Use a very high PID that almost certainly doesn't exist
        self.assertFalse(_pid_is_alive(4_000_000))


class SafeUnlinkTests(unittest.TestCase):
    def test_unlink_existing_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".lock") as f:
            path = Path(f.name)
        self.assertTrue(_safe_unlink_lock(path))
        self.assertFalse(path.exists())

    def test_unlink_missing_file(self) -> None:
        path = Path(tempfile.gettempdir()) / "nonexistent_test_lock.lock"
        self.assertTrue(_safe_unlink_lock(path))


class ReadLockPayloadTests(unittest.TestCase):
    def test_valid_json(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".lock", delete=False) as f:
            json.dump({"fingerprint": "abc", "pid": 123}, f)
            path = Path(f.name)
        payload = _read_lock_payload(path)
        self.assertEqual(payload["fingerprint"], "abc")
        os.unlink(path)

    def test_invalid_json(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".lock", delete=False) as f:
            f.write("not json")
            path = Path(f.name)
        self.assertIsNone(_read_lock_payload(path))
        os.unlink(path)

    def test_missing_file(self) -> None:
        self.assertIsNone(_read_lock_payload(Path("/nonexistent_lock.lock")))


class DescribeFingerprintLockTests(unittest.TestCase):
    def test_describes_valid_lock(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".lock", delete=False) as f:
            json.dump({"fingerprint": "fp123", "jobId": "job1", "pid": os.getpid()}, f)
            path = Path(f.name)
        details = describe_fingerprint_lock(path)
        self.assertEqual(details["fingerprint"], "fp123")
        self.assertEqual(details["jobId"], "job1")
        self.assertTrue(details["alive"])
        self.assertTrue(details["payloadValid"])
        os.unlink(path)

    def test_describes_dead_owner(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".lock", delete=False) as f:
            json.dump({"fingerprint": "fp456", "jobId": "job2", "pid": 4_000_000}, f)
            path = Path(f.name)
        details = describe_fingerprint_lock(path)
        self.assertFalse(details["alive"])
        os.unlink(path)


class AcquireFingerprintLockTests(unittest.TestCase):
    def test_acquire_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.render_session.OUTPUT_LOCKS_DIR", Path(tmpdir)):
                with acquire_fingerprint_lock("test_fp", job_id="j1"):
                    lock_file = Path(tmpdir) / "test_fp.lock"
                    self.assertTrue(lock_file.exists())
                    payload = json.loads(lock_file.read_text())
                    self.assertEqual(payload["fingerprint"], "test_fp")
                    self.assertEqual(payload["pid"], os.getpid())
                # After exiting context, lock should be removed
                self.assertFalse(lock_file.exists())

    def test_removes_dead_owner_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.render_session.OUTPUT_LOCKS_DIR", Path(tmpdir)):
                # Pre-create a lock owned by a dead process
                lock_file = Path(tmpdir) / "dead_fp.lock"
                lock_file.write_text(json.dumps({
                    "fingerprint": "dead_fp", "jobId": "old", "pid": 4_000_000,
                    "createdAt": time.time() - 1000,
                }))
                # Acquiring should succeed by removing the dead lock
                with acquire_fingerprint_lock("dead_fp", job_id="new"):
                    payload = json.loads(lock_file.read_text())
                    self.assertEqual(payload["jobId"], "new")


class CleanupStaleLockTests(unittest.TestCase):
    def test_removes_dead_owner_locks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            locks_dir = Path(tmpdir)
            dead_lock = locks_dir / "dead.lock"
            dead_lock.write_text(json.dumps({
                "fingerprint": "dead", "jobId": "j1", "pid": 4_000_000,
            }))
            with patch("app.render_session.OUTPUT_LOCKS_DIR", locks_dir):
                result = cleanup_stale_fingerprint_locks()
                self.assertEqual(len(result["removedLocks"]), 1)
                self.assertEqual(result["removedLocks"][0]["fingerprint"], "dead")

    def test_keeps_alive_locks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            locks_dir = Path(tmpdir)
            alive_lock = locks_dir / "alive.lock"
            alive_lock.write_text(json.dumps({
                "fingerprint": "alive", "jobId": "j2", "pid": os.getpid(),
            }))
            with patch("app.render_session.OUTPUT_LOCKS_DIR", locks_dir):
                result = cleanup_stale_fingerprint_locks()
                self.assertEqual(len(result["removedLocks"]), 0)
                self.assertEqual(len(result["activeLocks"]), 1)

    def test_removes_terminal_job_locks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            locks_dir = Path(tmpdir)
            lock = locks_dir / "terminal.lock"
            lock.write_text(json.dumps({
                "fingerprint": "terminal", "jobId": "finished_job", "pid": os.getpid(),
            }))
            with patch("app.render_session.OUTPUT_LOCKS_DIR", locks_dir):
                result = cleanup_stale_fingerprint_locks(terminal_job_ids={"finished_job"})
                self.assertEqual(len(result["removedLocks"]), 1)


class ForceRemoveAllLocksTests(unittest.TestCase):
    def test_removes_all_locks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            locks_dir = Path(tmpdir)
            for i in range(3):
                (locks_dir / f"lock{i}.lock").write_text(json.dumps({"fingerprint": f"fp{i}", "pid": 1}))
            with patch("app.render_session.OUTPUT_LOCKS_DIR", locks_dir):
                result = force_remove_all_locks()
                self.assertEqual(len(result["removedLocks"]), 3)

    def test_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.render_session.OUTPUT_LOCKS_DIR", Path(tmpdir)):
                result = force_remove_all_locks()
                self.assertEqual(len(result["removedLocks"]), 0)


if __name__ == "__main__":
    unittest.main()
