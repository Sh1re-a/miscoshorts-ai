from __future__ import annotations


def _slice_segment_words(words: list[dict], clip_start_time: float, clip_end_time: float) -> list[dict]:
    clip_duration = max(0.0, clip_end_time - clip_start_time)
    sliced_words: list[dict] = []

    for word in words:
        raw_text = (word.get("word") or "").strip()
        if not raw_text:
            continue

        try:
            word_start = float(word.get("start"))
            word_end = float(word.get("end"))
        except (TypeError, ValueError):
            continue

        if word_end <= clip_start_time or word_start >= clip_end_time:
            continue

        relative_start = max(0.0, word_start - clip_start_time)
        relative_end = min(clip_duration, word_end - clip_start_time)
        if relative_end <= relative_start:
            relative_end = min(clip_duration, relative_start + 0.12)

        sliced_words.append(
            {
                "word": raw_text,
                "start": relative_start,
                "end": relative_end,
            }
        )

    return sliced_words


def extract_clip_transcript_from_segments(segments: list[dict], clip_start_time: float, clip_end_time: float) -> tuple[dict, bool]:
    clip_duration = max(0.0, clip_end_time - clip_start_time)
    clip_segments: list[dict] = []
    transcript_text_parts: list[str] = []
    requires_precise_fallback = False

    for raw_segment in segments:
        raw_start = raw_segment.get("start")
        raw_end = raw_segment.get("end")
        if raw_start is None or raw_end is None:
            continue

        try:
            segment_start = float(raw_start)
            segment_end = float(raw_end)
        except (TypeError, ValueError):
            continue

        if segment_end <= clip_start_time or segment_start >= clip_end_time:
            continue

        relative_start = max(0.0, segment_start - clip_start_time)
        relative_end = min(clip_duration, segment_end - clip_start_time)
        if relative_end <= relative_start:
            continue

        sliced_words = _slice_segment_words(raw_segment.get("words") or [], clip_start_time, clip_end_time)
        if sliced_words:
            segment_text = " ".join(word["word"] for word in sliced_words)
        else:
            segment_text = (raw_segment.get("text") or "").strip()
            if segment_start < clip_start_time or segment_end > clip_end_time:
                requires_precise_fallback = True

        if not segment_text and not sliced_words:
            continue

        clipped_segment = {
            "start": relative_start,
            "end": relative_end,
            "text": segment_text,
        }
        if sliced_words:
            clipped_segment["words"] = sliced_words

        clip_segments.append(clipped_segment)
        if segment_text:
            transcript_text_parts.append(segment_text)

    return {
        "text": " ".join(transcript_text_parts).strip(),
        "segments": clip_segments,
    }, requires_precise_fallback
