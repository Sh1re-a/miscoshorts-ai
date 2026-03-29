from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from app.paths import OUTPUT_LOCKS_DIR, OUTPUT_TEMP_DIR, OUTPUTS_DIR
from app.render_session import cleanup_stale_fingerprint_locks
from app.runtime_recovery import JOB_STATE_DIR, recover_runtime_state


class RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.job_id = "testrecoverjob01"
        self.lock_fingerprint = "deadbeefcafefeed"
        self.job_path = JOB_STATE_DIR / f"{self.job_id}.json"
        self.lock_path = OUTPUT_LOCKS_DIR / f"{self.lock_fingerprint}.lock"
        self.workspace_path = OUTPUT_TEMP_DIR / f"{self.lock_fingerprint}-{self.job_id}-temp"
        JOB_STATE_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_TEMP_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.job_path.unlink(missing_ok=True)
        self.lock_path.unlink(missing_ok=True)
        if self.workspace_path.exists():
            if self.workspace_path.is_dir():
                for child in self.workspace_path.rglob("*"):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                for child in sorted(self.workspace_path.rglob("*"), reverse=True):
                    if child.is_dir():
                        child.rmdir()
                self.workspace_path.rmdir()
            else:
                self.workspace_path.unlink(missing_ok=True)

    def test_cleanup_stale_fingerprint_locks_removes_dead_owner(self) -> None:
        self.lock_path.write_text(
            json.dumps(
                {
                    "fingerprint": self.lock_fingerprint,
                    "jobId": self.job_id,
                    "pid": 999999,
                    "createdAt": time.time() - 30,
                    "ownerToken": "test-token",
                }
            ),
            encoding="utf-8",
        )

        report = cleanup_stale_fingerprint_locks()

        self.assertFalse(self.lock_path.exists())
        removed_fingerprints = {str(item["fingerprint"]) for item in report["removedLocks"]}
        self.assertIn(self.lock_fingerprint, removed_fingerprints)

    def test_recover_runtime_state_marks_interrupted_jobs_failed_and_cleans_temp(self) -> None:
        self.job_path.write_text(
            json.dumps(
                {
                    "status": "queued",
                    "queuePosition": 1,
                    "createdAt": time.time() - 20,
                    "updatedAt": time.time() - 20,
                    "logs": [],
                }
            ),
            encoding="utf-8",
        )
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "scratch.txt").write_text("temp", encoding="utf-8")

        report = recover_runtime_state()
        payload = json.loads(self.job_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "failed")
        self.assertTrue(payload["recoveredByRestart"])
        self.assertEqual(payload["errorCategory"], "recovered")
        self.assertFalse(self.workspace_path.exists())
        self.assertIn(self.job_id, report["recoveredJobIds"])


if __name__ == "__main__":
    unittest.main()
