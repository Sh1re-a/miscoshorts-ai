import platform
import re

from moviepy import ColorClip, CompositeVideoClip, TextClip
from moviepy.video.tools.subtitles import SubtitlesClip


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
    base_size = int(video_clip.h * 0.055)
    text_length = len(text)

    if text_length >= 55:
        base_size -= 8
    elif text_length >= 40:
        base_size -= 5
    elif text_length >= 28:
        base_size -= 2

    return max(26, min(58, base_size))


def create_textclip_with_fallback(text, video_clip, subtitle_style):
    last_error = None
    colors = COLOR_PRESETS[subtitle_style["colorPreset"]]
    preferred_font_size = resolve_font_size(video_clip, text)
    caption_width = int(video_clip.w * 0.64)
    max_text_height = int(video_clip.h * 0.2)

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

                if max(shadow.h, face.h) > max_text_height:
                    shadow.close()
                    face.close()
                    continue

                shadow = shadow.with_opacity(0.32).with_position((0, max(2, round(font_size * 0.08))))
                face = face.with_position((0, 0))
                clip = CompositeVideoClip([shadow, face], size=(caption_width, face.h + max(6, round(font_size * 0.1))))

                return clip
            except Exception as error:
                last_error = error

    raise RuntimeError(
        "Could not render subtitles with any compatible font."
    ) from last_error


def create_subtitles(video_clip, whisper_segments, clip_start_time, subtitle_style=None):
    print("📝 Building subtitle layers...")
    resolved_style = normalize_subtitle_style(subtitle_style)

    subtitles_data = []
    for segment in whisper_segments:
        start = segment['start'] - clip_start_time
        end = segment['end'] - clip_start_time
        text = segment['text'].strip()

        if end > 0 and start < video_clip.duration:
            start = max(0, start)
            end = min(video_clip.duration, end)
            subtitles_data.extend(split_subtitle_text(text, start, end))

    text_style = lambda txt: create_textclip_with_fallback(txt, video_clip, resolved_style)

    subtitles = SubtitlesClip(subtitles=subtitles_data, make_textclip=text_style)
    subtitles = subtitles.with_position(("center", int(video_clip.h * 0.74)))
    bottom_shade_height = int(video_clip.h * 0.36)
    bottom_shade = ColorClip(size=(video_clip.w, bottom_shade_height), color=(7, 12, 20))
    bottom_shade = bottom_shade.with_opacity(0.18).with_position((0, video_clip.h - bottom_shade_height))
    focus_shade = ColorClip(size=(video_clip.w, int(video_clip.h * 0.2)), color=(7, 12, 20))
    focus_shade = focus_shade.with_opacity(0.12).with_position((0, int(video_clip.h * 0.7)))
    final_clip = CompositeVideoClip([video_clip, bottom_shade, focus_shade, subtitles])
    
    return final_clip