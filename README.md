# Miscoshorts AI

Create vertical YouTube Shorts from long-form videos using local Whisper transcription, Gemini for clip selection, and MoviePy for subtitle-ready rendering.

This fork adds a cleaner local workflow, a browser-first React dashboard, and a more shareable setup for demos and non-technical users.

## Run The App

If you are not technical, use the app launch file and nothing else:

- macOS: double-click `launch_app.command`
- Windows: double-click `launch_app.bat`

That is the main way to run this project.

## Recommended Customer Flow

If someone is testing this from GitHub, the clean path is:

1. Click `Code` on GitHub and choose `Download ZIP`
2. Extract the full zip to a normal folder
3. Open the extracted project folder
4. Double-click `launch_app.bat` on Windows or `launch_app.command` on macOS
5. Keep the launcher window open while the app runs
6. Paste a YouTube URL, add a Gemini API key if needed, and start the render
7. Download the finished clips and transcript from the browser

The browser is just the interface. The launcher window is what keeps the local app alive.

On Windows, the launcher can also handle first-time setup for you. It installs missing dependencies, prepares the app, and opens it in the browser. Later launches are faster and reuse the existing setup.

On macOS, `launch_app.command` now prefers the built app when `frontend/dist` is included, and falls back to the local developer flow only when the built frontend is missing.

The launcher stores internal runtime files inside `.miscoshorts/` so the main project folder stays cleaner.

Speech models are also kept inside `.miscoshorts/runtime/model-cache/` now, not in a giant shared global cache. If a tester deletes that folder by mistake, the app just downloads the smaller default speech model again on the next run.

If the folder already contains `frontend/dist`, the launcher uses that built app directly and skips Node.js completely.

If you are sending this to a friend, send the full project folder, not just the launcher file.

## Thanks

Big thanks to the original creator for the idea, codebase foundation, and tutorial inspiration behind this project. This fork builds on that work and reshapes it into a more user-friendly local tool.

## What It Does

- Downloads a YouTube video with `yt-dlp`
- Transcribes the audio with local Whisper models through `faster-whisper`
- Uses Gemini to select the strongest short-form moments
- Can generate up to 5 export-ready clips from one source video
- Reframes the video into a cleaner 9:16 master with centered vertical composition
- Adds subtitles automatically
- Exports a Studio HQ `1080x1920` `.mp4` with stronger H.264 and AAC settings
- Offers both terminal mode and a local browser UI

## Browser Mode

The new default experience is a local browser dashboard built with React.

You paste:

- a YouTube link
- a Gemini API key

The local app then:

1. sends the request to a Python backend running on your machine
2. downloads the highest-quality source video and audio it can get from YouTube
3. transcribes it with Whisper
4. asks Gemini for the strongest 3 Shorts moments
5. renders dynamic subtitles and exports high-quality vertical MP4 files
6. lets you download both the video and the transcript from the browser

If `GEMINI_API_KEY` already exists in `.env`, the browser app can use that automatically. The user does not have to paste the key every time.

The current browser flow is intentionally simplified: it runs the default 3-clip Shorts workflow and exports them with the `Studio HQ 1080x1920 MP4` profile.

## Requirements

- Python 3.12+
- Node.js 20+
- FFmpeg installed and available in `PATH`
- A Gemini API key

Optional server-oriented environment variables:

- `MAX_CONCURRENT_JOBS` to cap how many renders run at once
- `MAX_QUEUED_JOBS` to cap how many jobs can wait in line
- `JOB_RETENTION_HOURS` to auto-delete old job state and rendered outputs
- `MISCOSHORTS_HOST` and `MISCOSHORTS_PORT` to bind the Flask server
- `DEFAULT_RENDER_PROFILE` to choose `fast`, `balanced`, or `studio`
- `LOCAL_CACHE_ENABLED` to enable local source/transcript reuse
- `SPEAKER_DIARIZATION_MODE` to choose `auto`, `heuristic`, or `pyannote`
- `PYANNOTE_AUTH_TOKEN` or `HF_TOKEN` to enable optional higher-accuracy pyannote diarization
- `WHISPER_BACKEND` to choose `auto`, `faster-whisper`, or `openai-whisper`
- `WHISPER_MODEL` to choose the local speech model order. The default is `small,base` for a better quality-to-size balance on customer machines.

## Free Pro Stack

For the strongest free local setup:

- keep `yt-dlp` on the default highest-quality source format
- use the `studio` render profile for final exports
- keep `LOCAL_CACHE_ENABLED=1` so repeat runs are faster
- install optional diarization support with `pip install -r requirements-optional.txt`
- let transcription default to `faster-whisper`
- keep the default `WHISPER_MODEL=small,base` unless you specifically want a larger model on a stronger machine
- set `PYANNOTE_AUTH_TOKEN` and keep `SPEAKER_DIARIZATION_MODE=auto`

That gives you:

- free local Whisper transcription
- faster local transcription through `faster-whisper` when installed
- free local heuristic speaker analysis by default
- optional higher-accuracy local pyannote diarization when available
- higher-quality H.264 exports with stronger rate control and AAC audio

The Python Gemini integration now uses the supported `google-genai` SDK.

For packaged Windows releases that already include `frontend/dist`, Node.js is not required for the end user.

## Developer Setup

### macOS / Linux

```bash
git clone git@github.com:Sh1re-a/miscoshorts-ai.git
cd miscoshorts-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd frontend
npm install
cd ..
```

### Windows PowerShell

```powershell
git clone git@github.com:Sh1re-a/miscoshorts-ai.git
cd miscoshorts-ai
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd frontend
npm install
cd ..
```

Install FFmpeg:

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update && sudo apt install ffmpeg
```

```powershell
# Windows with winget
winget install Gyan.FFmpeg

# or with Chocolatey
choco install ffmpeg
```

## Manual Run

A terminal-based local start is also available:

```bash
python3 -m app.start_local
```

```powershell
py -m app.start_local
```

That script starts the backend, starts the React frontend, and opens the browser automatically.

If you want the non-technical option instead of typing in the terminal:

- macOS: double-click `launch_app.command`
- Windows: double-click `launch_app.bat`

On macOS, the launcher uses the built frontend automatically when it is available. If no built frontend is present, it starts the local React dev server instead.

On Windows, `launch_app.bat` now does more than just start the app:

- installs Python if missing
- installs FFmpeg if missing
- creates a local virtual environment
- installs Python dependencies
- builds the frontend only when no prebuilt frontend is included
- opens the local app in the browser

On the first run it sets everything up. On later runs it reuses the existing setup and skips reinstalling or rebuilding unless something changed.

That makes it the best file to send to a non-technical Windows user together with the full project folder.

If you prefer to start everything manually:

Start the backend:

```bash
python3 -m app.server
```

```powershell
py -m app.server
```

Start the frontend in a second terminal:

```bash
cd frontend
npm run dev
```

Then open:

```text
http://127.0.0.1:5173
```

## Run As One Local App

If you build the frontend first, the Python server can serve the finished browser UI directly.

```bash
cd frontend
npm run build
cd ..
python3 -m app.server
```

Then open:

```text
http://127.0.0.1:5001
```

## Terminal Mode

The original terminal flow still exists if you prefer it.

```bash
python3 -m app.cli
```

```powershell
py -m app.cli
```

## Configuration

You can still save local values in a `.env` file.

Copy the example file:

```bash
cp .env.example .env
```

```powershell
Copy-Item .env.example .env
```

Example values:

```env
GEMINI_API_KEY=your_gemini_api_key_here
URL_VIDEO=https://www.youtube.com/watch?v=example
OUTPUT_FILENAME=short_con_subs.mp4
```

`.env` is ignored by git, so your key stays local.

## Output Files

Generated files are stored inside `outputs/<job-id>/`.

Typical artifacts:

- rendered short `.mp4`
- full transcript `.txt`
- temporary download cleaned up automatically after processing

## Troubleshooting

### Windows Cannot Install Or Find Python

If the Windows launcher says Python could not be downloaded or still cannot find Python after install:

1. run `launch_app.bat` again from the project folder
2. the launcher now checks Python, Node.js, and FFmpeg automatically when they are needed
3. if `winget` is blocked or missing, it falls back to direct downloads for Python, Node.js, and FFmpeg
4. if it still fails, leave the window open and read the exact error message shown there

Important:

- the launcher should keep the window open on failure now
- a full setup log is written to `.miscoshorts/setup/windows-setup.log`
- send the full error text from the launcher window if it still stops

### Missing FFmpeg

Check that FFmpeg is installed:

```bash
ffmpeg -version
```

If Windows still cannot find FFmpeg after install, close and reopen PowerShell so `PATH` refreshes.

### Missing Flask Or Other Python Packages

Reinstall Python dependencies:

```bash
pip install -r requirements.txt
```

### Missing Or Deleted Whisper Cache

If someone deletes the local speech-model cache, the app should now rebuild it automatically inside:

```text
.miscoshorts/runtime/model-cache/
```

The first transcription on that machine will be slower once while the smaller default model downloads again.

### Whisper Package Conflicts

If the wrong `whisper` package is installed:

```bash
pip uninstall whisper
pip install openai-whisper
```

### Font Errors

If subtitle rendering fails because of fonts, the app already tries several fallbacks, especially for Windows. If needed, change the font list in `app/subtitles.py`.

The current subtitle renderer now prefers higher-quality system fonts first, uses a narrower caption width, and places subtitles lower in the frame for a cleaner Shorts look.

### Gemini Errors

- make sure your API key is valid
- make sure you still have Gemini quota available
- check that your local firewall or proxy is not blocking requests

## Project Structure

```text
miscoshorts-ai/
├── app/
│   ├── __init__.py
│   ├── app_launcher.py
│   ├── cli.py
│   ├── gemini_analyzer.py
│   ├── paths.py
│   ├── server.py
│   ├── shorts_service.py
│   ├── start_local.py
│   └── subtitles.py
├── frontend/
├── outputs/
├── setup_windows.ps1
├── launch_app.command
├── launch_app.bat
├── requirements.txt
├── .env.example
└── README.md
```

For normal users, `launch_app.command` and `launch_app.bat` are the only entry points they should need. The backend implementation now lives inside `app/`, and Windows launcher-managed runtime files live in `.miscoshorts/`.

## Roadmap

- Better job history in the browser UI
- More control over clip selection before render
- Cleaner GitHub release workflow
