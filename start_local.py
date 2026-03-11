from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_HEALTH_URL = "http://127.0.0.1:5001/api/health"


def npm_command() -> str:
    return "npm.cmd" if sys.platform.startswith("win") else "npm"


def ensure_tools() -> None:
    if shutil.which(sys.executable) is None:
        raise EnvironmentError("Python could not be found in the current environment.")

    if shutil.which(npm_command()) is None:
        raise EnvironmentError("npm was not found. Install Node.js before starting the web app.")

    if not FRONTEND_DIR.exists():
        raise FileNotFoundError("The frontend directory was not found.")


def find_available_port(start_port: int = 5173, max_attempts: int = 20) -> int:
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port

    raise RuntimeError("Could not find an available port for the frontend.")


def wait_for_url(url: str, timeout: float, process: subprocess.Popen[str], name: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
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
    ensure_tools()
    frontend_port = find_available_port()
    frontend_url = f"http://127.0.0.1:{frontend_port}"

    backend_process = None
    if url_responds(BACKEND_HEALTH_URL):
        print("A local backend is already running on http://127.0.0.1:5001 and will be reused.")
    else:
        print("Starting local backend on http://127.0.0.1:5001 ...")
        backend_process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=ROOT_DIR,
        )

    frontend_process = None
    try:
        if backend_process is not None:
            wait_for_url(BACKEND_HEALTH_URL, timeout=20, process=backend_process, name="backend")

        print(f"Starting React frontend on {frontend_url} ...")
        frontend_process = subprocess.Popen(
            [npm_command(), "run", "dev", "--", "--port", str(frontend_port), "--strictPort"],
            cwd=FRONTEND_DIR,
        )

        wait_for_url(frontend_url, timeout=30, process=frontend_process, name="frontend")

        print("The web app is running. Opening the browser...")
        webbrowser.open(frontend_url)
        print("Press Ctrl+C to stop both backend and frontend.")

        while True:
            if backend_process is not None and backend_process.poll() is not None:
                raise RuntimeError(f"The backend stopped with exit code {backend_process.returncode}.")
            if frontend_process.poll() is not None:
                raise RuntimeError(f"The frontend stopped with exit code {frontend_process.returncode}.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping the local web app...")
    finally:
        stop_process(frontend_process)
        stop_process(backend_process)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\nStartup error: {error}")
        raise SystemExit(1) from error