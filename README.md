# Miscoshorts AI

Create vertical YouTube Shorts from long-form videos using Whisper for transcription, Gemini for clip selection, and MoviePy for the final render.

This fork focuses on a simpler setup flow so the tool is easier to demo, maintain, and share.

## What It Does

- Downloads a YouTube video with `yt-dlp`
- Transcribes the audio with `openai-whisper`
- Asks Gemini to find the strongest short-form segment
- Crops the video to 9:16 format
- Adds centered subtitles automatically
- Exports a ready-to-post `.mp4`

## Demo Flow

1. Paste a YouTube URL when the script asks for it.
2. Paste your Gemini API key or save it in a local `.env` file.
3. Review the suggested viral segment.
4. Accept the suggestion or type your own start and end time.
5. Wait for the short to render.

## Requirements

- Python 3.12+
- FFmpeg installed and available in `PATH`
- A Gemini API key

## Quick Start

```bash
git clone git@github.com:Sh1re-a/miscoshorts-ai.git
cd miscoshorts-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install FFmpeg:

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update && sudo apt install ffmpeg
```

Run the app:

```bash
python3 maker.py
```

## Configuration

You can run the tool without editing any Python files.

At startup the script can ask for:

- `GEMINI_API_KEY`
- `URL_VIDEO`
- `OUTPUT_FILENAME`

If you want to save those values locally, copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

Example `.env` values:

```env
GEMINI_API_KEY=your_gemini_api_key_here
URL_VIDEO=https://www.youtube.com/watch?v=example
OUTPUT_FILENAME=short_con_subs.mp4
```

`.env` is ignored by git, so your key stays local.

## Output Files

- `short_con_subs.mp4`: final rendered short
- `transcripcion_completa.txt`: full transcription
- `video_temp.mp4`: temporary download removed after processing

## Packaging Idea

If you want to share this with a friend later, package it as a standalone app with PyInstaller after testing the Python version locally.

Typical flow:

```bash
pip install pyinstaller
pyinstaller --onefile maker.py
```

Build the final app on the same operating system your friend will use.

## Troubleshooting

### Missing FFmpeg

Check that FFmpeg is installed:

```bash
ffmpeg -version
```

### Whisper Package Conflicts

If the wrong `whisper` package is installed:

```bash
pip uninstall whisper
pip install openai-whisper
```

### Font Errors

If subtitle rendering fails because of fonts, try changing the font in `subtitulos.py` to one available on your system.

Good fallback options:

- `Arial-Bold`
- `LiberationSans-Bold`
- `Ubuntu-Bold`

### Gemini Errors

- Make sure your API key is valid
- Make sure the `.env` file is in the project root
- Check your Gemini quota and model access

## Project Structure

```text
miscoshorts-ai/
├── maker.py
├── cerebro_gemini.py
├── subtitulos.py
├── requirements.txt
├── .env.example
└── README.md
```

## Roadmap

- Better error messages for failed downloads
- Easier packaging for non-technical users
- Optional desktop app build
- Cleaner release workflow for GitHub