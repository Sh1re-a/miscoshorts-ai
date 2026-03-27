import functools
import os
import platform
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import numpy as np
from moviepy import CompositeVideoClip, ImageClip, VideoClip
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont


FONT_PRESETS = {
    "clean": [
        "Satoshi-Black",
        "Satoshi-Bold",
        "TheBoldFont",
        "Inter ExtraBold",
        "Inter-Bold",
        "Montserrat ExtraBold",
        "Montserrat-Bold",
        "SF Pro Display Semibold",
        "Avenir Next Bold",
        "Avenir Next Demi Bold",
        "Helvetica Neue Bold",
        "Helvetica Neue Medium",
        "Aptos Display Bold",
        "Segoe UI Bold",
        "Segoe UI Semibold",
        "Arial-Bold",
        "Helvetica-Bold",
        "NotoSans-Bold",
        "DejaVuSans-Bold",
    ],
    "bold": [
        "Satoshi-Black",
        "TheBoldFont",
        "Inter ExtraBold",
        "Inter-Bold",
        "Montserrat ExtraBold",
        "Montserrat-Bold",
        "SF Pro Display Bold",
        "Avenir Next Heavy",
        "Avenir Next Bold",
        "Aptos Display Bold",
        "Bahnschrift SemiBold",
        "Segoe UI Bold",
        "Arial-Bold",
        "Impact",
        "Helvetica-Bold",
        "NotoSans-Bold",
        "DejaVuSans-Bold",
    ],
    "soft": [
        "Satoshi-Bold",
        "Inter-Bold",
        "Montserrat-Bold",
        "Raleway-Bold",
        "SF Pro Rounded Semibold",
        "Avenir Next Medium",
        "Avenir Next Demi Bold",
        "Helvetica Neue Medium",
        "Aptos Bold",
        "TrebuchetMS-Bold",
        "Gill Sans Bold",
        "Calibri",
        "Segoe UI Semibold",
        "Arial-Bold",
        "NotoSans-Bold",
        "DejaVuSans-Bold",
    ],
}

COLOR_PRESETS = {
    "editorial": {"base_color": "#f6f1e8", "active_color": "#d8c5a2", "stroke_color": "#111318"},
    "sun": {"base_color": "#ffffff", "active_color": "#ffd700", "stroke_color": "#000000"},
    "ivory": {"base_color": "#ffffff", "active_color": "#ffd700", "stroke_color": "#000000"},
    "mint": {"base_color": "#ffffff", "active_color": "#ffd700", "stroke_color": "#000000"},
}

HEADER_COLOR = "#f6f1e8"
HEADER_REASON_COLOR = "#cec2b1"
HEADER_PANEL_FILL = (10, 12, 16, 184)
HEADER_PANEL_BORDER = (255, 255, 255, 35)
SUBTITLE_SHADOW_ALPHA = 122
SUBTITLE_SHADOW_COLOR = (0, 0, 0, SUBTITLE_SHADOW_ALPHA)
SUBTITLE_INACTIVE_ALPHA = 212
GRADIENT_TOP_ALPHA = 62
GRADIENT_BOTTOM_ALPHA = 88

DEFAULT_STYLE = {
    "fontPreset": "soft",
    "colorPreset": "editorial",
}

FILLER_WORDS = {
    "uh",
    "um",
    "erm",
    "hmm",
    "mm",
}

LOW_VALUE_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "for",
    "from",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "so",
    "that",
    "the",
    "their",
    "there",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "you",
    "your",
}

TOP_OVERLAY_MIN_DURATION = 2.4
TOP_OVERLAY_MAX_DURATION = 4.2
WORD_HIGHLIGHT_LEAD = 0.05
WORD_HIGHLIGHT_TAIL = 0.08
SUBTITLE_Y_RATIO = 0.75
SUBTITLE_SAFE_WIDTH_RATIO = 0.85
SUBTITLE_MAX_HEIGHT_RATIO = 0.24
SUBTITLE_BASE_FONT_RATIO = 76 / 1920
SUBTITLE_MIN_FONT_SIZE = 60
SUBTITLE_MAX_FONT_SIZE = 82
ACTIVE_WORD_SCALE = 1.04
ACTIVE_WORD_SETTLE_SCALE = 1.015
HIGHLIGHT_TRANSITION_DURATION = 0.06
ENABLE_TOP_DESCRIPTION_OVERLAY = False
SUBTITLE_HORIZONTAL_MARGIN_RATIO = 0.1
SUBTITLE_VERTICAL_MARGIN_RATIO = 0.1
SUBTITLE_MAX_LINES = 2
SUBTITLE_TEXT_PADDING_X = 5
SUBTITLE_TEXT_PADDING_Y = 5
SUBTITLE_MIN_FONT_RATIO = 0.026
SUBTITLE_MAX_FONT_RATIO = 0.034
HEADER_SAFE_WIDTH_RATIO = 0.9
HEADER_TOP_RATIO = 0.1
HEADER_PANEL_PADDING_X = 30
HEADER_PANEL_PADDING_Y = 16
HEADER_PANEL_GAP = 12
HEADER_PANEL_RADIUS = 28
HEADER_TITLE_FONT_RATIO = 0.036
HEADER_REASON_FONT_RATIO = 0.018
HEADER_TITLE_MIN_RATIO = 0.030
HEADER_TITLE_MAX_RATIO = 0.044
HEADER_REASON_MIN_RATIO = 0.013
HEADER_REASON_MAX_RATIO = 0.020
SUBTITLE_FADE_IN = 0.14
SUBTITLE_FADE_OUT = 0.14
HEADER_FADE_IN = 0.5
HEADER_FADE_OUT = 0.5
HEADER_DURATION = 2.6
HEADER_PANEL_MIN_HEIGHT = 126

TITLE_FONT_PRESETS = [
    "Satoshi-Black",
    "Satoshi-Bold",
    "TheBoldFont",
    "Inter ExtraBold",
    "Inter-Bold",
    "Montserrat ExtraBold",
    "Montserrat-Bold",
    "SF Pro Display Semibold",
    "Avenir Next Bold",
    "Avenir Next Demi Bold",
    "Helvetica Neue Medium",
    "Aptos Display Bold",
    "Arial-Bold",
    "Helvetica-Bold",
    "DejaVuSans-Bold",
]

AUTO_FONT = "__AUTO_DEFAULT_FONT__"
SUBTITLE_LETTER_SPACING = -1
HEADER_TITLE_LETTER_SPACING = 2
HEADER_REASON_LETTER_SPACING = 0


def _existing_font_paths(*paths):
    return [path for path in paths if os.path.exists(path)]


def _existing_ttc_fonts(*entries):
    return [(path, index) for path, index in entries if os.path.exists(path)]


def get_preferred_fonts():
    system_name = platform.system().lower()
    user_fonts_dir = os.path.expanduser('~/Library/Fonts')

    if system_name == 'windows':
        windows_font_paths = _existing_font_paths(
            r"C:\Windows\Fonts\Inter-ExtraBold.ttf",
            r"C:\Windows\Fonts\Inter-Bold.ttf",
            r"C:\Windows\Fonts\Montserrat-ExtraBold.ttf",
            r"C:\Windows\Fonts\Montserrat-Bold.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
            r"C:\Windows\Fonts\bahnschrift.ttf",
        )
        return [*windows_font_paths, 'Aptos Display Bold', 'Aptos Bold', 'Segoe UI Semibold', 'Segoe UI Bold', 'Arial-Bold', 'Arial', 'Calibri', 'NotoSans-Bold', 'DejaVuSans-Bold']
    if system_name == 'darwin':
        return [
            *_existing_font_paths(
                os.path.join(user_fonts_dir, 'Satoshi-Black.otf'),
                os.path.join(user_fonts_dir, 'Satoshi-Bold.otf'),
                os.path.join(user_fonts_dir, 'Raleway-Bold.ttf'),
                '/Library/Fonts/Inter-ExtraBold.ttf',
                '/Library/Fonts/Inter-Bold.ttf',
                '/Library/Fonts/Montserrat-ExtraBold.ttf',
                '/Library/Fonts/Montserrat-Bold.ttf',
            ),
            *_existing_ttc_fonts(
                ('/System/Library/Fonts/Avenir Next.ttc', 8),
                ('/System/Library/Fonts/Avenir Next.ttc', 0),
                ('/System/Library/Fonts/HelveticaNeue.ttc', 1),
                ('/System/Library/Fonts/HelveticaNeue.ttc', 10),
                ('/System/Library/Fonts/Supplemental/Futura.ttc', 2),
            ),
            *_existing_font_paths(
                '/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf',
                '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
                '/System/Library/Fonts/Supplemental/Arial.ttf',
                '/System/Library/Fonts/Supplemental/Trebuchet MS Bold.ttf',
            ),
            'SF Pro Display Semibold',
            'SF Pro Display Bold',
            'Arial-Bold',
            'Arial',
            'DejaVuSans-Bold',
        ]
    return [
        *_existing_font_paths(
            '/usr/share/fonts/truetype/inter/Inter-ExtraBold.ttf',
            '/usr/share/fonts/truetype/inter/Inter-Bold.ttf',
            '/usr/share/fonts/truetype/montserrat/Montserrat-ExtraBold.ttf',
            '/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
            '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf',
        ),
        'NotoSans-Bold',
        'Inter-Bold',
        'Inter-SemiBold',
        'Montserrat-Bold',
        'DejaVuSans-Bold',
        'LiberationSans-Bold',
        'Arial-Bold',
        'Arial',
    ]


def normalize_subtitle_style(style=None):
    merged = dict(DEFAULT_STYLE)
    if isinstance(style, dict):
        merged.update({key: value for key, value in style.items() if value is not None})

    font_preset = merged.get("fontPreset", DEFAULT_STYLE["fontPreset"])
    if font_preset not in FONT_PRESETS:
        font_preset = DEFAULT_STYLE["fontPreset"]

    color_preset = merged.get("colorPreset", DEFAULT_STYLE["colorPreset"])
    if color_preset not in COLOR_PRESETS:
        color_preset = DEFAULT_STYLE["colorPreset"]

    return {
        "fontPreset": font_preset,
        "colorPreset": color_preset,
    }


def get_font_candidates(font_preset):
    preset_fonts = FONT_PRESETS.get(font_preset, [])
    fallback_fonts = get_preferred_fonts()
    return list(dict.fromkeys([*preset_fonts, *fallback_fonts, AUTO_FONT]))


def _font_kwargs(font):
    if font == AUTO_FONT:
        return {}
    return {"font": font}


def _clean_caption_text(text):
    cleaned = (text or "").replace("’", "'").replace("`", "'")
    cleaned = re.sub(r"\s+", " ", cleaned.strip())
    if not cleaned:
        return ""

    replacements = {
        r"\bv\s*'\s*all\b": "y'all",
        r"\by\s*'\s*all\b": "y'all",
        r"\bu\s*'\s*all\b": "y'all",
        r"\bi\s*'\s*m\b": "i'm",
        r"\bdon\s*'\s*t\b": "don't",
        r"\bcan\s*'\s*t\b": "can't",
        r"\bwon\s*'\s*t\b": "won't",
        r"\bit\s*'\s*s\b": "it's",
        r"\bthat\s*'\s*s\b": "that's",
        r"\bwhat\s*'\s*s\b": "what's",
        r"\b1800\s*s\b": "1800s",
        r"\b1900\s*s\b": "1900s",
    }

    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+([,!?;:.])", r"\1", cleaned)
    cleaned = re.sub(r"([A-Za-z])\s+'\s+([A-Za-z])", r"\1'\2", cleaned)
    cleaned = re.sub(r"\b(\d+)\s+%\b", r"\1%", cleaned)
    return cleaned.strip()


def _display_caption_text(text):
    return _clean_caption_text(text).upper()


def _normalized_compare_text(text):
    return re.sub(r"[^a-z0-9%]+", "", _clean_caption_text(text).lower())


def _word_entries_are_reliable(segment_text, word_entries):
    if not word_entries:
        return False

    segment_compare = _normalized_compare_text(segment_text)
    words_compare = _normalized_compare_text(" ".join(entry.get("text", "") for entry in word_entries))
    if not segment_compare or not words_compare:
        return False

    similarity = SequenceMatcher(None, segment_compare, words_compare).ratio()
    if similarity < 0.78:
        return False

    suspicious_words = 0
    for entry in word_entries:
        token = _clean_caption_text(entry.get("text", ""))
        if not token:
            suspicious_words += 1
            continue

        if re.search(r"[A-Za-z]{1,2}\d|\d[A-Za-z]{1,2}", token):
            suspicious_words += 1
            continue

        if len(re.sub(r"[^A-Za-z0-9]+", "", token)) <= 1 and token not in {"I", "A", "%"}:
            suspicious_words += 1
            continue

    return suspicious_words <= max(1, len(word_entries) // 4)


# Words with high emotional/viral weight — always highlighted when present
IMPACT_WORDS = {
    "never", "always", "biggest", "worst", "best", "insane", "crazy", "shocking",
    "secret", "truth", "lie", "lies", "fake", "real", "actually", "literally",
    "unbelievable", "incredible", "impossible", "dangerous", "critical", "urgent",
    "breaking", "exclusive", "revealed", "exposed", "banned", "deleted",
    "killed", "died", "dead", "billions", "millions", "trillion", "zero",
    "first", "last", "only", "every", "nobody", "everybody", "nobody",
    "wrong", "right", "must", "stop", "warning", "alert", "crisis",
    "why", "how", "what", "proven", "failed", "won", "lost",
}


def _score_highlight_word(word_text):
    cleaned = re.sub(r"[^A-Za-z0-9'%]+", "", (word_text or "")).lower()
    if not cleaned:
        return -999

    score = 0
    if cleaned in IMPACT_WORDS:
        score += 12
    elif cleaned not in LOW_VALUE_WORDS:
        score += 4
    if any(char.isdigit() for char in cleaned):
        score += 5
    if cleaned.endswith("!") or cleaned.endswith("?"):
        score += 2
    score += min(len(cleaned), 8)
    return score


def _choose_highlight_index(words):
    best_index = 0
    best_score = -999

    for index, word in enumerate(words):
        score = _score_highlight_word(word)
        if score > best_score:
            best_index = index
            best_score = score

    return best_index


def _build_caption_cue(start_time, end_time, words):
    cleaned_words = [_display_caption_text(word) for word in words if _display_caption_text(word)]
    if not cleaned_words:
        return None

    highlight_index = _choose_highlight_index(cleaned_words)
    return {
        "start": start_time,
        "end": end_time,
        "text": " ".join(cleaned_words),
        "words": cleaned_words,
        "highlightIndex": highlight_index,
        "highlight": cleaned_words[highlight_index],
        "wordEntries": None,
    }


def _finalize_subtitle_plan(cues, video_duration):
    finalized = []

    for cue in cues:
        start = max(0.0, min(video_duration, float(cue["start"])))
        end = max(start + 0.12, min(video_duration, float(cue["end"])))
        text = _display_caption_text(cue.get("text", ""))
        if not text:
            continue

        normalized_cue = dict(cue)
        normalized_cue["start"] = start
        normalized_cue["end"] = end
        normalized_cue["text"] = text

        if finalized and finalized[-1]["text"] == normalized_cue["text"] and start - finalized[-1]["end"] <= 0.08:
            finalized[-1]["end"] = end
            continue

        finalized.append(normalized_cue)

    return finalized


def export_subtitle_plan(cues):
    exported = []
    for cue in cues:
        exported.append(
            {
                "start": round(float(cue["start"]), 3),
                "end": round(float(cue["end"]), 3),
                "text": cue["text"],
                "highlight": cue["highlight"],
            }
        )
    return exported


def build_subtitle_plan(whisper_segments, clip_start_time, video_duration):
    cues = []

    for segment in whisper_segments:
        start = segment['start'] - clip_start_time
        end = segment['end'] - clip_start_time
        text = _clean_caption_text(segment['text'])

        if end <= 0 or start >= video_duration:
            continue

        start = max(0, start)
        end = min(video_duration, end)
        word_entries = extract_word_entries(segment, clip_start_time, video_duration)
        use_word_entries = _word_entries_are_reliable(text, word_entries)

        if use_word_entries:
            for chunk_words in split_word_entries(word_entries):
                cue = _build_caption_cue(chunk_words[0]["start"], chunk_words[-1]["end"], [word["text"] for word in chunk_words])
                if cue is None:
                    continue
                cue["wordEntries"] = chunk_words
                cues.append(cue)
        else:
            for (chunk_start, chunk_end), chunk_text in split_subtitle_text(text, start, end):
                cue = _build_caption_cue(chunk_start, chunk_end, chunk_text.split(" "))
                if cue is not None:
                    cues.append(cue)

    return _finalize_subtitle_plan(cues, video_duration)


def split_subtitle_text(text, start_time, end_time):
    cleaned_text = _clean_caption_text(text)
    if not cleaned_text:
        return []

    words = cleaned_text.split(" ")
    duration = max(0.2, end_time - start_time)

    if len(words) <= 3 and len(cleaned_text) <= 20:
        return [((start_time, end_time), cleaned_text)]

    if duration <= 1.0:
        max_words = 2
        max_chars = 12
    elif duration <= 2.0:
        max_words = 3
        max_chars = 16
    else:
        max_words = 3
        max_chars = 18

    chunks = []
    current_words = []

    for word in words:
        proposed_words = [*current_words, word]
        proposed_text = " ".join(proposed_words)
        hit_limit = len(current_words) >= max_words or len(proposed_text) > max_chars
        punctuation_break = current_words and current_words[-1][-1:] in ",.!?:;"

        if hit_limit or punctuation_break:
            chunks.append(" ".join(current_words))
            current_words = [word]
        else:
            current_words = proposed_words

    if current_words:
        chunks.append(" ".join(current_words))

    if len(chunks) == 1:
        return [((start_time, end_time), chunks[0])]

    total_weight = sum(max(len(chunk.replace(" ", "")), 1) for chunk in chunks)
    current_start = start_time
    timed_chunks = []

    for index, chunk in enumerate(chunks):
        remaining_time = end_time - current_start
        remaining_chunks = len(chunks) - index

        if index == len(chunks) - 1:
            chunk_end = end_time
        else:
            weight = max(len(chunk.replace(" ", "")), 1)
            proportional_duration = duration * (weight / total_weight)
            minimum_duration = min(0.68, duration / len(chunks))
            chunk_duration = max(minimum_duration, proportional_duration)
            max_end = end_time - (remaining_chunks - 1) * 0.18
            chunk_end = min(max_end, current_start + chunk_duration)

        if chunk_end - current_start >= 0.12:
            timed_chunks.append(((current_start, chunk_end), chunk))

        current_start = chunk_end

    return timed_chunks or [((start_time, end_time), cleaned_text)]


def resolve_font_size(video_clip, text):
    base_size = int(video_clip.h * SUBTITLE_BASE_FONT_RATIO)
    text_length = len(text)

    if text_length >= 36:
        base_size -= 8
    elif text_length >= 28:
        base_size -= 5
    elif text_length >= 20:
        base_size -= 2

    return max(SUBTITLE_MIN_FONT_SIZE, min(SUBTITLE_MAX_FONT_SIZE, base_size))


def resolve_top_text_size(video_clip, text, *, minimum, maximum, ratio):
    base_size = int(video_clip.h * ratio)
    text_length = len((text or "").strip())

    if text_length >= 90:
        base_size -= 7
    elif text_length >= 60:
        base_size -= 4
    elif text_length >= 36:
        base_size -= 2

    return max(minimum, min(maximum, base_size))


def _safe_duration(duration, fallback=0.2):
    if duration is None:
        return fallback
    return max(fallback, float(duration))


def _sanitize_overlay_text(text, max_length):
    cleaned_text = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned_text:
        return ""
    if len(cleaned_text) <= max_length:
        return cleaned_text
    shortened = cleaned_text[: max(0, max_length - 1)].rstrip(" ,.!?:;-")
    return f"{shortened}…"


def _with_clip_timing(clip, start_time, end_time):
    clip_duration = _safe_duration(end_time - start_time)
    return clip.with_start(start_time).with_duration(clip_duration)


def _cubic_ease(value):
    bounded = max(0.0, min(1.0, float(value)))
    return bounded * bounded * (3.0 - 2.0 * bounded)


def _opacity_at_time(local_t, duration, fade_in_duration, fade_out_duration):
    opacity = 1.0
    if fade_in_duration > 0 and local_t < fade_in_duration:
        opacity *= _cubic_ease(local_t / fade_in_duration)
    if fade_out_duration > 0 and local_t > duration - fade_out_duration:
        remaining = max(0.0, duration - local_t)
        opacity *= _cubic_ease(remaining / fade_out_duration)
    return opacity


def _create_rgba_video_clip(frame_provider, size, duration, *, fade_in_duration=0.0, fade_out_duration=0.0):
    def rgb_frame(t):
        frame = frame_provider(t)
        return frame[:, :, :3]

    def mask_frame(t):
        frame = frame_provider(t)
        alpha = frame[:, :, 3].astype(np.float32) / 255.0
        return alpha * _opacity_at_time(float(t), duration, fade_in_duration, fade_out_duration)

    clip = VideoClip(frame_function=rgb_frame, duration=duration)
    clip.size = size
    mask = VideoClip(frame_function=mask_frame, is_mask=True, duration=duration)
    mask.size = size
    return clip.with_mask(mask)


def _create_static_rgba_clip(rgba_frame, duration, *, fade_in_duration=0.0, fade_out_duration=0.0):
    frame = np.array(rgba_frame, copy=False)
    return _create_rgba_video_clip(
        lambda _t: frame,
        (frame.shape[1], frame.shape[0]),
        duration,
        fade_in_duration=fade_in_duration,
        fade_out_duration=fade_out_duration,
    )


def _prime_clip_duration(clip, duration=1.0):
    return clip.with_duration(_safe_duration(duration, fallback=1.0))


def _expand_clip_canvas(clip, pad_x=0, pad_y=0):
    expanded = CompositeVideoClip(
        [clip.with_position((pad_x, pad_y))],
        size=(clip.w + pad_x * 2, clip.h + pad_y * 2),
    )
    return _prime_clip_duration(expanded, getattr(clip, "duration", 1.0))


def _load_pil_font(font, font_size):
    if font == AUTO_FONT:
        return None

    font_key = font if isinstance(font, (str, tuple)) else str(font)
    return _load_pil_font_cached(font_key, font_size)


@functools.lru_cache(maxsize=64)
def _load_pil_font_cached(font_key, font_size):
    """Cached font loader — font_key is either a string name or a (path, index) tuple."""
    return _load_pil_font_uncached(font_key, font_size)


def _load_pil_font_uncached(font, font_size):
    try:
        if isinstance(font, tuple):
            path, face_index = font
            return ImageFont.truetype(path, font_size, index=face_index)
        return ImageFont.truetype(font, font_size)
    except Exception:
        return None


def _build_font_size_candidates(video_clip):
    preferred = round(video_clip.h * SUBTITLE_BASE_FONT_RATIO)
    minimum = round(video_clip.h * SUBTITLE_MIN_FONT_RATIO)
    maximum = round(video_clip.h * SUBTITLE_MAX_FONT_RATIO)
    upper = max(preferred, maximum)
    lower = max(28, min(minimum, upper))
    return range(upper, lower - 1, -4)


def _measure_caption_word(draw, word, word_index, font, stroke_width):
    bbox = draw.textbbox((0, 0), word, font=font, anchor='ls', stroke_width=stroke_width)
    return {
        "text": word,
        "index": word_index,
        "bbox": bbox,
        "width": bbox[2] - bbox[0],
        "ascent": max(0, -bbox[1]),
        "descent": max(0, bbox[3]),
    }


def _measure_tracked_text(draw, text, font, stroke_width, tracking):
    if not text:
        return {
            "bbox": (0, 0, 0, 0),
            "width": 0,
            "ascent": 0,
            "descent": 0,
        }

    if tracking == 0 or len(text) <= 1:
        return _measure_caption_word(draw, text, 0, font, stroke_width)

    glyph_boxes = []
    current_x = 0
    min_left = None
    min_top = None
    max_right = None
    max_bottom = None

    for char in text:
        bbox = draw.textbbox((0, 0), char, font=font, anchor='ls', stroke_width=stroke_width)
        left = current_x + bbox[0]
        right = current_x + bbox[2]
        min_left = left if min_left is None else min(min_left, left)
        min_top = bbox[1] if min_top is None else min(min_top, bbox[1])
        max_right = right if max_right is None else max(max_right, right)
        max_bottom = bbox[3] if max_bottom is None else max(max_bottom, bbox[3])
        advance = draw.textlength(char, font=font)
        current_x += advance + tracking
        glyph_boxes.append(bbox)

    if min_left is None:
        min_left = min_top = max_right = max_bottom = 0

    return {
        "bbox": (int(min_left), int(min_top), int(max_right), int(max_bottom)),
        "width": int(max_right - min_left),
        "ascent": max(0, -int(min_top)),
        "descent": max(0, int(max_bottom)),
    }


def _draw_tracked_text(draw, position, text, font, fill, tracking, anchor='ls', stroke_width=0, stroke_fill=None):
    if tracking == 0 or len(text) <= 1:
        kwargs = {
            "font": font,
            "fill": fill,
            "anchor": anchor,
        }
        if stroke_width > 0 and stroke_fill is not None:
            kwargs["stroke_width"] = stroke_width
            kwargs["stroke_fill"] = stroke_fill
        draw.text(position, text, **kwargs)
        return

    x, y = position
    current_x = x
    for char in text:
        kwargs = {
            "font": font,
            "fill": fill,
            "anchor": anchor,
        }
        if stroke_width > 0 and stroke_fill is not None:
            kwargs["stroke_width"] = stroke_width
            kwargs["stroke_fill"] = stroke_fill
        draw.text((current_x, y), char, **kwargs)
        current_x += draw.textlength(char, font=font) + tracking


def _wrap_caption_words(measured_words, max_width, space_width, max_lines=SUBTITLE_MAX_LINES):
    if not measured_words:
        return None

    lines = []
    current_line = []
    current_width = 0

    for word in measured_words:
        proposed_width = word["width"] if not current_line else current_width + space_width + word["width"]
        if current_line and proposed_width > max_width:
            lines.append({"words": current_line, "width": current_width})
            current_line = [word]
            current_width = word["width"]
        else:
            current_line.append(word)
            current_width = proposed_width

        if len(lines) >= max_lines:
            return None

    if current_line:
        lines.append({"words": current_line, "width": current_width})

    if len(lines) > max_lines:
        return None

    for line in lines:
        line["ascent"] = max(word["ascent"] for word in line["words"])
        line["descent"] = max(word["descent"] for word in line["words"])
        line["height"] = line["ascent"] + line["descent"]

    return lines


# Per-preset cache of the first font that successfully renders on this system.
# Avoids re-scanning the full font priority list for every subtitle cue after the first.
_LAYOUT_FONT_WINNER: dict[str, object] = {}


def _build_locked_text_layout(
    cue,
    video_clip,
    subtitle_style,
    *,
    max_width_ratio,
    max_height_ratio,
    base_font_ratio,
    min_font_ratio,
    max_font_ratio,
    padding_x,
    padding_y,
    line_gap_ratio,
    max_lines=SUBTITLE_MAX_LINES,
    stroke_color=None,
    shadow_offset_y=None,
    tracking=0,
):
    colors = COLOR_PRESETS[subtitle_style["colorPreset"]]
    max_width = int(video_clip.w * max_width_ratio)
    max_height = int(video_clip.h * max_height_ratio)
    stroke_width = 0 if not (stroke_color or colors["stroke_color"]) else max(1, min(3, round(video_clip.h * 0.0012)))
    shadow_offset_y = shadow_offset_y if shadow_offset_y is not None else max(2, round(video_clip.h * 0.0024))
    line_gap = max(6, round(video_clip.h * line_gap_ratio))
    padding_x = max(4, min(16, padding_x))
    padding_y = max(4, min(16, padding_y))
    probe_image = Image.new('RGBA', (max_width + padding_x * 2, max_height + padding_y * 2), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe_image)
    display_words = cue.get("words") or cue["text"].split()
    display_words = [_display_caption_text(word) for word in display_words if _display_caption_text(word)]

    if not display_words:
        raise RuntimeError("Subtitle cue has no renderable words.")

    font_preset = subtitle_style["fontPreset"]
    all_font_candidates = get_font_candidates(font_preset)
    cached_font = _LAYOUT_FONT_WINNER.get(font_preset)
    if cached_font is not None:
        # Promote cached winner to front — subsequent cues skip the full priority scan
        font_candidates = [cached_font] + [f for f in all_font_candidates if f != cached_font]
    else:
        font_candidates = all_font_candidates

    last_error = None
    for font in font_candidates:
        for font_size in _build_font_size_candidates_custom(video_clip, base_font_ratio, min_font_ratio, max_font_ratio):
            pil_font = _load_pil_font(font, font_size)
            if pil_font is None:
                continue

            try:
                space_width = max(4, round(probe_draw.textlength(' ', font=pil_font)))
                measured_words = [
                    {
                        "text": word,
                        "index": word_index,
                        **_measure_tracked_text(probe_draw, word, pil_font, stroke_width, tracking),
                    }
                    for word_index, word in enumerate(display_words)
                ]
                if any(word["width"] > max_width for word in measured_words):
                    continue

                lines = _wrap_caption_words(measured_words, max_width, space_width, max_lines=max_lines)
                if lines is None:
                    continue

                content_width = max(line["width"] for line in lines)
                content_height = sum(line["height"] for line in lines) + line_gap * (len(lines) - 1)
                canvas_width = content_width + padding_x * 2
                canvas_height = content_height + padding_y * 2 + shadow_offset_y
                if canvas_height > max_height:
                    continue

                positioned_words = []
                current_y = padding_y
                for line in lines:
                    line_left = padding_x + int((content_width - line["width"]) / 2)
                    baseline_y = current_y + line["ascent"]
                    current_x = line_left
                    for word in line["words"]:
                        positioned_words.append(
                            {
                                "index": word["index"],
                                "text": word["text"],
                                "anchor_x": current_x - word["bbox"][0],
                                "baseline_y": baseline_y,
                                "tracking": tracking,
                            }
                        )
                        current_x += word["width"] + space_width
                    current_y += line["height"] + line_gap

                _LAYOUT_FONT_WINNER[font_preset] = font
                return {
                    "font": pil_font,
                    "stroke_width": stroke_width,
                    "stroke_fill": ImageColor.getrgb(stroke_color or colors["stroke_color"]) if (stroke_color or colors["stroke_color"]) else None,
                    "shadow_offset_y": shadow_offset_y,
                    "size": (canvas_width, canvas_height),
                    "words": sorted(positioned_words, key=lambda entry: entry["index"]),
                }
            except Exception as error:
                last_error = error

    if display_words:
        safe_max_chars = max(6, int(max_width / 20))
        safe_words = [w[:safe_max_chars] + "…" if len(w) > safe_max_chars else w for w in display_words[:3]]
        safe_cue = dict(cue)
        safe_cue["words"] = safe_words
        safe_cue["text"] = " ".join(safe_words)
        safe_cue["highlightIndex"] = min(cue.get("highlightIndex", 0), len(safe_words) - 1)
        for font in font_candidates:
            for font_size in _build_font_size_candidates_custom(video_clip, base_font_ratio, min_font_ratio * 0.7, max_font_ratio):
                pil_font = _load_pil_font(font, font_size)
                if pil_font is None:
                    continue
                try:
                    space_width = max(4, round(probe_draw.textlength(' ', font=pil_font)))
                    measured = [
                        {"text": w, "index": i, **_measure_tracked_text(probe_draw, w, pil_font, stroke_width, tracking)}
                        for i, w in enumerate(safe_words)
                    ]
                    if any(word["width"] > max_width for word in measured):
                        continue
                    lines = _wrap_caption_words(measured, max_width, space_width, max_lines=max_lines)
                    if lines is None:
                        continue
                    content_width = max(line["width"] for line in lines)
                    content_height = sum(line["height"] for line in lines) + line_gap * (len(lines) - 1)
                    canvas_width = content_width + padding_x * 2
                    canvas_height = content_height + padding_y * 2 + shadow_offset_y
                    if canvas_height > max_height:
                        continue
                    positioned_words = []
                    current_y = padding_y
                    for line in lines:
                        line_left = padding_x + int((content_width - line["width"]) / 2)
                        baseline_y = current_y + line["ascent"]
                        current_x = line_left
                        for word in line["words"]:
                            positioned_words.append({"index": word["index"], "text": word["text"], "anchor_x": current_x - word["bbox"][0], "baseline_y": baseline_y, "tracking": tracking})
                            current_x += word["width"] + space_width
                        current_y += line["height"] + line_gap
                    _LAYOUT_FONT_WINNER[font_preset] = font
                    return {"font": pil_font, "stroke_width": stroke_width, "stroke_fill": ImageColor.getrgb(stroke_color or colors["stroke_color"]) if (stroke_color or colors["stroke_color"]) else None, "shadow_offset_y": shadow_offset_y, "size": (canvas_width, canvas_height), "words": sorted(positioned_words, key=lambda entry: entry["index"])}
                except Exception:
                    pass

    raise RuntimeError("Could not render subtitles with a glyph-safe PIL font.") from last_error


def _render_shadow_for_layout(layout):
    """Render the blurred drop-shadow for a cue layout.

    The shadow colour and position are identical for every highlight state of
    the same cue.  Pre-rendering once and reusing across all N word-states
    eliminates N redundant GaussianBlur(radius=8) calls per cue.
    """
    shadow_layer = Image.new('RGBA', layout["size"], (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    for word in layout["words"]:
        _draw_tracked_text(
            shadow_draw,
            (word["anchor_x"] + 2, word["baseline_y"] + layout["shadow_offset_y"]),
            word["text"],
            layout["font"],
            SUBTITLE_SHADOW_COLOR,
            word.get("tracking", 0),
            anchor='ls',
        )
    return shadow_layer.filter(ImageFilter.GaussianBlur(radius=8))


def _render_locked_text_image(
    layout,
    subtitle_style,
    *,
    highlight_index,
    base_color=None,
    active_color=None,
    inactive_alpha=1.0,
    shadow_fill=None,
    prebuilt_shadow=None,
):
    colors = COLOR_PRESETS[subtitle_style["colorPreset"]]
    base_rgb = ImageColor.getrgb(base_color or colors["base_color"])
    active_rgb = ImageColor.getrgb(active_color or colors["active_color"])
    inactive_channel = max(0, min(255, round(255 * inactive_alpha)))

    # Reuse the pre-rendered shadow when available — avoids a redraw + GaussianBlur per state
    if prebuilt_shadow is not None:
        shadow_composite = prebuilt_shadow
    else:
        shadow_rgba = shadow_fill or SUBTITLE_SHADOW_COLOR
        shadow_layer = Image.new('RGBA', layout["size"], (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        for word in layout["words"]:
            _draw_tracked_text(
                shadow_draw,
                (word["anchor_x"] + 2, word["baseline_y"] + layout["shadow_offset_y"]),
                word["text"],
                layout["font"],
                shadow_rgba,
                word.get("tracking", 0),
                anchor='ls',
            )
        shadow_composite = shadow_layer.filter(ImageFilter.GaussianBlur(radius=8))

    # Glow only exists when a word is actively highlighted — skip entirely for inactive state
    if highlight_index >= 0:
        glow_layer = Image.new('RGBA', layout["size"], (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        for word in layout["words"]:
            if word["index"] == highlight_index:
                _draw_tracked_text(
                    glow_draw,
                    (word["anchor_x"], word["baseline_y"]),
                    word["text"],
                    layout["font"],
                    (*active_rgb, 100),
                    word.get("tracking", 0),
                    anchor='ls',
                )
        base = Image.alpha_composite(shadow_composite, glow_layer.filter(ImageFilter.GaussianBlur(radius=12)))
    else:
        base = shadow_composite

    image = Image.new('RGBA', layout["size"], (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for word in layout["words"]:
        is_active = word["index"] == highlight_index
        fill = (*active_rgb, 255) if is_active else (*base_rgb, inactive_channel)
        _draw_tracked_text(
            draw,
            (word["anchor_x"], word["baseline_y"]),
            word["text"],
            layout["font"],
            fill,
            word.get("tracking", 0),
            anchor='ls',
            stroke_width=layout["stroke_width"],
            stroke_fill=layout["stroke_fill"],
        )
    return Image.alpha_composite(base, image)


def _render_subtitle_bitmap_image(cue, video_clip, subtitle_style):
    layout = _build_locked_text_layout(
        cue,
        video_clip,
        subtitle_style,
        max_width_ratio=SUBTITLE_SAFE_WIDTH_RATIO,
        max_height_ratio=SUBTITLE_MAX_HEIGHT_RATIO,
        base_font_ratio=SUBTITLE_BASE_FONT_RATIO,
        min_font_ratio=SUBTITLE_MIN_FONT_RATIO,
        max_font_ratio=SUBTITLE_MAX_FONT_RATIO,
        padding_x=SUBTITLE_TEXT_PADDING_X,
        padding_y=SUBTITLE_TEXT_PADDING_Y,
        line_gap_ratio=0.008,
        tracking=SUBTITLE_LETTER_SPACING,
    )
    highlight_index = cue.get("highlightIndex")
    if highlight_index is None or highlight_index >= len(layout["words"]):
        highlight_index = -1
    return _render_locked_text_image(
        layout,
        subtitle_style,
        highlight_index=highlight_index,
        inactive_alpha=SUBTITLE_INACTIVE_ALPHA / 255,
    )


def _render_subtitle_bitmap_clip(cue, video_clip, subtitle_style):
    image = _render_subtitle_bitmap_image(cue, video_clip, subtitle_style)
    clip = ImageClip(np.array(image))
    return _prime_clip_duration(clip)


def _build_font_size_candidates_custom(video_clip, base_font_ratio, min_font_ratio, max_font_ratio):
    preferred = round(video_clip.h * base_font_ratio)
    minimum = round(video_clip.h * min_font_ratio)
    maximum = round(video_clip.h * max_font_ratio)
    upper = max(preferred, maximum)
    lower = max(16, min(minimum, upper))
    return range(upper, lower - 1, -4)


def _render_header_bitmap_clip(text, video_clip, subtitle_style, *, reason=False):
    cue = {
        "text": text.upper() if not reason else text,
        "words": (text.upper() if not reason else text).split(),
        "highlightIndex": -1,
    }
    layout = _build_locked_text_layout(
        cue,
        video_clip,
        subtitle_style,
        max_width_ratio=HEADER_SAFE_WIDTH_RATIO - 0.08,
        max_height_ratio=0.16 if not reason else 0.1,
        base_font_ratio=HEADER_REASON_FONT_RATIO if reason else HEADER_TITLE_FONT_RATIO,
        min_font_ratio=HEADER_REASON_MIN_RATIO if reason else HEADER_TITLE_MIN_RATIO,
        max_font_ratio=HEADER_REASON_MAX_RATIO if reason else HEADER_TITLE_MAX_RATIO,
        padding_x=10,
        padding_y=10,
        line_gap_ratio=0.008,
        max_lines=3 if not reason else 2,
        stroke_color=None,
        shadow_offset_y=max(1, round(video_clip.h * 0.0014)),
        tracking=HEADER_REASON_LETTER_SPACING if reason else HEADER_TITLE_LETTER_SPACING,
    )
    image = _render_locked_text_image(
        layout,
        subtitle_style,
        highlight_index=-1,
        base_color=HEADER_REASON_COLOR if reason else HEADER_COLOR,
        active_color=HEADER_REASON_COLOR if reason else HEADER_COLOR,
        inactive_alpha=1.0,
        shadow_fill=(0, 0, 0, 32),
    )
    return _prime_clip_duration(ImageClip(np.array(image)))


def _render_vertical_gradient_image(width, height, *, height_ratio, anchor, max_alpha):
    gradient_height = max(32, int(height * height_ratio))
    gradient = np.zeros((gradient_height, width, 4), dtype=np.uint8)

    for row in range(gradient_height):
        progress = row / max(1, gradient_height - 1)
        opacity = int(round(max_alpha * (1.0 - progress if anchor == 'top' else progress)))
        gradient[row, :, 3] = opacity

    return Image.fromarray(gradient, mode='RGBA')


def _render_vertical_gradient_clip(video_clip, *, height_ratio, anchor, max_alpha):
    gradient = np.array(
        _render_vertical_gradient_image(
            video_clip.w,
            video_clip.h,
            height_ratio=height_ratio,
            anchor=anchor,
            max_alpha=max_alpha,
        )
    )
    gradient_height = gradient.shape[0]
    position_y = 0 if anchor == 'top' else video_clip.h - gradient_height
    return _prime_clip_duration(ImageClip(gradient)).with_position((0, position_y))


def _render_header_panel_layers(video_clip, title_clip, reason_clip):
    panel_width = int(video_clip.w * HEADER_SAFE_WIDTH_RATIO)
    panel_x = int((video_clip.w - panel_width) / 2)
    panel_y = int(video_clip.h * HEADER_TOP_RATIO)
    content_width = panel_width - HEADER_PANEL_PADDING_X * 2
    title_width = title_clip.w if title_clip is not None else 0
    reason_width = reason_clip.w if reason_clip is not None else 0
    text_width = min(content_width, max(title_width, reason_width, 0))
    text_start_x = panel_x + int((panel_width - text_width) / 2)

    panel_height = HEADER_PANEL_PADDING_Y * 2
    if title_clip is not None:
        panel_height += title_clip.h
    if title_clip is not None and reason_clip is not None:
        panel_height += HEADER_PANEL_GAP
    if reason_clip is not None:
        panel_height += reason_clip.h
    panel_height = max(panel_height, round(video_clip.h * (HEADER_PANEL_MIN_HEIGHT / 1920)))

    panel_image = Image.new('RGBA', (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel_image)
    draw.rounded_rectangle(
        (0, 0, panel_width - 1, panel_height - 1),
        radius=HEADER_PANEL_RADIUS,
        fill=HEADER_PANEL_FILL,
    )
    # Premium border — subtle white outline around the full panel
    draw.rounded_rectangle(
        (0, 0, panel_width - 1, panel_height - 1),
        radius=HEADER_PANEL_RADIUS,
        outline=HEADER_PANEL_BORDER,
        width=2,
    )
    # Bottom accent line — slightly brighter
    draw.line(
        [(HEADER_PANEL_RADIUS, panel_height - 1), (panel_width - HEADER_PANEL_RADIUS, panel_height - 1)],
        fill=(255, 255, 255, 70),
        width=1,
    )

    panel_clip = _prime_clip_duration(ImageClip(np.array(panel_image))).with_position((panel_x, panel_y))
    layers = [panel_clip]
    current_y = panel_y + HEADER_PANEL_PADDING_Y

    if title_clip is not None:
        layers.append(title_clip.with_position((text_start_x + int((text_width - title_clip.w) / 2), current_y)))
        current_y += title_clip.h + HEADER_PANEL_GAP

    if reason_clip is not None:
        if title_clip is None:
            current_y = panel_y + int((panel_height - reason_clip.h) / 2)
        layers.append(reason_clip.with_position((text_start_x + int((text_width - reason_clip.w) / 2), current_y)))

    return layers


def _build_cue_render_segments(cue):
    cue_start = float(cue["start"])
    cue_end = float(cue["end"])
    if cue_end - cue_start <= 0.02:
        return [(cue_start, cue_end, cue.get("highlightIndex", -1), True, True)]

    word_entries = cue.get("wordEntries") or []
    if not word_entries:
        return [(cue_start, cue_end, cue.get("highlightIndex", -1), True, True)]

    boundaries = {cue_start, cue_end}
    for entry in word_entries:
        start = max(cue_start, float(entry["start"]))
        end = min(cue_end, float(entry["end"]))
        if end - start <= 0.01:
            continue
        boundaries.add(start)
        boundaries.add(end)

    ordered_boundaries = sorted(boundaries)
    raw_segments = []

    for index in range(len(ordered_boundaries) - 1):
        start = ordered_boundaries[index]
        end = ordered_boundaries[index + 1]
        if end - start <= 0.01:
            continue

        midpoint = start + (end - start) / 2
        active_index = -1
        for word_index, entry in enumerate(word_entries):
            entry_start = max(cue_start, float(entry["start"]))
            entry_end = min(cue_end, float(entry["end"]))
            if entry_start <= midpoint < entry_end:
                active_index = word_index
                break

        raw_segments.append((start, end, active_index))

    if not raw_segments:
        return [(cue_start, cue_end, cue.get("highlightIndex", -1), True, True)]

    merged_segments = []
    for start, end, active_index in raw_segments:
        if merged_segments and merged_segments[-1][2] == active_index and start - merged_segments[-1][1] <= 0.001:
            merged_segments[-1] = (merged_segments[-1][0], end, active_index)
        else:
            merged_segments.append((start, end, active_index))

    finalized = []
    for index, (start, end, active_index) in enumerate(merged_segments):
        finalized.append((start, end, active_index, index == 0, index == len(merged_segments) - 1))

    return finalized


class _SubtitleProbeVideo:
    def __init__(self, width, height):
        self.w = width
        self.h = height


def validate_subtitle_plan_renderability(video_size, subtitle_cues, subtitle_style=None):
    resolved_style = normalize_subtitle_style(subtitle_style)
    probe_video = _SubtitleProbeVideo(video_size[0], video_size[1])
    results = []

    for cue in subtitle_cues:
        clip = None
        try:
            clip = _render_subtitle_bitmap_clip(cue, probe_video, resolved_style)
            positioned = _position_subtitle_clip(clip, probe_video)
            results.append(
                {
                    "start": round(float(cue["start"]), 3),
                    "end": round(float(cue["end"]), 3),
                    "text": cue["text"],
                    "width": int(clip.w),
                    "height": int(clip.h),
                    "position": tuple(positioned.pos(0)),
                }
            )
        finally:
            if clip is not None:
                clip.close()

    return results


def create_subtitle_preview_frames(video_size, subtitle_cues, subtitle_style=None, backgrounds=None):
    resolved_style = normalize_subtitle_style(subtitle_style)
    probe_video = _SubtitleProbeVideo(video_size[0], video_size[1])
    background_map = backgrounds or {
        "white": (245, 245, 245),
        "dark": (20, 24, 32),
    }
    top_gradient = _render_vertical_gradient_image(
        video_size[0],
        video_size[1],
        height_ratio=0.2,
        anchor='top',
        max_alpha=GRADIENT_TOP_ALPHA,
    )
    bottom_gradient = _render_vertical_gradient_image(
        video_size[0],
        video_size[1],
        height_ratio=0.24,
        anchor='bottom',
        max_alpha=GRADIENT_BOTTOM_ALPHA,
    )
    previews = []

    for cue in subtitle_cues:
        subtitle_layout = _build_locked_text_layout(
            cue,
            probe_video,
            resolved_style,
            max_width_ratio=SUBTITLE_SAFE_WIDTH_RATIO,
            max_height_ratio=SUBTITLE_MAX_HEIGHT_RATIO,
            base_font_ratio=SUBTITLE_BASE_FONT_RATIO,
            min_font_ratio=SUBTITLE_MIN_FONT_RATIO,
            max_font_ratio=SUBTITLE_MAX_FONT_RATIO,
            padding_x=SUBTITLE_TEXT_PADDING_X,
            padding_y=SUBTITLE_TEXT_PADDING_Y,
            line_gap_ratio=0.008,
        )
        subtitle_image = _render_locked_text_image(
            subtitle_layout,
            resolved_style,
            highlight_index=cue.get("highlightIndex", -1),
            inactive_alpha=SUBTITLE_INACTIVE_ALPHA / 255,
        )
        subtitle_width, subtitle_height = subtitle_image.size
        subtitle_clip = _prime_clip_duration(ImageClip(np.array(subtitle_image)))
        positioned = _position_subtitle_clip(subtitle_clip, probe_video)
        position = tuple(positioned.pos(0))
        subtitle_clip.close()

        cue_previews = []
        for background_name, background_color in background_map.items():
            frame = Image.new("RGBA", video_size, (*background_color, 255))
            frame.alpha_composite(top_gradient, dest=(0, 0))
            frame.alpha_composite(bottom_gradient, dest=(0, video_size[1] - bottom_gradient.height))
            frame.alpha_composite(subtitle_image, dest=position)
            cue_previews.append(
                {
                    "background": background_name,
                    "image": frame.convert("RGB"),
                }
            )

        previews.append(
            {
                "text": cue["text"],
                "start": round(float(cue["start"]), 3),
                "end": round(float(cue["end"]), 3),
                "width": subtitle_width,
                "height": subtitle_height,
                "position": position,
                "frames": cue_previews,
            }
        )

    return previews


def _normalize_word_text(text):
    cleaned = _clean_caption_text(text)
    return cleaned


def split_word_entries(word_entries):
    if not word_entries:
        return []

    chunks = []
    current_chunk = []

    for entry in word_entries:
        proposed_words = [*current_chunk, entry]
        proposed_text = " ".join(word["text"] for word in proposed_words)
        proposed_duration = proposed_words[-1]["end"] - proposed_words[0]["start"]
        hit_limit = bool(current_chunk) and (
            len(current_chunk) >= 3
            or len(proposed_text) > 16
            or proposed_duration > 1.45
        )
        punctuation_break = bool(current_chunk) and current_chunk[-1]["text"][-1:] in ",.!?:;"

        if hit_limit or punctuation_break:
            chunks.append(current_chunk)
            current_chunk = [entry]
        else:
            current_chunk = proposed_words

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def extract_word_entries(segment, clip_start_time, video_duration):
    words = segment.get("words") or []
    extracted = []

    for word in words:
        raw_text = _normalize_word_text(word.get("word"))
        start = word.get("start")
        end = word.get("end")
        if not raw_text or start is None or end is None:
            continue
        if raw_text.lower() in FILLER_WORDS:
            continue

        relative_start = max(0.0, float(start) - clip_start_time)
        relative_end = min(video_duration, float(end) - clip_start_time)
        if relative_end <= 0 or relative_start >= video_duration:
            continue

        if relative_end - relative_start < 0.06:
            relative_end = min(video_duration, relative_start + 0.12)

        extracted.append(
            {
                "text": raw_text,
                "start": relative_start,
                "end": relative_end,
            }
        )

    return extracted


def create_textclip_with_fallback(text, video_clip, subtitle_style):
    cue = {
        "text": text,
        "words": text.split(),
        "highlightIndex": -1,
    }
    return _render_subtitle_bitmap_clip(cue, video_clip, subtitle_style)


def _header_overlay_duration(video_duration):
    if video_duration <= 0:
        return 0.0
    return min(video_duration, HEADER_DURATION)


def _resolve_active_index_for_time(cue, local_time):
    word_entries = cue.get("wordEntries") or []
    if not word_entries:
        return cue.get("highlightIndex", -1)

    timestamp = max(0.0, float(local_time)) + float(cue.get("start", 0.0))
    for word_index, entry in enumerate(word_entries):
        word_start = float(entry["start"])
        word_end = float(entry["end"])
        if word_start <= timestamp < word_end:
            return word_index

    if timestamp < float(word_entries[0]["start"]):
        return 0
    return len(word_entries) - 1


def _position_subtitle_clip(clip, video_clip):
    safe_left = int(video_clip.w * SUBTITLE_HORIZONTAL_MARGIN_RATIO)
    safe_right = video_clip.w - safe_left
    safe_top = int(video_clip.h * SUBTITLE_VERTICAL_MARGIN_RATIO)
    safe_bottom = video_clip.h - safe_top
    x = max(safe_left, int((video_clip.w - clip.w) / 2))
    x = min(x, safe_right - clip.w)
    target_center_y = int(video_clip.h * SUBTITLE_Y_RATIO)
    y = int(target_center_y - (clip.h / 2))
    y = max(safe_top, min(y, safe_bottom - clip.h))
    return clip.with_position((x, y))


def _font_display_name(font):
    if isinstance(font, tuple):
        return f"{font[0]}#{font[1]}"
    return str(font)


def assert_subtitle_rendering_ready(subtitle_style=None):
    resolved_style = normalize_subtitle_style(subtitle_style)
    tested_fonts = []
    last_error = None
    test_sizes = [70, 54, 40]

    for font in get_font_candidates(resolved_style["fontPreset"]):
        tested_fonts.append(_font_display_name(font))
        try:
            all_sizes_ok = True
            for size in test_sizes:
                pil_font = _load_pil_font(font, size)
                if pil_font is None:
                    all_sizes_ok = False
                    break

                probe = Image.new('RGBA', (1080, 512), (0, 0, 0, 0))
                draw = ImageDraw.Draw(probe)
                draw.text(
                    (24, 200),
                    'SUBTITLE PROBE TEST',
                    font=pil_font,
                    fill=(255, 255, 255, 255),
                    anchor='ls',
                )
            if not all_sizes_ok:
                continue
            return _font_display_name(font)
        except Exception as error:
            last_error = error

    system_name = platform.system() or "Unknown OS"
    raise RuntimeError(
        "Could not render subtitles with any compatible font on this machine. "
        f"OS: {system_name}. Tested fonts: {', '.join(tested_fonts)}"
    ) from last_error


def create_top_description_overlay(video_clip, title, reason, subtitle_style):
    resolved_style = normalize_subtitle_style(subtitle_style)
    title_text = _sanitize_overlay_text(title, 72)
    reason_text = _sanitize_overlay_text(reason, 108)
    title_clip = None
    reason_clip = None

    if title_text:
        try:
            title_clip = _render_header_bitmap_clip(title_text, video_clip, resolved_style)
        except Exception:
            title_clip = None

    if reason_text:
        try:
            reason_style = dict(resolved_style)
            reason_style["fontPreset"] = "soft"
            reason_clip = _render_header_bitmap_clip(reason_text, video_clip, reason_style, reason=True)
        except Exception:
            reason_clip = None

    if title_clip is None and reason_clip is None:
        return []

    return _render_header_panel_layers(video_clip, title_clip, reason_clip)


def _build_word_boundaries(cue):
    """Build sorted list of word start timestamps (local to cue) for transition detection."""
    word_entries = cue.get("wordEntries") or []
    if not word_entries:
        return []
    cue_start = float(cue.get("start", 0.0))
    boundaries = []
    for entry in word_entries:
        boundary = float(entry["start"]) - cue_start
        if boundary > 0:
            boundaries.append(boundary)
    return sorted(set(boundaries))


def _resolve_highlight_blend(cue, local_time, transition_duration, word_boundaries):
    """Return (index_a, index_b, blend_factor) for smooth highlight crossfade.

    blend_factor=0.0 means fully index_a, 1.0 means fully index_b.
    When no transition is happening, index_a == index_b.
    """
    if not word_boundaries or transition_duration <= 0:
        active = _resolve_active_index_for_time(cue, local_time)
        return active, active, 0.0

    half_t = transition_duration / 2.0
    for boundary in word_boundaries:
        if boundary - half_t <= local_time <= boundary + half_t:
            before_time = boundary - half_t - 0.001
            after_time = boundary + half_t + 0.001
            index_before = _resolve_active_index_for_time(cue, max(0.0, before_time))
            index_after = _resolve_active_index_for_time(cue, after_time)
            if index_before == index_after:
                return index_before, index_before, 0.0
            progress = (local_time - (boundary - half_t)) / transition_duration
            blend = _cubic_ease(max(0.0, min(1.0, progress)))
            return index_before, index_after, blend

    active = _resolve_active_index_for_time(cue, local_time)
    return active, active, 0.0


def _blend_frames(frame_a, frame_b, factor):
    """Alpha-blend two RGBA numpy arrays. factor=0 → frame_a, factor=1 → frame_b."""
    if factor <= 0.0:
        return frame_a
    if factor >= 1.0:
        return frame_b
    # Single lerp: fa + (fb - fa) * t  — one fewer multiply vs the two-weight form,
    # and avoids allocating a second scaled array.
    fa = frame_a.astype(np.float32)
    return (fa + (frame_b.astype(np.float32) - fa) * factor + 0.5).astype(np.uint8)


def create_subtitles(video_clip, whisper_segments, clip_start_time, subtitle_style=None, clip_title=None, clip_reason=None):
    print("📝 Building subtitle layers...")
    resolved_style = normalize_subtitle_style(subtitle_style)
    video_duration = _safe_duration(video_clip.duration)
    header_duration = _header_overlay_duration(video_duration)

    subtitle_cues = build_subtitle_plan(whisper_segments, clip_start_time, video_duration)

    # Track all allocated clips for cleanup on failure
    _allocated_clips: list = []

    try:
        # --- Pre-build layouts for all cues ---
        cue_layouts = []
        for cue in subtitle_cues:
            locked_layout = _build_locked_text_layout(
                cue,
                video_clip,
                resolved_style,
                max_width_ratio=SUBTITLE_SAFE_WIDTH_RATIO,
                max_height_ratio=SUBTITLE_MAX_HEIGHT_RATIO,
                base_font_ratio=SUBTITLE_BASE_FONT_RATIO,
                min_font_ratio=SUBTITLE_MIN_FONT_RATIO,
                max_font_ratio=SUBTITLE_MAX_FONT_RATIO,
                padding_x=SUBTITLE_TEXT_PADDING_X,
                padding_y=SUBTITLE_TEXT_PADDING_Y,
                line_gap_ratio=0.008,
                tracking=SUBTITLE_LETTER_SPACING,
            )
            candidate_indexes = {-1}
            if cue.get("wordEntries"):
                candidate_indexes.update(range(len(cue["wordEntries"])))
            else:
                candidate_indexes.add(cue.get("highlightIndex", -1))
            cue_layouts.append((cue, locked_layout, candidate_indexes))

        # --- Parallel pre-render all highlight states ---
        def _render_state(args):
            layout, style, active_index, shadow = args
            return active_index, np.array(
                _render_locked_text_image(
                    layout,
                    style,
                    highlight_index=active_index,
                    inactive_alpha=SUBTITLE_INACTIVE_ALPHA / 255,
                    prebuilt_shadow=shadow,
                )
            )

        all_render_jobs = []
        for cue, locked_layout, candidate_indexes in cue_layouts:
            for active_index in candidate_indexes:
                all_render_jobs.append((cue, locked_layout, candidate_indexes, active_index))

        layout_shadows: dict[int, object] = {}
        for _, locked_layout, _ in cue_layouts:
            lid = id(locked_layout)
            if lid not in layout_shadows:
                layout_shadows[lid] = _render_shadow_for_layout(locked_layout)

        render_tasks = [(layout, resolved_style, idx, layout_shadows[id(layout)]) for (_, layout, _, idx) in all_render_jobs]
        with ThreadPoolExecutor(max_workers=min(8, len(render_tasks) or 1)) as executor:
            results = list(executor.map(_render_state, render_tasks))

        job_index = 0
        cue_rendered_states = []
        for cue, locked_layout, candidate_indexes in cue_layouts:
            rendered_states = {}
            for active_index in candidate_indexes:
                _, frame = results[job_index]
                rendered_states[active_index] = frame
                job_index += 1
            cue_rendered_states.append(rendered_states)

        # --- Build subtitle video clips with smooth crossfade ---
        subtitle_layers = []
        for cue_idx, (cue, locked_layout, candidate_indexes) in enumerate(cue_layouts):
            rendered_states = cue_rendered_states[cue_idx]
            word_boundaries = _build_word_boundaries(cue)

            cue_start = float(cue["start"])
            cue_end = float(cue["end"])
            cue_duration = _safe_duration(cue_end - cue_start)

            def cue_frame_provider(local_t, *, current_cue=cue, states=rendered_states, duration=cue_duration, boundaries=word_boundaries):
                timestamp = min(max(float(local_t), 0.0), max(0.0, duration - 1e-6))
                idx_a, idx_b, blend = _resolve_highlight_blend(current_cue, timestamp, HIGHLIGHT_TRANSITION_DURATION, boundaries)
                frame_a = states.get(idx_a, states.get(-1))
                if blend <= 0.01 or idx_a == idx_b:
                    return frame_a
                frame_b = states.get(idx_b, states.get(-1))
                return _blend_frames(frame_a, frame_b, blend)

            subtitle_clip = _create_rgba_video_clip(
                cue_frame_provider,
                locked_layout["size"],
                cue_duration,
                fade_in_duration=SUBTITLE_FADE_IN,
                fade_out_duration=SUBTITLE_FADE_OUT,
            )
            subtitle_clip = _with_clip_timing(subtitle_clip, cue_start, cue_end)
            subtitle_clip = _position_subtitle_clip(subtitle_clip, video_clip)
            subtitle_layers.append(subtitle_clip)
            _allocated_clips.append(subtitle_clip)

        top_description_layers = []
        for layer in create_top_description_overlay(video_clip, clip_title, clip_reason, resolved_style):
            frame = layer.get_frame(0)
            if frame.ndim == 3 and frame.shape[2] == 3:
                if layer.mask is not None:
                    alpha_channel = np.clip(layer.mask.get_frame(0) * 255.0, 0, 255).astype(np.uint8)
                else:
                    alpha_channel = np.full((frame.shape[0], frame.shape[1]), 255, dtype=np.uint8)
                frame = np.dstack([frame, alpha_channel])
            position = layer.pos(0)
            layer.close()
            header_clip = _create_static_rgba_clip(
                frame,
                header_duration,
                fade_in_duration=HEADER_FADE_IN,
                fade_out_duration=HEADER_FADE_OUT,
            )
            timed_header = _with_clip_timing(header_clip.with_position(position), 0, header_duration)
            top_description_layers.append(timed_header)
            _allocated_clips.append(timed_header)

        gradient_layers = [
            _with_clip_timing(_render_vertical_gradient_clip(video_clip, height_ratio=0.2, anchor='top', max_alpha=GRADIENT_TOP_ALPHA), 0, video_duration),
            _with_clip_timing(_render_vertical_gradient_clip(video_clip, height_ratio=0.24, anchor='bottom', max_alpha=GRADIENT_BOTTOM_ALPHA), 0, video_duration),
        ]
        _allocated_clips.extend(gradient_layers)

        layers = [video_clip, *gradient_layers, *top_description_layers, *subtitle_layers]
        final_clip = CompositeVideoClip(layers, size=video_clip.size).with_duration(video_duration)
        if video_clip.audio is not None:
            final_clip = final_clip.with_audio(video_clip.audio.with_duration(video_duration))

        return final_clip

    except Exception:
        # Clean up any allocated clips on failure to prevent resource leaks
        for c in _allocated_clips:
            try:
                c.close()
            except Exception:
                pass
        raise
