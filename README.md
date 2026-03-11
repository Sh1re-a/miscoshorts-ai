# Miscoshorts AI

Create vertical YouTube Shorts from long-form videos using Whisper for transcription, Gemini for clip selection, and MoviePy for subtitle-ready rendering.

This fork adds a cleaner local workflow, a browser-first React dashboard, and a more shareable setup for demos and non-technical users.

## Run The App

If you are not technical, use the app launch file and nothing else:

- macOS: double-click `launch_app.command`
- Windows: double-click `launch_app.bat`

That is the main way to run this project.

On Windows, the launcher can also handle first-time setup for you. It installs missing dependencies, prepares the app, and opens it in the browser. Later launches are faster and reuse the existing setup.

If you are sending this to a friend, send the full project folder, not just the launcher file.

## Thanks

Big thanks to the original creator for the idea, codebase foundation, and tutorial inspiration behind this project. This fork builds on that work and reshapes it into a more user-friendly local tool.

## What It Does

- Downloads a YouTube video with `yt-dlp`
- Transcribes the audio with `openai-whisper`
- Uses Gemini to select the strongest short-form moment
- Crops the video to 9:16 format
- Adds subtitles automatically
- Exports a ready-to-post `.mp4`
- Offers both terminal mode and a local browser UI

## Browser Mode

The new default experience is a local browser dashboard built with React.

You paste:

- a YouTube link
- a Gemini API key
- an optional output filename

The local app then:

1. sends the request to a Python backend running on your machine
2. downloads the source video
3. transcribes it with Whisper
4. asks Gemini for the best short clip
5. renders subtitles and exports the final MP4
6. lets you download both the video and the transcript from the browser

## Requirements

- Python 3.12+
- Node.js 20+
- FFmpeg installed and available in `PATH`
- A Gemini API key

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
python3 start_local.py
```

```powershell
py start_local.py
```

That script starts the backend, starts the React frontend, and opens the browser automatically.

If you want the non-technical option instead of typing in the terminal:

- macOS: double-click `launch_app.command`
- Windows: double-click `launch_app.bat`

On Windows, `launch_app.bat` now does more than just start the app:

- installs Python if missing
- installs Node.js if missing
- installs FFmpeg if missing
- creates a local virtual environment
- installs Python dependencies
- builds the frontend
- opens the local app in the browser

On the first run it sets everything up. On later runs it reuses the existing setup and skips reinstalling or rebuilding unless something changed.

That makes it the best file to send to a non-technical Windows user together with the full project folder.

If you prefer to start everything manually:

Start the backend:

```bash
python3 server.py
```

```powershell
py server.py
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
python3 server.py
```

Then open:

```text
http://127.0.0.1:5001
```

## Terminal Mode

The original terminal flow still exists if you prefer it.

```bash
python3 cli.py
```

```powershell
py cli.py
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

### Whisper Package Conflicts

If the wrong `whisper` package is installed:

```bash
pip uninstall whisper
pip install openai-whisper
```

### Font Errors

If subtitle rendering fails because of fonts, the app already tries several fallbacks, especially for Windows. If needed, change the font list in `subtitles.py`.

The current subtitle renderer now prefers higher-quality system fonts first, uses a narrower caption width, and places subtitles lower in the frame for a cleaner Shorts look.

### Gemini Errors

- make sure your API key is valid
- make sure you still have Gemini quota available
- check that your local firewall or proxy is not blocking requests

## Project Structure

```text
miscoshorts-ai/
├── frontend/
├── cli.py
├── gemini_analyzer.py
├── shorts_service.py
├── server.py
├── app_launcher.py
├── start_local.py
├── setup_windows.ps1
├── launch_app.command
├── launch_app.bat
├── subtitles.py
├── requirements.txt
├── .env.example
└── README.md
```

## Roadmap

- Better job history in the browser UI
- One-click packaging for non-technical users
- More control over clip selection before render
- Cleaner GitHub release workflow