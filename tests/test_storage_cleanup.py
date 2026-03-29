from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage_manager import delete_job_storage, prune_storage


class StorageCleanupTests(unittest.TestCase):
    def test_cannot_delete_active_job_storage(self) -> None:
        jobs = {"job-a": {"status": "rendering"}}
        with self.assertRaisesRegex(ValueError, "still queued or rendering"):
            delete_job_storage(jobs, "job-a", mode="job")

    def test_source_media_cleanup_keeps_clips_and_marks_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "job-output"
            (output_dir / "source").mkdir(parents=True, exist_ok=True)
            (output_dir / "clips").mkdir(parents=True, exist_ok=True)
            (output_dir / "meta").mkdir(parents=True, exist_ok=True)
            (output_dir / "source" / "source_video.mp4").write_bytes(b"0" * 32)
            clip_path = output_dir / "clips" / "clip.mp4"
            clip_path.write_bytes(b"1" * 64)
            manifest_path = output_dir / "meta" / "result.json"
            manifest_path.write_text(json.dumps({"jobFingerprint": "fingerprint-a"}), encoding="utf-8")
            jobs = {
                "job-a": {
                    "status": "completed",
                    "result": {
                        "outputDir": str(output_dir),
                    },
                },
            }

            report = delete_job_storage(jobs, "job-a", mode="source_media")

            self.assertEqual(report["removedItems"], 1)
            self.assertFalse((output_dir / "source").exists())
            self.assertTrue(clip_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(manifest["sourceMediaPresent"])

    def test_cannot_delete_shared_output_job(self) -> None:
        jobs = {
            "job-a": {"status": "completed", "result": {"outputDir": "/tmp/shared"}},
            "job-b": {"status": "completed", "result": {"outputDir": "/tmp/shared"}},
        }
        with self.assertRaisesRegex(ValueError, "another saved job still points to the same output"):
            delete_job_storage(jobs, "job-a", mode="job")

    def test_prune_storage_can_remove_failed_job_records_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            failed_job_state = Path(tmp_dir) / "failed.json"
            failed_job_state.write_text("{}", encoding="utf-8")
            jobs = {
                "job-failed": {"status": "failed"},
                "job-completed": {"status": "completed"},
            }
            with patch("app.storage_manager._job_state_path", return_value=failed_job_state):
                report = prune_storage(
                    jobs,
                    prune_temp=False,
                    prune_cache=False,
                    prune_jobs=False,
                    prune_failed_jobs=True,
                    dry_run=False,
                )

            self.assertEqual(report["failedJobs"]["removedItems"], 1)
            self.assertEqual(report["failedJobs"]["removedJobIds"], ["job-failed"])
            self.assertFalse(failed_job_state.exists())


if __name__ == "__main__":
    unittest.main()
