"""Tests for download retry logic and transient error detection.

Extends the existing test_download_resilience.py with more thorough
coverage of the retry loop, format fallback, and backoff timing.
All yt-dlp interactions are mocked.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from app.source_pipeline import (
    _download_attempt_profiles,
    _is_transient_download_error,
    _retry_sleep_seconds,
    _format_download_quality_label,
    _summarize_download_info,
    _resolve_downloaded_video_path,
)


class TransientErrorDetectionTests(unittest.TestCase):
    """Verify the full list of transient error markers."""

    TRANSIENT_MESSAGES = [
        "can't assign requested address",
        "Failed to establish a new connection",
        "Connection reset by peer",
        "Temporary failure in name resolution",
        "Request timed out",
        "Network is unreachable",
        "Connection aborted",
    ]

    def test_all_known_transient_markers(self) -> None:
        for msg in self.TRANSIENT_MESSAGES:
            with self.subTest(msg=msg):
                self.assertTrue(
                    _is_transient_download_error(RuntimeError(msg)),
                    f"Expected transient: {msg}",
                )

    def test_non_transient_errors(self) -> None:
        non_transient = [
            "Video not found",
            "Private video",
            "HTTP Error 403: Forbidden",
            "Unsupported URL",
        ]
        for msg in non_transient:
            with self.subTest(msg=msg):
                self.assertFalse(
                    _is_transient_download_error(RuntimeError(msg)),
                    f"Expected non-transient: {msg}",
                )

    def test_case_insensitive_matching(self) -> None:
        self.assertTrue(
            _is_transient_download_error(RuntimeError("TIMED OUT waiting for response"))
        )


class RetryBackoffTests(unittest.TestCase):
    def test_first_attempt_is_1s(self) -> None:
        self.assertEqual(_retry_sleep_seconds(1), 1.0)

    def test_second_attempt_is_2s(self) -> None:
        self.assertEqual(_retry_sleep_seconds(2), 2.0)

    def test_third_attempt_is_4s(self) -> None:
        self.assertEqual(_retry_sleep_seconds(3), 4.0)

    def test_capped_at_8s(self) -> None:
        self.assertEqual(_retry_sleep_seconds(10), 8.0)
        self.assertEqual(_retry_sleep_seconds(100), 8.0)


class DownloadAttemptProfilesTests(unittest.TestCase):
    def test_at_least_three_profiles(self) -> None:
        profiles = _download_attempt_profiles()
        self.assertGreaterEqual(len(profiles), 3)

    def test_first_profile_is_primary(self) -> None:
        profiles = _download_attempt_profiles()
        self.assertEqual(profiles[0]["label"], "primary")
        self.assertIsNone(profiles[0]["source_address"])

    def test_ipv4_fallback_uses_0000(self) -> None:
        profiles = _download_attempt_profiles()
        ipv4 = [p for p in profiles if p["label"] == "ipv4-fallback"]
        self.assertEqual(len(ipv4), 1)
        self.assertEqual(ipv4[0]["source_address"], "0.0.0.0")

    def test_progressive_uses_ipv4_and_different_format(self) -> None:
        profiles = _download_attempt_profiles()
        prog = [p for p in profiles if p["label"] == "progressive-fallback"]
        self.assertEqual(len(prog), 1)
        self.assertEqual(prog[0]["source_address"], "0.0.0.0")
        # Progressive uses a simpler format than primary
        self.assertNotEqual(prog[0]["format"], profiles[0]["format"])


class DownloadInfoSummaryTests(unittest.TestCase):
    def test_empty_info(self) -> None:
        self.assertEqual(_summarize_download_info(None), {})
        self.assertEqual(_summarize_download_info({}), {})

    def test_basic_info_extraction(self) -> None:
        info = {
            "id": "abc123",
            "title": "Test Video",
            "webpage_url": "https://youtube.com/watch?v=abc123",
            "ext": "mp4",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "vcodec": "h264",
            "acodec": "aac",
        }
        summary = _summarize_download_info(info)
        self.assertEqual(summary["id"], "abc123")
        self.assertEqual(summary["width"], 1920)
        self.assertEqual(summary["height"], 1080)
        self.assertEqual(summary["videoCodec"], "h264")

    def test_requested_formats_preferred(self) -> None:
        info = {
            "requested_formats": [
                {"width": 1920, "height": 1080, "fps": 60, "vcodec": "vp9"},
                {"acodec": "opus", "abr": 128},
            ],
            "width": 640,
            "height": 360,
        }
        summary = _summarize_download_info(info)
        self.assertEqual(summary["width"], 1920)
        self.assertEqual(summary["videoCodec"], "vp9")
        self.assertEqual(summary["audioCodec"], "opus")


class DownloadQualityLabelTests(unittest.TestCase):
    def test_empty_info(self) -> None:
        self.assertIn("complete", _format_download_quality_label({}).lower())

    def test_rich_info(self) -> None:
        info = {"width": 1920, "height": 1080, "fps": 60, "videoCodec": "h264"}
        label = _format_download_quality_label(info)
        self.assertIn("1920x1080", label)
        self.assertIn("60fps", label)
        self.assertIn("h264", label)


class ResolveDownloadedVideoPathTests(unittest.TestCase):
    def test_finds_video_file(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "video"
            video_file = Path(tmpdir) / "video.mp4"
            video_file.write_bytes(b"x" * 2048)
            result = _resolve_downloaded_video_path(base)
            self.assertEqual(result, video_file)

    def test_skips_small_files(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "video"
            small_file = Path(tmpdir) / "video.part"
            small_file.write_bytes(b"x" * 100)  # Too small
            big_file = Path(tmpdir) / "video.mp4"
            big_file.write_bytes(b"x" * 2048)
            result = _resolve_downloaded_video_path(base)
            self.assertEqual(result, big_file)

    def test_raises_when_no_file_found(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "missing_video"
            with self.assertRaises(FileNotFoundError):
                _resolve_downloaded_video_path(base)


if __name__ == "__main__":
    unittest.main()
