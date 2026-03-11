import platform

from moviepy import TextClip, CompositeVideoClip
from moviepy.video.tools.subtitles import SubtitlesClip


def get_preferred_fonts():
    system_name = platform.system().lower()

    if system_name == 'windows':
        return ['Arial-Bold', 'Arial', 'Calibri', 'DejaVuSans-Bold']
    if system_name == 'darwin':
        return ['Arial-Bold', 'Helvetica-Bold', 'Arial', 'DejaVuSans-Bold']
    return ['DejaVuSans-Bold', 'LiberationSans-Bold', 'Arial-Bold', 'Arial']


def create_textclip_with_fallback(text, video_clip):
    last_error = None

    for font in get_preferred_fonts():
        try:
            return TextClip(text=text,
                            font=font,
                            font_size=35,
                            color='yellow',
                            stroke_color='black',
                            stroke_width=3,
                            method='caption',
                            size=(int(video_clip.w * 0.8), None),
                            text_align='center')
        except Exception as error:
            last_error = error

    raise RuntimeError(
        "Could not render subtitles with any compatible font."
    ) from last_error


def create_subtitles(video_clip, whisper_segments, clip_start_time):
    print("📝 Building subtitle layers...")

    subtitles_data = []
    for segment in whisper_segments:
        start = segment['start'] - clip_start_time
        end = segment['end'] - clip_start_time
        text = segment['text'].strip()

        if end > 0 and start < video_clip.duration:
            start = max(0, start)
            end = min(video_clip.duration, end)
            subtitles_data.append(((start, end), text))

    text_style = lambda txt: create_textclip_with_fallback(txt, video_clip)

    subtitles = SubtitlesClip(subtitles=subtitles_data, make_textclip=text_style)
    subtitles = subtitles.with_position(('center', 'center'))
    final_clip = CompositeVideoClip([video_clip, subtitles])
    
    return final_clip