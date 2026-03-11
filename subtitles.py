import platform

from moviepy import TextClip, CompositeVideoClip
from moviepy.video.tools.subtitles import SubtitlesClip


FONT_PRESETS = {
    "clean": ["Arial-Bold", "Helvetica-Bold", "Arial", "DejaVuSans-Bold"],
    "bold": ["Arial-Bold", "Impact", "Helvetica-Bold", "DejaVuSans-Bold"],
    "soft": ["TrebuchetMS-Bold", "Arial-Bold", "Calibri", "DejaVuSans-Bold"],
}

COLOR_PRESETS = {
    "sun": {"color": "#f6d34a", "stroke_color": "#111111"},
    "ivory": {"color": "#fff7e8", "stroke_color": "#111111"},
    "mint": {"color": "#d8fff3", "stroke_color": "#102a43"},
}

DEFAULT_STYLE = {
    "fontPreset": "clean",
    "colorPreset": "sun",
    "fontSize": 35,
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

    try:
        font_size = int(merged.get("fontSize", DEFAULT_STYLE["fontSize"]))
    except (TypeError, ValueError):
        font_size = DEFAULT_STYLE["fontSize"]

    font_size = max(24, min(56, font_size))

    return {
        "fontPreset": font_preset,
        "colorPreset": color_preset,
        "fontSize": font_size,
    }


def get_font_candidates(font_preset):
    preset_fonts = FONT_PRESETS.get(font_preset, [])
    fallback_fonts = get_preferred_fonts()
    return list(dict.fromkeys([*preset_fonts, *fallback_fonts]))


def create_textclip_with_fallback(text, video_clip, subtitle_style):
    last_error = None
    colors = COLOR_PRESETS[subtitle_style["colorPreset"]]

    for font in get_font_candidates(subtitle_style["fontPreset"]):
        try:
            return TextClip(text=text,
                            font=font,
                            font_size=subtitle_style["fontSize"],
                            color=colors["color"],
                            stroke_color=colors["stroke_color"],
                            stroke_width=3,
                            method='caption',
                            size=(int(video_clip.w * 0.8), None),
                            text_align='center')
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
            subtitles_data.append(((start, end), text))

            text_style = lambda txt: create_textclip_with_fallback(txt, video_clip, resolved_style)

    subtitles = SubtitlesClip(subtitles=subtitles_data, make_textclip=text_style)
    subtitles = subtitles.with_position(('center', 'center'))
    final_clip = CompositeVideoClip([video_clip, subtitles])
    
    return final_clip