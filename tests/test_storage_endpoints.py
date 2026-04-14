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

    def test_prune_endpoint_returns_protected_paths_metadata(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
        response = self.client.post("/api/storage/prune", json={"pruneTemp": True})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("protectedPaths", payload)
        self.assertIn("temp", payload["protectedPaths"])
        self.assertIn("cache", payload["protectedPaths"])
        self.assertIn("jobs", payload["protectedPaths"])

    def test_prune_temp_returns_temp_key(self) -> None:
        """Prune with pruneTemp=true must include a 'temp' key in the response."""
        response = self.client.post("/api/storage/prune", json={"pruneTemp": True})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("temp", data)
        self.assertIn("removedItems", data["temp"])
        self.assertIn("removedBytes", data["temp"])

    # ── Storage report shape ──────────────────────────────────────────────────

    def test_storage_report_contains_required_keys(self) -> None:
        """GET /api/storage must return all keys the frontend depends on."""
        with server.jobs_lock:
            server.jobs.clear()

        response = self.client.get("/api/storage")
        data = response.get_json()

        self.assertIn("summary", data)
        self.assertIn("jobs", data["summary"])
        self.assertIn("cache", data["summary"])
        self.assertIn("temp", data["summary"])
        self.assertIn("bytes", data["summary"]["jobs"])
        self.assertIn("bytes", data["summary"]["cache"])
        self.assertIn("bytes", data["summary"]["temp"])

        self.assertIn("jobStateCounts", data)
        self.assertIn("completed", data["jobStateCounts"])
        self.assertIn("failed", data["jobStateCounts"])
        self.assertIn("active", data["jobStateCounts"])

        self.assertIn("recommendations", data)
        self.assertIn("canPruneTemp", data["recommendations"])
        self.assertIn("canDeleteJobSourceMedia", data["recommendations"])

    def test_storage_report_job_counts_reflect_injected_jobs(self) -> None:
        """Job state counts in GET /api/storage must match injected server.jobs."""
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs["j-done"] = {"status": "completed", "result": {"outputDir": ""}}
            server.jobs["j-fail"] = {"status": "failed"}
        try:
            response = self.client.get("/api/storage")
            counts = response.get_json()["jobStateCounts"]
            self.assertEqual(counts["completed"], 1)
            self.assertEqual(counts["failed"], 1)
        finally:
            with server.jobs_lock:
                server.jobs.pop("j-done", None)
                server.jobs.pop("j-fail", None)

    # ── 409 response body ─────────────────────────────────────────────────────

    def test_active_job_409_has_error_field(self) -> None:
        """409 response must include an 'error' field the frontend can display."""
        with server.jobs_lock:
            server.jobs["job-rendering"] = {"status": "rendering"}
        try:
            response = self.client.post("/api/storage/jobs/job-rendering/cleanup", json={"mode": "job"})
            self.assertEqual(response.status_code, 409)
            data = response.get_json()
            self.assertIn("error", data)
            self.assertIsInstance(data["error"], str)
            self.assertGreater(len(data["error"]), 0)
        finally:
            with server.jobs_lock:
                server.jobs.pop("job-rendering", None)

    # ── source_media response shape ───────────────────────────────────────────

    def test_source_media_response_includes_mode_and_counts(self) -> None:
        """mode=source_media response must include mode, removedItems, removedBytes."""
        with server.jobs_lock:
            server.jobs["job-sm"] = {"status": "completed", "result": {"outputDir": ""}}
        try:
            fake_result = {"jobId": "job-sm", "mode": "source_media", "removedItems": 1, "removedBytes": 1024}
            with patch("app.storage_manager.delete_job_storage", return_value=fake_result):
                response = self.client.post(
                    "/api/storage/jobs/job-sm/cleanup", json={"mode": "source_media"}
                )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["mode"], "source_media")
            self.assertIn("removedItems", data)
            self.assertIn("removedBytes", data)
        finally:
            with server.jobs_lock:
                server.jobs.pop("job-sm", None)

    # ── polling contract after cleanup ────────────────────────────────────────

    def test_job_still_accessible_after_source_media_cleanup(self) -> None:
        """After source_media cleanup GET /api/jobs/{id} must still return 200."""
        with server.jobs_lock:
            server.jobs["job-poll-src"] = {
                "status": "completed",
                "result": {"outputDir": "", "sourceMediaPresent": True},
            }
        try:
            fake_result = {"jobId": "job-poll-src", "mode": "source_media", "removedItems": 0, "removedBytes": 0}
            with patch("app.storage_manager.delete_job_storage", return_value=fake_result):
                self.client.post("/api/storage/jobs/job-poll-src/cleanup", json={"mode": "source_media"})
            response = self.client.get("/api/jobs/job-poll-src")
            self.assertEqual(response.status_code, 200)
        finally:
            with server.jobs_lock:
                server.jobs.pop("job-poll-src", None)

    def test_job_not_accessible_after_full_job_cleanup(self) -> None:
        """After full-job cleanup GET /api/jobs/{id} must return 404."""
        with server.jobs_lock:
            server.jobs["job-poll-full"] = {"status": "completed", "result": {"outputDir": ""}}
        fake_result = {"jobId": "job-poll-full", "mode": "job", "removedItems": 1, "removedBytes": 0}
        with tempfile.TemporaryDirectory() as tmp:
            fake_state = Path(tmp) / "job-poll-full.json"
            fake_state.write_text("{}", encoding="utf-8")
            with patch("app.storage_manager.delete_job_storage", return_value=fake_result):
                with patch("app.server._job_state_path", return_value=fake_state):
                    self.client.post("/api/storage/jobs/job-poll-full/cleanup", json={"mode": "job"})
        response = self.client.get("/api/jobs/job-poll-full")
        self.assertEqual(response.status_code, 404)

    def test_source_media_cleanup_updates_in_memory_sourceMediaPresent(self) -> None:
        """After source_media cleanup server.jobs must reflect sourceMediaPresent=False."""
        with server.jobs_lock:
            server.jobs["job-smp"] = {
                "status": "completed",
                "result": {"outputDir": "", "sourceMediaPresent": True},
            }
        try:
            fake_result = {"jobId": "job-smp", "mode": "source_media", "removedItems": 1, "removedBytes": 512}
            with patch("app.storage_manager.delete_job_storage", return_value=fake_result):
                response = self.client.post("/api/storage/jobs/job-smp/cleanup", json={"mode": "source_media"})
            self.assertEqual(response.status_code, 200)
            with server.jobs_lock:
                job_result = server.jobs["job-smp"].get("result", {})
            self.assertFalse(
                job_result.get("sourceMediaPresent"),
                "In-memory job must reflect sourceMediaPresent=False after source_media cleanup",
            )
        finally:
            with server.jobs_lock:
                server.jobs.pop("job-smp", None)


if __name__ == "__main__":
    unittest.main()
