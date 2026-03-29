from __future__ import annotations

import copy
import unittest
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

    def test_storage_endpoint_returns_report(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()

        response = self.client.get("/api/storage")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("summary", payload)
        self.assertIn("manageableJobs", payload)

    def test_cleanup_endpoint_rejects_invalid_mode(self) -> None:
        response = self.client.post("/api/storage/jobs/job-1/cleanup", json={"mode": "bad"})
        self.assertEqual(response.status_code, 400)

    def test_cleanup_endpoint_surfaces_safe_conflict(self) -> None:
        with patch("app.server.delete_job_storage", side_effect=ValueError("Cannot delete storage for a job that is still queued or rendering.")):
            response = self.client.post("/api/storage/jobs/job-1/cleanup", json={"mode": "job"})

        self.assertEqual(response.status_code, 409)
        self.assertIn("still queued or rendering", response.get_json()["error"])

    def test_prune_endpoint_requires_selected_category(self) -> None:
        response = self.client.post("/api/storage/prune", json={})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
