from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class CacheVersioningTests(unittest.TestCase):
    def test_job_fingerprint_changes_with_pipeline_signature(self) -> None:
        from app import render_session

        kwargs = dict(
            video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            output_filename="clip.mp4",
            clip_count=3,
            render_profile="studio",
            subtitle_style={"fontPreset": "soft", "colorPreset": "editorial"},
        )

        with patch("app.render_session.pipeline_compat_signature", return_value="sig-a"):
            first = render_session.job_fingerprint(**kwargs)
        with patch("app.render_session.pipeline_compat_signature", return_value="sig-b"):
            second = render_session.job_fingerprint(**kwargs)

        self.assertNotEqual(first, second)

    def test_cache_dir_is_namespaced_by_pipeline_signature(self) -> None:
        from app import media_cache

        with patch("app.media_cache.pipeline_compat_signature", return_value="compat-a"):
            first = media_cache.cache_dir_for_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        with patch("app.media_cache.pipeline_compat_signature", return_value="compat-b"):
            second = media_cache.cache_dir_for_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        self.assertNotEqual(first, second)
        self.assertIn("compat-a", str(first))
        self.assertIn("compat-b", str(second))


if __name__ == "__main__":
    unittest.main()
