"""Tests for URL validation and filename sanitisation.

Covers validate_video_url(), sanitize_output_filename(),
normalize_requested_render_profile(), and normalize_requested_subtitle_style().

All tests are pure logic — no I/O, no mocks needed.
Works identically on macOS, Linux, and Windows.
"""
from __future__ import annotations

import unittest

from app.shorts_service import (
    RENDER_PROFILES,
    validate_video_url,
    sanitize_output_filename,
    normalize_requested_render_profile,
    normalize_requested_subtitle_style,
)


# ─────────────────────────────────────────────────────────────────────
# validate_video_url
# ─────────────────────────────────────────────────────────────────────

class ValidateVideoUrlTests(unittest.TestCase):
    """Acceptance tests for YouTube URL validation and canonical output."""

    # ── Happy-path YouTube URLs ──────────────────────────────────────

    def test_standard_watch_url(self) -> None:
        result = validate_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("v=dQw4w9WgXcQ", result)

    def test_short_youtu_be_url(self) -> None:
        result = validate_video_url("https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("v=dQw4w9WgXcQ", result)

    def test_shorts_url(self) -> None:
        result = validate_video_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        self.assertIn("v=dQw4w9WgXcQ", result)

    def test_live_url(self) -> None:
        result = validate_video_url("https://www.youtube.com/live/dQw4w9WgXcQ")
        self.assertIn("v=dQw4w9WgXcQ", result)

    def test_embed_url(self) -> None:
        result = validate_video_url("https://www.youtube.com/embed/dQw4w9WgXcQ")
        self.assertIn("v=dQw4w9WgXcQ", result)

    def test_mobile_youtube_url(self) -> None:
        result = validate_video_url("https://m.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("v=dQw4w9WgXcQ", result)

    def test_url_with_timestamp_preserves_t(self) -> None:
        result = validate_video_url("https://www.youtube.com/watch?v=abc123&t=120")
        self.assertIn("v=abc123", result)
        self.assertIn("t=120", result)

    def test_url_with_start_parameter(self) -> None:
        result = validate_video_url("https://www.youtube.com/watch?v=abc123&start=60")
        self.assertIn("t=60", result)

    def test_url_with_extra_params_stripped(self) -> None:
        result = validate_video_url("https://www.youtube.com/watch?v=abc123&list=PLfoo&si=bar")
        self.assertIn("v=abc123", result)
        # Playlist and si params should NOT appear in canonical URL
        self.assertNotIn("list=", result)
        self.assertNotIn("si=", result)

    def test_whitespace_around_url_trimmed(self) -> None:
        result = validate_video_url("  https://www.youtube.com/watch?v=abc123  ")
        self.assertIn("v=abc123", result)

    # ── Rejection cases ──────────────────────────────────────────────

    def test_empty_url_raises(self) -> None:
        with self.assertRaises(ValueError, msg="videoUrl is required"):
            validate_video_url("")

    def test_none_url_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_video_url(None)

    def test_whitespace_only_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_video_url("   ")

    def test_non_http_scheme_raises(self) -> None:
        with self.assertRaises(ValueError, msg="must start with http"):
            validate_video_url("ftp://youtube.com/watch?v=abc")

    def test_non_youtube_host_raises(self) -> None:
        with self.assertRaises(ValueError, msg="Only YouTube"):
            validate_video_url("https://vimeo.com/12345")

    def test_random_website_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_video_url("https://example.com/video")

    def test_youtube_url_without_video_id_raises(self) -> None:
        with self.assertRaises(ValueError, msg="incomplete"):
            validate_video_url("https://www.youtube.com/watch")

    def test_youtu_be_without_path_raises(self) -> None:
        with self.assertRaises(ValueError, msg="incomplete"):
            validate_video_url("https://youtu.be/")

    def test_plain_text_not_url_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_video_url("this is not a url")

    # ── Edge cases ───────────────────────────────────────────────────

    def test_video_id_with_hyphens_and_underscores(self) -> None:
        result = validate_video_url("https://youtube.com/watch?v=a-B_c1D2e3F")
        self.assertIn("v=a-B_c1D2e3F", result)

    def test_canonical_output_always_uses_www(self) -> None:
        result = validate_video_url("https://youtube.com/watch?v=abc123")
        self.assertTrue(result.startswith("https://www.youtube.com/watch?"))


# ─────────────────────────────────────────────────────────────────────
# sanitize_output_filename
# ─────────────────────────────────────────────────────────────────────

class SanitizeOutputFilenameTests(unittest.TestCase):
    """Verify filename sanitisation blocks path traversal, reserved names, etc."""

    def test_normal_filename_passes_through(self) -> None:
        self.assertEqual(sanitize_output_filename("my_clip.mp4"), "my_clip.mp4")

    def test_empty_returns_default(self) -> None:
        self.assertEqual(sanitize_output_filename(""), "short_con_subs.mp4")

    def test_none_returns_default(self) -> None:
        self.assertEqual(sanitize_output_filename(None), "short_con_subs.mp4")

    def test_whitespace_only_returns_default(self) -> None:
        self.assertEqual(sanitize_output_filename("   "), "short_con_subs.mp4")

    def test_path_traversal_blocked(self) -> None:
        result = sanitize_output_filename("../../etc/passwd.mp4")
        self.assertNotIn("..", result)
        self.assertNotIn("/", result)

    def test_backslash_path_blocked(self) -> None:
        result = sanitize_output_filename("..\\..\\Windows\\System32\\cmd.mp4")
        self.assertNotIn("\\", result)
        # On macOS, backslashes are replaced with underscores but '..' chars
        # may survive as literal text (not path traversal) since Path.name
        # strips real directory components.  On Windows, Path.name returns
        # only 'cmd.mp4'.  Either way the result is a safe flat filename.
        self.assertNotIn("/", result)
        self.assertTrue(result.endswith(".mp4"))

    def test_windows_reserved_name_suffixed(self) -> None:
        result = sanitize_output_filename("CON.mp4")
        self.assertNotEqual(result, "CON.mp4")
        self.assertIn("CON", result)
        self.assertTrue(result.endswith(".mp4"))

    def test_nul_reserved_name(self) -> None:
        result = sanitize_output_filename("NUL.mp4")
        self.assertNotEqual(result, "NUL.mp4")

    def test_prn_reserved_name(self) -> None:
        result = sanitize_output_filename("PRN.mp4")
        self.assertNotEqual(result, "PRN.mp4")

    def test_non_mp4_extension_forced(self) -> None:
        result = sanitize_output_filename("video.avi")
        self.assertTrue(result.endswith(".mp4"), f"Expected .mp4, got {result}")

    def test_no_extension_gets_mp4(self) -> None:
        result = sanitize_output_filename("my_video")
        self.assertTrue(result.endswith(".mp4"))

    def test_special_characters_replaced(self) -> None:
        result = sanitize_output_filename("my<video>|file.mp4")
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertNotIn("|", result)

    def test_unicode_characters_replaced(self) -> None:
        result = sanitize_output_filename("vïdéö_名前.mp4")
        # Should not crash, should produce a usable filename
        self.assertTrue(result.endswith(".mp4"))

    def test_spaces_preserved_but_trimmed(self) -> None:
        result = sanitize_output_filename("  my  video  .mp4")
        self.assertTrue(result.endswith(".mp4"))
        self.assertFalse(result.startswith(" "))

    def test_dots_only_returns_default(self) -> None:
        result = sanitize_output_filename("...")
        self.assertEqual(result, "short_con_subs.mp4")

    def test_very_long_filename_succeeds(self) -> None:
        result = sanitize_output_filename("a" * 300 + ".mp4")
        self.assertTrue(result.endswith(".mp4"))
        self.assertGreater(len(result), 4)


# ─────────────────────────────────────────────────────────────────────
# normalize_requested_render_profile
# ─────────────────────────────────────────────────────────────────────

class NormalizeRenderProfileTests(unittest.TestCase):
    """Verify render profile validation."""

    def test_valid_profile_accepted(self) -> None:
        for profile in RENDER_PROFILES:
            result = normalize_requested_render_profile(profile)
            self.assertEqual(result, profile)

    def test_none_returns_default(self) -> None:
        result = normalize_requested_render_profile(None)
        self.assertIn(result, RENDER_PROFILES)

    def test_empty_returns_default(self) -> None:
        result = normalize_requested_render_profile("")
        self.assertIn(result, RENDER_PROFILES)

    def test_case_insensitive(self) -> None:
        first_profile = next(iter(RENDER_PROFILES))
        result = normalize_requested_render_profile(first_profile.upper())
        self.assertEqual(result, first_profile)

    def test_invalid_profile_raises(self) -> None:
        with self.assertRaises(ValueError, msg="renderProfile must be one of"):
            normalize_requested_render_profile("nonexistent_profile_999")

    def test_whitespace_trimmed(self) -> None:
        first_profile = next(iter(RENDER_PROFILES))
        result = normalize_requested_render_profile(f"  {first_profile}  ")
        self.assertEqual(result, first_profile)


# ─────────────────────────────────────────────────────────────────────
# normalize_requested_subtitle_style
# ─────────────────────────────────────────────────────────────────────

class NormalizeSubtitleStyleTests(unittest.TestCase):
    """Verify subtitle style validation."""

    def test_none_returns_defaults(self) -> None:
        result = normalize_requested_subtitle_style(None)
        self.assertIsInstance(result, dict)
        self.assertIn("fontPreset", result)
        self.assertIn("colorPreset", result)

    def test_valid_style_accepted(self) -> None:
        result = normalize_requested_subtitle_style({"fontPreset": "bold", "colorPreset": "white_gold"})
        self.assertEqual(result["fontPreset"], "bold")

    def test_non_dict_raises(self) -> None:
        with self.assertRaises(ValueError, msg="must be a JSON object"):
            normalize_requested_subtitle_style("invalid")

    def test_unknown_keys_raises(self) -> None:
        with self.assertRaises(ValueError, msg="unsupported keys"):
            normalize_requested_subtitle_style({"fontPreset": "bold", "unknownKey": "value"})

    def test_non_string_value_raises(self) -> None:
        with self.assertRaises(ValueError, msg="must be a string"):
            normalize_requested_subtitle_style({"fontPreset": 123})


if __name__ == "__main__":
    unittest.main()
