import platform
import re

from moviepy import ColorClip, CompositeVideoClip, TextClip


FONT_PRESETS = {
    "clean": [
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
    "sun": {"base_color": "#fffaf0", "active_color": "#ffe07a", "stroke_color": "#101820"},
    "ivory": {"base_color": "#fffdf8", "active_color": "#f7d98a", "stroke_color": "#111827"},
    "mint": {"base_color": "#f5fffb", "active_color": "#9ae8c6", "stroke_color": "#18324a"},
}

DEFAULT_STYLE = {
    "fontPreset": "clean",
    "colorPreset": "ivory",
}

TOP_OVERLAY_MIN_DURATION = 1.8
TOP_OVERLAY_MAX_DURATION = 3.2
WORD_HIGHLIGHT_LEAD = 0.03
WORD_HIGHLIGHT_TAIL = 0.05

TITLE_FONT_PRESETS = [
    "SF Pro Display Semibold",
    "Avenir Next Bold",
    "Avenir Next Demi Bold",
    "Helvetica Neue Medium",
    "Aptos Display Bold",
    "Arial-Bold",
    "Helvetica-Bold",
    "DejaVuSans-Bold",
]


def get_preferred_fonts():
    system_name = platform.system().lower()

    if system_name == 'windows':
        return ['Aptos Display Bold', 'Aptos Bold', 'Segoe UI Semibold', 'Segoe UI Bold', 'Arial-Bold', 'Arial', 'Calibri', 'NotoSans-Bold', 'DejaVuSans-Bold']
    if system_name == 'darwin':
        return ['SF Pro Display Semibold', 'SF Pro Display Bold', 'Avenir Next Bold', 'Avenir Next Demi Bold', 'Helvetica Neue Medium', 'Helvetica Neue Bold', 'Helvetica-Bold', 'Arial-Bold', 'Arial', 'DejaVuSans-Bold']
    return ['NotoSans-Bold', 'Inter-SemiBold', 'DejaVuSans-Bold', 'LiberationSans-Bold', 'Arial-Bold', 'Arial']


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
    base_size = int(video_clip.h * 0.05)
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


def _expand_clip_canvas(clip, pad_x=0, pad_y=0):
    expanded = CompositeVideoClip(
        [clip.with_position((pad_x, pad_y))],
        size=(clip.w + pad_x * 2, clip.h + pad_y * 2),
    )
    return _prime_clip_duration(expanded, getattr(clip, "duration", 1.0))


def _intro_overlay_duration(video_duration):
    return min(TOP_OVERLAY_MAX_DURATION, max(TOP_OVERLAY_MIN_DURATION, video_duration * 0.36))


def _smooth_word_window(word_start, word_end, video_duration):
    start = max(0.0, word_start - WORD_HIGHLIGHT_LEAD)
    end = min(video_duration, word_end + WORD_HIGHLIGHT_TAIL)
    if end - start < 0.12:
        end = min(video_duration, start + 0.12)
    return start, end


def _create_caption_text_clip(text, font, font_size, color, stroke_color, stroke_width, width, interline):
    clip = TextClip(
        text=text,
        font=font,
        font_size=font_size,
        color=color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        method='caption',
        size=(width, None),
        interline=interline,
        text_align='center',
    )
    clip = _prime_clip_duration(clip)
    vertical_pad = max(6, round(font_size * 0.18) + stroke_width)
    return _expand_clip_canvas(clip, pad_y=vertical_pad)


def _create_label_text_clip(text, font, font_size, color, stroke_color, stroke_width):
    clip = TextClip(
        text=text,
        font=font,
        font_size=font_size,
        color=color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
    )
    clip = _prime_clip_duration(clip)
    horizontal_pad = max(6, round(font_size * 0.12) + stroke_width)
    vertical_pad = max(6, round(font_size * 0.18) + stroke_width)
    return _expand_clip_canvas(clip, pad_x=horizontal_pad, pad_y=vertical_pad)


def _normalize_word_text(text):
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
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
            len(current_chunk) >= 4
            or len(proposed_text) > 24
            or proposed_duration > 2.4
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


def _layout_word_clips(word_clips, caption_width, space_width, line_gap):
    if not word_clips:
        return None

    lines = []
    current_line = []
    current_width = 0

    for index, clip in enumerate(word_clips):
        proposed_width = clip.w if not current_line else current_width + space_width + clip.w
        if current_line and proposed_width > caption_width:
            lines.append((current_line, current_width))
            current_line = [index]
            current_width = clip.w
        else:
            current_line.append(index)
            current_width = proposed_width

    if current_line:
        lines.append((current_line, current_width))

    positions = {}
    current_y = 0
    total_height = 0

    for line_indexes, line_width in lines:
        line_height = max(word_clips[index].h for index in line_indexes)
        start_x = max(0, round((caption_width - line_width) / 2))
        current_x = start_x
        for line_index, word_index in enumerate(line_indexes):
            positions[word_index] = (current_x, current_y)
            current_x += word_clips[word_index].w
            if line_index < len(line_indexes) - 1:
                current_x += space_width
        current_y += line_height + line_gap
        total_height = current_y - line_gap

    return positions, total_height


def create_highlighted_chunk_variants(chunk_words, video_clip, subtitle_style):
    if not chunk_words:
        return []

    last_error = None
    palette = COLOR_PRESETS[subtitle_style["colorPreset"]]
    chunk_text = " ".join(word["text"] for word in chunk_words)
    preferred_font_size = resolve_font_size(video_clip, chunk_text)
    caption_width = int(video_clip.w * 0.68)
    max_text_height = int(video_clip.h * 0.22)

    for font in get_font_candidates(subtitle_style["fontPreset"]):
        for font_size in range(preferred_font_size, 23, -2):
            stroke_width = max(2, round(font_size * 0.1))
            shadow_stroke_width = max(1, round(font_size * 0.05))
            shadow_y = max(2, round(font_size * 0.07))
            line_gap = max(4, round(font_size * 0.08))
            space_width = max(round(font_size * 0.18), 8)

            base_word_clips = []
            try:
                for word in chunk_words:
                    base_word_clips.append(
                        _create_label_text_clip(
                            word["text"],
                            font,
                            font_size,
                            palette["base_color"],
                            palette["stroke_color"],
                            stroke_width,
                        )
                    )

                layout = _layout_word_clips(base_word_clips, caption_width, space_width, line_gap)
                if layout is None:
                    for clip in base_word_clips:
                        clip.close()
                    continue

                positions, total_height = layout
                canvas_height = total_height + max(10, round(font_size * 0.14))
                if canvas_height > max_text_height:
                    for clip in base_word_clips:
                        clip.close()
                    continue

                base_layers = []
                for index, word in enumerate(chunk_words):
                    position_x, position_y = positions[index]
                    shadow = _create_label_text_clip(
                        word["text"],
                        font,
                        font_size,
                        "#000000",
                        "#000000",
                        shadow_stroke_width,
                    )
                    shadow = shadow.with_opacity(0.16).with_position((position_x, position_y + shadow_y))
                    face = base_word_clips[index].with_position((position_x, position_y))
                    base_layers.extend([shadow, face])

                base_clip = CompositeVideoClip(base_layers, size=(caption_width, canvas_height))
                base_clip = _prime_clip_duration(base_clip)

                variants = []
                for index, word in enumerate(chunk_words):
                    active_shadow = _create_label_text_clip(
                        word["text"],
                        font,
                        font_size,
                        "#000000",
                        "#000000",
                        shadow_stroke_width,
                    )
                    active_shadow = active_shadow.with_opacity(0.18).with_position((positions[index][0], positions[index][1] + shadow_y))
                    active_face = _create_label_text_clip(
                        word["text"],
                        font,
                        font_size,
                        palette["active_color"],
                        palette["stroke_color"],
                        stroke_width,
                    )
                    active_face = active_face.with_position(positions[index])
                    variant = CompositeVideoClip([base_clip, active_shadow, active_face], size=(caption_width, canvas_height))
                    highlight_start, highlight_end = _smooth_word_window(word["start"], word["end"], _safe_duration(video_clip.duration))
                    variant = _prime_clip_duration(variant, highlight_end - highlight_start)
                    variants.append(((highlight_start, highlight_end), variant))

                return variants
            except Exception as error:
                last_error = error
            finally:
                for clip in base_word_clips:
                    try:
                        clip.close()
                    except Exception:
                        pass

    if last_error is not None:
        raise RuntimeError("Could not render highlighted word subtitles with any compatible font.") from last_error
    return []


def create_textclip_with_fallback(text, video_clip, subtitle_style):
    last_error = None
    colors = COLOR_PRESETS[subtitle_style["colorPreset"]]
    preferred_font_size = resolve_font_size(video_clip, text)
    caption_width = int(video_clip.w * 0.64)
    max_text_height = int(video_clip.h * 0.2)

    for font in get_font_candidates(subtitle_style["fontPreset"]):
        for font_size in range(preferred_font_size, 23, -2):
            stroke_width = max(2, round(font_size * 0.11))

            try:
                shadow = _create_caption_text_clip(
                    text,
                    font,
                    font_size,
                    "#000000",
                    "#000000",
                    max(1, round(font_size * 0.05)),
                    caption_width,
                    max(2, round(font_size * 0.1)),
                )

                face = _create_caption_text_clip(
                    text,
                    font,
                    font_size,
                    colors["base_color"],
                    colors["stroke_color"],
                    stroke_width,
                    caption_width,
                    max(2, round(font_size * 0.1)),
                )

                if max(shadow.h, face.h) > max_text_height:
                    shadow.close()
                    face.close()
                    continue

                shadow = shadow.with_opacity(0.18).with_position((0, max(2, round(font_size * 0.07))))
                face = face.with_position((0, 0))
                clip = CompositeVideoClip(
                    [shadow, face],
                    size=(max(shadow.w, face.w), max(shadow.h, face.h) + max(8, round(font_size * 0.12))),
                )
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
                shadow = TextClip(
                    text=text,
                    font=font,
                    font_size=candidate_size,
                    color="#000000",
                    stroke_color="#000000",
                    stroke_width=max(1, stroke_width),
                    method="caption",
                    size=(width, None),
                    interline=max(2, round(candidate_size * 0.1)),
                    text_align="center",
                )
                shadow = _prime_clip_duration(shadow)
                shadow = _expand_clip_canvas(shadow, pad_x=max(8, round(candidate_size * 0.08)), pad_y=max(8, round(candidate_size * 0.14)))

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
                clip = _expand_clip_canvas(clip, pad_x=max(8, round(candidate_size * 0.08)), pad_y=max(8, round(candidate_size * 0.14)))
                if clip.h > max_height:
                    shadow.close()
                    clip.close()
                    continue
                shadow = shadow.with_opacity(0.18).with_position((0, max(2, round(candidate_size * 0.08))))
                if opacity != 1.0:
                    clip = clip.with_opacity(opacity)
                layered = CompositeVideoClip([shadow, clip], size=(max(shadow.w, clip.w), max(shadow.h, clip.h) + max(4, round(candidate_size * 0.08))))
                layered = _prime_clip_duration(layered)
                return layered
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
    overlay_duration = _intro_overlay_duration(video_duration)
    top_bar_height = int(video_clip.h * 0.12)
    top_bar = ColorClip(size=(video_clip.w, top_bar_height), color=(6, 10, 18))
    top_bar = top_bar.with_opacity(0.16).with_position((0, 0)).with_duration(overlay_duration)
    overlays.append(top_bar)

    current_y = int(video_clip.h * 0.028)

    if title_text:
        try:
            title_clip = create_header_text_clip(
                title_text,
                video_clip,
                font_candidates=font_candidates,
                font_size=resolve_top_text_size(video_clip, title_text, minimum=22, maximum=34, ratio=0.02),
                color="#f8fbff",
                stroke_color="#0f172a",
                stroke_width=1,
                width_ratio=0.74,
                max_height_ratio=0.042,
            )
            title_clip = title_clip.with_position(("center", current_y)).with_duration(overlay_duration)
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
                font_size=resolve_top_text_size(video_clip, reason_text, minimum=15, maximum=20, ratio=0.013),
                color="#dce7f4",
                stroke_color="#0f172a",
                stroke_width=1,
                width_ratio=0.74,
                max_height_ratio=0.035,
                opacity=0.82,
            )
            reason_clip = reason_clip.with_position(("center", current_y)).with_duration(overlay_duration)
            overlays.append(reason_clip)
        except Exception:
            pass

    return overlays


def create_subtitles(video_clip, whisper_segments, clip_start_time, subtitle_style=None, clip_title=None, clip_reason=None):
    print("📝 Building subtitle layers...")
    resolved_style = normalize_subtitle_style(subtitle_style)
    video_duration = _safe_duration(video_clip.duration)

    subtitles_data = []
    highlighted_subtitle_layers = []
    for segment in whisper_segments:
        start = segment['start'] - clip_start_time
        end = segment['end'] - clip_start_time
        text = segment['text'].strip()

        if end > 0 and start < video_duration:
            start = max(0, start)
            end = min(video_duration, end)
            word_entries = extract_word_entries(segment, clip_start_time, video_duration)
            if word_entries:
                for chunk_words in split_word_entries(word_entries):
                    try:
                        variants = create_highlighted_chunk_variants(chunk_words, video_clip, resolved_style)
                    except Exception:
                        variants = []

                    if variants:
                        for (variant_start, variant_end), variant in variants:
                            variant = _with_clip_timing(variant, variant_start, variant_end)
                            highlighted_subtitle_layers.append(variant.with_position(("center", int(video_clip.h * 0.78))))
                    else:
                        chunk_text = " ".join(word["text"] for word in chunk_words)
                        subtitles_data.append(((chunk_words[0]["start"], chunk_words[-1]["end"]), chunk_text))
            else:
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

    layers = [video_clip, bottom_shade, focus_shade, *top_description_layers, *subtitle_layers, *highlighted_subtitle_layers]
    final_clip = CompositeVideoClip(layers, size=video_clip.size).with_duration(video_duration)
    if video_clip.audio is not None:
        final_clip = final_clip.with_audio(video_clip.audio.with_duration(video_duration))

    return final_clip