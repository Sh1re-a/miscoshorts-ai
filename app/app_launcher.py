from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from app.paths import DOCTOR_REPORT_PATH, PROJECT_ROOT
from app.runtime import backend_code_signature, configure_logging, load_local_env, runtime_summary

load_local_env()
logger, LOG_PATH = configure_logging("launcher")

APP_URL = "http://127.0.0.1:5001"
HEALTH_URL = f"{APP_URL}/api/health"
BOOTSTRAP_URL = f"{APP_URL}/api/bootstrap"
_WINDOWS_NETSTAT_PATTERN = re.compile(r"^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE)


def wait_for_url(url: str, timeout: float, process: subprocess.Popen[str] | None, name: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"{name} stopped unexpectedly with exit code {process.returncode}.")

        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except urllib.error.URLError:
            time.sleep(0.25)

    raise TimeoutError(f"Timed out while waiting for {name} to start.")


def url_responds(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except urllib.error.URLError:
        return False


def load_bootstrap_payload(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def bootstrap_is_compatible(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False

    if not (isinstance(payload.get("renderProfiles"), dict) and bool(payload.get("defaultRenderProfile"))):
        return False

    return payload.get("backendSignature") == backend_code_signature()


def find_listener_pid(port: int) -> int | None:
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        for line in completed.stdout.splitlines():
            match = _WINDOWS_NETSTAT_PATTERN.match(line)
            if not match:
                continue
            try:
                local_port = int(match.group(1))
                pid = int(match.group(2))
            except ValueError:
                continue
            if local_port == port:
                return pid
        return None

    try:
        completed = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True, timeout=3)
    except Exception:
        return None

    if completed.returncode != 0:
        return None

    raw_pid = completed.stdout.strip().splitlines()
    if not raw_pid:
        return None

    try:
        return int(raw_pid[0].strip())
    except ValueError:
        return None


def stop_listener_on_port(port: int) -> bool:
    pid = find_listener_pid(port)
    if pid is None or pid == os.getpid():
        return False

    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> None:
    backend_process = None
    whisper_cache_dir = os.getenv("WHISPER_MODEL_CACHE_DIR", ".miscoshorts/runtime/model-cache/whisper")
    runtime_paths = runtime_summary()
    launch_env = dict(os.environ)
    launch_env.setdefault("PYTHONUTF8", "1")
    launch_env.setdefault("PYTHONIOENCODING", "utf-8")

    if url_responds(HEALTH_URL):
        bootstrap_payload = load_bootstrap_payload(BOOTSTRAP_URL)
        if bootstrap_is_compatible(bootstrap_payload):
            print(f"A local app is already available on {APP_URL}. Reusing it.")
            logger.info("Reusing local app at %s", APP_URL)
        else:
            running_signature = bootstrap_payload.get("backendSignature") if isinstance(bootstrap_payload, dict) else None
            expected_signature = backend_code_signature()
            print(f"An older local app is already running on {APP_URL}. Restarting it...")
            logger.info(
                "Restarting incompatible local app at %s (running signature=%s, expected=%s)",
                APP_URL,
                running_signature,
                expected_signature,
            )
            stop_listener_on_port(5001)
            time.sleep(1)
            backend_process = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=PROJECT_ROOT, env=launch_env)
            wait_for_url(HEALTH_URL, timeout=20, process=backend_process, name="local app")
    else:
        print(f"Starting local app on {APP_URL} ...")
        logger.info("Starting local app at %s", APP_URL)
        backend_process = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=PROJECT_ROOT, env=launch_env)
        wait_for_url(HEALTH_URL, timeout=20, process=backend_process, name="local app")

    print(f"Private speech-model cache: {whisper_cache_dir}")
    print(f"Support log: {LOG_PATH}")
    print(f"Doctor report: {DOCTOR_REPORT_PATH}")
    print(f"Outputs: {runtime_paths['outputsDir']}")
    print(f"Run diagnostics later with: {sys.executable} -m app.doctor")
    print("Opening browser...")
    webbrowser.open(APP_URL)
    print("Press Ctrl+C to stop the local app.")

    try:
        while True:
            if backend_process is not None and backend_process.poll() is not None:
                raise RuntimeError(f"The local app stopped with exit code {backend_process.returncode}.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping local app...")
    finally:
        stop_process(backend_process)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logger.exception("Launcher failed")
        print(f"\nLaunch error: {error}")
        print(f"See log for details: {LOG_PATH}")
        print(f"Doctor report path: {DOCTOR_REPORT_PATH}")
        raise SystemExit(1) from error
