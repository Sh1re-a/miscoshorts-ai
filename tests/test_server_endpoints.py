"""Tests for server error handlers and progress derivation.

All network/disk I/O is mocked.
"""
from __future__ import annotations

import copy
import time
import unittest

from app import server


class GlobalErrorHandlerTests(unittest.TestCase):
    """Verify Flask error handlers return proper JSON instead of HTML."""

    def setUp(self) -> None:
        self.client = server.app.test_client()

    def test_404_returns_json(self) -> None:
        response = self.client.get("/api/this-does-not-exist")
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertIsNotNone(data, "404 should return JSON, not HTML")
        self.assertIn("error", data)

    def test_global_error_handler_is_registered(self) -> None:
        """Verify the catch-all Exception handler is registered on the app."""
        handlers = server.app.error_handler_spec.get(None, {})
        # Flask stores error handlers by code or exception type
        self.assertTrue(
            500 in handlers or Exception in handlers.get(None, {}),
            "A global 500 or Exception error handler should be registered",
        )


class ProgressDerivationTests(unittest.TestCase):
    """Unit-test _derive_progress_fields for various stages and messages."""

    def test_downloading_with_eta_in_message(self) -> None:
        job = {"clipCount": 3}
        fields = server._derive_progress_fields(job, "downloading", "Downloading... 42% | ~180s left")
        self.assertAlmostEqual(fields["stageProgress"], 42.0)
        self.assertAlmostEqual(fields["etaSeconds"], 180.0)

    def test_downloading_without_eta_uses_fallback(self) -> None:
        job = {"clipCount": 3}
        fields = server._derive_progress_fields(job, "downloading", "Downloading... 10%")
        self.assertAlmostEqual(fields["stageProgress"], 10.0)
        # Fallback: max(30, (100 - 10) * 3) = 270
        self.assertAlmostEqual(fields["etaSeconds"], 270.0)

    def test_downloading_no_percent_defaults_to_20(self) -> None:
        job = {"clipCount": 3}
        fields = server._derive_progress_fields(job, "downloading", "Downloading source video...")
        self.assertAlmostEqual(fields["stageProgress"], 20.0)

    def test_transcribing_preparing_model(self) -> None:
        job = {"clipCount": 2}
        fields = server._derive_progress_fields(job, "transcribing", "Preparing the local speech model...")
        self.assertAlmostEqual(fields["stageProgress"], 12.0)

    def test_analyzing_gemini(self) -> None:
        job = {"clipCount": 3}
        fields = server._derive_progress_fields(job, "analyzing", "Asking Gemini to pick the best clips...")
        self.assertAlmostEqual(fields["stageProgress"], 45.0)
        self.assertGreater(fields["etaSeconds"], 0)

    def test_rendering_clip_progress(self) -> None:
        job = {"clipCount": 3}
        fields = server._derive_progress_fields(job, "rendering", "Rendering clip 2 of 3")
        # stage_progress = (2-1)/3 * 100 = 33.3
        self.assertAlmostEqual(fields["stageProgress"], 33.3, places=0)
        self.assertGreater(fields["overallProgress"], 62.0)

    def test_completed_stage(self) -> None:
        job = {"clipCount": 3}
        fields = server._derive_progress_fields(job, "completed", "Done")
        self.assertAlmostEqual(fields["stageProgress"], 100.0)
        self.assertAlmostEqual(fields["etaSeconds"], 0.0)

    def test_failed_stage(self) -> None:
        job = {"clipCount": 3}
        fields = server._derive_progress_fields(job, "failed", "Something broke")
        self.assertAlmostEqual(fields["stageProgress"], 100.0)

    def test_queued_with_position(self) -> None:
        job = {"clipCount": 3, "queuePosition": 2, "queueState": "waiting_for_worker"}
        fields = server._derive_progress_fields(job, "queued", "Waiting for worker...")
        self.assertIsNotNone(fields["etaSeconds"])
        self.assertGreater(fields["etaSeconds"], 0)


class HealthEndpointTests(unittest.TestCase):
    """GET /api/health always returns a JSON payload with status=ok."""

    def setUp(self) -> None:
        self.client = server.app.test_client()

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "ok")

    def test_health_contains_queue_counts(self) -> None:
        response = self.client.get("/api/health")
        data = response.get_json()
        self.assertIn("jobs", data)
        self.assertIn("active", data["jobs"])
        self.assertIn("queued", data["jobs"])


class BootstrapEndpointTests(unittest.TestCase):
    """GET /api/bootstrap returns required schema fields."""

    def setUp(self) -> None:
        self.client = server.app.test_client()

    def test_bootstrap_returns_render_profiles(self) -> None:
        response = self.client.get("/api/bootstrap")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("renderProfiles", data)
        self.assertIsInstance(data["renderProfiles"], dict)
        self.assertIn("studio", data["renderProfiles"])

    def test_bootstrap_contains_runtime_session_id(self) -> None:
        response = self.client.get("/api/bootstrap")
        data = response.get_json()
        self.assertIn("runtimeSessionId", data)
        self.assertIsInstance(data["runtimeSessionId"], str)
        self.assertGreater(len(data["runtimeSessionId"]), 0)

    def test_bootstrap_contains_frontend_built(self) -> None:
        response = self.client.get("/api/bootstrap")
        data = response.get_json()
        self.assertIn("frontendBuilt", data)
        self.assertIsInstance(data["frontendBuilt"], bool)


class RuntimeSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        with server.jobs_lock:
            self._saved_jobs = {k: copy.deepcopy(v) for k, v in server.jobs.items()}

    def tearDown(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs.update(self._saved_jobs)

    def test_queue_snapshot_hides_old_recovered_failures_from_recent_jobs(self) -> None:
        now = time.time()
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs["job-recovered-old"] = {
                "status": "failed",
                "recoveredByRestart": True,
                "createdAt": now - server.RECOVERED_JOB_VISIBILITY_SECONDS - 30,
                "updatedAt": now - server.RECOVERED_JOB_VISIBILITY_SECONDS - 30,
            }
            server.jobs["job-fresh-failed"] = {
                "status": "failed",
                "createdAt": now,
                "updatedAt": now,
            }

        snapshot = server._queue_snapshot()
        recent_ids = [job["jobId"] for job in snapshot["recentJobs"]]
        self.assertIn("job-fresh-failed", recent_ids)
        self.assertNotIn("job-recovered-old", recent_ids)

    def test_queue_snapshot_hides_placeholder_previous_session_jobs(self) -> None:
        now = time.time()
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs["job-placeholder"] = {
                "status": "completed",
                "createdAt": now,
                "updatedAt": now,
                "runtimeSessionId": "previous-session",
            }
            server.jobs["job-real"] = {
                "status": "completed",
                "createdAt": now,
                "updatedAt": now,
                "runtimeSessionId": "previous-session",
                "message": "Real job summary",
            }

        snapshot = server._queue_snapshot()
        recent_ids = [job["jobId"] for job in snapshot["recentJobs"]]
        self.assertIn("job-real", recent_ids)
        self.assertNotIn("job-placeholder", recent_ids)

    def test_friendly_job_error_fields_classify_gemini_overload(self) -> None:
        fields = server._friendly_job_error_fields(
            "Gemini request failed: This model is currently experiencing high demand. Please try again later.",
            fallback_log_path=server.SERVER_LOG_PATH,
        )
        self.assertEqual(fields["errorCategory"], "api_quota")
        self.assertIn("temporarily unavailable", fields["error"].lower())


if __name__ == "__main__":
    unittest.main()
