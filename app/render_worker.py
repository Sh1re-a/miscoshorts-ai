"""Isolated render worker — runs create_short_from_url in a separate process.

This module is invoked as ``python -m app.render_worker <params.json>`` by the
server.  Running the heavy render pipeline in a child process means that a
segfault, OOM-kill, or unrecoverable native crash in moviepy / ffmpeg / PIL
kills only this worker — the Flask backend survives and can report the failure
cleanly to the frontend.

Communication with the parent (server.py) goes through three JSON files placed
in a temporary directory the parent creates before spawning the worker:

* ``params.json``   — input: job parameters (read-only for the worker)
* ``progress.jsonl`` — output: one JSON object per line, each a progress update
* ``result.json``   — output: final result dict on success, or error dict on
  failure.  Written atomically (write-to-tmp then rename).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

# Ensure UTF-8 on Windows regardless of console code page.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.render_worker <params.json>", file=sys.stderr)
        raise SystemExit(2)

    params_path = Path(sys.argv[1])
    params = json.loads(params_path.read_text(encoding="utf-8"))

    work_dir = params_path.parent
    progress_path = work_dir / "progress.jsonl"
    result_path = work_dir / "result.json"

    # Lazily import the heavy pipeline only inside the worker process so
    # import-time side effects don't touch the parent.
    from app.runtime import load_local_env

    load_local_env()

    from app.shorts_service import (
        create_short_from_url,
        normalize_requested_render_profile,
    )

    def progress_callback(stage: str, message: str) -> None:
        line = json.dumps({"stage": stage, "message": message}, ensure_ascii=True)
        try:
            with open(progress_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except OSError:
            pass
        # Also print so the parent can optionally capture stdout.
        print(f"[{stage}] {message}", flush=True)

    try:
        result = create_short_from_url(
            video_url=params["videoUrl"],
            api_key=params.get("apiKey") or "",
            output_filename=params.get("outputFilename", "short_con_subs.mp4"),
            clip_count=params.get("clipCount", 3),
            job_id=params["jobId"],
            progress_callback=progress_callback,
            subtitle_style=params.get("subtitleStyle"),
            render_profile=params.get("renderProfile")
            or normalize_requested_render_profile(None),
        )
        _atomic_write_json(result_path, {"ok": True, "result": result})
    except Exception as exc:
        tb = traceback.format_exc()
        _atomic_write_json(
            result_path,
            {
                "ok": False,
                "error": str(exc),
                "traceback": tb,
                "errorType": type(exc).__name__,
            },
        )
        print(tb, file=sys.stderr, flush=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
