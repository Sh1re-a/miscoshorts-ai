from __future__ import annotations

import argparse
import os

from app.doctor import run_doctor
from app.errors import explain_exception
from app.runtime import configure_logging, load_local_env
from app.shorts_service import create_short_from_url

load_local_env()
logger, LOG_PATH = configure_logging("cli")


def _prompt(message: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or default


def _progress(stage: str, message: str) -> None:
    print(f"[{stage.upper()}] {message}")


def _resolve_api_key(explicit_value: str | None) -> str:
    if explicit_value and explicit_value.strip():
        return explicit_value.strip()
    env_value = (os.getenv("GEMINI_API_KEY") or "").strip()
    if env_value:
        return env_value
    return _prompt("Gemini API key")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local MicroShorts AI pipeline without the browser UI.")
    parser.add_argument("--doctor", action="store_true", help="Run environment checks and exit.")
    parser.add_argument("--video-url", help="YouTube URL to process.")
    parser.add_argument("--api-key", help="Gemini API key.")
    parser.add_argument("--output", default="short_con_subs.mp4", help="Output filename.")
    parser.add_argument("--clips", type=int, default=3, help="Number of clips to request (1-5).")
    parser.add_argument("--render-profile", default="studio", help="Render profile: fast, balanced, studio.")
    args = parser.parse_args(argv)

    if args.doctor:
        report = run_doctor(prepare_whisper=False)
        print(f"Doctor status: {report['status']}")
        for check in report["checks"]:
            print(f"[{check['status']}] {check['name']}: {check['message']}")
        print(f"Log file: {report['logPath']}")
        print(f"Doctor report: {report['reportPath']}")
        return 0 if report["status"] != "FAIL" else 1

    video_url = args.video_url or _prompt("YouTube video URL")
    api_key = _resolve_api_key(args.api_key)
    if not api_key:
        print("Gemini API key is required.")
        return 1

    print("MicroShorts AI CLI")
    print("==================")
    print(f"Log file: {LOG_PATH}")
    print(f"Output filename: {args.output}")
    print(f"Clip count: {args.clips}")
    print(f"Render profile: {args.render_profile}")
    print("")

    try:
        result = create_short_from_url(
            video_url=video_url,
            api_key=api_key,
            output_filename=args.output,
            clip_count=args.clips,
            render_profile=args.render_profile,
            progress_callback=_progress,
        )
    except Exception as error:
        friendly = explain_exception(error)
        logger.exception("CLI render failed")
        print("")
        print(f"Failed: {friendly.summary}")
        if friendly.hint:
            print(f"How to fix: {friendly.hint}")
        print(f"Log file: {LOG_PATH}")
        return 1

    clips = result.get("clips") or []
    print("")
    print("Done")
    print(f"Output folder: {result.get('outputDir')}")
    print(f"Transcript: {result.get('transcriptPath')}")
    print(f"Clips exported: {len(clips)}")
    for clip in clips:
        print(f"  - {clip.get('outputPath')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
