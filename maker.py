import yt_dlp
from moviepy import VideoFileClip
import os
import shutil
import whisper
import warnings
from dotenv import load_dotenv

# own modules
import cerebro_gemini as cerebro
import subtitulos

warnings.filterwarnings("ignore")
load_dotenv()

URL_VIDEO = os.getenv("URL_VIDEO", "").strip()
NOMBRE_SALIDA = os.getenv("OUTPUT_FILENAME", "short_con_subs.mp4").strip() or "short_con_subs.mp4"
ENV_PATH = ".env"


def actualizar_env(clave, valor):
    lineas = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as archivo:
            lineas = archivo.readlines()

    nueva_linea = f"{clave}={valor}\n"
    reemplazada = False

    for indice, linea in enumerate(lineas):
        if linea.startswith(f"{clave}="):
            lineas[indice] = nueva_linea
            reemplazada = True
            break

    if not reemplazada:
        lineas.append(nueva_linea)

    with open(ENV_PATH, "w", encoding="utf-8") as archivo:
        archivo.writelines(lineas)


def pedir_valor(mensaje, valor_actual=""):
    sufijo = f" [{valor_actual}]" if valor_actual else ""
    valor = input(f"{mensaje}{sufijo}: ").strip()
    return valor or valor_actual


def es_confirmacion_positiva(valor):
    return valor.strip().lower() in {"s", "si", "y", "yes"}


def validar_dependencias():
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg no esta instalado o no esta disponible en PATH. En Windows puedes instalarlo con 'winget install Gyan.FFmpeg' y despues reiniciar la terminal."
    )


def obtener_url_video():
    url = URL_VIDEO
    if not url or url == "TU_URL_DE_VIDEO_AQUI":
        url = pedir_valor("Pega la URL del video de YouTube")

    if not url:
        raise ValueError("Necesitas indicar una URL de YouTube para continuar.")

    guardar = input("Quieres guardar esta URL como valor por defecto en .env? (s/N): ").strip()
    if es_confirmacion_positiva(guardar):
        actualizar_env("URL_VIDEO", url)

    return url


def obtener_nombre_salida():
    return pedir_valor("Nombre del archivo de salida", NOMBRE_SALIDA) or "short_con_subs.mp4"


def obtener_api_key():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        return api_key

    api_key = pedir_valor("Escribe tu GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No se proporciono GEMINI_API_KEY.")

    guardar = input("Quieres guardar tu API key en .env para no escribirla cada vez? (s/N): ").strip()
    if es_confirmacion_positiva(guardar):
        actualizar_env("GEMINI_API_KEY", api_key)

    return api_key

def descargar_video(url):
    print(f"📥 Descargando video: {url}...")
    ydl_opts = {'format': 'best[ext=mp4]', 'outtmpl': 'video_temp.%(ext)s', 'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return "video_temp.mp4"

def parsear_respuesta_gemini(texto):
    """Extrae los datos limpios de la respuesta de texto de Gemini"""
    datos = {}
    lines = texto.split('\n')
    for line in lines:
        if "TITULO:" in line: datos['titulo'] = line.split("TITULO:")[1].strip()
        if "INICIO:" in line: datos['inicio'] = float(line.split("INICIO:")[1].strip())
        if "FIN:" in line: datos['fin'] = float(line.split("FIN:")[1].strip())
        if "RAZON:" in line: datos['razon'] = line.split("RAZON:")[1].strip()
    return datos

def main():
    video_path = None
    clip = None
    clip_final = None

    try:
        validar_dependencias()
        url_video = obtener_url_video()
        nombre_salida = obtener_nombre_salida()
        api_key = obtener_api_key()

        video_path = descargar_video(url_video)

        print("🔍 Transcribiendo audio para obtener tiempos...")
        model = whisper.load_model("base")
        resultado = model.transcribe(video_path)
        with open("transcripcion_completa.txt", "w", encoding="utf-8") as f:
            f.write(f"URL: {url_video}\n")
            f.write(resultado['text'])

        analisis = cerebro.encontrar_clip_viral(resultado['segments'], api_key)
        clip_data = parsear_respuesta_gemini(analisis)

        if 'inicio' not in clip_data or 'fin' not in clip_data:
            raise ValueError("Gemini no devolvio un rango valido de INICIO y FIN.")

        print("🤖 PROPUESTA DE SHORT:")
        print(f"📌 Título: {clip_data.get('titulo')}")
        print(f"⏱️ Tiempo: {clip_data.get('inicio')}s --> {clip_data.get('fin')}s")
        print(f"💡 Razón: {clip_data.get('razon')}")
        confirmacion = input("¿Te mola? Escribe 's' para crearlo, o introduce nuevos tiempos (ej: 120-140): ")

        start = 0
        end = 0
        if confirmacion.lower() == 's':
            start = clip_data['inicio']
            end = clip_data['fin']
        elif '-' in confirmacion:
            partes = confirmacion.split('-')
            start = float(partes[0])
            end = float(partes[1])
        else:
            print("Cancelado.")
            return

        print(f"🚀 Cocinando el Short ({start}s a {end}s)...")
        clip = VideoFileClip(video_path).subclipped(start, end)

        w, h = clip.size
        new_width = h * (9/16)
        clip_vertical = clip.cropped(x1=w/2 - new_width/2, y1=0, x2=w/2 + new_width/2, y2=h)

        clip_final = subtitulos.generar_subtitulos(clip_vertical, resultado['segments'], start)
        clip_final.write_videofile(nombre_salida,
                                   codec='libx264',
                                   audio_codec='aac',
                                   fps=24,
                                   threads=4)

        print(f"🎉 ¡Video listo: {nombre_salida}!")
    finally:
        if clip_final is not None:
            clip_final.close()
        if clip is not None:
            clip.close()
        if video_path and os.path.exists(video_path):
            os.remove(video_path)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\n❌ Error: {error}")