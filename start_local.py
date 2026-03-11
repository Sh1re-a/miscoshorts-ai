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


def npm_kommando() -> str:
    return "npm.cmd" if sys.platform.startswith("win") else "npm"


def kontrollera_verktyg() -> None:
    if shutil.which(sys.executable) is None:
        raise EnvironmentError("Python kunde inte hittas i den aktuella miljoen.")

    if shutil.which(npm_kommando()) is None:
        raise EnvironmentError("npm hittades inte. Installera Node.js innan du startar webbappen.")

    if not FRONTEND_DIR.exists():
        raise FileNotFoundError("Frontend-mappen hittades inte.")


def hitta_ledig_port(startport: int = 5173, max_forsok: int = 20) -> int:
    for port in range(startport, startport + max_forsok):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port

    raise RuntimeError("Kunde inte hitta en ledig port for frontenden.")


def vantapah_url(url: str, timeout: float, process: subprocess.Popen[str], namn: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{namn} stangdes ovantat med exitkod {process.returncode}.")

        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except urllib.error.URLError:
            time.sleep(1)

    raise TimeoutError(f"Tidsgrans overskreds medan {namn} startade.")


def url_svarar(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except urllib.error.URLError:
        return False


def stang_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> None:
    kontrollera_verktyg()
    frontend_port = hitta_ledig_port()
    frontend_url = f"http://127.0.0.1:{frontend_port}"

    backend_process = None
    if url_svarar(BACKEND_HEALTH_URL):
        print("En lokal backend kor redan pa http://127.0.0.1:5001 och kommer att ateranvandas.")
    else:
        print("Startar lokal backend pa http://127.0.0.1:5001 ...")
        backend_process = subprocess.Popen(
            [sys.executable, "webapp.py"],
            cwd=ROOT_DIR,
        )

    frontend_process = None
    try:
        if backend_process is not None:
            vantapah_url(BACKEND_HEALTH_URL, timeout=20, process=backend_process, namn="backend")

        print(f"Startar React-frontenden pa {frontend_url} ...")
        frontend_process = subprocess.Popen(
            [npm_kommando(), "run", "dev", "--", "--port", str(frontend_port), "--strictPort"],
            cwd=FRONTEND_DIR,
        )

        vantapah_url(frontend_url, timeout=30, process=frontend_process, namn="frontend")

        print("Webbappen ar igang. Oppnar webblasaren...")
        webbrowser.open(frontend_url)
        print("Tryck Ctrl+C for att stanga backend och frontend.")

        while True:
            if backend_process is not None and backend_process.poll() is not None:
                raise RuntimeError(f"Backenden stoppades med exitkod {backend_process.returncode}.")
            if frontend_process.poll() is not None:
                raise RuntimeError(f"Frontenden stoppades med exitkod {frontend_process.returncode}.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStanger den lokala webbappen...")
    finally:
        stang_process(frontend_process)
        stang_process(backend_process)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\nFel vid uppstart: {error}")
        raise SystemExit(1) from error