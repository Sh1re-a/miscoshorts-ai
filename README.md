# Miscoshorts AI

Create vertical YouTube Shorts from long-form videos using local Whisper transcription, Gemini for clip selection, and MoviePy for subtitle-ready rendering.

This fork adds a cleaner local workflow, a browser-first React dashboard, and a more shareable setup for demos and non-technical users.

## Quickstart

For real testers, use the launcher and nothing else:

- macOS: double-click `launch_app.command`
- Windows: double-click `launch_app.bat`

That is the supported first-run path.

### Recommended Tester Flow

1. Download the full GitHub ZIP.
2. Extract it to a normal writable folder.
3. Open the extracted folder.
4. Run `launch_app.bat` on Windows or `launch_app.command` on macOS.
5. Keep the launcher window open while the app is running.
6. Wait for setup/preflight to finish.
7. Open the browser app, paste a YouTube URL, add a Gemini key if needed, and start the render.

### What The Launcher Does

- checks Python
- checks FFmpeg
- checks the local writable folders
- prepares a local virtual environment when needed
- installs Python dependencies only when needed
- prepares the configured Whisper model before the first render
- builds the frontend only when `frontend/dist` is missing or outdated
- opens the local browser app

### First-Run Behavior

The first run is intentionally heavier than later runs.

- The local runtime lives in `.miscoshorts/`
- Logs live in `.miscoshorts/logs/`
- Setup state lives in `.miscoshorts/setup/`
- The Whisper model cache lives in `.miscoshorts/runtime/model-cache/`
- Reusable source/transcript cache lives in `outputs/cache/`
- Job outputs live in `outputs/jobs/<job-id>/`

Nothing inside `.miscoshorts/` is meant for GitHub. It is private local runtime state.

If the Whisper cache is deleted, the launcher preflight prepares it again before the next real render.

If the folder already contains `frontend/dist`, the launcher can skip Node.js entirely for normal users.

If you are sending this to another person, send the full project folder, not only the launcher file.

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

## Doctor / Diagnostics

You can run a machine check without starting a render:

```bash
python3 -m app.doctor
```

```powershell
py -m app.doctor
```

This reports friendly `PASS`, `WARN`, and `FAIL` checks for:

- Python
- FFmpeg
- writable runtime folders
- frontend availability
- Gemini key presence
- required Python packages
- diarization state
- Whisper cache state

To force preparation of the configured Whisper model during diagnostics:

```bash
python3 -m app.doctor --prepare-whisper
```

```powershell
py -m app.doctor --prepare-whisper
```

## Requirements

- Python 3.12+ recommended
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
- `WHISPER_MODEL` to choose the local speech model order. The default is `distil-large-v3,large-v3` so quality stays high by default.

## Free Pro Stack

For the strongest free local setup:

- keep `yt-dlp` on the default highest-quality source format
- use the `studio` render profile for final exports
- keep `LOCAL_CACHE_ENABLED=1` so repeat runs are faster
- install optional diarization support with `pip install -r requirements-optional.txt`
- let transcription default to `faster-whisper`
- keep the default `WHISPER_MODEL=distil-large-v3,large-v3` if you want the stronger standard quality path
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

The terminal flow now uses the same main pipeline as the browser app.

```bash
python3 -m app.cli
```

```powershell
py -m app.cli
```

You can also run:

```bash
python3 -m app.cli --doctor
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
DEFAULT_RENDER_PROFILE=studio
LOCAL_CACHE_ENABLED=1
WHISPER_BACKEND=auto
WHISPER_MODEL=distil-large-v3,large-v3
SPEAKER_DIARIZATION_MODE=auto
MISCOSHORTS_DEBUG=0
```

`.env` is ignored by git, so your key stays local.

## Output Files

Generated files are stored inside `outputs/<job-id>/`.

Typical artifacts:

- rendered short `.mp4`
- full transcript `.txt`
- temporary download cleaned up automatically after processing

Reusable caches live here:

- `outputs/cache/` for source video and transcript reuse
- `.miscoshorts/runtime/model-cache/` for Whisper and related model downloads

Support logs live here:

- `.miscoshorts/logs/`

Windows setup logs live here:

- `.miscoshorts/setup/windows-setup.log`

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

If someone deletes the local speech-model cache, the launcher and doctor can rebuild it automatically inside:

```text
.miscoshorts/runtime/model-cache/
```

The first transcription or Whisper preflight on that machine will be slower once while the configured model downloads again.

### Run Diagnostics Before Asking For Help

```bash
python3 -m app.doctor
```

```powershell
py -m app.doctor
```

Send:

- the launcher error text
- the doctor output
- the latest file in `.miscoshorts/logs/`
- `windows-setup.log` if setup failed on Windows

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

### Debug Mode

To keep normal users out of raw stack traces, the app runs in a calmer mode by default.

If you need more technical detail for debugging:

```bash
MISCOSHORTS_DEBUG=1 python3 -m app.doctor
```

```powershell
$env:MISCOSHORTS_DEBUG=1
py -m app.doctor
```

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
