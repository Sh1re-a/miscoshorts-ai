import platform
import re

from moviepy import ColorClip, CompositeVideoClip, TextClip


FONT_PRESETS = {
    "clean": [
        "Avenir Next Demi Bold",
        "Helvetica Neue Bold",
        "Segoe UI Bold",
        "Arial-Bold",
        "Helvetica-Bold",
        "DejaVuSans-Bold",
    ],
    "bold": [
        "Avenir Next Heavy",
        "Bahnschrift SemiBold",
        "Arial-Bold",
        "Impact",
        "Helvetica-Bold",
        "DejaVuSans-Bold",
    ],
    "soft": [
        "Avenir Next Demi Bold",
        "TrebuchetMS-Bold",
        "Gill Sans Bold",
        "Calibri",
        "Arial-Bold",
        "DejaVuSans-Bold",
    ],
}

COLOR_PRESETS = {
    "sun": {"color": "#f6d34a", "stroke_color": "#111111"},
    "ivory": {"color": "#fff7e8", "stroke_color": "#111111"},
    "mint": {"color": "#d8fff3", "stroke_color": "#102a43"},
}

DEFAULT_STYLE = {
    "fontPreset": "clean",
    "colorPreset": "sun",
}

TITLE_FONT_PRESETS = [
    "Avenir Next Demi Bold",
    "Helvetica Neue Medium",
    "Arial-Bold",
    "Helvetica-Bold",
    "DejaVuSans-Bold",
]


def get_preferred_fonts():
    system_name = platform.system().lower()

    if system_name == 'windows':
        return ['Arial-Bold', 'Arial', 'Calibri', 'DejaVuSans-Bold']
    if system_name == 'darwin':
        return ['Arial-Bold', 'Helvetica-Bold', 'Arial', 'DejaVuSans-Bold']
    return ['DejaVuSans-Bold', 'LiberationSans-Bold', 'Arial-Bold', 'Arial']


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
    return list(dict.fromkeys([*preset_fonts, *fallback_fonts]))


def split_subtitle_text(text, start_time, end_time):
    cleaned_text = re.sub(r"\s+", " ", text).strip()
    if not cleaned_text:
        return []

    words = cleaned_text.split(" ")
    duration = max(0.2, end_time - start_time)

    if len(words) <= 4 and len(cleaned_text) <= 28:
        return [((start_time, end_time), cleaned_text)]

    if duration <= 1.4:
        max_words = 3
        max_chars = 16
    elif duration <= 2.6:
        max_words = 4
        max_chars = 22
    else:
        max_words = 5
        max_chars = 30

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
            minimum_duration = min(0.9, duration / len(chunks))
            chunk_duration = max(minimum_duration, proportional_duration)
            max_end = end_time - (remaining_chunks - 1) * 0.18
            chunk_end = min(max_end, current_start + chunk_duration)

        if chunk_end - current_start >= 0.12:
            timed_chunks.append(((current_start, chunk_end), chunk))

        current_start = chunk_end

    return timed_chunks or [((start_time, end_time), cleaned_text)]


def resolve_font_size(video_clip, text):
    base_size = int(video_clip.h * 0.049)
    text_length = len(text)

    if text_length >= 55:
        base_size -= 8
    elif text_length >= 40:
        base_size -= 5
    elif text_length >= 28:
        base_size -= 2

    return max(24, min(50, base_size))


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


def _prime_clip_duration(clip, duration=1.0):
    return clip.with_duration(_safe_duration(duration, fallback=1.0))


def create_textclip_with_fallback(text, video_clip, subtitle_style):
    last_error = None
    colors = COLOR_PRESETS[subtitle_style["colorPreset"]]
    preferred_font_size = resolve_font_size(video_clip, text)
    caption_width = int(video_clip.w * 0.58)
    max_text_height = int(video_clip.h * 0.17)

    for font in get_font_candidates(subtitle_style["fontPreset"]):
        for font_size in range(preferred_font_size, 23, -2):
            stroke_width = max(2, round(font_size * 0.12))

            try:
                shadow = TextClip(text=text,
                                  font=font,
                                  font_size=font_size,
                                  color="#000000",
                                  stroke_color="#000000",
                                  stroke_width=max(1, round(font_size * 0.06)),
                                  method='caption',
                                  size=(caption_width, None),
                                  interline=max(2, round(font_size * 0.1)),
                                  text_align='center')
                shadow = _prime_clip_duration(shadow)

                face = TextClip(text=text,
                                font=font,
                                font_size=font_size,
                                color=colors["color"],
                                stroke_color=colors["stroke_color"],
                                stroke_width=stroke_width,
                                method='caption',
                                size=(caption_width, None),
                                interline=max(2, round(font_size * 0.1)),
                                text_align='center')
                face = _prime_clip_duration(face)

                if max(shadow.h, face.h) > max_text_height:
                    shadow.close()
                    face.close()
                    continue

                shadow = shadow.with_opacity(0.24).with_position((0, max(2, round(font_size * 0.08))))
                face = face.with_position((0, 0))
                clip = CompositeVideoClip([shadow, face], size=(caption_width, face.h + max(6, round(font_size * 0.1))))
                clip = _prime_clip_duration(clip)

                return clip
            except Exception as error:
                last_error = error

    raise RuntimeError(
        "Could not render subtitles with any compatible font."
    ) from last_error


def create_header_text_clip(text, video_clip, *, font_candidates, font_size, color, stroke_color, stroke_width, width_ratio, max_height_ratio, opacity=1.0):
    last_error = None
    width = int(video_clip.w * width_ratio)
    max_height = int(video_clip.h * max_height_ratio)

    for font in font_candidates:
        for candidate_size in range(font_size, max(font_size - 10, 11), -2):
            try:
                clip = TextClip(
                    text=text,
                    font=font,
                    font_size=candidate_size,
                    color=color,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    method="caption",
                    size=(width, None),
                    interline=max(2, round(candidate_size * 0.12)),
                    text_align="center",
                )
                clip = _prime_clip_duration(clip)
                if clip.h > max_height:
                    clip.close()
                    continue
                if opacity != 1.0:
                    clip = clip.with_opacity(opacity)
                return clip
            except Exception as error:
                last_error = error

    raise RuntimeError("Could not render header text with any compatible font.") from last_error


def create_top_description_overlay(video_clip, title, reason, subtitle_style):
    title_text = _sanitize_overlay_text(title, 52)
    reason_text = _sanitize_overlay_text(reason, 82)
    if not title_text and not reason_text:
        return []

    font_candidates = list(dict.fromkeys([*get_font_candidates(subtitle_style["fontPreset"]), *TITLE_FONT_PRESETS]))
    overlays = []
    video_duration = _safe_duration(video_clip.duration)
    top_bar_height = int(video_clip.h * 0.12)
    top_bar = ColorClip(size=(video_clip.w, top_bar_height), color=(6, 10, 18))
    top_bar = top_bar.with_opacity(0.16).with_position((0, 0)).with_duration(video_duration)
    overlays.append(top_bar)

    current_y = int(video_clip.h * 0.028)

    if title_text:
        try:
            title_clip = create_header_text_clip(
                title_text,
                video_clip,
                font_candidates=font_candidates,
                font_size=resolve_top_text_size(video_clip, title_text, minimum=24, maximum=38, ratio=0.022),
                color="#f8fafc",
                stroke_color="#09111c",
                stroke_width=1,
                width_ratio=0.76,
                max_height_ratio=0.05,
            )
            title_clip = title_clip.with_position(("center", current_y)).with_duration(video_duration)
            overlays.append(title_clip)
            current_y += title_clip.h + int(video_clip.h * 0.005)
        except Exception:
            pass

    if reason_text:
        try:
            reason_clip = create_header_text_clip(
                reason_text,
                video_clip,
                font_candidates=font_candidates,
                font_size=resolve_top_text_size(video_clip, reason_text, minimum=17, maximum=24, ratio=0.015),
                color="#dbe7f3",
                stroke_color="#09111c",
                stroke_width=1,
                width_ratio=0.78,
                max_height_ratio=0.045,
                opacity=0.88,
            )
            reason_clip = reason_clip.with_position(("center", current_y)).with_duration(video_duration)
            overlays.append(reason_clip)
        except Exception:
            pass

    return overlays


def create_subtitles(video_clip, whisper_segments, clip_start_time, subtitle_style=None, clip_title=None, clip_reason=None):
    print("📝 Building subtitle layers...")
    resolved_style = normalize_subtitle_style(subtitle_style)
    video_duration = _safe_duration(video_clip.duration)

    subtitles_data = []
    for segment in whisper_segments:
        start = segment['start'] - clip_start_time
        end = segment['end'] - clip_start_time
        text = segment['text'].strip()

        if end > 0 and start < video_duration:
            start = max(0, start)
            end = min(video_duration, end)
            subtitles_data.extend(split_subtitle_text(text, start, end))

    subtitle_layers = []
    subtitle_y = int(video_clip.h * 0.78)
    for (start, end), text in subtitles_data:
        subtitle_clip = create_textclip_with_fallback(text, video_clip, resolved_style)
        subtitle_clip = _with_clip_timing(subtitle_clip, start, end)
        subtitle_clip = subtitle_clip.with_position(("center", subtitle_y))
        subtitle_layers.append(subtitle_clip)

    bottom_shade_height = int(video_clip.h * 0.3)
    bottom_shade = ColorClip(size=(video_clip.w, bottom_shade_height), color=(7, 12, 20))
    bottom_shade = bottom_shade.with_opacity(0.13).with_position((0, video_clip.h - bottom_shade_height)).with_duration(video_duration)
    focus_shade = ColorClip(size=(video_clip.w, int(video_clip.h * 0.2)), color=(7, 12, 20))
    focus_shade = focus_shade.with_opacity(0.08).with_position((0, int(video_clip.h * 0.74))).with_duration(video_duration)
    top_description_layers = create_top_description_overlay(video_clip, clip_title, clip_reason, resolved_style)

    layers = [video_clip, bottom_shade, focus_shade, *top_description_layers, *subtitle_layers]
    final_clip = CompositeVideoClip(layers, size=video_clip.size).with_duration(video_duration)
    if video_clip.audio is not None:
        final_clip = final_clip.with_audio(video_clip.audio.with_duration(video_duration))

    return final_clip