from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from app.paths import LOGS_DIR
from app.storage import atomic_write_json, path_size

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psutil = None

try:
    import resource
except Exception:  # pragma: no cover - platform dependent
    resource = None


def current_memory_snapshot() -> dict[str, int | None]:
    rss_bytes = None
    peak_rss_bytes = None

    if psutil is not None:
        try:
            rss_bytes = int(psutil.Process(os.getpid()).memory_info().rss)
        except Exception:
            rss_bytes = None

    if resource is not None:
        try:
            peak_value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            if os.name == "posix" and os.uname().sysname.lower() == "darwin":
                peak_rss_bytes = peak_value
            else:
                peak_rss_bytes = peak_value * 1024
        except Exception:
            peak_rss_bytes = None

    return {
        "rssBytes": rss_bytes,
        "peakRssBytes": peak_rss_bytes,
    }


class RunObserver:
    def __init__(
        self,
        *,
        job_id: str,
        fingerprint: str,
        video_url: str,
        output_filename: str,
        render_profile: str,
    ) -> None:
        self.job_id = job_id
        self.fingerprint = fingerprint
        self.video_url = video_url
        self.output_filename = output_filename
        self.render_profile = render_profile
        self.started_at = time.time()
        self.events: list[dict] = []
        self.phases: dict[str, dict] = {}
        self.cache: dict[str, str] = {}
        self.snapshots: list[dict] = []
        self.clip_metrics: list[dict] = []
        self.summary: dict = {}

    def log(self, phase: str, message: str, **data) -> None:
        payload = {
            "time": time.time(),
            "phase": phase,
            "message": message,
        }
        if data:
            payload["data"] = data
        self.events.append(payload)

    def mark_cache(self, key: str, hit: bool) -> None:
        self.cache[key] = "hit" if hit else "miss"

    def record_phase(self, key: str, seconds: float, **data) -> None:
        payload = {"seconds": round(float(seconds), 2)}
        if data:
            payload.update(data)
        self.phases[key] = payload

    def record_clip(self, clip_payload: dict) -> None:
        self.clip_metrics.append(clip_payload)

    def snapshot(self, label: str, *, workspace_dir: Path | None = None, final_output_dir: Path | None = None) -> dict:
        payload = {
            "time": time.time(),
            "label": label,
            "memory": current_memory_snapshot(),
            "workspaceBytes": path_size(workspace_dir) if workspace_dir else None,
            "finalOutputBytes": path_size(final_output_dir) if final_output_dir else None,
        }
        self.snapshots.append(payload)
        return payload

    def build_summary(self, *, status: str, result_payload: dict | None = None, cleanup_ok: bool | None = None, promotion_ok: bool | None = None) -> dict:
        ended_at = time.time()
        slowest_clip = None
        if self.clip_metrics:
            slowest_clip = max(self.clip_metrics, key=lambda clip: float((clip.get("renderMetrics") or {}).get("totalClipSeconds") or 0.0))
        clip_count = len(self.clip_metrics) if self.clip_metrics else int((result_payload or {}).get("clipCount") or 0)
        reused_existing = bool((result_payload or {}).get("reusedExisting"))
        generated_clip_count = 0 if reused_existing and not self.clip_metrics else len(self.clip_metrics)
        reused_clip_count = max(0, clip_count - generated_clip_count)

        phase_ranking = sorted(
            ((key, float(payload.get("seconds") or 0.0)) for key, payload in self.phases.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        peak_rss_bytes = max(
            (
                int(snapshot.get("memory", {}).get("rssBytes") or 0)
                for snapshot in self.snapshots
            ),
            default=0,
        ) or None
        peak_process_rss_bytes = max(
            (
                int(snapshot.get("memory", {}).get("peakRssBytes") or 0)
                for snapshot in self.snapshots
            ),
            default=0,
        ) or None
        peak_workspace_bytes = max(
            (int(snapshot.get("workspaceBytes") or 0) for snapshot in self.snapshots),
            default=0,
        ) or int((result_payload or {}).get("metrics", {}).get("workspaceBytesBeforePromotion") or 0) or None
        final_output_bytes = int((result_payload or {}).get("metrics", {}).get("finalOutputBytes") or 0) or None
        largest_clip = None
        if self.clip_metrics:
            largest_clip = max(
                self.clip_metrics,
                key=lambda clip: int((clip.get("renderMetrics") or {}).get("outputBytes") or 0),
            )
        cache_hits = sorted(key for key, value in self.cache.items() if value == "hit")
        cache_misses = sorted(key for key, value in self.cache.items() if value == "miss")

        summary = {
            "status": status,
            "totalJobSeconds": round(ended_at - self.started_at, 2),
            "clipCount": clip_count,
            "generatedClipCount": generated_clip_count,
            "reusedClipCount": reused_clip_count,
            "reusedExisting": reused_existing,
            "cache": dict(self.cache),
            "cacheHits": cache_hits,
            "cacheMisses": cache_misses,
            "slowestClip": slowest_clip["index"] if slowest_clip else None,
            "slowestClipSeconds": round(float((slowest_clip.get("renderMetrics") or {}).get("totalClipSeconds") or 0.0), 2) if slowest_clip else None,
            "slowestPhases": phase_ranking[:5],
            "peakRssBytes": peak_rss_bytes,
            "peakProcessRssBytes": peak_process_rss_bytes,
            "peakWorkspaceBytes": peak_workspace_bytes,
            "finalOutputBytes": final_output_bytes,
            "largestClipIndex": largest_clip["index"] if largest_clip else None,
            "largestClipOutputBytes": int((largest_clip.get("renderMetrics") or {}).get("outputBytes") or 0) if largest_clip else None,
            "cleanupSucceeded": cleanup_ok,
            "promotionSucceeded": promotion_ok,
        }
        self.summary = summary
        return summary

    def write_success_report(self, output_dir: Path, result_payload: dict) -> Path:
        report_path = output_dir / "meta" / "run_report.json"
        summary = self.build_summary(
            status="completed",
            result_payload=result_payload,
            cleanup_ok=True,
            promotion_ok=bool(result_payload.get("metrics", {}).get("promotedFromTemp")),
        )
        atomic_write_json(
            report_path,
            {
                "jobId": self.job_id,
                "jobFingerprint": self.fingerprint,
                "videoUrl": self.video_url,
                "outputFilename": self.output_filename,
                "renderProfile": self.render_profile,
                "startedAt": self.started_at,
                "endedAt": time.time(),
                "summary": summary,
                "phases": self.phases,
                "cache": self.cache,
                "snapshots": self.snapshots,
                "events": self.events,
                "clips": self.clip_metrics,
                "resultMetrics": result_payload.get("metrics") or {},
            },
        )
        return report_path

    def write_failure_report(self, error_summary: str, *, cleanup_ok: bool | None = None) -> Path:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = LOGS_DIR / f"run-{self.job_id}.json"
        summary = self.build_summary(status="failed", cleanup_ok=cleanup_ok, promotion_ok=False)
        atomic_write_json(
            report_path,
            {
                "jobId": self.job_id,
                "jobFingerprint": self.fingerprint,
                "videoUrl": self.video_url,
                "outputFilename": self.output_filename,
                "renderProfile": self.render_profile,
                "startedAt": self.started_at,
                "endedAt": time.time(),
                "error": error_summary,
                "summary": summary,
                "phases": self.phases,
                "cache": self.cache,
                "snapshots": self.snapshots,
                "events": self.events,
                "clips": self.clip_metrics,
            },
        )
        return report_path


def load_run_report(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def format_run_report_summary(report: dict) -> list[str]:
    summary = report.get("summary") or {}
    phases = summary.get("slowestPhases") or []
    lines = [
        "MicroShorts AI Run Report",
        "=========================",
        f"Job ID: {report.get('jobId')}",
        f"Fingerprint: {report.get('jobFingerprint')}",
        f"Status: {summary.get('status')}",
        f"Total time: {summary.get('totalJobSeconds')}s",
        f"Clips: {summary.get('generatedClipCount', 0)} generated, {summary.get('reusedClipCount', 0)} reused",
        f"Cache hits: {', '.join(summary.get('cacheHits') or []) or 'none'}",
        f"Cache misses: {', '.join(summary.get('cacheMisses') or []) or 'none'}",
        f"Peak RSS hint: {summary.get('peakRssBytes') or summary.get('peakProcessRssBytes') or 'unknown'} bytes",
        f"Peak workspace size: {summary.get('peakWorkspaceBytes') or 0} bytes",
        f"Final output size: {summary.get('finalOutputBytes') or 0} bytes",
    ]
    if summary.get("slowestClip") is not None:
        lines.append(f"Slowest clip: #{summary.get('slowestClip')} ({summary.get('slowestClipSeconds')}s)")
    if phases:
        lines.append("Top phases:")
        for key, seconds in phases[:5]:
            lines.append(f"  - {key}: {seconds}s")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a MicroShorts AI run report.")
    parser.add_argument("report_path", help="Path to meta/run_report.json or a failure run-<job>.json file.")
    parser.add_argument("--json", action="store_true", help="Print the raw report JSON.")
    args = parser.parse_args(argv)

    report = load_run_report(args.report_path)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        for line in format_run_report_summary(report):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
