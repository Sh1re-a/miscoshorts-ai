from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from app.paths import DOCTOR_REPORT_PATH, FRONTEND_DIST_DIR, FRONTEND_DIR, LOGS_DIR, MODEL_CACHE_DIR, OUTPUT_CACHE_DIR, OUTPUTS_DIR, OUTPUT_TEMP_DIR, RUNTIME_DIR
from app.runtime import configure_logging, ensure_runtime_dirs, is_debug_enabled, load_local_env, runtime_summary
from app.shorts_service import ensure_dependencies
from app.storage import atomic_write_json, storage_summary
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
    requirement: str = "required"
    blocks_rendering: bool = False


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


def _add_check(
    results: list[DoctorCheck],
    status: str,
    name: str,
    message: str,
    fix: str | None = None,
    *,
    requirement: str = "required",
    blocks_rendering: bool | None = None,
) -> None:
    if blocks_rendering is None:
        blocks_rendering = status == "FAIL" and requirement == "required"
    results.append(
        DoctorCheck(
            status=status,
            name=name,
            message=message,
            fix=fix,
            requirement=requirement,
            blocks_rendering=blocks_rendering,
        )
    )


def _module_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def _managed_runtime_python() -> Path | None:
    candidates = [
        RUNTIME_DIR / "venv" / "bin" / "python",
        RUNTIME_DIR / "venv" / "bin" / "python3",
        RUNTIME_DIR / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _probe_modules_in_python(python_path: Path, module_names: list[str]) -> dict[str, bool]:
    probe_script = (
        "import importlib.util, json; "
        f"modules={module_names!r}; "
        "print(json.dumps({name: importlib.util.find_spec(name) is not None for name in modules}))"
    )
    completed = subprocess.run(
        [str(python_path), "-c", probe_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    if completed.returncode != 0:
        return {name: False for name in module_names}
    try:
        payload = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {name: False for name in module_names}
    return {name: bool(payload.get(name)) for name in module_names}


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
    atomic_write_json(DOCTOR_REPORT_PATH, report)


def run_doctor(*, prepare_whisper: bool = False, render_smoke: bool = False) -> dict:
    load_local_env()
    ensure_runtime_dirs()
    logger, log_path = configure_logging("doctor")

    checks: list[DoctorCheck] = []
    managed_python = _managed_runtime_python()
    current_python_path = Path(sys.executable).resolve()
    managed_python_path = managed_python.resolve() if managed_python is not None else None
    using_managed_python = managed_python_path is not None and managed_python_path == current_python_path
    managed_probe_modules = _probe_modules_in_python(
        managed_python_path,
        ["faster_whisper", "yt_dlp", "moviepy", "PIL", "flask", "google.genai", "cv2"],
    ) if managed_python_path is not None else {}

    python_version = sys.version_info
    if python_version >= (3, 12):
        _add_check(checks, "PASS", "Python", f"Python {python_version.major}.{python_version.minor} is supported.")
    elif python_version >= (3, 10):
        _add_check(checks, "WARN", "Python", f"Python {python_version.major}.{python_version.minor} works, but 3.12 is recommended.", "Upgrade to Python 3.12 for the cleanest Windows setup experience.")
    else:
        _add_check(checks, "FAIL", "Python", f"Python {python_version.major}.{python_version.minor} is too old.", "Install Python 3.12+ and rerun the launcher.")

    if managed_python_path is not None:
        if using_managed_python:
            _add_check(
                checks,
                "PASS",
                "Managed runtime",
                f"The supported managed runtime is active: {managed_python_path}.",
            )
        else:
            _add_check(
                checks,
                "WARN",
                "Managed runtime",
                f"The supported runtime exists at {managed_python_path}, but the current shell is using {current_python_path}.",
                "Use the launcher, or run the managed runtime directly when checking readiness.",
                requirement="optional",
                blocks_rendering=False,
            )
    else:
        _add_check(
            checks,
            "WARN",
            "Managed runtime",
            "The local managed runtime has not been created yet.",
            "Run the launcher once so it can create the private runtime and install dependencies.",
            requirement="required",
            blocks_rendering=False,
        )

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
        ("Temporary workspace", OUTPUT_TEMP_DIR),
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
        _add_check(checks, "PASS", "Gemini API key", "A GEMINI_API_KEY is configured in the environment.", requirement="optional", blocks_rendering=False)
    else:
        _add_check(
            checks,
            "WARN",
            "Gemini API key",
            "No GEMINI_API_KEY is configured yet.",
            "The user can still paste a key in the app before rendering.",
            requirement="optional",
            blocks_rendering=False,
        )

    faster_whisper_available = _module_exists("faster_whisper")
    managed_faster_whisper_available = bool(managed_probe_modules.get("faster_whisper"))
    if faster_whisper_available:
        _add_check(checks, "PASS", "faster-whisper", f"Configured backend mode is '{WHISPER_BACKEND}'.")
    elif managed_faster_whisper_available and not using_managed_python:
        _add_check(
            checks,
            "PASS",
            "faster-whisper",
            f"The managed runtime has faster-whisper installed at {managed_python_path}. The current shell interpreter does not.",
            "Use the launcher or the managed runtime Python when running the app.",
        )
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
    if missing_modules and managed_probe_modules and not using_managed_python:
        managed_missing_modules = [
            package_name
            for module_name, package_name in required_modules.items()
            if not managed_probe_modules.get(module_name, False)
        ]
        if not managed_missing_modules:
            missing_modules = []
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
        _add_check(checks, "FAIL", "Pyannote diarization", "Pyannote mode is enabled but pyannote.audio is not installed.", "Install requirements-optional.txt or switch diarization mode back to auto.", requirement="optional-premium", blocks_rendering=False)
    elif pyannote_requested and not pyannote_token:
        _add_check(checks, "FAIL", "Pyannote diarization", "Pyannote mode is enabled but no Hugging Face token was found.", "Set PYANNOTE_AUTH_TOKEN or HF_TOKEN.", requirement="optional-premium", blocks_rendering=False)
    elif pyannote_installed and pyannote_token:
        _add_check(checks, "PASS", "Pyannote diarization", "Optional pyannote diarization is available.", requirement="optional-premium", blocks_rendering=False)
    else:
        _add_check(checks, "WARN", "Pyannote diarization", "Optional pyannote diarization is not active.", "This is fine unless you explicitly want the higher-accuracy diarization path.", requirement="optional-premium", blocks_rendering=False)

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
            requirement="required",
            blocks_rendering=False,
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

    if render_smoke:
        try:
            backend, model_name, _model = load_whisper_model()
        except Exception as error:
            logger.exception("Render smoke test failed")
            _add_check(
                checks,
                "FAIL",
                "Render smoke test",
                f"Core render stack could not load the speech backend: {error}",
                "Run the launcher again and keep the window open until setup completes.",
            )
        else:
            _add_check(
                checks,
                "PASS",
                "Render smoke test",
                f"Core render stack loaded successfully with {backend} / {model_name}.",
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
        "storage": storage_summary(),
        "renderReady": not any(check.blocks_rendering for check in checks),
        "blockingChecks": [asdict(check) for check in checks if check.blocks_rendering],
        "warningChecks": [asdict(check) for check in checks if check.status == "WARN"],
        "logPath": str(log_path),
        "reportPath": str(DOCTOR_REPORT_PATH),
        "whisper": {
            "backendMode": WHISPER_BACKEND,
            "requestedModels": get_whisper_model_candidates(),
            "configuredValue": WHISPER_MODEL,
            "cacheSizeBytes": _directory_size(MODEL_CACHE_DIR),
        },
        "python": {
            "currentExecutable": str(current_python_path),
            "managedExecutable": str(managed_python_path) if managed_python_path is not None else None,
            "usingManagedRuntime": using_managed_python,
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
    readiness = "READY" if report.get("renderReady") else "BLOCKED"
    print(f"Render readiness: {readiness}")
    print("")
    for check in report["checks"]:
        requirement = check.get("requirement", "required").upper()
        block_note = " BLOCKS_RENDER" if check.get("blocks_rendering") else ""
        print(f"[{check['status']}] {requirement}{block_note} {check['name']}: {check['message']}")
        if check.get("fix"):
            print(f"  Fix: {check['fix']}")
    print("")
    print("Paths")
    for key, value in report["paths"].items():
        print(f"  {key}: {value}")
    print("")
    print("Storage")
    for key, value in report.get("storage", {}).items():
        print(f"  {key}: {value['path']} ({value['bytes']} bytes)")
    print(f"  logPath: {report['logPath']}")
    print(f"  reportPath: {report['reportPath']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether MicroShorts AI is ready to run.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument("--prepare-whisper", action="store_true", help="Prepare the configured Whisper model during the check.")
    parser.add_argument("--render-smoke", action="store_true", help="Load the core render speech stack to verify render readiness.")
    args = parser.parse_args(argv)

    report = run_doctor(prepare_whisper=args.prepare_whisper or args.render_smoke, render_smoke=args.render_smoke)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        _print_report(report)
    return 0 if report.get("renderReady") else 1


if __name__ == "__main__":
    raise SystemExit(main())
