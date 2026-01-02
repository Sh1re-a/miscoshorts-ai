from moviepy import TextClip, CompositeVideoClip
from moviepy.video.tools.subtitles import SubtitlesClip

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

    estilo_texto = lambda txt: TextClip(text=txt, 
                                        font='DejaVuSans-Bold', 
                                        font_size=35, 
                                        color='yellow', 
                                        stroke_color='black', 
                                        stroke_width=3, 
                                        method='caption',
                                        size=(int(video_clip.w * 0.8), None),
                                        text_align='center')

    subtitles = SubtitlesClip(subtitles=subs, make_textclip=estilo_texto)
    subtitles = subtitles.with_position(('center', 'center'))
    final_clip = CompositeVideoClip([video_clip, subtitles])
    
    return final_clip