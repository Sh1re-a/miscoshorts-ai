"""Tests for the /api/process job submission endpoint.

Exercises input validation, queue-full rejection, doctor blocking,
and successful job creation.  All I/O and background threads are mocked.
"""
from __future__ import annotations

import copy
import time
import unittest
from unittest.mock import patch, MagicMock

from app import server


class _SubmissionTestBase(unittest.TestCase):
    """Shared setup/teardown for submission tests."""

    def setUp(self) -> None:
        self.client = server.app.test_client()
        with server.jobs_lock:
            self._saved_jobs = copy.deepcopy(server.jobs)
        self._saved_active = server._active_jobs

    def tearDown(self) -> None:
        with server.jobs_lock:
            server.jobs.clear()
            server.jobs.update(self._saved_jobs)
        server._active_jobs = self._saved_active

    def _valid_payload(self, **overrides) -> dict:
        payload = {
            "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "apiKey": "test-key-1234",
            "outputFilename": "test.mp4",
            "clipCount": 3,
        }
        payload.update(overrides)
        return payload


class SubmissionValidationTests(_SubmissionTestBase):
    """Input validation for /api/process."""

    @patch("app.server.run_doctor")
    def test_missing_api_key_and_no_env_returns_400(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        with patch.dict("os.environ", {}, clear=False):
            # Remove GEMINI_API_KEY from env if present
            import os
            saved = os.environ.pop("GEMINI_API_KEY", None)
            try:
                res = self.client.post("/api/process", json=self._valid_payload(apiKey=""))
                self.assertEqual(res.status_code, 400)
                self.assertIn("API key", res.get_json()["error"])
            finally:
                if saved is not None:
                    os.environ["GEMINI_API_KEY"] = saved

    @patch("app.server.run_doctor")
    def test_empty_video_url_returns_400(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        res = self.client.post("/api/process", json=self._valid_payload(videoUrl=""))
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    @patch("app.server.run_doctor")
    def test_non_youtube_url_returns_400(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        res = self.client.post("/api/process", json=self._valid_payload(videoUrl="https://vimeo.com/12345"))
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("YouTube", data["error"])

    @patch("app.server.run_doctor")
    def test_invalid_clip_count_returns_400(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        res = self.client.post("/api/process", json=self._valid_payload(clipCount="not_a_number"))
        self.assertEqual(res.status_code, 400)
        self.assertIn("clipCount", res.get_json()["error"])

    @patch("app.server.run_doctor")
    def test_clip_count_clamped_to_range(self, mock_doctor) -> None:
        """clipCount=10 should be clamped to 5, clipCount=0 to 1."""
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        with patch("app.server.threading") as mock_threading:
            mock_threading.Thread.return_value = MagicMock()
            res = self.client.post("/api/process", json=self._valid_payload(clipCount=10))
            self.assertEqual(res.status_code, 202)
            data = res.get_json()
            self.assertLessEqual(data["clipCount"], 5)

    @patch("app.server.run_doctor")
    def test_negative_clip_count_clamped_to_1(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        with patch("app.server.threading") as mock_threading:
            mock_threading.Thread.return_value = MagicMock()
            res = self.client.post("/api/process", json=self._valid_payload(clipCount=-5))
            self.assertEqual(res.status_code, 202)
            data = res.get_json()
            self.assertGreaterEqual(data["clipCount"], 1)

    @patch("app.server.run_doctor")
    def test_invalid_render_profile_returns_400(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        res = self.client.post("/api/process", json=self._valid_payload(renderProfile="ultra_4k_fantasy"))
        self.assertEqual(res.status_code, 400)
        self.assertIn("renderProfile", res.get_json()["error"])

    @patch("app.server.run_doctor")
    def test_invalid_subtitle_style_returns_400(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        res = self.client.post("/api/process", json=self._valid_payload(subtitleStyle="not_a_dict"))
        self.assertEqual(res.status_code, 400)


class SubmissionDoctorBlockTests(_SubmissionTestBase):
    """Doctor-blocking conditions should prevent job submission."""

    @patch("app.server.run_doctor")
    def test_blocking_doctor_check_returns_503(self, mock_doctor) -> None:
        mock_doctor.return_value = {
            "status": "FAIL",
            "blockingChecks": [
                {"name": "ffmpeg", "message": "FFmpeg is not installed", "blocks_rendering": True}
            ],
            "checks": [],
        }
        res = self.client.post("/api/process", json=self._valid_payload())
        self.assertEqual(res.status_code, 503)
        data = res.get_json()
        self.assertIn("error", data)
        self.assertIn("blockingChecks", data)
        self.assertIn("not ready", data["error"])

    @patch("app.server.run_doctor")
    def test_blocking_checks_from_checks_array_fallback(self, mock_doctor) -> None:
        mock_doctor.return_value = {
            "status": "FAIL",
            "checks": [
                {"name": "ffmpeg", "message": "FFmpeg missing", "blocks_rendering": True, "status": "FAIL"}
            ],
        }
        res = self.client.post("/api/process", json=self._valid_payload())
        self.assertEqual(res.status_code, 503)


class SubmissionQueueTests(_SubmissionTestBase):
    """Queue-full scenarios."""

    @patch("app.server.run_doctor")
    def test_queue_full_returns_503(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        # Fill queue
        with server.jobs_lock:
            for i in range(server.MAX_CONCURRENT_JOBS):
                server.jobs[f"active-{i}"] = {"status": "rendering", "createdAt": time.time(), "updatedAt": time.time()}
            for i in range(server.MAX_QUEUED_JOBS):
                server.jobs[f"queued-{i}"] = {"status": "queued", "createdAt": time.time(), "updatedAt": time.time()}
            server._active_jobs = list(server.jobs.keys())

        res = self.client.post("/api/process", json=self._valid_payload())
        self.assertEqual(res.status_code, 503)
        data = res.get_json()
        self.assertIn("queue is full", data["error"])


class SubmissionSuccessTests(_SubmissionTestBase):
    """Successful job creation."""

    @patch("app.server.run_doctor")
    def test_valid_submission_returns_202_with_job_id(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        with patch("app.server.threading") as mock_threading:
            mock_threading.Thread.return_value = MagicMock()
            res = self.client.post("/api/process", json=self._valid_payload())
            self.assertEqual(res.status_code, 202)
            data = res.get_json()
            self.assertIn("jobId", data)
            self.assertEqual(data["status"], "queued")
            self.assertIn("jobFingerprint", data)
            self.assertIn("runtimeSessionId", data)

    @patch("app.server.run_doctor")
    def test_submission_creates_job_in_memory(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        with patch("app.server.threading") as mock_threading:
            mock_threading.Thread.return_value = MagicMock()
            res = self.client.post("/api/process", json=self._valid_payload())
            job_id = res.get_json()["jobId"]
            # Verify job exists in server memory
            job = server._get_job(job_id)
            self.assertIsNotNone(job)
            self.assertEqual(job["status"], "queued")

    @patch("app.server.run_doctor")
    def test_submission_spawns_worker_thread(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        with patch("app.server.threading") as mock_threading:
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread
            self.client.post("/api/process", json=self._valid_payload())
            mock_threading.Thread.assert_called_once()
            mock_thread.start.assert_called_once()


class RetryJobTests(_SubmissionTestBase):
    @patch("app.server.run_doctor")
    def test_retry_failed_job_returns_new_job_id(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        with server.jobs_lock:
            server.jobs["failed-job"] = {
                "status": "failed",
                "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "outputFilename": "retry.mp4",
                "clipCount": 2,
                "renderProfile": "studio",
                "subtitleStyle": {"fontPreset": "soft", "colorPreset": "editorial"},
                "createdAt": time.time(),
                "updatedAt": time.time(),
            }
        with patch("app.server.threading") as mock_threading:
            mock_threading.Thread.return_value = MagicMock()
            res = self.client.post("/api/jobs/failed-job/retry", json={"apiKey": "test-key-1234"})

        self.assertEqual(res.status_code, 202)
        data = res.get_json()
        self.assertIn("jobId", data)
        self.assertNotEqual(data["jobId"], "failed-job")
        self.assertEqual(data["retriedFromJobId"], "failed-job")

    def test_retry_non_failed_job_returns_400(self) -> None:
        with server.jobs_lock:
            server.jobs["queued-job"] = {
                "status": "queued",
                "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "createdAt": time.time(),
                "updatedAt": time.time(),
            }

        res = self.client.post("/api/jobs/queued-job/retry", json={"apiKey": "test-key-1234"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Only failed jobs", res.get_json()["error"])

    def test_retry_missing_job_returns_404(self) -> None:
        res = self.client.post("/api/jobs/missing-job/retry", json={"apiKey": "test-key-1234"})
        self.assertEqual(res.status_code, 404)

    @patch("app.server.run_doctor")
    def test_retry_analysis_failed_job_sets_retry_mode(self, mock_doctor) -> None:
        mock_doctor.return_value = {"status": "PASS", "blockingChecks": [], "checks": []}
        with server.jobs_lock:
            server.jobs["failed-analysis-job"] = {
                "status": "failed",
                "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "outputFilename": "retry-analysis.mp4",
                "clipCount": 1,
                "renderProfile": "balanced",
                "subtitleStyle": {"fontPreset": "soft", "colorPreset": "editorial"},
                "createdAt": time.time(),
                "updatedAt": time.time(),
            }
        with patch("app.server.threading") as mock_threading:
            mock_threading.Thread.return_value = MagicMock()
            res = self.client.post("/api/jobs/failed-analysis-job/retry-analysis", json={"apiKey": "test-key-1234"})

        self.assertEqual(res.status_code, 202)
        data = res.get_json()
        self.assertEqual(data["retriedFromJobId"], "failed-analysis-job")
        self.assertEqual(data["retryMode"], "analysis")


class GetJobEndpointTests(_SubmissionTestBase):
    """GET /api/jobs/<id> response shape tests."""

    def test_unknown_job_returns_404(self) -> None:
        res = self.client.get("/api/jobs/nonexistent-job-id")
        self.assertEqual(res.status_code, 404)
        data = res.get_json()
        self.assertIn("error", data)

    def test_queued_job_returns_200(self) -> None:
        with server.jobs_lock:
            server.jobs["test-q"] = {
                "status": "queued",
                "createdAt": time.time(),
                "updatedAt": time.time(),
                "clipCount": 3,
            }
            server._active_jobs = list(server.jobs.keys())
        try:
            res = self.client.get("/api/jobs/test-q")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["status"], "queued")
        finally:
            with server.jobs_lock:
                server.jobs.pop("test-q", None)

    def test_completed_job_includes_result(self) -> None:
        with server.jobs_lock:
            server.jobs["test-c"] = {
                "status": "completed",
                "createdAt": time.time(),
                "updatedAt": time.time(),
                "result": {"outputDir": "/tmp/test", "clips": [], "clipCount": 2},
            }
            server._active_jobs = list(server.jobs.keys())
        try:
            res = self.client.get("/api/jobs/test-c")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["status"], "completed")
            self.assertIn("result", data)
        finally:
            with server.jobs_lock:
                server.jobs.pop("test-c", None)

    def test_failed_job_includes_error_and_help(self) -> None:
        with server.jobs_lock:
            server.jobs["test-f"] = {
                "status": "failed",
                "error": "Something went wrong",
                "errorHelp": "Try again",
                "errorCategory": "unknown",
                "createdAt": time.time(),
                "updatedAt": time.time(),
            }
            server._active_jobs = list(server.jobs.keys())
        try:
            res = self.client.get("/api/jobs/test-f")
            data = res.get_json()
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["error"], "Something went wrong")
            self.assertEqual(data["errorHelp"], "Try again")
        finally:
            with server.jobs_lock:
                server.jobs.pop("test-f", None)


if __name__ == "__main__":
    unittest.main()
