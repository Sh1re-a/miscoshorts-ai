# Miscoshorts AI

Create vertical YouTube Shorts from long-form videos using local Whisper transcription, Gemini for clip selection, and MoviePy for subtitle-ready rendering.

This fork adds a cleaner local workflow, a browser-first React dashboard, and a more shareable setup for demos and non-technical users.

## Quickstart

For real testers, use the launcher and nothing else:

- macOS: double-click `launch_app.command`
- Windows: double-click `launch_app.bat`

That is the supported first-run path.

## Get Me Render-Ready

If the app feels broken or blocked, use this exact recovery path:

### macOS

```bash
cd /Users/shirre/ws/miscoshorts-ai
zsh launch_app.command
```

### Windows

Double-click [launch_app.bat](/Users/shirre/ws/miscoshorts-ai/launch_app.bat)

What “render-ready” means now:

- `REQUIRED` checks are passing
- the managed runtime has `faster-whisper`
- FFmpeg is available
- runtime/cache/output folders are writable
- the configured Whisper model can load

If the launcher says `Render readiness: BLOCKED`, fix only the `REQUIRED` items it prints.

If it says `Render readiness: READY WITH WARNINGS`, rendering can continue. Warnings are optional.

### Recommended Tester Flow

1. Download the full GitHub ZIP.
2. Extract it to a normal writable folder.
3. Open the extracted folder.
4. Run `launch_app.bat` on Windows or `launch_app.command` on macOS.
5. Keep the launcher window open while the app is running.
6. Wait for setup/preflight to finish.
7. Open the browser app, paste a YouTube URL, add a Gemini key if needed, and start the render.

On Windows, the project folder can live on an external SSD, but the app now defaults to storing runtime files and outputs under the user's local AppData folder for better write reliability.

For backward compatibility, the Windows setup also tries to create project-level compatibility links such as `.miscoshorts` so older scripts that still look inside the repo keep working.

### What The Launcher Does

- checks Python
- checks FFmpeg
- checks the local writable folders
- prepares a local virtual environment when needed
- installs Python dependencies only when needed
- prepares the configured Whisper model before the first render
- builds the frontend only when `frontend/dist` is missing or outdated
- writes a reusable doctor report to `.miscoshorts/setup/doctor-report.json`
- opens the local browser app

### First-Run Behavior

The first run is intentionally heavier than later runs.

- The local runtime lives in `.miscoshorts/`
- Logs live in `.miscoshorts/logs/`
- Setup state lives in `.miscoshorts/setup/`
- The reusable doctor/support report lives in `.miscoshorts/setup/doctor-report.json`
- The Whisper model cache lives in `.miscoshorts/runtime/model-cache/`
- Reusable source/transcript cache lives in `outputs/cache/`
- Temporary scratch files live in `outputs/temp/`
- Final job outputs live in `outputs/jobs/<job-fingerprint>/`

Nothing inside `.miscoshorts/` is meant for GitHub. It is private local runtime state.

On Windows, the default storage behavior is different on purpose:

- the project folder can stay on an external SSD
- internal runtime files default to `%LOCALAPPDATA%\\MiscoshortsAI\\internal`
- outputs default to `%LOCALAPPDATA%\\MiscoshortsAI\\outputs`

This avoids common Windows write-permission issues on external drives, shared drives, and protected folders.

If the Whisper cache is deleted, the launcher preflight prepares it again before the next real render.

If the Python environment is already healthy, the launcher skips reinstalling it.

If the frontend is already built, the launcher skips Node.js entirely.

If a tester hits a failure, the two most important files are:

- `.miscoshorts/setup/windows-setup.log` on Windows or `.miscoshorts/setup/macos-setup.log` on macOS
- `.miscoshorts/setup/doctor-report.json`

If the folder already contains `frontend/dist`, the launcher can skip Node.js entirely for normal users.

### Reuse, Cleanup, And Retention

The app is now more deliberate about disk usage:

- repeated renders with the same source URL, clip count, render profile, output filename, and subtitle style reuse the same deterministic job fingerprint
- each render now runs inside an isolated temporary workspace first and only promotes finished artifacts into the final output folder after success
- if a completed render already exists for that fingerprint, the app reuses it instead of generating duplicate outputs again
- identical concurrent renders wait on a fingerprint lock instead of writing into the same output folder at the same time
- source downloads are reused from `outputs/cache/` instead of being copied into every new job folder
- cached full transcripts are reused when the source URL matches
- cached Gemini clip selection is reused per source URL + requested clip count
- subtitle diagnostics are not saved by default anymore; set `KEEP_RENDER_DIAGNOSTICS=1` only when you are debugging subtitle issues
- partial failed workspaces are removed automatically at the end of failed renders before anything is promoted into final outputs
- stale runtime storage can be pruned with the commands below

Default retention policy:

- `outputs/temp/` is pruned after 12 hours
- `outputs/cache/` is pruned after 30 days
- `outputs/jobs/` is pruned after 30 days based on the result manifest `lastUsedAt`

You can override those with:

- `TEMP_RETENTION_HOURS`
- `CACHE_RETENTION_DAYS`
- `JOB_OUTPUT_RETENTION_DAYS`

Storage inspection / cleanup commands:

```bash
python3 -m app.storage --json
python3 -m app.storage --prune --dry-run
python3 -m app.storage --prune
```

```powershell
py -m app.storage --json
py -m app.storage --prune --dry-run
py -m app.storage --prune
```

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

Doctor now also tells you:

- `requirement`: `required`, `optional`, or `optional-premium`
- `blocks_rendering`: whether that item really stops rendering
- `renderReady`: whether the machine is actually ready for a real render

For a stronger readiness check that loads the speech backend too:

```bash
.miscoshorts/runtime/venv/bin/python -m app.doctor --render-smoke
```

```powershell
.miscoshorts\runtime\venv\Scripts\python.exe -m app.doctor --render-smoke
```

Every doctor run also refreshes `.miscoshorts/setup/doctor-report.json` so a tester can send a stable support snapshot instead of pasting random terminal output.

The doctor report now also includes storage usage for:

- final job outputs
- reusable cache
- temporary workspace
- logs
- speech-model cache

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

Important:

- `REQUIRED`: `faster-whisper`, FFmpeg, core Python packages, writable runtime folders
- `REQUIRED`: Whisper model preflight must pass before a real render
- `OPTIONAL`: Gemini key can be pasted in the app at render time
- `OPTIONAL`: Whisper cache may start empty if the launcher can prepare it
- `OPTIONAL-PREMIUM`: pyannote diarization is not required for normal rendering

Important distinction:

- a missing package in your normal shell Python does not matter if the managed launcher runtime is healthy
- the supported runtime is the private environment under `.miscoshorts/runtime/venv` on macOS/Linux or the corresponding Windows runtime path

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

## Windows First-Run Checklist

For a confused Windows tester, the safest path is:

1. Download the full GitHub ZIP.
2. Extract it fully to a normal writable folder such as Desktop or Documents.
3. Double-click `launch_app.bat`.
4. Leave the launcher window open.
5. Wait for setup to finish and the browser to open.

If the project itself is on an external SSD, that is fine. The Windows launcher now keeps the working runtime and output folders under local AppData by default so the render pipeline is less likely to fail on drive permissions.

What may be downloaded on the first Windows run:

- Python only if Windows does not already have a usable Python 3.12 install
- FFmpeg only if it is missing
- Node.js only if `frontend/dist` is missing and the dashboard must be built locally
- Python packages only if the local `.miscoshorts/runtime/venv` is missing or outdated
- The configured Whisper model only if its private cache is missing

What should not happen on every run:

- the Python environment should not reinstall every time
- the frontend should not rebuild every time unless its sources changed
- the Whisper model should not redownload unless the private cache was deleted or the configured model changed

## What To Send For Support

If a tester gets stuck, ask for these exact files:

- `.miscoshorts/setup/doctor-report.json`
- `.miscoshorts/setup/windows-setup.log` or `.miscoshorts/setup/macos-setup.log`
- the exact job error message from the browser, including the support ID if one is shown

That is the fastest way to diagnose Windows issues remotely.

## Common Windows Issues

`PowerShell could not be found`

- This blocks startup. Restore Windows PowerShell and rerun `launch_app.bat`.

`FFmpeg is missing`

- This blocks rendering. Rerun the launcher and let it install FFmpeg, or install FFmpeg manually and rerun.

`The local speech engine is missing`

- This usually means the Python environment is incomplete. Rerun `launch_app.bat` so the launcher can repair the local runtime.

`No local speech-model cache was found yet`

- This is only a warning. The launcher should prepare the cache before the first render.

`Optional pyannote diarization is not active`

- This is only a warning unless you explicitly want pyannote.

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
