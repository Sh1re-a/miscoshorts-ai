from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from app.paths import DOCTOR_REPORT_PATH, FRONTEND_DIST_DIR, FRONTEND_DIR, LOGS_DIR, MODEL_CACHE_DIR, OUTPUT_CACHE_DIR, OUTPUTS_DIR, RUNTIME_DIR
from app.runtime import configure_logging, ensure_runtime_dirs, is_debug_enabled, load_local_env, runtime_summary
from app.shorts_service import ensure_dependencies
from app.transcription import (
    WHISPER_BACKEND,
    WHISPER_MODEL,
    get_speaker_diarization_token,
    get_whisper_model_candidates,
    load_whisper_model,
)


@dataclass
class DoctorCheck:
    status: str
    name: str
    message: str
    fix: str | None = None


def _directory_size(path: Path) -> int:
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        return 0


def _format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024 * 1024):.1f} GB"
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.0f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes} B"


def _add_check(results: list[DoctorCheck], status: str, name: str, message: str, fix: str | None = None) -> None:
    results.append(DoctorCheck(status=status, name=name, message=message, fix=fix))


def _module_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def _check_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _writable_fix_message(path: Path) -> str:
    base_message = "Move the project to a normal writable folder."
    if os.name == "nt":
        return (
            f"{base_message} Avoid network drives, shared drives, and protected folders. "
            f"A good default is C:\\Users\\<your-name>\\Desktop\\miscoshorts-ai."
        )
    return base_message


def _write_report_snapshot(report: dict) -> None:
    ensure_runtime_dirs()
    DOCTOR_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DOCTOR_REPORT_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    temp_path.replace(DOCTOR_REPORT_PATH)


def run_doctor(*, prepare_whisper: bool = False) -> dict:
    load_local_env()
    ensure_runtime_dirs()
    logger, log_path = configure_logging("doctor")

    checks: list[DoctorCheck] = []

    python_version = sys.version_info
    if python_version >= (3, 12):
        _add_check(checks, "PASS", "Python", f"Python {python_version.major}.{python_version.minor} is supported.")
    elif python_version >= (3, 10):
        _add_check(checks, "WARN", "Python", f"Python {python_version.major}.{python_version.minor} works, but 3.12 is recommended.", "Upgrade to Python 3.12 for the cleanest Windows setup experience.")
    else:
        _add_check(checks, "FAIL", "Python", f"Python {python_version.major}.{python_version.minor} is too old.", "Install Python 3.12+ and rerun the launcher.")

    try:
        ensure_dependencies()
    except Exception as error:
        _add_check(checks, "FAIL", "FFmpeg", str(error), "Install FFmpeg and rerun the launcher.")
    else:
        ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
        _add_check(checks, "PASS", "FFmpeg", f"FFmpeg is available at {ffmpeg_path}.")

    for label, path in (
        ("Internal runtime", RUNTIME_DIR),
        ("Logs", LOGS_DIR),
        ("Outputs", OUTPUTS_DIR),
        ("Reusable cache", OUTPUT_CACHE_DIR),
        ("Speech-model cache", MODEL_CACHE_DIR),
    ):
        if _check_writable(path):
            _add_check(checks, "PASS", label, f"{path} is writable.")
        else:
            _add_check(checks, "FAIL", label, f"{path} is not writable.", _writable_fix_message(path))

    if FRONTEND_DIST_DIR.exists():
        _add_check(checks, "PASS", "Frontend", f"Bundled dashboard found in {FRONTEND_DIST_DIR}.")
    elif FRONTEND_DIR.exists():
        npm_name = "npm.cmd" if os.name == "nt" else "npm"
        npm_path = shutil.which(npm_name) or shutil.which("npm")
        if npm_path:
            _add_check(checks, "WARN", "Frontend", "Bundled dashboard is missing, but npm is available for a local build.", "Run the launcher and allow it to build the frontend.")
        else:
            _add_check(checks, "FAIL", "Frontend", "Bundled dashboard is missing and npm was not found.", "Install Node.js or include frontend/dist before sharing the app.")
    else:
        _add_check(checks, "FAIL", "Frontend", "The frontend folder is missing.", "Re-download the full project folder.")

    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if api_key:
        _add_check(checks, "PASS", "Gemini API key", "A GEMINI_API_KEY is configured in the environment.")
    else:
        _add_check(checks, "WARN", "Gemini API key", "No GEMINI_API_KEY is configured yet.", "The user can still paste a key in the app before rendering.")

    faster_whisper_available = _module_exists("faster_whisper")
    if faster_whisper_available:
        _add_check(checks, "PASS", "faster-whisper", f"Configured backend mode is '{WHISPER_BACKEND}'.")
    else:
        _add_check(checks, "FAIL", "faster-whisper", "The faster-whisper package is missing.", "Reinstall Python dependencies with the launcher.")

    required_modules = {
        "yt_dlp": "yt-dlp",
        "moviepy": "moviepy",
        "PIL": "Pillow",
        "flask": "Flask",
        "google.genai": "google-genai",
        "cv2": "opencv-python",
    }
    missing_modules = [package_name for module_name, package_name in required_modules.items() if not _module_exists(module_name)]
    if missing_modules:
        _add_check(
            checks,
            "FAIL",
            "Python packages",
            f"Missing required packages: {', '.join(missing_modules)}.",
            "Re-run the launcher so it can reinstall the Python environment.",
        )
    else:
        _add_check(checks, "PASS", "Python packages", "Core Python packages are available.")

    pyannote_requested = os.getenv("SPEAKER_DIARIZATION_MODE", "auto").strip().lower() == "pyannote"
    pyannote_installed = _module_exists("pyannote.audio")
    pyannote_token = bool(get_speaker_diarization_token())
    if pyannote_requested and not pyannote_installed:
        _add_check(checks, "FAIL", "Pyannote diarization", "Pyannote mode is enabled but pyannote.audio is not installed.", "Install requirements-optional.txt or switch diarization mode back to auto.")
    elif pyannote_requested and not pyannote_token:
        _add_check(checks, "FAIL", "Pyannote diarization", "Pyannote mode is enabled but no Hugging Face token was found.", "Set PYANNOTE_AUTH_TOKEN or HF_TOKEN.")
    elif pyannote_installed and pyannote_token:
        _add_check(checks, "PASS", "Pyannote diarization", "Optional pyannote diarization is available.")
    else:
        _add_check(checks, "WARN", "Pyannote diarization", "Optional pyannote diarization is not active.", "This is fine unless you explicitly want the higher-accuracy diarization path.")

    existing_model_cache_size = _directory_size(MODEL_CACHE_DIR)
    if existing_model_cache_size > 0:
        _add_check(
            checks,
            "PASS",
            "Whisper cache",
            f"Reusable speech-model cache already exists ({_format_bytes(existing_model_cache_size)}).",
        )
    else:
        _add_check(
            checks,
            "WARN",
            "Whisper cache",
            f"No local speech-model cache was found yet. Requested model order: {', '.join(get_whisper_model_candidates())}.",
            "The launcher can prepare this automatically before the first render.",
        )

    if prepare_whisper:
        try:
            backend, model_name, _model = load_whisper_model()
        except Exception as error:
            logger.exception("Whisper doctor check failed")
            _add_check(
                checks,
                "FAIL",
                "Whisper model preflight",
                str(error),
                "Leave the log file open, fix the missing dependency or cache problem, and rerun the launcher.",
            )
        else:
            cache_size = _directory_size(MODEL_CACHE_DIR)
            _add_check(
                checks,
                "PASS",
                "Whisper model preflight",
                f"Prepared {backend} with model {model_name}. Cache size: {_format_bytes(cache_size)}.",
            )

    status_order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    overall_status = "PASS"
    for check in checks:
        if status_order[check.status] > status_order[overall_status]:
            overall_status = check.status

    report = {
        "status": overall_status,
        "checks": [asdict(check) for check in checks],
        "paths": runtime_summary(),
        "logPath": str(log_path),
        "reportPath": str(DOCTOR_REPORT_PATH),
        "whisper": {
            "backendMode": WHISPER_BACKEND,
            "requestedModels": get_whisper_model_candidates(),
            "configuredValue": WHISPER_MODEL,
            "cacheSizeBytes": _directory_size(MODEL_CACHE_DIR),
        },
        "debugEnabled": is_debug_enabled(),
    }
    _write_report_snapshot(report)
    logger.info("Doctor report generated with status %s", overall_status)
    return report


def _print_report(report: dict) -> None:
    print("")
    print("MicroShorts AI Doctor")
    print("=====================")
    print(f"Overall status: {report['status']}")
    print("")
    for check in report["checks"]:
        print(f"[{check['status']}] {check['name']}: {check['message']}")
        if check.get("fix"):
            print(f"  Fix: {check['fix']}")
    print("")
    print("Paths")
    for key, value in report["paths"].items():
        print(f"  {key}: {value}")
    print(f"  logPath: {report['logPath']}")
    print(f"  reportPath: {report['reportPath']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether MicroShorts AI is ready to run.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument("--prepare-whisper", action="store_true", help="Prepare the configured Whisper model during the check.")
    args = parser.parse_args(argv)

    report = run_doctor(prepare_whisper=args.prepare_whisper)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        _print_report(report)
    return 0 if report["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
