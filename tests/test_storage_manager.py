from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage_manager import build_storage_report


class StorageManagerTests(unittest.TestCase):
    def test_build_storage_report_marks_shared_outputs_as_not_deletable(self) -> None:
        jobs = {
            "job-a": {
                "status": "completed",
                "updatedAt": 2.0,
                "result": {
                    "outputDir": "/tmp/shared-output",
                    "videoUrl": "https://example.com/video",
                    "jobFingerprint": "fingerprint-a",
                },
            },
            "job-b": {
                "status": "completed",
                "updatedAt": 1.0,
                "result": {
                    "outputDir": "/tmp/shared-output",
                    "videoUrl": "https://example.com/video",
                    "jobFingerprint": "fingerprint-a",
                },
            },
        }

        with patch("app.storage_manager.storage_summary", return_value={
            "jobs": {"path": "/tmp/jobs", "bytes": 10},
            "cache": {"path": "/tmp/cache", "bytes": 20},
            "temp": {"path": "/tmp/temp", "bytes": 0},
            "logs": {"path": "/tmp/logs", "bytes": 1},
            "modelCache": {"path": "/tmp/model", "bytes": 2},
        }):
            report = build_storage_report(jobs)

        self.assertEqual(len(report["manageableJobs"]), 2)
        self.assertTrue(all(job["sharedOutputRefs"] == 2 for job in report["manageableJobs"]))
        self.assertTrue(all(not job["canDeleteJob"] for job in report["manageableJobs"]))

    def test_build_storage_report_detects_source_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "job-output"
            source_dir = output_dir / "source"
            clips_dir = output_dir / "clips"
            meta_dir = output_dir / "meta"
            source_dir.mkdir(parents=True, exist_ok=True)
            clips_dir.mkdir(parents=True, exist_ok=True)
            meta_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "source_video.mp4").write_bytes(b"0" * 32)
            (clips_dir / "clip.mp4").write_bytes(b"1" * 64)
            (meta_dir / "result.json").write_text("{}", encoding="utf-8")

            jobs = {
                "job-a": {
                    "status": "completed",
                    "updatedAt": 2.0,
                    "result": {
                        "outputDir": str(output_dir),
                        "videoUrl": "https://example.com/video",
                        "jobFingerprint": "fingerprint-a",
                    },
                },
            }

            with patch("app.storage_manager.storage_summary", return_value={
                "jobs": {"path": "/tmp/jobs", "bytes": 10},
                "cache": {"path": "/tmp/cache", "bytes": 20},
                "temp": {"path": "/tmp/temp", "bytes": 0},
                "logs": {"path": "/tmp/logs", "bytes": 1},
                "modelCache": {"path": "/tmp/model", "bytes": 2},
            }):
                report = build_storage_report(jobs)

            self.assertEqual(len(report["manageableJobs"]), 1)
            entry = report["manageableJobs"][0]
            self.assertTrue(entry["canDeleteJob"])
            self.assertTrue(entry["canDeleteSourceMedia"])
            self.assertEqual(entry["storage"]["sourceMediaBytes"], 32)
            self.assertEqual(entry["storage"]["clipsBytes"], 64)


if __name__ == "__main__":
    unittest.main()
