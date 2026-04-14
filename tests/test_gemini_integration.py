"""Tests for Gemini API integration (app/gemini_analyzer.py).

All Google API calls are mocked. No real network traffic.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

import app.gemini_analyzer as gemini_analyzer
from app.gemini_analyzer import get_gemini_api_key, find_viral_clips


class GetGeminiApiKeyTests(unittest.TestCase):
    """Validate API key resolution from params and environment."""

    def test_explicit_key_returned(self) -> None:
        self.assertEqual(get_gemini_api_key("my-key-123"), "my-key-123")

    def test_env_key_returned_when_no_param(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key-456"}):
            self.assertEqual(get_gemini_api_key(), "env-key-456")

    def test_explicit_key_wins_over_env(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "env-key"}):
            self.assertEqual(get_gemini_api_key("explicit-key"), "explicit-key")

    def test_no_key_raises_value_error(self) -> None:
        saved = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with self.assertRaises(ValueError, msg="GEMINI_API_KEY"):
                get_gemini_api_key("")
        finally:
            if saved is not None:
                os.environ["GEMINI_API_KEY"] = saved

    def test_whitespace_key_raises(self) -> None:
        saved = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                get_gemini_api_key("   ")
        finally:
            if saved is not None:
                os.environ["GEMINI_API_KEY"] = saved

    def test_none_with_no_env_raises(self) -> None:
        saved = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                get_gemini_api_key(None)
        finally:
            if saved is not None:
                os.environ["GEMINI_API_KEY"] = saved


class FindViralClipsTests(unittest.TestCase):
    """Test Gemini clip selection with mocked API responses."""

    _SAMPLE_SEGMENTS = [
        {"start": 0.0, "end": 30.0, "text": "Welcome to our show today."},
        {"start": 30.0, "end": 60.0, "text": "I never expected this to happen."},
        {"start": 60.0, "end": 120.0, "text": "Here is the shocking truth about AI."},
    ]

    @patch("app.gemini_analyzer.genai")
    def test_successful_clip_selection(self, mock_genai) -> None:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = """CLIP 1:
Title: The Shocking Truth
Reason: Strong hook with surprising reveal
Start: 30.0
End: 60.0

CLIP 2:
Title: Unexpected AI Story
Reason: Emotional engagement
Start: 60.0
End: 120.0
"""
        mock_client.models.generate_content.return_value = mock_response
        result = find_viral_clips(self._SAMPLE_SEGMENTS, api_key="test-key", clip_count=2)
        self.assertIsNotNone(result)
        mock_client.close.assert_called()

    @patch("app.gemini_analyzer.genai")
    def test_empty_response_retries(self, mock_genai) -> None:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        empty_response = MagicMock()
        empty_response.text = ""

        valid_response = MagicMock()
        valid_response.text = """CLIP 1:
Title: The Shocking Truth
Reason: Strong hook
Start: 30.0
End: 60.0
"""
        mock_client.models.generate_content.side_effect = [empty_response, valid_response]
        result = find_viral_clips(self._SAMPLE_SEGMENTS, api_key="test-key", clip_count=1)
        # Should have retried after empty response
        self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("app.gemini_analyzer.time.sleep")
    @patch("app.gemini_analyzer.genai")
    def test_api_error_retries_with_backoff(self, mock_genai, mock_sleep) -> None:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        from google.genai import errors as genai_errors
        api_error = genai_errors.APIError(
            code=429,
            response_json={"error": {"message": "Resource exhausted"}},
        )
        mock_client.models.generate_content.side_effect = api_error

        with self.assertRaises(RuntimeError):
            find_viral_clips(self._SAMPLE_SEGMENTS, api_key="test-key", clip_count=1)

        self.assertEqual(mock_client.models.generate_content.call_count, gemini_analyzer._GEMINI_MAX_RETRIES)
        self.assertEqual(mock_sleep.call_count, gemini_analyzer._GEMINI_MAX_RETRIES - 1)

    @patch("app.gemini_analyzer.time.sleep")
    @patch("app.gemini_analyzer.genai")
    def test_high_demand_error_emits_retry_progress(self, mock_genai, mock_sleep) -> None:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        from google.genai import errors as genai_errors
        busy_error = genai_errors.APIError(
            code=429,
            response_json={"error": {"message": "This model is currently experiencing high demand. Please try again later."}},
        )
        valid_response = MagicMock()
        valid_response.text = """CLIP 1:
Title: Test
Reason: Test
Start: 0.0
End: 30.0
"""
        mock_client.models.generate_content.side_effect = [busy_error, valid_response]
        progress = MagicMock()

        result = find_viral_clips(self._SAMPLE_SEGMENTS, api_key="test-key", clip_count=1, progress_callback=progress)

        self.assertIn("CLIP 1", result)
        self.assertEqual(mock_client.models.generate_content.call_count, 2)
        progress.assert_any_call("analyzing", unittest.mock.ANY)

    @patch("app.gemini_analyzer.genai")
    def test_client_closed_on_success(self, mock_genai) -> None:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = """CLIP 1:
Title: Test
Reason: Test
Start: 0.0
End: 30.0
"""
        mock_client.models.generate_content.return_value = mock_response
        find_viral_clips(self._SAMPLE_SEGMENTS, api_key="test-key", clip_count=1)
        mock_client.close.assert_called()

    @patch("app.gemini_analyzer.genai")
    def test_client_closed_on_failure(self, mock_genai) -> None:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.side_effect = Exception("API error")

        with self.assertRaises(Exception):
            find_viral_clips(self._SAMPLE_SEGMENTS, api_key="test-key", clip_count=1)

        mock_client.close.assert_called()


if __name__ == "__main__":
    unittest.main()
