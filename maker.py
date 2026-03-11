import yt_dlp
from moviepy import VideoFileClip
import os
import shutil
import whisper
import warnings
from dotenv import load_dotenv

# egna moduler
import cerebro_gemini as gemini_tjanst
import subtitulos as undertextning

warnings.filterwarnings("ignore")
load_dotenv()

URL_VIDEO = os.getenv("URL_VIDEO", "").strip()
UTDATAFILNAMN = os.getenv("OUTPUT_FILENAME", "short_con_subs.mp4").strip() or "short_con_subs.mp4"
ENV_PATH = ".env"


def uppdatera_env(nyckel, varde):
    rader = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as fil:
            rader = fil.readlines()

    ny_rad = f"{nyckel}={varde}\n"
    ersatt = False

    for index, rad in enumerate(rader):
        if rad.startswith(f"{nyckel}="):
            rader[index] = ny_rad
            ersatt = True
            break

    if not ersatt:
        rader.append(ny_rad)

    with open(ENV_PATH, "w", encoding="utf-8") as fil:
        fil.writelines(rader)


def be_om_varde(meddelande, nuvarande_varde=""):
    suffix = f" [{nuvarande_varde}]" if nuvarande_varde else ""
    varde = input(f"{meddelande}{suffix}: ").strip()
    return varde or nuvarande_varde


def ar_positiv_bekraftelse(varde):
    return varde.strip().lower() in {"j", "ja", "s", "si", "y", "yes"}


def kontrollera_beroenden():
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg ar inte installerat eller finns inte i PATH. I Windows kan du installera det med 'winget install Gyan.FFmpeg' och sedan starta om terminalen."
    )


def hamta_video_url():
    url = URL_VIDEO
    if not url or url == "TU_URL_DE_VIDEO_AQUI":
        url = be_om_varde("Klistra in URL:en till YouTube-videon")

    if not url:
        raise ValueError("Du maste ange en YouTube-URL for att fortsatta.")

    spara = input("Vill du spara den har URL:en som standard i .env? (j/N): ").strip()
    if ar_positiv_bekraftelse(spara):
        uppdatera_env("URL_VIDEO", url)

    return url


def hamta_utdatafilnamn():
    return be_om_varde("Namn pa utdatafilen", UTDATAFILNAMN) or "short_con_subs.mp4"


def hamta_api_nyckel():
    api_nyckel = os.getenv("GEMINI_API_KEY", "").strip()
    if api_nyckel:
        return api_nyckel

    api_nyckel = be_om_varde("Skriv in din GEMINI_API_KEY")
    if not api_nyckel:
        raise ValueError("Ingen GEMINI_API_KEY angavs.")

    spara = input("Vill du spara din API-nyckel i .env sa du slipper skriva in den varje gang? (j/N): ").strip()
    if ar_positiv_bekraftelse(spara):
        uppdatera_env("GEMINI_API_KEY", api_nyckel)

    return api_nyckel

def ladda_ner_video(url):
    print(f"📥 Laddar ner video: {url}...")
    ydl_opts = {'format': 'best[ext=mp4]', 'outtmpl': 'video_temp.%(ext)s', 'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return "video_temp.mp4"

def tolka_gemini_svar(text):
    """Plockar ut de strukturerade fälten ur Geminis textsvar."""
    data = {}
    for rad in text.split('\n'):
        if "TITEL:" in rad:
            data['titel'] = rad.split("TITEL:")[1].strip()
        if "START:" in rad:
            data['start'] = float(rad.split("START:")[1].strip())
        if "SLUT:" in rad:
            data['slut'] = float(rad.split("SLUT:")[1].strip())
        if "ORSAK:" in rad:
            data['orsak'] = rad.split("ORSAK:")[1].strip()
    return data

def main():
    video_path = None
    clip = None
    clip_final = None

    try:
        kontrollera_beroenden()
        url_video = hamta_video_url()
        utdatafilnamn = hamta_utdatafilnamn()
        api_nyckel = hamta_api_nyckel()

        video_path = ladda_ner_video(url_video)

        print("🔍 Transkriberar ljudet for att hitta tider...")
        model = whisper.load_model("base")
        resultat = model.transcribe(video_path)
        with open("transcripcion_completa.txt", "w", encoding="utf-8") as f:
            f.write(f"URL: {url_video}\n")
            f.write(resultat['text'])

        analys = gemini_tjanst.hitta_viralt_klipp(resultat['segments'], api_nyckel)
        klippdata = tolka_gemini_svar(analys)

        if 'start' not in klippdata or 'slut' not in klippdata:
            raise ValueError("Gemini returnerade inget giltigt START- och SLUT-intervall.")

        print("🤖 FORSLAG TILL SHORT:")
        print(f"📌 Titel: {klippdata.get('titel')}")
        print(f"⏱️ Tid: {klippdata.get('start')}s --> {klippdata.get('slut')}s")
        print(f"💡 Orsak: {klippdata.get('orsak')}")
        bekraftelse = input("Vill du skapa den? Skriv 'j' for att godkanna, eller ange egna tider (ex: 120-140): ")

        start = 0
        end = 0
        if ar_positiv_bekraftelse(bekraftelse):
            start = klippdata['start']
            end = klippdata['slut']
        elif '-' in bekraftelse:
            delar = bekraftelse.split('-')
            start = float(delar[0])
            end = float(delar[1])
        else:
            print("Avbrutet.")
            return

        print(f"🚀 Renderar shorten ({start}s till {end}s)...")
        clip = VideoFileClip(video_path).subclipped(start, end)

        w, h = clip.size
        new_width = h * (9/16)
        clip_vertical = clip.cropped(x1=w/2 - new_width/2, y1=0, x2=w/2 + new_width/2, y2=h)

        clip_final = undertextning.skapa_undertexter(clip_vertical, resultat['segments'], start)
        clip_final.write_videofile(utdatafilnamn,
                                   codec='libx264',
                                   audio_codec='aac',
                                   fps=24,
                                   threads=4)

        print(f"🎉 Videon ar klar: {utdatafilnamn}!")
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