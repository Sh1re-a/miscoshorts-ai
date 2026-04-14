"""Tests for error classification (app/errors.py).

Verifies that explain_exception() returns the right FriendlyError
for every known error pattern. Pure logic — no I/O.
"""
from __future__ import annotations

import unittest

from app.errors import FriendlyError, explain_exception


class ExplainExceptionTests(unittest.TestCase):
    """Verify error classification produces user-friendly messages."""

    # ── Gemini / API key ─────────────────────────────────────────────

    def test_gemini_key_missing(self) -> None:
        err = explain_exception(ValueError("GEMINI_API_KEY was not found"))
        self.assertEqual(err.category, "config")
        self.assertIn("missing", err.summary.lower())

    def test_gemini_key_invalid(self) -> None:
        err = explain_exception(Exception("API key not valid. Please pass a valid API key."))
        self.assertEqual(err.category, "api_key")
        self.assertIn("rejected", err.summary.lower())

    def test_gemini_quota_exceeded(self) -> None:
        err = explain_exception(Exception("Resource has been exhausted (429 rate limit)"))
        self.assertEqual(err.category, "api_quota")

    # ── Dependencies ─────────────────────────────────────────────────

    def test_ffmpeg_missing(self) -> None:
        err = explain_exception(FileNotFoundError("ffmpeg is not installed on this system"))
        self.assertEqual(err.category, "dependency")
        self.assertIn("FFmpeg", err.summary)

    def test_faster_whisper_missing(self) -> None:
        err = explain_exception(ImportError("The faster-whisper package is missing"))
        self.assertEqual(err.category, "dependency")
        self.assertIn("speech engine", err.summary.lower())

    def test_whisper_transcription_failed(self) -> None:
        err = explain_exception(RuntimeError("Whisper transcription failed after 3 retries"))
        self.assertEqual(err.category, "speech_model")

    # ── File system ──────────────────────────────────────────────────

    def test_permission_denied(self) -> None:
        err = explain_exception(PermissionError("Permission denied: '/opt/data/output'"))
        self.assertEqual(err.category, "permissions")
        self.assertIn("writable folder", err.hint.lower())

    def test_disk_full(self) -> None:
        err = explain_exception(OSError("No space left on device"))
        self.assertEqual(err.category, "disk_space")

    def test_insufficient_disk_space(self) -> None:
        err = explain_exception(RuntimeError("Insufficient free disk space for this render"))
        self.assertEqual(err.category, "disk_space")

    # ── Input validation ─────────────────────────────────────────────

    def test_invalid_youtube_url(self) -> None:
        err = explain_exception(ValueError("Only YouTube video URLs are supported"))
        self.assertEqual(err.category, "input")
        self.assertIn("YouTube", err.summary)

    def test_incomplete_youtube_url(self) -> None:
        err = explain_exception(ValueError("The YouTube URL appears to be incomplete"))
        self.assertEqual(err.category, "input")

    # ── Download ─────────────────────────────────────────────────────

    def test_download_no_video_file(self) -> None:
        err = explain_exception(FileNotFoundError("yt-dlp completed without producing a video file"))
        self.assertEqual(err.category, "download")

    def test_download_network_error(self) -> None:
        err = explain_exception(Exception("Can't assign requested address"))
        self.assertEqual(err.category, "download_network")

    def test_connection_reset(self) -> None:
        err = explain_exception(Exception("Failed to establish a new connection"))
        self.assertEqual(err.category, "download_network")

    # ── Concurrency ─────────────────────────────────────────────────

    def test_lock_timeout(self) -> None:
        err = explain_exception(TimeoutError("Timed out while waiting for another identical render to finish"))
        self.assertEqual(err.category, "concurrency")

    # ── Frontend ─────────────────────────────────────────────────────

    def test_frontend_not_built(self) -> None:
        err = explain_exception(RuntimeError("Frontend not built"))
        self.assertEqual(err.category, "frontend")

    # ── Subtitles ────────────────────────────────────────────────────

    def test_subtitle_compatibility(self) -> None:
        err = explain_exception(RuntimeError("Subtitle compatibility check failed"))
        self.assertEqual(err.category, "subtitles")

    # ── Unknown fallback ─────────────────────────────────────────────

    def test_unknown_error_returns_category_unknown(self) -> None:
        err = explain_exception(RuntimeError("Something completely unexpected happened"))
        self.assertEqual(err.category, "unknown")
        self.assertIn("Something completely unexpected", err.summary)

    def test_unknown_error_has_log_hint(self) -> None:
        err = explain_exception(RuntimeError("Random crash"))
        self.assertIn("log", err.hint.lower())

    def test_empty_error_message_uses_class_name(self) -> None:
        err = explain_exception(ValueError(""))
        self.assertEqual(err.summary, "ValueError")

    # ── FriendlyError structure ──────────────────────────────────────

    def test_friendly_error_is_immutable(self) -> None:
        err = FriendlyError(category="test", summary="test", hint="test")
        with self.assertRaises(AttributeError):
            err.category = "changed"

    def test_friendly_error_hint_optional(self) -> None:
        err = FriendlyError(category="test", summary="test")
        self.assertIsNone(err.hint)


if __name__ == "__main__":
    unittest.main()
