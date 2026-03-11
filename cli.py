import yt_dlp
from moviepy import VideoFileClip
import os
import shutil
import whisper
import warnings
from dotenv import load_dotenv

# local modules
import gemini_analyzer
import subtitles

warnings.filterwarnings("ignore")
load_dotenv()

URL_VIDEO = os.getenv("URL_VIDEO", "").strip()
OUTPUT_FILENAME = os.getenv("OUTPUT_FILENAME", "short_con_subs.mp4").strip() or "short_con_subs.mp4"
ENV_PATH = ".env"


def update_env_file(key, value):
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as file_handle:
            lines = file_handle.readlines()

    new_line = f"{key}={value}\n"
    replaced = False

    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = new_line
            replaced = True
            break

    if not replaced:
        lines.append(new_line)

    with open(ENV_PATH, "w", encoding="utf-8") as file_handle:
        file_handle.writelines(lines)


def prompt_value(message, current_value=""):
    suffix = f" [{current_value}]" if current_value else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or current_value


def is_positive_confirmation(value):
    return value.strip().lower() in {"j", "ja", "s", "si", "y", "yes"}


def ensure_dependencies():
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg is not installed or not available in PATH. On Windows, install it with 'winget install Gyan.FFmpeg' and restart the terminal."
    )


def get_video_url():
    url = URL_VIDEO
    if not url or url == "TU_URL_DE_VIDEO_AQUI":
        url = prompt_value("Paste the YouTube video URL")

    if not url:
        raise ValueError("You must provide a YouTube URL to continue.")

    save_value = input("Do you want to save this URL as the default in .env? (y/N): ").strip()
    if is_positive_confirmation(save_value):
        update_env_file("URL_VIDEO", url)

    return url


def get_output_filename():
    return prompt_value("Output filename", OUTPUT_FILENAME) or "short_con_subs.mp4"


def get_api_key():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        return api_key

    api_key = prompt_value("Enter your GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No GEMINI_API_KEY was provided.")

    save_value = input("Do you want to save your API key in .env so you do not need to enter it every time? (y/N): ").strip()
    if is_positive_confirmation(save_value):
        update_env_file("GEMINI_API_KEY", api_key)

    return api_key

def download_video(url):
    print(f"📥 Downloading video: {url}...")
    ydl_opts = {'format': 'best[ext=mp4]', 'outtmpl': 'video_temp.%(ext)s', 'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return "video_temp.mp4"

def parse_gemini_response(text):
    """Extract structured fields from Gemini's plain-text response."""
    data = {}
    for line in text.split('\n'):
        if "TITLE:" in line:
            data['title'] = line.split("TITLE:")[1].strip()
        if "START:" in line:
            data['start'] = float(line.split("START:")[1].strip())
        if "END:" in line:
            data['end'] = float(line.split("END:")[1].strip())
        if "REASON:" in line:
            data['reason'] = line.split("REASON:")[1].strip()
    return data

def main():
    video_path = None
    clip = None
    clip_final = None

    try:
        ensure_dependencies()
        video_url = get_video_url()
        output_filename = get_output_filename()
        api_key = get_api_key()

        video_path = download_video(video_url)

        print("🔍 Transcribing audio to get timestamps...")
        model = whisper.load_model("base")
        result = model.transcribe(video_path)
        with open("transcripcion_completa.txt", "w", encoding="utf-8") as f:
            f.write(f"URL: {video_url}\n")
            f.write(result['text'])

        analysis = gemini_analyzer.find_viral_clip(result['segments'], api_key)
        clip_data = parse_gemini_response(analysis)

        if 'start' not in clip_data or 'end' not in clip_data:
            raise ValueError("Gemini did not return a valid START and END range.")

        print("🤖 SHORT PROPOSAL:")
        print(f"📌 Title: {clip_data.get('title')}")
        print(f"⏱️ Time: {clip_data.get('start')}s --> {clip_data.get('end')}s")
        print(f"💡 Reason: {clip_data.get('reason')}")
        confirmation = input("Do you want to create it? Type 'y' to accept, or enter custom timestamps (for example 120-140): ")

        start = 0
        end = 0
        if is_positive_confirmation(confirmation):
            start = clip_data['start']
            end = clip_data['end']
        elif '-' in confirmation:
            parts = confirmation.split('-')
            start = float(parts[0])
            end = float(parts[1])
        else:
            print("Cancelled.")
            return

        print(f"🚀 Rendering the short ({start}s to {end}s)...")
        clip = VideoFileClip(video_path).subclipped(start, end)

        w, h = clip.size
        new_width = h * (9/16)
        clip_vertical = clip.cropped(x1=w/2 - new_width/2, y1=0, x2=w/2 + new_width/2, y2=h)

        clip_final = subtitles.create_subtitles(clip_vertical, result['segments'], start)
        clip_final.write_videofile(output_filename,
                                   codec='libx264',
                                   audio_codec='aac',
                                   fps=24,
                                   threads=4)

        print(f"🎉 Video ready: {output_filename}!")
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