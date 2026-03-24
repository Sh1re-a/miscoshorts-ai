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
PYTHON_DEPS_STAMP="$SETUP_DIR/python-deps.state"
FRONTEND_STAMP="$SETUP_DIR/frontend.state"

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

deps_signature() {
	sha_file requirements.txt
}

frontend_signature() {
	find frontend/src frontend/package.json -type f -print0 2>/dev/null | sort -z | xargs -0 shasum -a 256 2>/dev/null | shasum -a 256 | awk '{print $1}'
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

echo ""
echo "${CYAN}Miscoshorts AI${RESET}"
echo "${DIM}Local setup and launch${RESET}"

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
	fi
fi

if [[ ! -f "$VENV_PYTHON" ]]; then
	action "Creating virtual environment..."
	"$PYTHON_CMD" -m venv "$VENV_DIR" || fail "Failed to create virtual environment."
	done_msg "Virtual environment created"
else
	reuse "existing virtual environment"
fi

CURRENT_SIGNATURE=$(deps_signature)
if stamp_matches "$PYTHON_DEPS_STAMP" "$CURRENT_SIGNATURE"; then
	reuse "Python packages (unchanged)"
else
	action "Installing Python packages..."
	"$VENV_PYTHON" -m pip install --disable-pip-version-check --quiet -r requirements.txt >> "$LOG_PATH" 2>&1 || fail "pip install failed. Check $LOG_PATH for details."
	echo "$CURRENT_SIGNATURE" > "$PYTHON_DEPS_STAMP"
	done_msg "Python packages installed"
fi

# ─── Step 3: Frontend ───
step "Preparing app interface"

FRONTEND_SIG=$(frontend_signature)
FRONTEND_NEEDS_BUILD=false

if [[ ! -f "frontend/dist/index.html" ]]; then
	FRONTEND_NEEDS_BUILD=true
elif ! stamp_matches "$FRONTEND_STAMP" "$FRONTEND_SIG"; then
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

	action "Installing frontend packages..."
	(cd frontend && npm ci --silent >> "../$LOG_PATH" 2>&1) || fail "npm ci failed."
	action "Building frontend..."
	(cd frontend && npm run build >> "../$LOG_PATH" 2>&1) || fail "Frontend build failed."

	if [[ ! -f "frontend/dist/index.html" ]]; then
		fail "Frontend build completed but dist/index.html was not created."
	fi
	echo "$FRONTEND_SIG" > "$FRONTEND_STAMP"
	done_msg "Frontend built successfully"
fi

# ─── Step 4: Launch ───
step "Starting app"

info "Opening the local app in your browser. Keep this window open."
"$VENV_PYTHON" -m app.app_launcher && status=0 || status=$?

if [[ $status -ne 0 ]]; then
	echo ""
	echo "${RED}App exited with code $status.${RESET}"
	read "?Press Enter to close this window"
fi

exit $status