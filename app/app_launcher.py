from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from app.paths import PROJECT_ROOT


APP_URL = "http://127.0.0.1:5001"
HEALTH_URL = f"{APP_URL}/api/health"


def wait_for_url(url: str, timeout: float, process: subprocess.Popen[str] | None, name: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"{name} stopped unexpectedly with exit code {process.returncode}.")

        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except urllib.error.URLError:
            time.sleep(1)

    raise TimeoutError(f"Timed out while waiting for {name} to start.")


def url_responds(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except urllib.error.URLError:
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

    if url_responds(HEALTH_URL):
        print(f"A local app is already available on {APP_URL}. Reusing it.")
    else:
        print(f"Starting local app on {APP_URL} ...")
        backend_process = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=PROJECT_ROOT)
        wait_for_url(HEALTH_URL, timeout=20, process=backend_process, name="local app")

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
        print(f"\nLaunch error: {error}")
        raise SystemExit(1) from error