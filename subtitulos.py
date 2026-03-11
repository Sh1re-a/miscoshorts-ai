import platform

from moviepy import TextClip, CompositeVideoClip
from moviepy.video.tools.subtitles import SubtitlesClip


def hamta_foredragna_fonter():
    systemnamn = platform.system().lower()

    if systemnamn == 'windows':
        return ['Arial-Bold', 'Arial', 'Calibri', 'DejaVuSans-Bold']
    if systemnamn == 'darwin':
        return ['Arial-Bold', 'Helvetica-Bold', 'Arial', 'DejaVuSans-Bold']
    return ['DejaVuSans-Bold', 'LiberationSans-Bold', 'Arial-Bold', 'Arial']


def skapa_textklipp_med_reservfont(text, video_clip):
    senaste_fel = None

    for font in hamta_foredragna_fonter():
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
            senaste_fel = error

    raise RuntimeError(
        "Det gick inte att skapa undertexten med nagon kompatibel font."
    ) from senaste_fel


def skapa_undertexter(video_clip, whisper_segment, beskarning_start):
    print("📝 Skapar undertextlager...")

    undertexter = []
    for segment in whisper_segment:
        start = segment['start'] - beskarning_start
        end = segment['end'] - beskarning_start
        text = segment['text'].strip()

        if end > 0 and start < video_clip.duration:
            start = max(0, start)
            end = min(video_clip.duration, end)
            undertexter.append(((start, end), text))

    textstil = lambda txt: skapa_textklipp_med_reservfont(txt, video_clip)

    subtitles = SubtitlesClip(subtitles=undertexter, make_textclip=textstil)
    subtitles = subtitles.with_position(('center', 'center'))
    final_clip = CompositeVideoClip([video_clip, subtitles])
    
    return final_clip