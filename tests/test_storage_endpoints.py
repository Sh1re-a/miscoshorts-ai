from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import server


class StorageEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = server.app.test_client()
        with server.jobs_lock:
            self.original_jobs = copy.deepcopy(server.jobs)

    def tearDown(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs.update(self.original_jobs)

    # ── GET /api/storage ──────────────────────────────────────────────────────

    def test_storage_endpoint_returns_report(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()

        response = self.client.get("/api/storage")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("summary", payload)
        self.assertIn("manageableJobs", payload)

    # ── POST /api/storage/jobs/<id>/cleanup ───────────────────────────────────

    def test_cleanup_endpoint_rejects_invalid_mode(self) -> None:
        response = self.client.post("/api/storage/jobs/job-1/cleanup", json={"mode": "bad"})
        self.assertEqual(response.status_code, 400)

    def test_cleanup_active_job_returns_409(self) -> None:
        """Active or queued jobs must be refused with 409 Conflict, not 400."""
        with server.jobs_lock:
            server.jobs["job-active"] = {"status": "rendering"}
        try:
            response = self.client.post("/api/storage/jobs/job-active/cleanup", json={"mode": "job"})
            self.assertEqual(response.status_code, 409)
            self.assertIn("still queued or rendering", response.get_json()["error"])
        finally:
            with server.jobs_lock:
                server.jobs.pop("job-active", None)

    def test_cleanup_unknown_job_returns_400(self) -> None:
        with server.jobs_lock:
            server.jobs.pop("job-unknown", None)
        response = self.client.post("/api/storage/jobs/job-unknown/cleanup", json={"mode": "job"})
        self.assertEqual(response.status_code, 400)

    def test_delete_source_media_keeps_job_in_server_memory(self) -> None:
        """Deleting source media (mode=source_media) must NOT remove the job from memory."""
        with server.jobs_lock:
            server.jobs["job-src"] = {"status": "completed", "result": {"outputDir": ""}}
        try:
            fake_result = {"jobId": "job-src", "mode": "source_media", "removedItems": 0, "removedBytes": 0}
            with patch("app.storage_manager.delete_job_storage", return_value=fake_result):
                response = self.client.post(
                    "/api/storage/jobs/job-src/cleanup", json={"mode": "source_media"}
                )
            self.assertEqual(response.status_code, 200)
            with server.jobs_lock:
                self.assertIn("job-src", server.jobs, "source_media mode must not evict the job from server memory")
        finally:
            with server.jobs_lock:
                server.jobs.pop("job-src", None)

    def test_delete_job_removes_job_from_server_memory(self) -> None:
        """Deleting a whole job (mode=job) must evict the job from server.jobs."""
        with server.jobs_lock:
            server.jobs["job-full"] = {"status": "completed", "result": {"outputDir": ""}}
        try:
            fake_result = {"jobId": "job-full", "mode": "job", "removedItems": 1, "removedBytes": 0}
            with tempfile.TemporaryDirectory() as tmp:
                fake_state = Path(tmp) / "job-full.json"
                fake_state.write_text("{}", encoding="utf-8")
                with patch("app.storage_manager.delete_job_storage", return_value=fake_result):
                    with patch("app.server._job_state_path", return_value=fake_state):
                        response = self.client.post(
                            "/api/storage/jobs/job-full/cleanup", json={"mode": "job"}
                        )
            self.assertEqual(response.status_code, 200)
            with server.jobs_lock:
                self.assertNotIn("job-full", server.jobs, "job mode must evict the job from server memory")
        finally:
            with server.jobs_lock:
                server.jobs.pop("job-full", None)

    # ── POST /api/storage/prune ───────────────────────────────────────────────

    def test_prune_endpoint_empty_body_returns_400(self) -> None:
        """Prune with no explicit categories must be rejected."""
        response = self.client.post("/api/storage/prune", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one", response.get_json()["error"])

    def test_prune_endpoint_all_false_returns_400(self) -> None:
        response = self.client.post(
            "/api/storage/prune",
            json={"pruneTemp": False, "pruneCache": False, "pruneJobs": False, "pruneFailedJobs": False},
        )
        self.assertEqual(response.status_code, 400)

    def test_prune_endpoint_accepts_prune_failed_jobs(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
        response = self.client.post("/api/storage/prune", json={"pruneFailedJobs": True})
        self.assertEqual(response.status_code, 200)
        self.assertIn("failedJobs", response.get_json())


if __name__ == "__main__":
    unittest.main()
