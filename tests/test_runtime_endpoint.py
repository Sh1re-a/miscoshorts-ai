from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from app import server


class RuntimeEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = server.app.test_client()
        with server.jobs_lock:
            self.original_jobs = copy.deepcopy(server.jobs)
        self.original_active_jobs = server._active_jobs

    def tearDown(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs.update(self.original_jobs)
        server._active_jobs = self.original_active_jobs

    def test_runtime_endpoint_reports_healthy_empty_queue(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
        server._active_jobs = 0

        with patch("app.server.list_active_fingerprint_locks", return_value=[]):
            response = self.client.get("/api/runtime")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["queue"]["activeCount"], 0)
        self.assertEqual(payload["queue"]["queuedCount"], 0)
        self.assertEqual(payload["consistency"]["status"], "ok")
        self.assertEqual(payload["consistency"]["issues"], [])

    def test_runtime_endpoint_reports_queue_inconsistency_when_waiting_lock_is_missing(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs["job01"] = {
                "status": "queued",
                "queueState": "waiting_for_identical_render",
                "waitingOnFingerprint": "feedfacefeedface",
                "createdAt": 1.0,
                "updatedAt": 1.0,
            }
            server._refresh_queue_positions_locked()
        server._active_jobs = 0

        with patch("app.server.list_active_fingerprint_locks", return_value=[]):
            response = self.client.get("/api/runtime")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["consistency"]["status"], "degraded")
        self.assertTrue(any("feedfacefeedface" in issue for issue in payload["consistency"]["issues"]))


if __name__ == "__main__":
    unittest.main()
