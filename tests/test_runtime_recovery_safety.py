from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.runtime_recovery import _kill_orphaned_lock_owners, cleanup_temp_workspaces


class RuntimeRecoverySafetyTests(unittest.TestCase):
    def test_does_not_kill_unverified_pid_from_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            locks_dir = Path(tmp) / "_locks"
            locks_dir.mkdir()
            lock_path = locks_dir / "abc.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "fingerprint": "abc",
                        "jobId": "job-1",
                        "pid": 43210,
                        "createdAt": time.time(),
                    }
                ),
                encoding="utf-8",
            )

            with patch("app.runtime_recovery.OUTPUT_LOCKS_DIR", locks_dir), \
                 patch("app.runtime_recovery._pid_matches_miscoshorts_worker", return_value=False), \
                 patch("app.runtime_recovery.subprocess.run") as mock_run:
                killed = _kill_orphaned_lock_owners()

        self.assertEqual(killed, [])
        mock_run.assert_not_called()

    def test_cleanup_temp_workspaces_only_removes_recovered_or_old_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp) / "temp"
            temp_dir.mkdir()

            recovered_dir = temp_dir / "fp-recovered-job-123-abc123"
            recovered_dir.mkdir()

            fresh_unrelated = temp_dir / "fp-other-job-999-def456"
            fresh_unrelated.mkdir()

            old_unrelated = temp_dir / "very-old"
            old_unrelated.mkdir()
            old_mtime = time.time() - (13 * 3600)
            old_unrelated.touch()
            os_utime_path = old_unrelated
            import os
            os.utime(os_utime_path, (old_mtime, old_mtime))

            with patch("app.runtime_recovery.OUTPUT_TEMP_DIR", temp_dir):
                report = cleanup_temp_workspaces(recovered_job_ids={"recovered"}, cleared_lock_fingerprints=set())

            self.assertFalse(recovered_dir.exists())
            self.assertTrue(fresh_unrelated.exists())
            self.assertFalse(old_unrelated.exists())
            self.assertEqual(len(report["clearedTempWorkspacePaths"]), 2)


if __name__ == "__main__":
    unittest.main()
