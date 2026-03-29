from __future__ import annotations

import unittest

import numpy as np
from moviepy import ColorClip

from app import subtitles


class SubtitleLifecycleTests(unittest.TestCase):
    def test_word_highlight_clears_when_no_word_window_is_active(self) -> None:
        cue = {
            "start": 0.0,
            "end": 0.8,
            "highlightIndex": 1,
            "wordEntries": [
                {"text": "HE'S", "start": 0.0, "end": 0.14},
                {"text": "DEAD.", "start": 0.14, "end": 0.32},
            ],
        }

        self.assertEqual(subtitles._resolve_active_index_for_time(cue, 0.05), 0)
        self.assertEqual(subtitles._resolve_active_index_for_time(cue, 0.2), 1)
        self.assertEqual(subtitles._resolve_active_index_for_time(cue, 0.5), -1)

    def test_each_cue_renders_its_own_text_and_clears_after_end(self) -> None:
        video_clip = ColorClip((360, 640), color=(18, 24, 32)).with_duration(1.3)
        whisper_segments = [
            {
                "start": 0.0,
                "end": 0.36,
                "text": "He's dead.",
                "words": [
                    {"word": "He's", "start": 0.0, "end": 0.14},
                    {"word": "dead.", "start": 0.14, "end": 0.36},
                ],
            },
            {
                "start": 0.58,
                "end": 0.96,
                "text": "Move on.",
                "words": [
                    {"word": "Move", "start": 0.58, "end": 0.76},
                    {"word": "on.", "start": 0.76, "end": 0.96},
                ],
            },
        ]

        with_subtitles = subtitles.create_subtitles(video_clip, whisper_segments, 0.0)
        without_subtitles = subtitles.create_subtitles(video_clip, [], 0.0)

        frame_first = with_subtitles.get_frame(0.2)
        frame_gap = with_subtitles.get_frame(0.48)
        frame_second = with_subtitles.get_frame(0.75)
        frame_after = with_subtitles.get_frame(1.15)
        baseline_gap = without_subtitles.get_frame(0.48)
        baseline_second = without_subtitles.get_frame(0.75)
        baseline_after = without_subtitles.get_frame(1.15)

        self.assertGreater(np.abs(frame_first.astype(np.int16) - baseline_second.astype(np.int16)).sum(), 0)
        self.assertEqual(int(np.abs(frame_gap.astype(np.int16) - baseline_gap.astype(np.int16)).sum()), 0)
        self.assertGreater(np.abs(frame_second.astype(np.int16) - baseline_second.astype(np.int16)).sum(), 0)
        self.assertGreater(np.abs(frame_first.astype(np.int16) - frame_second.astype(np.int16)).sum(), 0)
        self.assertEqual(int(np.abs(frame_after.astype(np.int16) - baseline_after.astype(np.int16)).sum()), 0)

        with_subtitles.close()
        without_subtitles.close()
        video_clip.close()

    def test_last_word_highlight_does_not_stick_through_cue_tail(self) -> None:
        video_clip = ColorClip((360, 640), color=(18, 24, 32)).with_duration(1.0)
        cue = {
            "start": 0.0,
            "end": 0.8,
            "text": "HE'S DEAD.",
            "words": ["HE'S", "DEAD."],
            "highlightIndex": 1,
            "wordEntries": [
                {"text": "HE'S", "start": 0.0, "end": 0.14},
                {"text": "DEAD.", "start": 0.14, "end": 0.32},
            ],
        }
        resolved_style = subtitles.normalize_subtitle_style(None)
        prepared_runtime = {
            "resolvedStyle": resolved_style,
            "videoDuration": 1.0,
            "headerDuration": 0.0,
            "subtitleCues": [cue],
            "cueLayouts": subtitles._prepare_subtitle_runtime(video_clip, [cue], resolved_style),
        }

        with_subtitles = subtitles.create_subtitles(video_clip, [], 0.0, prepared_runtime=prepared_runtime)
        active_word_frame = with_subtitles.get_frame(0.2)
        inactive_tail_frame = with_subtitles.get_frame(0.5)

        self.assertGreater(int(np.abs(active_word_frame.astype(np.int16) - inactive_tail_frame.astype(np.int16)).sum()), 0)

        with_subtitles.close()
        video_clip.close()


if __name__ == "__main__":
    unittest.main()
