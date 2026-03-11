import platform

from moviepy import TextClip, CompositeVideoClip
from moviepy.video.tools.subtitles import SubtitlesClip


def obtener_fuentes_preferidas():
    sistema = platform.system().lower()

    if sistema == 'windows':
        return ['Arial-Bold', 'Arial', 'Calibri', 'DejaVuSans-Bold']
    if sistema == 'darwin':
        return ['Arial-Bold', 'Helvetica-Bold', 'Arial', 'DejaVuSans-Bold']
    return ['DejaVuSans-Bold', 'LiberationSans-Bold', 'Arial-Bold', 'Arial']


def crear_textclip_con_fallback(txt, video_clip):
    ultimo_error = None

    for fuente in obtener_fuentes_preferidas():
        try:
            return TextClip(text=txt,
                            font=fuente,
                            font_size=35,
                            color='yellow',
                            stroke_color='black',
                            stroke_width=3,
                            method='caption',
                            size=(int(video_clip.w * 0.8), None),
                            text_align='center')
        except Exception as error:
            ultimo_error = error

    raise RuntimeError(
        "No se pudo crear el texto de subtitulos con ninguna fuente compatible."
    ) from ultimo_error

def generar_subtitulos(video_clip, segmentos_whisper, tiempo_inicio_recorte):
    print("📝 Generando capas de subtítulos...")

    subs = []
    for segmento in segmentos_whisper:
        start = segmento['start'] - tiempo_inicio_recorte
        end = segmento['end'] - tiempo_inicio_recorte
        texto = segmento['text'].strip()

        if end > 0 and start < video_clip.duration:
            start = max(0, start)
            end = min(video_clip.duration, end)
            subs.append(((start, end), texto))

    estilo_texto = lambda txt: crear_textclip_con_fallback(txt, video_clip)

    subtitles = SubtitlesClip(subtitles=subs, make_textclip=estilo_texto)
    subtitles = subtitles.with_position(('center', 'center'))
    final_clip = CompositeVideoClip([video_clip, subtitles])
    
    return final_clip