"""Analytics & feedback collection for adaptive improvement.

Collects per-clip classifier signals, user feedback (ratings + tags), and
aggregates historical data to produce adaptive threshold recommendations
that the classifier can incorporate in future runs.

Data layout under outputs/_analytics/:
  feedback/{job_id}_{clip_index}.json   – user rating per clip
  insights.json                         – aggregated stats & adaptive config
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from app.paths import OUTPUTS_DIR

_ANALYTICS_DIR = OUTPUTS_DIR / "_analytics"
_FEEDBACK_DIR = _ANALYTICS_DIR / "feedback"
_INSIGHTS_PATH = _ANALYTICS_DIR / "insights.json"

# ── Allowed feedback values ──────────────────────────────────────────
ALLOWED_RATINGS = {"good", "bad"}
ALLOWED_TAGS = frozenset({
    "great_content",
    "boring_content",
    "good_framing",
    "bad_crop",
    "wrong_layout",
    "good_subtitles",
    "bad_subtitles",
    "audio_issue",
})


def save_feedback(
    job_id: str,
    clip_index: int,
    rating: str,
    tags: list[str] | None = None,
    note: str | None = None,
) -> dict:
    """Persist user feedback for a specific clip.

    Returns the saved feedback dict.
    """
    if rating not in ALLOWED_RATINGS:
        raise ValueError(f"rating must be one of {ALLOWED_RATINGS}")

    safe_tags = [t for t in (tags or []) if t in ALLOWED_TAGS]

    feedback = {
        "jobId": job_id,
        "clipIndex": clip_index,
        "rating": rating,
        "tags": safe_tags,
        "note": (note or "")[:500],  # cap free-text length
        "createdAt": time.time(),
    }

    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    path = _FEEDBACK_DIR / f"{job_id}_{clip_index}.json"
    _atomic_write_json(path, feedback)
    return feedback


def get_feedback(job_id: str, clip_index: int) -> dict | None:
    """Retrieve saved feedback for a clip, or None."""
    path = _FEEDBACK_DIR / f"{job_id}_{clip_index}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_insights() -> dict:
    """Aggregate all job results + feedback into actionable insights.

    Reads completed job states and feedback files, computes per-content-type
    statistics (avg confidence, rating distribution, common negative tags),
    and produces adaptive threshold suggestions.
    """
    job_state_dir = OUTPUTS_DIR / "_job_state"
    if not job_state_dir.exists():
        return _empty_insights()

    # Load all feedback into a lookup dict
    feedback_lookup: dict[str, dict] = {}
    if _FEEDBACK_DIR.exists():
        for fb_path in _FEEDBACK_DIR.glob("*.json"):
            try:
                fb = json.loads(fb_path.read_text(encoding="utf-8"))
                key = f"{fb['jobId']}_{fb['clipIndex']}"
                feedback_lookup[key] = fb
            except (OSError, json.JSONDecodeError, KeyError):
                continue

    # Aggregate per content type
    type_stats: dict[str, dict[str, Any]] = {}
    total_clips = 0
    total_rated = 0
    total_good = 0
    total_bad = 0
    tag_counts: dict[str, int] = {}

    for job_path in job_state_dir.glob("*.json"):
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if job.get("status") != "completed":
            continue

        job_id = job_path.stem
        clips = job.get("result", {}).get("clips") or []

        for clip in clips:
            ct = clip.get("contentType", "unknown")
            analytics = clip.get("analytics") or {}
            clip_idx = clip.get("index", 0)
            total_clips += 1

            if ct not in type_stats:
                type_stats[ct] = {
                    "count": 0,
                    "confidences": [],
                    "good": 0,
                    "bad": 0,
                    "rated": 0,
                    "tags": {},
                    "avg_faces_list": [],
                    "avg_edge_list": [],
                    "avg_text_list": [],
                    "avg_motion_list": [],
                    "fallbacks": 0,
                }

            ts = type_stats[ct]
            ts["count"] += 1

            conf = analytics.get("confidence")
            if conf is not None:
                ts["confidences"].append(conf)

            if analytics.get("layout_fallback"):
                ts["fallbacks"] += 1

            for signal_key in ("avg_faces", "avg_edge", "avg_text", "avg_motion"):
                val = analytics.get(signal_key)
                if val is not None:
                    ts[f"{signal_key}_list"].append(val)

            # Match feedback
            fb_key = f"{job_id}_{clip_idx}"
            fb = feedback_lookup.get(fb_key)
            if fb:
                total_rated += 1
                ts["rated"] += 1
                if fb["rating"] == "good":
                    ts["good"] += 1
                    total_good += 1
                else:
                    ts["bad"] += 1
                    total_bad += 1
                for tag in fb.get("tags", []):
                    ts["tags"][tag] = ts["tags"].get(tag, 0) + 1
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # Compute summaries per type
    type_summaries: dict[str, dict] = {}
    for ct, ts in type_stats.items():
        confs = ts["confidences"]
        type_summaries[ct] = {
            "clipCount": ts["count"],
            "avgConfidence": round(sum(confs) / len(confs), 3) if confs else None,
            "minConfidence": round(min(confs), 3) if confs else None,
            "rated": ts["rated"],
            "good": ts["good"],
            "bad": ts["bad"],
            "approvalRate": round(ts["good"] / ts["rated"], 3) if ts["rated"] else None,
            "fallbackCount": ts["fallbacks"],
            "topNegativeTags": _top_n({t: c for t, c in ts["tags"].items()
                                       if t in ("bad_crop", "wrong_layout", "boring_content", "bad_subtitles", "audio_issue")}, 3),
            "avgFaces": _safe_mean(ts["avg_faces_list"]),
            "avgEdge": _safe_mean(ts["avg_edge_list"]),
            "avgText": _safe_mean(ts["avg_text_list"]),
            "avgMotion": _safe_mean(ts["avg_motion_list"]),
        }

    # Produce adaptive threshold suggestions
    suggestions = _build_threshold_suggestions(type_summaries)

    insights = {
        "generatedAt": time.time(),
        "totalClips": total_clips,
        "totalRated": total_rated,
        "totalGood": total_good,
        "totalBad": total_bad,
        "overallApprovalRate": round(total_good / total_rated, 3) if total_rated else None,
        "topTags": _top_n(tag_counts, 5),
        "perContentType": type_summaries,
        "thresholdSuggestions": suggestions,
    }

    # Persist insights
    _ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(_INSIGHTS_PATH, insights)
    return insights


def get_insights() -> dict:
    """Load cached insights, or build fresh if missing."""
    if _INSIGHTS_PATH.exists():
        try:
            data = json.loads(_INSIGHTS_PATH.read_text(encoding="utf-8"))
            # Rebuild if stale (> 5 min)
            if time.time() - data.get("generatedAt", 0) < 300:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return build_insights()


def get_adaptive_config() -> dict:
    """Return the current adaptive threshold adjustments.

    These are read by the classifier at the start of each clip to fine-tune
    behaviour based on accumulated feedback.
    """
    insights = get_insights()
    return insights.get("thresholdSuggestions", {})


# ── Internal helpers ─────────────────────────────────────────────────

def _build_threshold_suggestions(type_summaries: dict[str, dict]) -> dict:
    """Generate threshold adjustment suggestions based on feedback patterns.

    Returns a dict of content_type → suggestion with reasoning.
    """
    suggestions: dict[str, dict] = {}

    for ct, stats in type_summaries.items():
        rated = stats.get("rated", 0)
        if rated < 3:
            # Not enough data to make suggestions
            continue

        approval = stats.get("approvalRate")
        if approval is None:
            continue

        suggestion: dict[str, Any] = {"approvalRate": approval, "sampleSize": rated, "actions": []}

        # Low approval → something is wrong with this content type
        if approval < 0.5:
            neg_tags = stats.get("topNegativeTags", [])
            tag_names = [t["tag"] for t in neg_tags]

            if "wrong_layout" in tag_names:
                suggestion["actions"].append({
                    "type": "lower_confidence_threshold",
                    "reason": f"{ct} often gets wrong_layout feedback — classifier may be mis-classifying",
                    "severity": "high",
                })
            if "bad_crop" in tag_names:
                suggestion["actions"].append({
                    "type": "review_face_detection",
                    "reason": f"{ct} gets bad_crop feedback — face detection or crop logic may need tuning",
                    "severity": "medium",
                })
            if "boring_content" in tag_names:
                suggestion["actions"].append({
                    "type": "review_gemini_selection",
                    "reason": f"{ct} gets boring_content feedback — Gemini prompt may need refinement",
                    "severity": "medium",
                })

            if not suggestion["actions"]:
                suggestion["actions"].append({
                    "type": "general_review",
                    "reason": f"{ct} has {approval:.0%} approval rate across {rated} rated clips",
                    "severity": "high",
                })

        # High fallback rate
        fallbacks = stats.get("fallbackCount", 0)
        total = stats.get("clipCount", 0)
        if total >= 3 and fallbacks / total > 0.3:
            suggestion["actions"].append({
                "type": "layout_stability",
                "reason": f"{ct} layout fell back to centre-crop in {fallbacks}/{total} clips",
                "severity": "high",
            })

        if suggestion["actions"]:
            suggestions[ct] = suggestion

    return suggestions


def _empty_insights() -> dict:
    return {
        "generatedAt": time.time(),
        "totalClips": 0,
        "totalRated": 0,
        "totalGood": 0,
        "totalBad": 0,
        "overallApprovalRate": None,
        "topTags": [],
        "perContentType": {},
        "thresholdSuggestions": {},
    }


def _safe_mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _top_n(counts: dict[str, int], n: int) -> list[dict]:
    """Return top-n items sorted by count descending."""
    return [{"tag": k, "count": v} for k, v in
            sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]]


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)
