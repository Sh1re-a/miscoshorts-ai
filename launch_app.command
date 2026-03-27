#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")" || exit 1

# ─── Colors ───
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
RESET='\033[0m'

INTERNAL_DIR=".miscoshorts"
RUNTIME_DIR="$INTERNAL_DIR/runtime"
SETUP_DIR="$INTERNAL_DIR/setup"
VENV_DIR="$RUNTIME_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
LOG_PATH="$SETUP_DIR/macos-setup.log"
PYTHON_CORE_STAMP="$SETUP_DIR/python-core.state"
PYTHON_OPTIONAL_STAMP="$SETUP_DIR/python-optional.state"
FRONTEND_DEPS_STAMP="$SETUP_DIR/frontend-deps.state"
FRONTEND_BUILD_STAMP="$SETUP_DIR/frontend-build.state"
MODEL_CACHE_DIR="$RUNTIME_DIR/model-cache"
WHISPER_DISTIL_LARGE_V3_DOWNLOAD_BYTES=$((1536 * 1024 * 1024))
WHISPER_LARGE_V3_DOWNLOAD_BYTES=$((3100 * 1024 * 1024))

STEP=0
TOTAL_STEPS=4

step() {
	STEP=$((STEP + 1))
	echo ""
	echo "${CYAN}[$STEP/$TOTAL_STEPS] $1${RESET}"
}

info() { echo "  ${DIM}$1${RESET}"; }
reuse() { echo "  ${GREEN}✓ Reusing: $1${RESET}"; }
action() { echo "  ${YELLOW}→ $1${RESET}"; }
done_msg() { echo "  ${GREEN}✓ $1${RESET}"; }

format_bytes() {
	local bytes="$1"
	if (( bytes >= 1073741824 )); then
		awk "BEGIN { printf \"%.1f GB\", $bytes / 1073741824 }"
	elif (( bytes >= 1048576 )); then
		awk "BEGIN { printf \"%.0f MB\", $bytes / 1048576 }"
	elif (( bytes >= 1024 )); then
		awk "BEGIN { printf \"%.0f KB\", $bytes / 1024 }"
	else
		echo "${bytes} B"
	fi
}

dir_has_files() {
	local target="$1"
	[[ -d "$target" ]] && find "$target" -type f -print -quit 2>/dev/null | grep -q .
}

fail() {
	echo ""
	echo "${RED}Setup failed: $1${RESET}"
	echo "${DIM}Log file: $LOG_PATH${RESET}"
	echo ""
	read "?Press Enter to close this window"
	exit 1
}

sha_file() {
	if [[ -f "$1" ]]; then
		shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'
	else
		echo "missing"
	fi
}

core_deps_signature() {
	sha_file requirements.txt
}

optional_deps_signature() {
	sha_file requirements-optional.txt
	if [[ -n "${PYANNOTE_AUTH_TOKEN:-}" || -n "${HF_TOKEN:-}" || "${AUTO_INSTALL_PRO_DEPS:-0}" == "1" ]]; then
		echo "optional:on"
	else
		echo "optional:off"
	fi
}

frontend_deps_signature() {
	find frontend/package.json frontend/package-lock.json -type f -print0 2>/dev/null | sort -z | xargs -0 shasum -a 256 2>/dev/null | shasum -a 256 | awk '{print $1}'
}

frontend_build_signature() {
	find frontend/src frontend/index.html frontend/package.json frontend/vite.config.ts -type f -print0 2>/dev/null | sort -z | xargs -0 shasum -a 256 2>/dev/null | shasum -a 256 | awk '{print $1}'
}

stamp_matches() {
	local stamp_path="$1"
	local expected="$2"
	if [[ -f "$stamp_path" ]]; then
		local current
		current=$(cat "$stamp_path" 2>/dev/null)
		[[ "$current" == "$expected" ]]
	else
		return 1
	fi
}

# ─── Ensure directories ───
mkdir -p "$RUNTIME_DIR" "$SETUP_DIR"
mkdir -p "$MODEL_CACHE_DIR"

echo ""
echo "${CYAN}Miscoshorts AI${RESET}"
echo "${DIM}Local setup and launch${RESET}"
info "Private runtime files live in $INTERNAL_DIR and are ignored by Git/GitHub."
info "Speech model cache lives in $MODEL_CACHE_DIR."

# ─── Step 1: Check & install tools ───
step "Checking local tools"

# --- Python 3 ---
PYTHON_CMD=""
for candidate in python3 python; do
	if command -v "$candidate" &>/dev/null; then
		if "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
			PYTHON_CMD="$candidate"
			break
		fi
	fi
done

if [[ -z "$PYTHON_CMD" ]]; then
	if command -v brew &>/dev/null; then
		action "Installing Python via Homebrew..."
		brew install python@3.12 >> "$LOG_PATH" 2>&1 || fail "Homebrew Python install failed."
		rehash
		PYTHON_CMD="python3"
	else
		fail "Python 3.10+ not found and Homebrew is not installed. Install Python from https://www.python.org/downloads/ or install Homebrew first: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
	fi
fi
done_msg "Python: $($PYTHON_CMD --version 2>&1)"

# --- FFmpeg ---
if command -v ffmpeg &>/dev/null; then
	reuse "FFmpeg"
else
	if command -v brew &>/dev/null; then
		action "Installing FFmpeg via Homebrew..."
		brew install ffmpeg >> "$LOG_PATH" 2>&1 || fail "Homebrew FFmpeg install failed."
		rehash
		done_msg "FFmpeg installed"
	else
		fail "FFmpeg not found. Install it with: brew install ffmpeg"
	fi
fi

# ─── Step 2: Python environment ───
step "Preparing Python environment"

if [[ -f "$VENV_PYTHON" ]]; then
	if ! "$VENV_PYTHON" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
		action "Existing venv is invalid, recreating..."
		rm -rf "$VENV_DIR"
		rm -f "$PYTHON_CORE_STAMP" "$PYTHON_OPTIONAL_STAMP"
	fi
fi

if [[ ! -f "$VENV_PYTHON" ]]; then
	action "Creating virtual environment..."
	"$PYTHON_CMD" -m venv "$VENV_DIR" || fail "Failed to create virtual environment."
	done_msg "Virtual environment created"
else
	reuse "existing virtual environment"
fi

CURRENT_CORE_SIGNATURE=$(core_deps_signature)
CURRENT_OPTIONAL_SIGNATURE=$(optional_deps_signature)

if stamp_matches "$PYTHON_CORE_STAMP" "$CURRENT_CORE_SIGNATURE"; then
	reuse "Python core packages (unchanged)"
else
	action "Installing Python core packages..."
	info "Includes the local app dependencies only. The speech model is downloaded later on first transcription."
	info "Expected first-time app dependency download: usually under a few hundred MB, depending on your Mac."
	"$VENV_PYTHON" -m pip install --disable-pip-version-check --prefer-binary --quiet -r requirements.txt >> "$LOG_PATH" 2>&1 || fail "pip install failed. Check $LOG_PATH for details."
	echo "$CURRENT_CORE_SIGNATURE" > "$PYTHON_CORE_STAMP"
	done_msg "Python core packages installed"
fi

if [[ -f "requirements-optional.txt" && ( -n "${PYANNOTE_AUTH_TOKEN:-}" || -n "${HF_TOKEN:-}" || "${AUTO_INSTALL_PRO_DEPS:-0}" == "1" ) ]]; then
	if stamp_matches "$PYTHON_OPTIONAL_STAMP" "$CURRENT_OPTIONAL_SIGNATURE"; then
		reuse "Optional Python add-ons (unchanged)"
	else
		action "Installing optional pro diarization add-ons..."
		info "This optional bundle is much heavier than the default setup and is only used when diarization add-ons are enabled."
		"$VENV_PYTHON" -m pip install --disable-pip-version-check --prefer-binary --quiet -r requirements-optional.txt >> "$LOG_PATH" 2>&1 || fail "Optional pro dependency install failed. Check $LOG_PATH for details."
		echo "$CURRENT_OPTIONAL_SIGNATURE" > "$PYTHON_OPTIONAL_STAMP"
		done_msg "Optional Python add-ons installed"
	fi
else
	reuse "Optional Python add-ons disabled"
fi

# ─── Step 3: Frontend ───
step "Preparing app interface"

FRONTEND_DEPS_SIG=$(frontend_deps_signature)
FRONTEND_BUILD_SIG=$(frontend_build_signature)
FRONTEND_NEEDS_BUILD=false

if [[ ! -f "frontend/dist/index.html" ]]; then
	FRONTEND_NEEDS_BUILD=true
elif ! stamp_matches "$FRONTEND_BUILD_STAMP" "$FRONTEND_BUILD_SIG"; then
	FRONTEND_NEEDS_BUILD=true
fi

if [[ "$FRONTEND_NEEDS_BUILD" == "false" ]]; then
	reuse "prebuilt frontend (unchanged)"
else
	if [[ ! -f "frontend/package.json" ]]; then
		fail "Frontend source not found. Re-download the project from GitHub."
	fi

	# Ensure Node.js
	if ! command -v npm &>/dev/null; then
		if command -v brew &>/dev/null; then
			action "Installing Node.js via Homebrew..."
			brew install node >> "$LOG_PATH" 2>&1 || fail "Homebrew Node.js install failed."
			rehash
		else
			fail "npm not found. Install Node.js from https://nodejs.org/"
		fi
	fi

	if stamp_matches "$FRONTEND_DEPS_STAMP" "$FRONTEND_DEPS_SIG"; then
		reuse "frontend packages (unchanged)"
	else
		action "Installing frontend packages..."
		info "This is the browser dashboard toolchain. It is only needed when frontend/dist is missing or outdated."
		(cd frontend && npm ci --silent >> "../$LOG_PATH" 2>&1) || fail "npm ci failed."
		echo "$FRONTEND_DEPS_SIG" > "$FRONTEND_DEPS_STAMP"
	fi
	action "Building frontend..."
	(cd frontend && npm run build >> "../$LOG_PATH" 2>&1) || fail "Frontend build failed."

	if [[ ! -f "frontend/dist/index.html" ]]; then
		fail "Frontend build completed but dist/index.html was not created."
	fi
	echo "$FRONTEND_BUILD_SIG" > "$FRONTEND_BUILD_STAMP"
	done_msg "Frontend built successfully"
fi

# ─── Step 4: Launch ───
step "Starting app"

info "Opening the local app in your browser. Keep this window open."
if dir_has_files "$MODEL_CACHE_DIR/whisper"; then
	reuse "existing local speech-model cache"
else
	action "First transcription will download the configured Whisper model into the private cache."
	info "Planned model order: distil-large-v3 first ($(format_bytes "$WHISPER_DISTIL_LARGE_V3_DOWNLOAD_BYTES")), then large-v3 fallback ($(format_bytes "$WHISPER_LARGE_V3_DOWNLOAD_BYTES")) only if needed."
	info "If the cache was deleted, the same configured model will be downloaded again automatically."
fi
HF_HOME="$MODEL_CACHE_DIR/huggingface" \
XDG_CACHE_HOME="$MODEL_CACHE_DIR/xdg" \
WHISPER_MODEL_CACHE_DIR="$MODEL_CACHE_DIR/whisper" \
"$VENV_PYTHON" -m app.app_launcher && status=0 || status=$?

if [[ $status -ne 0 ]]; then
	echo ""
	echo "${RED}App exited with code $status.${RESET}"
	read "?Press Enter to close this window"
fi

exit $status
