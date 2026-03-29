from __future__ import annotations

import unittest

from app.errors import explain_exception
from app.source_pipeline import _download_attempt_profiles, _is_transient_download_error


class DownloadResilienceTests(unittest.TestCase):
    def test_transient_download_error_detection_matches_errno_49_socket_failure(self) -> None:
        error = RuntimeError(
            "HTTPSConnection(host='rr5---sn-jvhixh-5gor.googlevideo.com', port=443): Failed to establish a new connection: [Errno 49] Can't assign requested address"
        )
        self.assertTrue(_is_transient_download_error(error))

    def test_download_attempt_profiles_end_with_progressive_ipv4_fallback(self) -> None:
        profiles = _download_attempt_profiles()
        self.assertGreaterEqual(len(profiles), 3)
        self.assertEqual(profiles[-1]["label"], "progressive-fallback")
        self.assertEqual(profiles[-1]["source_address"], "0.0.0.0")

    def test_friendly_error_for_socket_assignment_failure(self) -> None:
        friendly = explain_exception(
            RuntimeError(
                "ERROR: [download] Got error: HTTPSConnection(host='example', port=443): Failed to establish a new connection: [Errno 49] Can't assign requested address"
            )
        )
        self.assertEqual(friendly.category, "download_network")


if __name__ == "__main__":
    unittest.main()
