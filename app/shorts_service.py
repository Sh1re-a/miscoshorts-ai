from __future__ import annotations

import gc
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

from moviepy import CompositeVideoClip, ImageClip, VideoFileClip

try:
    import numpy as _np
except ImportError:
    _np = None

_cv2 = None
_CV2_AVAILABLE: bool | None = None

# Haar cascade singletons — loaded once per process, reused across all clips
_FRONTAL_CASCADE: "_cv2.CascadeClassifier | None" = None
_PROFILE_CASCADE: "_cv2.CascadeClassifier | None" = None
# Scale detection frames to this width — 960 keeps faces large enough for
# reliable Haar detection even in two-person podcast frames.
_FACE_DETECT_WIDTH = 960


# ─── Face detection cache ────────────────────────────────────────────
# Built once during classification; reused by layout builders so faces
# are never detected twice for the same clip.

@dataclass
class _FaceCache:
    """Stores face analysis results for a single clip's lifetime."""
    # Per-frame sample data from the classifier (list of dicts)
    samples: list[dict] = field(default_factory=list)
    # Median face centre-x in source coords (or None)
    face_cx: int | None = None
    # Median face centre-y in source coords (or None)
    face_cy: int | None = None
    # Duo speaker positions (left_cx, right_cx) or None
    duo_positions: tuple[int, int] | None = None
    # Bounding box covering all detected faces or None
    face_bbox: tuple[int, int, int, int] | None = None
    # Per-frame face x-centre values (source coords)
    per_frame_cx: list[int] = field(default_factory=list)
    # Per-frame face y-centre values (source coords)
    per_frame_cy: list[int] = field(default_factory=list)
    # Whether the cache has been populated
    populated: bool = False

# Thread-local storage for passing face cache from classifier to layout
_face_cache_local = threading.local()


def _get_face_cache() -> _FaceCache | None:
    """Return the current clip's face cache, or None if not populated."""
    return getattr(_face_cache_local, "cache", None)


def _set_face_cache(cache: _FaceCache) -> None:
    _face_cache_local.cache = cache


def _clear_face_cache() -> None:
    _face_cache_local.cache = None

# ─── Content type constants ──────────────────────────────────────────
_CONTENT_SINGLE_SPEAKER = "single_speaker"
_CONTENT_PODCAST_DUO = "podcast_duo"
_CONTENT_MEETING_GALLERY = "meeting_gallery"
_CONTENT_SCREEN_SHARE = "screen_share"
_CONTENT_SCREEN_SHARE_WITH_CAM = "screen_share_with_cam"
_CONTENT_NEWS_BROADCAST = "news_broadcast"
_CONTENT_BROLL = "broll"
_CONTENT_MIXED = "mixed"

# ─── Classifier tuning constants ─────────────────────────────────────
# All magic numbers from the classification decision tree, extracted here
# so they can be tuned in one place.

# Sample counts
_CLS_NUM_SAMPLES = 12               # frames to sample for classification
_CLS_MIN_SAMPLES = 3                # minimum frames regardless of duration

# Mixed / voice-over detection
_CLS_VOTE_CONFIDENCE_MIXED = 0.45   # below this → MIXED
_CLS_TRANSITION_RATE_MIXED = 0.20   # above this → MIXED
_CLS_TRANSITION_COUNT_MIXED = 2     # min transitions for secondary mixed check
_CLS_FACE_PCT_MIXED = 0.70          # face presence ceiling for secondary mixed

# Screen share
_CLS_TEXT_SCREEN = 0.25             # text density above this → screen share
_CLS_FACE_PCT_SCREEN = 0.30        # face presence below this → screen share
_CLS_EDGE_SCREEN = 0.08            # edge density above this → screen share

# Screen share with webcam
_CLS_TEXT_SCREENCAM = 0.18          # text for screen+cam
_CLS_FACE_RATIO_SCREENCAM = 0.025  # max face-to-frame ratio (tiny webcam)
_CLS_FACE_RATIO_PIP = 0.015        # even tinier face → PiP layout
_CLS_EDGE_PIP = 0.08               # edge density for PiP detection

# B-roll
_CLS_FACE_PCT_BROLL = 0.25         # face presence below this → B-roll candidate
_CLS_TEXT_BROLL = 0.15              # text density below this → B-roll candidate
_CLS_MOTION_BROLL = 0.02           # motion above this → B-roll (vs static slides)
_CLS_SAT_BROLL = 0.30              # saturation above this → B-roll (colourful)

# Meeting gallery
_CLS_3PLUS_FRAC_MEETING = 0.30     # fraction of frames with 3+ faces
_CLS_AVG_FACES_MEETING = 2.3       # average faces for gallery
_CLS_MULTI_PCT_MEETING = 0.45      # multi-face frame percentage
_CLS_AVG_FACES_MEETING2 = 1.8      # lower avg faces if high multi_pct
_CLS_FACE_RATIO_MEETING = 0.06     # max avg face ratio (small = gallery)

# Podcast duo
_CLS_MULTI_PCT_PODCAST = 0.35      # multi-face frames for podcast
_CLS_AVG_FACES_PODCAST_LO = 1.3    # min avg faces for podcast
_CLS_AVG_FACES_PODCAST_HI = 3.0    # max avg faces for podcast
_CLS_FACE_SIZE_STD_PODCAST = 0.025 # max face-size variance (similar sizes)
_CLS_DUO_SEPARATION_MIN = 0.15     # min x-separation for two-speaker detection

# News broadcast
_CLS_FACE_PCT_NEWS = 0.50          # face presence above this → news candidate
_CLS_FACE_RATIO_NEWS = 0.02        # face size above this → news candidate
_CLS_LOWER_TEXT_NEWS = 0.08        # lower-third text above this → news
_CLS_HORIZ_NEWS = 0.01             # horizontal line density for news

# Per-frame voting thresholds
_CLS_VOTE_TEXT_SCREEN = 0.20       # per-frame text density for screen share vote
_CLS_VOTE_EDGE_BROLL = 0.05        # per-frame edge below this → B-roll vote
_CLS_VOTE_MOTION_BROLL = 0.01      # per-frame motion above this → B-roll vote
_CLS_VOTE_FACE_RATIO_CAM = 0.02    # per-frame face ratio below → webcam overlay vote
_CLS_VOTE_TEXT_CAM = 0.15           # per-frame text for webcam overlay vote
_CLS_VOTE_TEXT_NEWS = 0.10          # per-frame text for news vote

# Scene-change detection
_CLS_SCENE_CHANGE_THRESHOLD = 0.06 # inter-frame diff above this = scene change

from app import subtitles
from app.clip_transcript import extract_clip_transcript_from_segments
from app.paths import OUTPUT_JOBS_DIR
from app.render_session import (
    RenderWorkspace,
    acquire_fingerprint_lock,
    job_fingerprint,
    load_existing_result,
    write_result_manifest,
)
from app.run_report import RunObserver
from app.runtime import configure_logging, load_local_env
from app.source_pipeline import (
    emit_workload_warning,
    ensure_disk_headroom,
    probe_video_duration,
    resolve_source_video,
    resolve_transcript,
    select_clip_candidates,
    validate_clip_candidates,
)
from app.storage import path_size, prune_runtime_storage
from app.transcription import (
    analyze_audio_speakers,
    speaker_analysis_backend_label,
    transcribe_audio_path_for_subtitles,
)
from app.video_render import DEFAULT_RENDER_THREADS, extract_audio_segment, write_high_quality_video

load_local_env()
logger, _LOG_PATH = configure_logging("shorts-service")

ProgressCallback = Callable[[str, str], None]
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
TARGET_ASPECT_RATIO = OUTPUT_WIDTH / OUTPUT_HEIGHT
VIDEO_CRF = os.getenv("VIDEO_CRF", "13")
VIDEO_PRESET = os.getenv("VIDEO_PRESET", "slow")
VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "12M")
VIDEO_MAXRATE = os.getenv("VIDEO_MAXRATE", "18M")
VIDEO_BUFSIZE = os.getenv("VIDEO_BUFSIZE", "24M")
VIDEO_AUDIO_BITRATE = os.getenv("VIDEO_AUDIO_BITRATE", "320k")
RENDER_THREADS = DEFAULT_RENDER_THREADS
DEFAULT_CLIP_COUNT = 3
DEFAULT_RENDER_PROFILE = os.getenv("DEFAULT_RENDER_PROFILE", "studio").strip().lower() or "studio"
KEEP_RENDER_DIAGNOSTICS = os.getenv("KEEP_RENDER_DIAGNOSTICS", "0").strip().lower() in {"1", "true", "yes", "on"}
REUSE_COMPLETED_RENDERS = os.getenv("REUSE_COMPLETED_RENDERS", "1").strip().lower() not in {"0", "false", "no"}
_LAST_STORAGE_PRUNE_AT = 0.0
RENDER_PROFILES = {
    "fast": {
        "label": "Fast Draft 1080x1920 MP4",
        "video_crf": "19",
        "video_preset": "veryfast",
        "video_bitrate": "6M",
        "video_maxrate": "8M",
        "video_bufsize": "12M",
        "audio_bitrate": "192k",
        "x264_params": "aq-mode=2:aq-strength=0.8:deblock=0,0",
    },
    "balanced": {
        "label": "Balanced 1080x1920 MP4",
        "video_crf": "16",
        "video_preset": "faster",
        "video_bitrate": "7M",
        "video_maxrate": "10M",
        "video_bufsize": "14M",
        "audio_bitrate": "224k",
        "x264_params": "aq-mode=3:aq-strength=0.95:deblock=-1,-1:rc-lookahead=24",
    },
    "studio": {
        "label": "Studio HQ 1080x1920 MP4",
        "video_crf": VIDEO_CRF,
        "video_preset": VIDEO_PRESET,
        "video_bitrate": VIDEO_BITRATE,
        "video_maxrate": VIDEO_MAXRATE,
        "video_bufsize": VIDEO_BUFSIZE,
        "audio_bitrate": VIDEO_AUDIO_BITRATE,
        "x264_params": os.getenv(
            "VIDEO_X264_PARAMS",
            "aq-mode=3:aq-strength=1.05:deblock=-1,-1:rc-lookahead=40:ref=4:bframes=3:direct=auto:me=umh:subme=7:merange=24",
        ),
    },
}
RENDER_PROFILE_LABEL = RENDER_PROFILES.get(DEFAULT_RENDER_PROFILE, RENDER_PROFILES["studio"])["label"]
ALLOWED_VIDEO_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def _ensure_cv2() -> bool:
    global _cv2, _CV2_AVAILABLE
    if _CV2_AVAILABLE is not None:
        return _CV2_AVAILABLE

    if _np is None:
        _CV2_AVAILABLE = False
        return False

    try:
        import cv2 as cv2_module
    except Exception:
        _cv2 = None
        _CV2_AVAILABLE = False
    else:
        _cv2 = cv2_module
        _CV2_AVAILABLE = True
    return _CV2_AVAILABLE


def _emit(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback is not None:
        callback(stage, message)
    logger.info("[%s] %s", stage, message)


def _emit_labeled(
    callback: ProgressCallback | None,
    stage: str,
    phase: str,
    message: str,
    *,
    observer: RunObserver | None = None,
    **data,
) -> None:
    if observer is not None:
        observer.log(phase, message, **data)
    _emit(callback, stage, f"{phase} | {message}")


def _debug_note(message: str) -> None:
    logger.debug(message)


def _warn_note(message: str) -> None:
    logger.warning(message)


def normalize_requested_render_profile(render_profile: str | None) -> str:
    normalized = (render_profile or DEFAULT_RENDER_PROFILE).strip().lower()
    if normalized not in RENDER_PROFILES:
        raise ValueError(f"renderProfile must be one of: {', '.join(sorted(RENDER_PROFILES))}")
    return normalized


def _get_render_profile_settings(render_profile: str) -> dict[str, str]:
    return dict(RENDER_PROFILES[normalize_requested_render_profile(render_profile)])


def _make_even(value: float) -> int:
    return max(2, int(round(value / 2) * 2))


def validate_video_url(url: str) -> str:
    normalized_url = (url or "").strip()
    if not normalized_url:
        raise ValueError("videoUrl is required")

    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Video URL must start with http:// or https://")

    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_VIDEO_HOSTS and not hostname.endswith(".youtube.com"):
        raise ValueError("Only YouTube video URLs are supported.")

    video_id = ""
    path_parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)

    if hostname in {"youtu.be", "www.youtu.be"}:
        if path_parts:
            video_id = path_parts[0]
    elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "live", "embed", "v"}:
        video_id = path_parts[1]
    else:
        video_id = (query.get("v") or [""])[0]

    video_id = re.sub(r"[^A-Za-z0-9_-]", "", video_id or "")
    if not video_id:
        raise ValueError("The YouTube URL appears to be incomplete.")

    canonical_query: dict[str, str] = {"v": video_id}
    start_value = (query.get("t") or query.get("start") or [""])[0].strip()
    if start_value:
        canonical_query["t"] = start_value

    return f"https://www.youtube.com/watch?{urlencode(canonical_query)}"


def sanitize_output_filename(output_filename: str) -> str:
    raw_value = (output_filename or "").strip() or "short_con_subs.mp4"
    filename = Path(raw_value).name.strip().strip(".")
    filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", filename)
    filename = re.sub(r"\s+", " ", filename).strip()

    if not filename:
        filename = "short_con_subs.mp4"

    path = Path(filename)
    stem = (path.stem or "short_con_subs").strip(" .") or "short_con_subs"
    suffix = path.suffix.lower() or ".mp4"

    if suffix != ".mp4":
        suffix = ".mp4"

    if stem.upper() in WINDOWS_RESERVED_FILENAMES:
        stem = f"{stem}_video"

    return f"{stem}{suffix}"


def normalize_requested_subtitle_style(subtitle_style: dict | None) -> dict:
    if subtitle_style is None:
        return subtitles.normalize_subtitle_style(None)

    if not isinstance(subtitle_style, dict):
        raise ValueError("subtitleStyle must be a JSON object.")

    allowed_keys = {"fontPreset", "colorPreset"}
    unknown_keys = sorted(set(subtitle_style) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"subtitleStyle contains unsupported keys: {', '.join(unknown_keys)}")

    for key, value in subtitle_style.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(f"subtitleStyle field '{key}' must be a string.")

    return subtitles.normalize_subtitle_style(subtitle_style)
def _load_cascades() -> "tuple[_cv2.CascadeClassifier, _cv2.CascadeClassifier]":
    """Lazy-load Haar cascades into module-level singletons (once per process)."""
    global _FRONTAL_CASCADE, _PROFILE_CASCADE
    if not _ensure_cv2():
        raise RuntimeError("OpenCV is not available.")
    if _FRONTAL_CASCADE is None:
        _FRONTAL_CASCADE = _cv2.CascadeClassifier(
            _cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        _PROFILE_CASCADE = _cv2.CascadeClassifier(
            _cv2.data.haarcascades + "haarcascade_profileface.xml"
        )
    return _FRONTAL_CASCADE, _PROFILE_CASCADE


def _detect_face_center_x(clip: VideoFileClip, sample_interval_s: float = 0.5) -> int | None:
    """Return the dominant speaker's horizontal center-x in source pixels.

    Two-pass strategy for reliable detection on podcast/interview content:
      Pass 1 — moderate Haar on raw grayscale (fast, low false-positive rate).
      Pass 2 — relaxed Haar on histogram-equalised frame (catches faces in
               challenging lighting / low-contrast conditions).
    Per frame only the largest face votes.  Temporal median across all frames
    picks the speaker with the most screen-time.

    Returns None when opencv is unavailable or zero faces found in any frame.
    """
    if not _ensure_cv2():
        return None

    frontal_cascade, profile_cascade = _load_cascades()

    duration = clip.duration or 0.0
    src_width, src_height = clip.size

    t_start = min(0.5, duration * 0.05)
    t_end = max(t_start + sample_interval_s, duration - 0.5)
    sample_times: list[float] = []
    t = t_start
    while t <= t_end:
        sample_times.append(t)
        t += sample_interval_s
    if not sample_times:
        sample_times = [duration / 2.0]

    # Detection geometry
    detect_scale = _FACE_DETECT_WIDTH / src_width if src_width > _FACE_DETECT_WIDTH else 1.0
    detect_w = int(src_width * detect_scale)
    detect_h = int(src_height * detect_scale)

    # Two sets of parameters: moderate → relaxed fallback
    min_face_mod = max(30, int(60 * detect_scale))
    min_face_rel = max(20, int(40 * detect_scale))
    _args_moderate = dict(scaleFactor=1.1, minNeighbors=4, minSize=(min_face_mod, min_face_mod))
    _args_relaxed = dict(scaleFactor=1.05, minNeighbors=2, minSize=(min_face_rel, min_face_rel))

    per_frame_cx: list[int] = []

    for t in sample_times:
        frame_rgb = clip.get_frame(t)
        frame_gray = _cv2.cvtColor(frame_rgb.astype(_np.uint8), _cv2.COLOR_RGB2GRAY)
        if detect_scale < 1.0:
            frame_gray = _cv2.resize(frame_gray, (detect_w, detect_h), interpolation=_cv2.INTER_AREA)

        # --- Pass 1: moderate params on raw grayscale ---
        candidates = _collect_face_candidates(
            frontal_cascade, profile_cascade, frame_gray, detect_w, detect_scale, _args_moderate
        )

        # --- Pass 2: relaxed params on equalised frame (if pass 1 found nothing) ---
        if not candidates:
            frame_eq = _cv2.equalizeHist(frame_gray)
            candidates = _collect_face_candidates(
                frontal_cascade, profile_cascade, frame_eq, detect_w, detect_scale, _args_relaxed
            )

        if candidates:
            best_cx = max(candidates, key=lambda item: item[1])[0]
            per_frame_cx.append(best_cx)

    if not per_frame_cx:
        return None

    per_frame_cx.sort()
    return per_frame_cx[len(per_frame_cx) // 2]


def _collect_face_candidates(
    frontal_cascade: "_cv2.CascadeClassifier",
    profile_cascade: "_cv2.CascadeClassifier",
    gray_frame: "_np.ndarray",
    detect_w: int,
    detect_scale: float,
    cascade_args: dict,
) -> list[tuple[int, int]]:
    """Run frontal + profile cascades and return [(cx_source, area_source), ...]."""
    candidates: list[tuple[int, int]] = []

    for x, y, w, h in _run_cascade(frontal_cascade, gray_frame, cascade_args):
        cx = int((x + w / 2) / detect_scale)
        candidates.append((cx, int(w * h / detect_scale ** 2)))

    for flipped_frame, mirror in ((gray_frame, False), (_cv2.flip(gray_frame, 1), True)):
        for x, y, w, h in _run_cascade(profile_cascade, flipped_frame, cascade_args):
            raw_cx = x + w / 2
            orig_cx = (detect_w - raw_cx) if mirror else raw_cx
            cx = int(orig_cx / detect_scale)
            candidates.append((cx, int(w * h / detect_scale ** 2)))

    return candidates


def _run_cascade(
    cascade: "_cv2.CascadeClassifier",
    frame: "_np.ndarray",
    kwargs: dict,
) -> list[tuple[int, int, int, int]]:
    """Run a Haar cascade and return detections as a plain list (empty on failure)."""
    found = cascade.detectMultiScale(frame, **kwargs)
    return found.tolist() if len(found) > 0 else []


# ─── Advanced content-type classification ────────────────────────────

def _detect_faces_full(
    gray_frame: "_np.ndarray",
    frontal_cascade: "_cv2.CascadeClassifier",
    profile_cascade: "_cv2.CascadeClassifier",
    detect_w: int,
    detect_scale: float,
    args_mod: dict,
    args_rel: dict,
) -> list[dict]:
    """Detect faces and return rich per-face metadata (cx, cy, w, h, area in source coords)."""
    faces: list[dict] = []

    def _add(x: int, y: int, w: int, h: int) -> None:
        cx = int((x + w / 2) / detect_scale)
        cy = int((y + h / 2) / detect_scale)
        sw = int(w / detect_scale)
        sh = int(h / detect_scale)
        faces.append({"cx": cx, "cy": cy, "w": sw, "h": sh, "area": sw * sh})

    for x, y, w, h in _run_cascade(frontal_cascade, gray_frame, args_mod):
        _add(x, y, w, h)
    for flipped, mirror in ((gray_frame, False), (_cv2.flip(gray_frame, 1), True)):
        for x, y, w, h in _run_cascade(profile_cascade, flipped, args_mod):
            raw_cx = x + w / 2
            ox = (detect_w - raw_cx) if mirror else raw_cx
            _add(int(ox - w / 2), y, w, h)

    if not faces:
        eq = _cv2.equalizeHist(gray_frame)
        for x, y, w, h in _run_cascade(frontal_cascade, eq, args_rel):
            _add(x, y, w, h)
        for flipped, mirror in ((eq, False), (_cv2.flip(eq, 1), True)):
            for x, y, w, h in _run_cascade(profile_cascade, flipped, args_rel):
                raw_cx = x + w / 2
                ox = (detect_w - raw_cx) if mirror else raw_cx
                _add(int(ox - w / 2), y, w, h)

    return faces


def _sample_frame_data(
    clip: VideoFileClip,
    frontal_cascade: "_cv2.CascadeClassifier",
    profile_cascade: "_cv2.CascadeClassifier",
    num_samples: int = _CLS_NUM_SAMPLES,
) -> list[dict]:
    """Sample frames with scene-change awareness and return per-frame analysis.

    Stage 1: Uniform pre-scan detects scene changes via inter-frame diff.
    Stage 2: Final sample set includes uniform samples *plus* extra samples
             around detected scene changes — this catches mixed content much
             more reliably than uniform sampling alone.
    """
    duration = clip.duration or 0.0
    src_w, src_h = clip.size
    detect_scale = min(1.0, _FACE_DETECT_WIDTH / src_w)
    detect_w = int(src_w * detect_scale)
    detect_h = int(src_h * detect_scale)

    min_face = max(20, int(40 * detect_scale))
    args_mod = dict(scaleFactor=1.08, minNeighbors=3, minSize=(min_face, min_face))
    args_rel = dict(scaleFactor=1.05, minNeighbors=2, minSize=(min_face, min_face))

    num_samples = min(num_samples, max(_CLS_MIN_SAMPLES, int(duration / 2)))

    # ── Stage 1: Uniform pre-scan for scene changes ──
    prescan_n = max(num_samples, min(20, max(6, int(duration / 1.5))))
    prescan_times = [(i + 1) * duration / (prescan_n + 1) for i in range(prescan_n)]

    scene_change_times: list[float] = []
    prev_gray_prescan = None
    for t in prescan_times:
        frame_rgb = clip.get_frame(t)
        gray = _cv2.cvtColor(frame_rgb.astype(_np.uint8), _cv2.COLOR_RGB2GRAY)
        if detect_scale < 1.0:
            gray = _cv2.resize(gray, (detect_w, detect_h), interpolation=_cv2.INTER_AREA)
        if prev_gray_prescan is not None and prev_gray_prescan.shape == gray.shape:
            diff = float(_cv2.absdiff(prev_gray_prescan, gray).mean()) / 255.0
            if diff > _CLS_SCENE_CHANGE_THRESHOLD:
                scene_change_times.append(t)
        prev_gray_prescan = gray

    # ── Stage 2: Build smart sample set ──
    uniform_times = [(i + 1) * duration / (num_samples + 1) for i in range(num_samples)]
    # Add samples around scene changes (± 0.3s) for better transition coverage
    extra_times: list[float] = []
    for sc_t in scene_change_times:
        for offset in (-0.3, 0.0, 0.3):
            et = sc_t + offset
            if 0.1 < et < duration - 0.1:
                extra_times.append(et)

    # Merge and deduplicate (within 0.2s)
    all_times = sorted(set(uniform_times + extra_times))
    deduped: list[float] = []
    for t in all_times:
        if not deduped or t - deduped[-1] >= 0.2:
            deduped.append(t)
    sample_times = deduped

    # ── Stage 3: Full analysis on each sample ──
    prev_gray = None
    results: list[dict] = []

    for t in sample_times:
        frame_rgb = clip.get_frame(t)
        gray = _cv2.cvtColor(frame_rgb.astype(_np.uint8), _cv2.COLOR_RGB2GRAY)
        if detect_scale < 1.0:
            gray_small = _cv2.resize(gray, (detect_w, detect_h), interpolation=_cv2.INTER_AREA)
        else:
            gray_small = gray

        faces = _detect_faces_full(
            gray_small, frontal_cascade, profile_cascade, detect_w, detect_scale, args_mod, args_rel,
        )

        edges = _cv2.Canny(gray_small, 50, 150)
        edge_density = float(edges.mean()) / 255.0

        horiz_kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (25, 1))
        horiz_lines = _cv2.morphologyEx(edges, _cv2.MORPH_OPEN, horiz_kernel)
        horiz_density = float(horiz_lines.mean()) / 255.0

        text_kernel = _np.ones((5, 5), _np.uint8)
        text_regions = _cv2.dilate(edges, text_kernel, iterations=2)
        text_density = float(text_regions.mean()) / 255.0

        hsv = _cv2.cvtColor(frame_rgb.astype(_np.uint8), _cv2.COLOR_RGB2HSV)
        sat_mean = float(hsv[:, :, 1].mean()) / 255.0
        val_std = float(hsv[:, :, 2].std()) / 255.0

        motion = 0.0
        if prev_gray is not None and prev_gray.shape == gray_small.shape:
            diff = _cv2.absdiff(prev_gray, gray_small)
            motion = float(diff.mean()) / 255.0
        prev_gray = gray_small

        face_cx_positions = [f["cx"] / src_w for f in faces] if faces else []
        face_cy_positions = [f["cy"] / src_h for f in faces] if faces else []
        face_sizes = [f["area"] for f in faces]
        biggest_face = max(face_sizes) if face_sizes else 0

        is_scene_change = any(abs(t - sc_t) < 0.35 for sc_t in scene_change_times)

        results.append({
            "t": t,
            "n_faces": len(faces),
            "faces": faces,
            "face_cx_norm": face_cx_positions,
            "face_cy_norm": face_cy_positions,
            "biggest_face_area": biggest_face,
            "edge_density": edge_density,
            "horiz_density": horiz_density,
            "text_density": text_density,
            "sat_mean": sat_mean,
            "val_std": val_std,
            "motion": motion,
            "is_scene_change": is_scene_change,
        })

    return results


def _classify_content_type(clip: VideoFileClip) -> tuple[str, dict]:
    """Advanced multi-signal content classifier with confidence scoring.

    Analyses faces, edge/text density, colour, motion and spatial distributions
    across sampled frames.  Populates the thread-local ``_FaceCache`` so that
    downstream layout builders can reuse face data without re-detecting.

    Returns ``(content_type, metadata_dict)`` where metadata includes a
    ``confidence`` score (0-1).
    """
    if not _ensure_cv2():
        return _CONTENT_SINGLE_SPEAKER, {"confidence": 0.0}

    try:
        frontal_cascade, profile_cascade = _load_cascades()
    except Exception:
        return _CONTENT_SINGLE_SPEAKER, {"confidence": 0.0}

    src_w, src_h = clip.size
    frame_area = src_w * src_h

    try:
        samples = _sample_frame_data(clip, frontal_cascade, profile_cascade, num_samples=_CLS_NUM_SAMPLES)
    except Exception:
        return _CONTENT_MIXED, {"confidence": 0.0}

    n = len(samples)
    if n == 0:
        return _CONTENT_MIXED, {"confidence": 0.0}

    # ── Populate face cache from sample data ──
    cache = _FaceCache(samples=samples, populated=True)

    # Collect per-frame face centres for cache (biggest face per frame)
    all_face_cx_src: list[int] = []
    all_face_cy_src: list[int] = []
    for s in samples:
        if s["faces"]:
            biggest = max(s["faces"], key=lambda f: f["area"])
            all_face_cx_src.append(biggest["cx"])
            all_face_cy_src.append(biggest["cy"])

    if all_face_cx_src:
        sorted_cx = sorted(all_face_cx_src)
        sorted_cy = sorted(all_face_cy_src)
        cache.face_cx = sorted_cx[len(sorted_cx) // 2]
        cache.face_cy = sorted_cy[len(sorted_cy) // 2]
        cache.per_frame_cx = all_face_cx_src
        cache.per_frame_cy = all_face_cy_src

    # ── Aggregate statistics ──
    face_counts = [s["n_faces"] for s in samples]
    avg_faces = sum(face_counts) / n
    max_faces = max(face_counts)
    frames_with_face = sum(1 for c in face_counts if c >= 1)
    frames_with_multi = sum(1 for c in face_counts if c >= 2)
    frames_with_3plus = sum(1 for c in face_counts if c >= 3)
    face_pct = frames_with_face / n
    multi_pct = frames_with_multi / n

    all_areas = [f["area"] for s in samples for f in s["faces"]]
    max_face_ratio = max(all_areas) / frame_area if all_areas else 0.0
    avg_face_ratio = (sum(all_areas) / len(all_areas) / frame_area) if all_areas else 0.0

    avg_edge = sum(s["edge_density"] for s in samples) / n
    avg_horiz = sum(s["horiz_density"] for s in samples) / n
    avg_text = sum(s["text_density"] for s in samples) / n
    avg_sat = sum(s["sat_mean"] for s in samples) / n
    avg_val_std = sum(s["val_std"] for s in samples) / n
    motion_samples = [s["motion"] for s in samples if s["motion"] > 0]
    avg_motion = sum(motion_samples) / len(motion_samples) if motion_samples else 0.0

    biggest_per_frame = [s["biggest_face_area"] / frame_area for s in samples if s["n_faces"] > 0]
    face_size_std = float(_np.std(biggest_per_frame)) if len(biggest_per_frame) > 2 else 0.0

    all_cx_norm = [cx for s in samples for cx in s["face_cx_norm"]]
    cx_clusters = _count_x_clusters(all_cx_norm) if len(all_cx_norm) > 4 else 0

    # Scene-change-aware transition counting
    transitions = sum(
        1 for i in range(1, n) if (face_counts[i] > 0) != (face_counts[i - 1] > 0)
    )
    transition_rate = transitions / max(1, n - 1)

    # Count transitions at actual scene changes (more significant)
    scene_change_transitions = sum(
        1 for i in range(1, n)
        if (face_counts[i] > 0) != (face_counts[i - 1] > 0)
        and samples[i].get("is_scene_change", False)
    )

    lower_text_scores = [s["horiz_density"] + s["text_density"] * 0.5 for s in samples]
    avg_lower_text = sum(lower_text_scores) / n

    # ── Per-frame voting ──
    votes: dict[str, int] = {
        _CONTENT_SINGLE_SPEAKER: 0,
        _CONTENT_PODCAST_DUO: 0,
        _CONTENT_MEETING_GALLERY: 0,
        _CONTENT_SCREEN_SHARE: 0,
        _CONTENT_SCREEN_SHARE_WITH_CAM: 0,
        _CONTENT_NEWS_BROADCAST: 0,
        _CONTENT_BROLL: 0,
    }
    for s in samples:
        nf = s["n_faces"]
        td = s["text_density"]
        ed = s["edge_density"]
        bf = s["biggest_face_area"] / frame_area if s["biggest_face_area"] > 0 else 0.0

        if nf == 0 and td > _CLS_VOTE_TEXT_SCREEN:
            votes[_CONTENT_SCREEN_SHARE] += 1
        elif nf == 0 and ed < _CLS_VOTE_EDGE_BROLL and s.get("motion", 0) > _CLS_VOTE_MOTION_BROLL:
            votes[_CONTENT_BROLL] += 1
        elif nf == 0:
            votes[_CONTENT_BROLL] += 1
        elif nf >= 1 and bf < _CLS_VOTE_FACE_RATIO_CAM and td > _CLS_VOTE_TEXT_CAM:
            votes[_CONTENT_SCREEN_SHARE_WITH_CAM] += 1
        elif nf >= 3:
            votes[_CONTENT_MEETING_GALLERY] += 1
        elif nf == 2:
            votes[_CONTENT_PODCAST_DUO] += 1
        elif nf == 1 and td > _CLS_VOTE_TEXT_NEWS:
            votes[_CONTENT_NEWS_BROADCAST] += 1
        else:
            votes[_CONTENT_SINGLE_SPEAKER] += 1

    top_vote_type = max(votes, key=lambda k: votes[k])
    top_vote_count = votes[top_vote_type]
    vote_confidence = top_vote_count / n

    meta = {
        "avg_faces": round(avg_faces, 2),
        "max_faces": max_faces,
        "face_pct": round(face_pct, 2),
        "multi_pct": round(multi_pct, 2),
        "max_face_ratio": round(max_face_ratio, 5),
        "avg_face_ratio": round(avg_face_ratio, 5),
        "face_size_std": round(face_size_std, 5),
        "avg_edge": round(avg_edge, 4),
        "avg_horiz": round(avg_horiz, 4),
        "avg_text": round(avg_text, 4),
        "avg_sat": round(avg_sat, 4),
        "avg_val_std": round(avg_val_std, 4),
        "avg_motion": round(avg_motion, 5),
        "cx_clusters": cx_clusters,
        "transitions": transitions,
        "transition_rate": round(transition_rate, 3),
        "scene_change_transitions": scene_change_transitions,
        "vote_winner": top_vote_type,
        "vote_confidence": round(vote_confidence, 2),
        "confidence": 0.0,
    }

    # ── Decision tree with named constants ──

    # EARLY RETURN: low frame agreement OR high face/no-face transitions
    if vote_confidence < _CLS_VOTE_CONFIDENCE_MIXED or transition_rate >= _CLS_TRANSITION_RATE_MIXED:
        meta["confidence"] = round(max(0.2, vote_confidence), 2)
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_MIXED, meta

    # 1) SCREEN SHARE
    if avg_text > _CLS_TEXT_SCREEN and face_pct < _CLS_FACE_PCT_SCREEN and avg_edge > _CLS_EDGE_SCREEN:
        meta["confidence"] = round(min(1.0, avg_text / 0.30), 2)
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_SCREEN_SHARE, meta

    # 2) SCREEN SHARE WITH WEBCAM
    if avg_text > _CLS_TEXT_SCREENCAM and face_pct > _CLS_FACE_PCT_SCREEN and max_face_ratio < _CLS_FACE_RATIO_SCREENCAM:
        meta["confidence"] = round(min(1.0, avg_text / 0.25), 2)
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_SCREEN_SHARE_WITH_CAM, meta

    # 3) PiP webcam overlay on slides
    if max_face_ratio < _CLS_FACE_RATIO_PIP and avg_edge > _CLS_EDGE_PIP:
        meta["confidence"] = round(min(1.0, avg_edge / 0.12), 2)
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_SCREEN_SHARE_WITH_CAM, meta

    # 4) B-ROLL
    if face_pct < _CLS_FACE_PCT_BROLL and avg_text < _CLS_TEXT_BROLL:
        if avg_motion > _CLS_MOTION_BROLL or avg_sat > _CLS_SAT_BROLL:
            meta["confidence"] = round(min(1.0, (1.0 - face_pct) * 0.8), 2)
            _populate_cache_extras(cache, samples, src_w, src_h)
            _set_face_cache(cache)
            return _CONTENT_BROLL, meta
        meta["confidence"] = 0.5
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_SCREEN_SHARE, meta

    # 5) MEETING GALLERY (3+ faces)
    if frames_with_3plus >= n * _CLS_3PLUS_FRAC_MEETING and avg_faces >= _CLS_AVG_FACES_MEETING:
        meta["confidence"] = round(min(1.0, frames_with_3plus / n), 2)
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_MEETING_GALLERY, meta

    # 6) MEETING GALLERY (2+ consistent small faces)
    if multi_pct >= _CLS_MULTI_PCT_MEETING and avg_faces >= _CLS_AVG_FACES_MEETING2 and avg_face_ratio < _CLS_FACE_RATIO_MEETING:
        meta["confidence"] = round(multi_pct * 0.9, 2)
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_MEETING_GALLERY, meta

    # 7) PODCAST DUO
    if cx_clusters >= 2 and multi_pct >= _CLS_MULTI_PCT_PODCAST and _CLS_AVG_FACES_PODCAST_LO <= avg_faces < _CLS_AVG_FACES_PODCAST_HI:
        if face_size_std < _CLS_FACE_SIZE_STD_PODCAST:
            meta["confidence"] = round(min(1.0, multi_pct), 2)
            # Also populate duo positions in cache
            _populate_duo_cache(cache, samples, src_w, src_h)
            _populate_cache_extras(cache, samples, src_w, src_h)
            _set_face_cache(cache)
            return _CONTENT_PODCAST_DUO, meta

    # 8) NEWS BROADCAST
    if face_pct > _CLS_FACE_PCT_NEWS and max_face_ratio > _CLS_FACE_RATIO_NEWS and avg_lower_text > _CLS_LOWER_TEXT_NEWS and avg_horiz > _CLS_HORIZ_NEWS:
        meta["confidence"] = round(min(1.0, face_pct * 0.8), 2)
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_NEWS_BROADCAST, meta

    # 9) Secondary mixed check
    if transitions >= _CLS_TRANSITION_COUNT_MIXED and face_pct < _CLS_FACE_PCT_MIXED:
        meta["confidence"] = round(max(0.3, 1.0 - transition_rate), 2)
        _populate_cache_extras(cache, samples, src_w, src_h)
        _set_face_cache(cache)
        return _CONTENT_MIXED, meta

    # 10) SINGLE SPEAKER
    meta["confidence"] = round(min(1.0, face_pct), 2)
    _populate_cache_extras(cache, samples, src_w, src_h)
    _set_face_cache(cache)
    return _CONTENT_SINGLE_SPEAKER, meta


def _populate_cache_extras(cache: _FaceCache, samples: list[dict], src_w: int, src_h: int) -> None:
    """Compute face_bbox from cached sample data."""
    all_faces = [f for s in samples for f in s["faces"]]
    if all_faces:
        x1 = min(f["cx"] - f["w"] // 2 for f in all_faces)
        y1 = min(f["cy"] - f["h"] // 2 for f in all_faces)
        x2 = max(f["cx"] + f["w"] // 2 for f in all_faces)
        y2 = max(f["cy"] + f["h"] // 2 for f in all_faces)
        cache.face_bbox = (max(0, x1), max(0, y1), min(src_w, x2), min(src_h, y2))


def _populate_duo_cache(cache: _FaceCache, samples: list[dict], src_w: int, src_h: int) -> None:
    """Compute duo speaker positions from cached sample data."""
    all_cx = [f["cx"] for s in samples for f in s["faces"]]
    if len(all_cx) < 4:
        return
    all_cx.sort()
    mid = src_w / 2
    left_group = [x for x in all_cx if x < mid]
    right_group = [x for x in all_cx if x >= mid]
    if not left_group or not right_group:
        median_cx = all_cx[len(all_cx) // 2]
        left_group = [x for x in all_cx if x <= median_cx]
        right_group = [x for x in all_cx if x > median_cx]
    if not left_group or not right_group:
        return
    left_cx = int(sum(left_group) / len(left_group))
    right_cx = int(sum(right_group) / len(right_group))
    if abs(right_cx - left_cx) < src_w * _CLS_DUO_SEPARATION_MIN:
        return
    cache.duo_positions = (left_cx, right_cx)


def _count_x_clusters(cx_values: list[float], threshold: float = 0.20) -> int:
    """Count distinct horizontal clusters in face positions (0-1 normalised)."""
    if not cx_values:
        return 0
    sorted_cx = sorted(cx_values)
    clusters = 1
    prev = sorted_cx[0]
    for v in sorted_cx[1:]:
        if v - prev > threshold:
            clusters += 1
        prev = v
    return clusters


def _estimate_speaker_switches(cache: _FaceCache, src_w: int) -> int:
    if not cache.per_frame_cx:
        return 0

    states: list[str] = []
    for cx in cache.per_frame_cx:
        if cx < src_w * 0.45:
            states.append("left")
        elif cx > src_w * 0.55:
            states.append("right")
        else:
            states.append("center")

    switches = 0
    previous = states[0]
    for state in states[1:]:
        if state != previous and state != "center" and previous != "center":
            switches += 1
        previous = state
    return switches


def _estimate_speaker_balance(cache: _FaceCache, src_w: int) -> float | None:
    if not cache.per_frame_cx:
        return None

    left = sum(1 for cx in cache.per_frame_cx if cx < src_w * 0.5)
    right = sum(1 for cx in cache.per_frame_cx if cx >= src_w * 0.5)
    total = left + right
    if total == 0:
        return None
    return round(min(left, right) / total, 3)


def _estimate_tracking_stability(cache: _FaceCache, src_w: int) -> float | None:
    if len(cache.per_frame_cx) < 2:
        return None
    deltas = [abs(curr - prev) / max(1, src_w) for prev, curr in zip(cache.per_frame_cx, cache.per_frame_cx[1:])]
    if not deltas:
        return None
    avg_delta = sum(deltas) / len(deltas)
    return round(max(0.0, 1.0 - min(1.0, avg_delta * 3.2)), 3)


def _augment_meta_with_speaker_data(meta: dict, content_type: str, clip: VideoFileClip) -> dict:
    cache = _get_face_cache()
    if cache is None or not cache.populated:
        return meta

    src_w, src_h = clip.size
    enriched = dict(meta)
    enriched["speakerCountEstimate"] = int(max(1, round(meta.get("avg_faces", 1) or 1)))
    enriched["speakerTrackingMode"] = {
        _CONTENT_SINGLE_SPEAKER: "smooth_pan",
        _CONTENT_PODCAST_DUO: "duo_split",
        _CONTENT_MEETING_GALLERY: "group_frame",
        _CONTENT_SCREEN_SHARE_WITH_CAM: "pip_focus",
        _CONTENT_NEWS_BROADCAST: "anchor_focus",
    }.get(content_type, "fullframe")

    if cache.face_cx is not None:
        enriched["dominantSpeakerX"] = round(cache.face_cx / max(1, src_w), 4)
    if cache.face_cy is not None:
        enriched["dominantSpeakerY"] = round(cache.face_cy / max(1, src_h), 4)
    if cache.face_bbox is not None:
        x1, y1, x2, y2 = cache.face_bbox
        enriched["speakerBBox"] = {
            "x1": round(x1 / max(1, src_w), 4),
            "y1": round(y1 / max(1, src_h), 4),
            "x2": round(x2 / max(1, src_w), 4),
            "y2": round(y2 / max(1, src_h), 4),
        }
    if cache.duo_positions is not None:
        left_cx, right_cx = cache.duo_positions
        enriched["speakerSlots"] = [round(left_cx / max(1, src_w), 4), round(right_cx / max(1, src_w), 4)]
    enriched["speakerSwitches"] = _estimate_speaker_switches(cache, src_w)
    enriched["speakerBalance"] = _estimate_speaker_balance(cache, src_w)
    enriched["speakerTrackingStability"] = _estimate_tracking_stability(cache, src_w)
    enriched["speakerTrackingSamples"] = len(cache.samples)
    return enriched


def _refine_content_type_with_speaker_data(
    content_type: str,
    meta: dict,
    audio_speaker_meta: dict | None = None,
) -> tuple[str, dict]:
    adjusted = dict(meta)

    if audio_speaker_meta:
        adjusted.update(audio_speaker_meta)

    if content_type == _CONTENT_PODCAST_DUO:
        switches = int(adjusted.get("speakerSwitches") or 0)
        balance = adjusted.get("speakerBalance")
        audio_count = int(adjusted.get("audioSpeakerCount") or 0)
        audio_confidence = float(adjusted.get("audioSpeakerConfidence") or 0.0)
        audio_dominant_share = float(adjusted.get("audioDominantShare") or 0.0)

        if audio_count <= 1 and audio_confidence >= 0.5:
            adjusted["speaker_layout_override"] = "audio_single"
            adjusted["speaker_override_reason"] = "Audio analysis suggests one dominant speaker."
            adjusted["confidence"] = round(max(0.25, float(adjusted.get("confidence") or 0.5) - 0.12), 2)
            return _CONTENT_SINGLE_SPEAKER, adjusted

        if audio_count >= 2 and audio_confidence >= 0.75 and audio_dominant_share >= 0.84:
            adjusted["speaker_layout_override"] = "audio_dominant_single"
            adjusted["speaker_override_reason"] = "Two voices were detected, but one voice dominates the clip."
            adjusted["confidence"] = round(max(0.25, float(adjusted.get("confidence") or 0.5) - 0.15), 2)
            return _CONTENT_SINGLE_SPEAKER, adjusted

        if balance is not None and balance < 0.12 and switches <= 1:
            adjusted["speaker_layout_override"] = "dominant_single"
            adjusted["speaker_override_reason"] = "Duo candidate is visually dominated by one speaker."
            adjusted["confidence"] = round(max(0.25, float(adjusted.get("confidence") or 0.5) - 0.18), 2)
            return _CONTENT_SINGLE_SPEAKER, adjusted

    if content_type in {_CONTENT_SINGLE_SPEAKER, _CONTENT_MIXED, _CONTENT_MEETING_GALLERY}:
        audio_count = int(adjusted.get("audioSpeakerCount") or 0)
        audio_confidence = float(adjusted.get("audioSpeakerConfidence") or 0.0)
        audio_switches = int(adjusted.get("audioSpeakerSwitches") or 0)
        audio_dominant_share = float(adjusted.get("audioDominantShare") or 0.0)
        visual_speakers = int(adjusted.get("speakerCountEstimate") or 1)

        if (
            audio_count >= 2
            and audio_confidence >= 0.78
            and audio_switches >= 2
            and audio_dominant_share <= 0.76
            and visual_speakers >= 2
        ):
            adjusted["speaker_layout_override"] = "audio_promoted_duo"
            adjusted["speaker_override_reason"] = "Audio turn-taking and visual speaker count indicate a two-person layout."
            adjusted["confidence"] = round(min(0.95, max(0.55, float(adjusted.get("confidence") or 0.5) + 0.1)), 2)
            return _CONTENT_PODCAST_DUO, adjusted

    return content_type, adjusted


_CONTENT_LABELS = {
    _CONTENT_SINGLE_SPEAKER: "single speaker",
    _CONTENT_PODCAST_DUO: "podcast / two-person interview",
    _CONTENT_MEETING_GALLERY: "meeting / multi-speaker gallery",
    _CONTENT_SCREEN_SHARE: "screen share / slides",
    _CONTENT_SCREEN_SHARE_WITH_CAM: "screen share + webcam overlay",
    _CONTENT_NEWS_BROADCAST: "news / broadcast (lower-third)",
    _CONTENT_BROLL: "B-roll / cinematic footage",
    _CONTENT_MIXED: "mixed content (transitions mid-clip)",
}


# ─── Composition helpers (shared) ────────────────────────────────────

def _blur_darken_frame(frame: "_np.ndarray") -> "_np.ndarray":
    """Blur heavily and darken a frame for use as a background layer."""
    if not _ensure_cv2():
        return (frame.astype(_np.float32) * 0.3).astype(_np.uint8)
    blurred = _cv2.GaussianBlur(frame, (51, 51), 0)
    return (blurred.astype(_np.float32) * 0.3).astype(_np.uint8)


def _make_blur_background(clip_silent: VideoFileClip, src_w: int, src_h: int,
                          out_w: int, out_h: int) -> VideoFileClip:
    """Zoom source to fill 9:16, then blur + darken for background layer."""
    bg_zoom = max(out_w / src_w, out_h / src_h) * 1.08
    bg_w = _make_even(int(src_w * bg_zoom))
    bg_h = _make_even(int(src_h * bg_zoom))
    bg = clip_silent.resized(new_size=(bg_w, bg_h))
    bx = max(0, (bg_w - out_w) // 2)
    by = max(0, (bg_h - out_h) // 2)
    bg = bg.cropped(x1=bx, y1=by, x2=bx + out_w, y2=by + out_h)
    return bg.image_transform(_blur_darken_frame)


def _make_gradient_separator(width: int, height: int, direction: str = "horizontal") -> "_np.ndarray":
    """Create a subtle gradient separator bar (RGBA)."""
    bar = _np.zeros((height, width, 4), dtype=_np.uint8)
    if direction == "horizontal":
        for y in range(height):
            alpha = int(80 * (1.0 - abs(y - height / 2) / (height / 2)))
            bar[y, :, :3] = 255
            bar[y, :, 3] = alpha
    return bar


# ─── Layout: Full-frame with blur background ─────────────────────────

def _build_fullframe_vertical_clip(clip: VideoFileClip) -> VideoFileClip:
    """Preserves entire source frame scaled to fit width, over blurred background.

    Used for screen shares, pure slides, and B-roll — anything where the
    full frame matters more than a tight face crop.
    This is the safe fallback target, so it has its own internal safety net.
    """
    src_w, src_h = clip.size
    out_w, out_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
    clip_silent = clip.without_audio() if clip.audio is not None else clip

    try:
        fg_scale = out_w / src_w
        fg_h = _make_even(int(src_h * fg_scale))
        fg_w = out_w
        if fg_h > out_h:
            fg_scale = out_h / src_h
            fg_w = _make_even(int(src_w * fg_scale))
            fg_h = out_h
        foreground = clip_silent.resized(new_size=(fg_w, fg_h))

        y_offset = max(0, int((out_h - fg_h) * 0.35))
        x_offset = max(0, (out_w - fg_w) // 2)

        background = _make_blur_background(clip_silent, src_w, src_h, out_w, out_h)

        layers = [background, foreground.with_position((x_offset, y_offset))]

        # Add subtle separator lines above and below the foreground window
        if fg_h < out_h - 20:
            sep = _make_gradient_separator(out_w, 3)
            top_sep = ImageClip(sep).with_duration(clip.duration).with_position((0, y_offset - 2))
            bot_sep = ImageClip(sep).with_duration(clip.duration).with_position((0, y_offset + fg_h))
            layers.extend([top_sep, bot_sep])

        return CompositeVideoClip(layers, size=(out_w, out_h)).with_duration(clip.duration)
    except Exception:
        # Absolute fallback — just resize the entire clip
        return clip_silent.resized(new_size=(out_w, out_h))


# ─── Layout: Screen share + webcam picture-in-picture ─────────────────

def _build_screenshare_with_cam_clip(clip: VideoFileClip) -> VideoFileClip:
    """Screen share fills the main area; a circular-masked webcam inset shows the speaker.

    The speaker face is detected, cropped square, placed in the bottom-right
    corner at 25% width.  If no face is found or any step fails, falls back
    to fullframe layout.
    """
    src_w, src_h = clip.size
    out_w, out_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
    clip_silent = clip.without_audio() if clip.audio is not None else clip

    # Main screen share — full width
    fg_scale = out_w / src_w
    fg_h = _make_even(int(src_h * fg_scale))
    fg_w = out_w
    if fg_h > out_h:
        fg_scale = out_h / src_h
        fg_w = _make_even(int(src_w * fg_scale))
        fg_h = out_h
    foreground = clip_silent.resized(new_size=(fg_w, fg_h))
    y_offset = max(0, int((out_h - fg_h) * 0.30))
    x_offset = max(0, (out_w - fg_w) // 2)

    background = _make_blur_background(clip_silent, src_w, src_h, out_w, out_h)

    layers = [background, foreground.with_position((x_offset, y_offset))]

    # Try to find speaker face for PiP inset — wrapped in try/except for safety
    try:
        cache = _get_face_cache()
        face_cx = cache.face_cx if cache and cache.populated else _detect_face_center_x(clip)
        if face_cx is not None:
            pip_size = int(out_w * 0.28)
            pip_src_size = max(10, int(pip_size / fg_scale))
            half = pip_src_size // 2

            face_cy = cache.face_cy if cache and cache.populated else _detect_face_center_y(clip)
            if face_cy is None:
                face_cy = src_h // 3

            fx1 = max(0, min(src_w - pip_src_size, face_cx - half))
            fy1 = max(0, min(src_h - pip_src_size, face_cy - half))
            fx2 = min(src_w, fx1 + pip_src_size)
            fy2 = min(src_h, fy1 + pip_src_size)

            # Safety: skip PiP if crop region is too small
            if (fx2 - fx1) >= 20 and (fy2 - fy1) >= 20:
                pip_clip = clip_silent.cropped(x1=fx1, y1=fy1, x2=fx2, y2=fy2)
                pip_clip = pip_clip.resized(new_size=(pip_size, pip_size))

                pip_mask = _make_circle_mask(pip_size)
                pip_clip = pip_clip.with_mask(ImageClip(pip_mask, is_mask=True).with_duration(clip.duration))

                pip_margin = int(out_w * 0.04)
                pip_x = out_w - pip_size - pip_margin
                pip_y = out_h - pip_size - pip_margin - 180
                layers.append(pip_clip.with_position((pip_x, pip_y)))

                ring = _make_circle_ring(pip_size + 6, 3)
                ring_clip = ImageClip(ring).with_duration(clip.duration).with_position((pip_x - 3, pip_y - 3))
                layers.append(ring_clip)
    except Exception:
        pass  # PiP failed — still have a valid fullframe layout in layers

    return CompositeVideoClip(layers, size=(out_w, out_h)).with_duration(clip.duration)


def _detect_face_center_y(clip: VideoFileClip, sample_interval_s: float = 1.0) -> int | None:
    """Like _detect_face_center_x but returns the dominant face's vertical centre."""
    if not _ensure_cv2():
        return None

    frontal_cascade, profile_cascade = _load_cascades()
    duration = clip.duration or 0.0
    src_w, src_h = clip.size
    detect_scale = min(1.0, _FACE_DETECT_WIDTH / src_w)
    detect_w = int(src_w * detect_scale)
    detect_h = int(src_h * detect_scale)

    min_face = max(20, int(40 * detect_scale))
    args = dict(scaleFactor=1.08, minNeighbors=3, minSize=(min_face, min_face))

    t_start = min(0.5, duration * 0.05)
    t_end = max(t_start + sample_interval_s, duration - 0.5)
    sample_times: list[float] = []
    t = t_start
    while t <= t_end:
        sample_times.append(t)
        t += sample_interval_s
    if not sample_times:
        sample_times = [duration / 2.0]

    cy_values: list[int] = []
    for t in sample_times:
        frame_rgb = clip.get_frame(t)
        gray = _cv2.cvtColor(frame_rgb.astype(_np.uint8), _cv2.COLOR_RGB2GRAY)
        if detect_scale < 1.0:
            gray = _cv2.resize(gray, (detect_w, detect_h), interpolation=_cv2.INTER_AREA)

        faces = _detect_faces_full(gray, frontal_cascade, profile_cascade, detect_w, detect_scale, args, args)
        if faces:
            biggest = max(faces, key=lambda f: f["area"])
            cy_values.append(biggest["cy"])

    if not cy_values:
        return None
    cy_values.sort()
    return cy_values[len(cy_values) // 2]


def _make_circle_mask(size: int) -> "_np.ndarray":
    """Create a smooth circular alpha mask."""
    y, x = _np.ogrid[:size, :size]
    centre = size / 2.0
    dist = _np.sqrt((x - centre) ** 2 + (y - centre) ** 2)
    mask = _np.clip(1.0 - (dist - centre + 1.5) / 1.5, 0, 1)
    return (mask * 255).astype(_np.uint8)


def _make_circle_ring(size: int, thickness: int) -> "_np.ndarray":
    """Create a white circle ring with anti-aliased edges (RGBA)."""
    ring = _np.zeros((size, size, 4), dtype=_np.uint8)
    y, x = _np.ogrid[:size, :size]
    centre = size / 2.0
    dist = _np.sqrt((x - centre) ** 2 + (y - centre) ** 2)
    outer = centre
    inner = centre - thickness
    # Anti-aliased ring alpha
    alpha = _np.clip(1.0 - abs(dist - (inner + outer) / 2) / (thickness / 2 + 0.5), 0, 1)
    ring[:, :, :3] = 255
    ring[:, :, 3] = (alpha * 200).astype(_np.uint8)
    return ring


# ─── Layout: Podcast duo (split-screen with speaker focus) ───────────

def _build_podcast_duo_clip(clip: VideoFileClip) -> VideoFileClip:
    """Two-person content: split-screen or dynamic focus on active speaker.

    Detects two face clusters, creates a stacked top/bottom split with each
    speaker cropped tightly, separated by a subtle gradient line.
    Falls back to fullframe on any failure.
    """
    try:
        src_w, src_h = clip.size
        out_w, out_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
        clip_silent = clip.without_audio() if clip.audio is not None else clip

        # Detect face positions to find the two speakers
        face_positions = _detect_duo_face_positions(clip)

        if face_positions is None or len(face_positions) < 2:
            return _build_fullframe_vertical_clip(clip)

        left_cx, right_cx = face_positions

        # Each speaker gets half the vertical output
        half_h = out_h // 2
        crop_h_src = src_h
        crop_w_src = min(src_w, _make_even(int(crop_h_src * (out_w / half_h))))

        speakers: list[VideoFileClip] = []
        for cx in (left_cx, right_cx):
            x1 = max(0, min(src_w - crop_w_src, cx - crop_w_src // 2))
            x2 = min(src_w, x1 + crop_w_src)
            if x2 - x1 < 20:
                return _build_fullframe_vertical_clip(clip)
            speaker_clip = clip_silent.cropped(x1=x1, y1=0, x2=x2, y2=src_h)
            speaker_clip = speaker_clip.resized(new_size=(out_w, half_h))
            speakers.append(speaker_clip)

        background = _make_blur_background(clip_silent, src_w, src_h, out_w, out_h)

        layers = [
            background,
            speakers[0].with_position((0, 0)),
            speakers[1].with_position((0, half_h)),
        ]

        sep = _make_gradient_separator(out_w, 6)
        sep_clip = ImageClip(sep).with_duration(clip.duration).with_position((0, half_h - 3))
        layers.append(sep_clip)

        return CompositeVideoClip(layers, size=(out_w, out_h)).with_duration(clip.duration)
    except Exception as exc:
        _warn_note(f"Podcast duo layout failed ({exc}), falling back to fullframe.")
        return _build_fullframe_vertical_clip(clip)


def _detect_duo_face_positions(clip: VideoFileClip) -> tuple[int, int] | None:
    """Find the x centres of two face clusters (left speaker, right speaker).
    Uses face cache if available to avoid redundant detection.
    """
    cache = _get_face_cache()
    if cache is not None and cache.populated and cache.duo_positions is not None:
        return cache.duo_positions

    if not _ensure_cv2():
        return None

    frontal_cascade, profile_cascade = _load_cascades()
    duration = clip.duration or 0.0
    src_w, src_h = clip.size
    detect_scale = min(1.0, _FACE_DETECT_WIDTH / src_w)
    detect_w = int(src_w * detect_scale)
    detect_h = int(src_h * detect_scale)

    min_face = max(20, int(40 * detect_scale))
    args = dict(scaleFactor=1.08, minNeighbors=3, minSize=(min_face, min_face))

    # Use cached samples if available
    all_cx: list[int] = []
    if cache is not None and cache.populated and cache.samples:
        for s in cache.samples:
            for f in s["faces"]:
                all_cx.append(f["cx"])
    else:
        num_samples = min(10, max(4, int(duration / 3)))
        sample_times = [(i + 1) * duration / (num_samples + 1) for i in range(num_samples)]
        for t in sample_times:
            frame_rgb = clip.get_frame(t)
            gray = _cv2.cvtColor(frame_rgb.astype(_np.uint8), _cv2.COLOR_RGB2GRAY)
            if detect_scale < 1.0:
                gray = _cv2.resize(gray, (detect_w, detect_h), interpolation=_cv2.INTER_AREA)
            faces = _detect_faces_full(gray, frontal_cascade, profile_cascade, detect_w, detect_scale, args, args)
            for f in faces:
                all_cx.append(f["cx"])

    if len(all_cx) < 4:
        return None

    all_cx.sort()
    mid = src_w / 2
    left_group = [x for x in all_cx if x < mid]
    right_group = [x for x in all_cx if x >= mid]

    if not left_group or not right_group:
        median_cx = all_cx[len(all_cx) // 2]
        left_group = [x for x in all_cx if x <= median_cx]
        right_group = [x for x in all_cx if x > median_cx]

    if not left_group or not right_group:
        return None

    left_cx = int(sum(left_group) / len(left_group))
    right_cx = int(sum(right_group) / len(right_group))

    if abs(right_cx - left_cx) < src_w * _CLS_DUO_SEPARATION_MIN:
        return None

    return (left_cx, right_cx)


# ─── Layout: Meeting gallery (grid extract + zoom) ───────────────────

def _build_meeting_gallery_clip(clip: VideoFileClip) -> VideoFileClip:
    """Multi-speaker gallery: shows full frame (to preserve all participants)
    with enhanced framing — slightly zoomed to reduce empty borders and centred
    on the cluster of detected faces.  Falls back to fullframe on any failure.
    """
    try:
        src_w, src_h = clip.size
        out_w, out_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
        clip_silent = clip.without_audio() if clip.audio is not None else clip

        face_bbox = _detect_face_bbox(clip)

        if face_bbox is not None:
            fx1, fy1, fx2, fy2 = face_bbox
            pad_x = int((fx2 - fx1) * 0.3)
            pad_y = int((fy2 - fy1) * 0.3)
            cx1 = max(0, fx1 - pad_x)
            cy1 = max(0, fy1 - pad_y)
            cx2 = min(src_w, fx2 + pad_x)
            cy2 = min(src_h, fy2 + pad_y)

            crop_w = cx2 - cx1
            crop_h = cy2 - cy1
            target_h = int(crop_w / TARGET_ASPECT_RATIO)

            if target_h > crop_h:
                extra = target_h - crop_h
                cy1 = max(0, cy1 - extra // 2)
                cy2 = min(src_h, cy1 + target_h)
                if cy2 - cy1 < target_h:
                    cy1 = max(0, cy2 - target_h)
            elif target_h < crop_h:
                target_w = int(crop_h * TARGET_ASPECT_RATIO)
                extra = target_w - crop_w
                cx1 = max(0, cx1 - extra // 2)
                cx2 = min(src_w, cx1 + target_w)
                if cx2 - cx1 < target_w:
                    cx1 = max(0, cx2 - target_w)

            crop_w = _make_even(cx2 - cx1)
            crop_h = _make_even(cy2 - cy1)

            if crop_w >= 20 and crop_h >= 20 and crop_w >= src_w * 0.4 and crop_h >= src_h * 0.4:
                cropped = clip_silent.cropped(x1=cx1, y1=cy1, x2=cx1 + crop_w, y2=cy1 + crop_h)
                # Scale to fit canvas preserving aspect ratio (same as fullframe logic)
                fg_scale = out_w / crop_w
                fg_h = _make_even(int(crop_h * fg_scale))
                fg_w = out_w
                if fg_h > out_h:
                    fg_scale = out_h / crop_h
                    fg_w = _make_even(int(crop_w * fg_scale))
                    fg_h = out_h
                fg = cropped.resized(new_size=(fg_w, fg_h))
                y_offset = max(0, int((out_h - fg_h) * 0.35))
                x_offset = max(0, (out_w - fg_w) // 2)
                background = _make_blur_background(clip_silent, src_w, src_h, out_w, out_h)
                return CompositeVideoClip(
                    [background, fg.with_position((x_offset, y_offset))],
                    size=(out_w, out_h),
                ).with_duration(clip.duration)

        return _build_fullframe_vertical_clip(clip)
    except Exception as exc:
        _warn_note(f"Meeting gallery layout failed ({exc}), falling back to fullframe.")
        return _build_fullframe_vertical_clip(clip)


def _detect_face_bbox(clip: VideoFileClip) -> tuple[int, int, int, int] | None:
    """Return the bounding box (x1, y1, x2, y2) covering all detected faces.
    Uses face cache if available.
    """
    cache = _get_face_cache()
    if cache is not None and cache.populated and cache.face_bbox is not None:
        return cache.face_bbox

    if not _ensure_cv2():
        return None

    frontal_cascade, profile_cascade = _load_cascades()
    duration = clip.duration or 0.0
    src_w, src_h = clip.size
    detect_scale = min(1.0, _FACE_DETECT_WIDTH / src_w)
    detect_w = int(src_w * detect_scale)
    detect_h = int(src_h * detect_scale)

    min_face = max(20, int(40 * detect_scale))
    args = dict(scaleFactor=1.08, minNeighbors=3, minSize=(min_face, min_face))

    # Use cached samples if available
    all_faces: list[dict] = []
    if cache is not None and cache.populated and cache.samples:
        all_faces = [f for s in cache.samples for f in s["faces"]]
    else:
        num_samples = min(8, max(3, int(duration / 3)))
        sample_times = [(i + 1) * duration / (num_samples + 1) for i in range(num_samples)]
        for t in sample_times:
            frame_rgb = clip.get_frame(t)
            gray = _cv2.cvtColor(frame_rgb.astype(_np.uint8), _cv2.COLOR_RGB2GRAY)
            if detect_scale < 1.0:
                gray = _cv2.resize(gray, (detect_w, detect_h), interpolation=_cv2.INTER_AREA)
            faces = _detect_faces_full(gray, frontal_cascade, profile_cascade, detect_w, detect_scale, args, args)
            all_faces.extend(faces)

    if not all_faces:
        return None

    x1 = min(f["cx"] - f["w"] // 2 for f in all_faces)
    y1 = min(f["cy"] - f["h"] // 2 for f in all_faces)
    x2 = max(f["cx"] + f["w"] // 2 for f in all_faces)
    y2 = max(f["cy"] + f["h"] // 2 for f in all_faces)

    return (max(0, x1), max(0, y1), min(src_w, x2), min(src_h, y2))


# ─── Layout: News broadcast (face crop + lower-third safe zone) ──────

def _build_news_broadcast_clip(clip: VideoFileClip) -> VideoFileClip:
    """News/broadcast: face-centred crop, but reserves bottom area for
    lower-third graphics and subtitle clearance.
    Falls back to fullframe on any failure.
    """
    try:
        src_w, src_h = clip.size
        out_w, out_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
        clip_silent = clip.without_audio() if clip.audio is not None else clip

        cache = _get_face_cache()
        face_cx = cache.face_cx if cache and cache.populated else _detect_face_center_x(clip)
        face_cy = cache.face_cy if cache and cache.populated else _detect_face_center_y(clip)

        if face_cx is not None and face_cy is not None:
            crop_w = min(src_w, _make_even(int(src_h * TARGET_ASPECT_RATIO)))
            if crop_w < 20:
                return _build_fullframe_vertical_clip(clip)
            x1 = max(0, min(src_w - crop_w, face_cx - crop_w // 2))
            x2 = min(src_w, x1 + crop_w)
            if x2 - x1 < 20:
                return _build_fullframe_vertical_clip(clip)
            cropped = clip_silent.cropped(x1=x1, y1=0, x2=x2, y2=src_h)
            fg = cropped.resized(new_size=(out_w, out_h))

            background = _make_blur_background(clip_silent, src_w, src_h, out_w, out_h)
            return CompositeVideoClip(
                [background, fg.with_position((0, 0))],
                size=(out_w, out_h),
            ).with_duration(clip.duration)

        return _build_fullframe_vertical_clip(clip)
    except Exception as exc:
        _warn_note(f"News broadcast layout failed ({exc}), falling back to fullframe.")
        return _build_fullframe_vertical_clip(clip)


# ─── Layout: Mixed content (use fullframe as safe default) ───────────

def _build_mixed_content_clip(clip: VideoFileClip) -> VideoFileClip:
    """Mixed content with transitions between face/slide/B-roll — use the
    fullframe layout which preserves everything regardless of what's on screen.
    Slightly stronger blur + brighter foreground to compensate for variance.
    """
    return _build_fullframe_vertical_clip(clip)


# ─── Layout: Dynamic face-tracking single speaker ────────────────────

def _build_smooth_pan_speaker_clip(clip: VideoFileClip) -> VideoFileClip:
    """Single speaker with smooth horizontal panning that follows the face.

    Instead of a static crop at the median face position, this uses keyframe-
    based interpolation from the face cache to create a subtle pan that tracks
    speaker movement.  The pan is smoothed with ease-in-out to avoid jitter.
    Falls back to static crop if cache or tracking data is insufficient.
    """
    try:
        src_w, src_h = clip.size
        out_w, out_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
        duration = clip.duration or 0.0

        cache = _get_face_cache()
        if cache is None or not cache.populated or not cache.samples:
            return _build_static_speaker_crop(clip)

        # Build keyframes: (time, face_cx) from cached sample data
        keyframes: list[tuple[float, int]] = []
        for s in cache.samples:
            if s["faces"]:
                biggest = max(s["faces"], key=lambda f: f["area"])
                keyframes.append((s["t"], biggest["cx"]))

        if len(keyframes) < 2:
            # Not enough tracking data — use static crop
            return _build_static_speaker_crop(clip)

        # Smooth the keyframes: moving average to reduce jitter
        smoothed_cx = [kf[1] for kf in keyframes]
        if len(smoothed_cx) >= 3:
            smoothed = [smoothed_cx[0]]
            for i in range(1, len(smoothed_cx) - 1):
                smoothed.append(int(0.25 * smoothed_cx[i - 1] + 0.5 * smoothed_cx[i] + 0.25 * smoothed_cx[i + 1]))
            smoothed.append(smoothed_cx[-1])
            smoothed_cx = smoothed

        # Clamp pan range: don't let the crop window move more than 15% of src_w
        # to avoid disorienting jumps
        median_cx = cache.face_cx or src_w // 2
        max_pan = int(src_w * 0.15)
        smoothed_cx = [max(median_cx - max_pan, min(median_cx + max_pan, cx)) for cx in smoothed_cx]

        kf_times = [kf[0] for kf in keyframes]
        kf_cx_values = smoothed_cx

        # Determine crop geometry
        source_ratio = src_w / src_h
        if source_ratio > TARGET_ASPECT_RATIO:
            crop_width = min(src_w, _make_even(int(src_h * TARGET_ASPECT_RATIO)))
            crop_height = src_h
        else:
            crop_width = src_w
            crop_height = min(src_h, _make_even(int(src_w / TARGET_ASPECT_RATIO)))

        def _interpolate_cx(t: float) -> int:
            """Interpolate face_cx at time t using smoothed keyframes with ease-in-out."""
            if t <= kf_times[0]:
                return kf_cx_values[0]
            if t >= kf_times[-1]:
                return kf_cx_values[-1]
            # Find bracketing keyframes
            for i in range(len(kf_times) - 1):
                if kf_times[i] <= t <= kf_times[i + 1]:
                    span = kf_times[i + 1] - kf_times[i]
                    if span <= 0:
                        return kf_cx_values[i]
                    frac = (t - kf_times[i]) / span
                    # Smooth-step (ease in-out)
                    frac = frac * frac * (3.0 - 2.0 * frac)
                    return int(kf_cx_values[i] + (kf_cx_values[i + 1] - kf_cx_values[i]) * frac)
            return kf_cx_values[-1]

        clip_silent = clip.without_audio() if clip.audio is not None else clip

        def _make_pan_frame(get_frame, t):
            """Create a panned crop frame at time t."""
            frame = get_frame(t)
            cx = _interpolate_cx(t)
            x1 = max(0, min(src_w - crop_width, cx - crop_width // 2))
            y1 = max(0, (src_h - crop_height) // 2)
            x2 = x1 + crop_width
            y2 = y1 + crop_height
            cropped = frame[y1:y2, x1:x2]
            # Resize to output dimensions
            return _cv2.resize(cropped, (out_w, out_h), interpolation=_cv2.INTER_LANCZOS4)

        from moviepy import VideoClip

        pan_clip = VideoClip(
            lambda t: _make_pan_frame(clip_silent.get_frame, t),
            duration=duration,
        ).with_fps(clip.fps or 24)

        return pan_clip

    except Exception as exc:
        _warn_note(f"Smooth pan layout failed ({exc}), falling back to static crop.")
        return _build_static_speaker_crop(clip)


def _build_static_speaker_crop(clip: VideoFileClip) -> VideoFileClip:
    """Static face-centred crop for single speaker — used as fallback when
    smooth pan is not possible.
    """
    src_w, src_h = clip.size
    out_w, out_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
    source_ratio = src_w / src_h
    clip_silent = clip.without_audio() if clip.audio is not None else clip

    cache = _get_face_cache()
    face_cx = cache.face_cx if cache and cache.populated else _detect_face_center_x(clip)

    if source_ratio > TARGET_ASPECT_RATIO:
        crop_width = min(src_w, _make_even(int(src_h * TARGET_ASPECT_RATIO)))
        if face_cx is not None:
            x1 = max(0, min(src_w - crop_width, face_cx - crop_width // 2))
        else:
            x1 = max(0, (src_w - crop_width) // 2)
        x2 = min(src_w, x1 + crop_width)
        cropped_clip = clip_silent.cropped(x1=x1, y1=0, x2=x2, y2=src_h)
    else:
        crop_height = min(src_h, _make_even(int(src_w / TARGET_ASPECT_RATIO)))
        y1 = max(0, (src_h - crop_height) // 2)
        y2 = min(src_h, y1 + crop_height)
        cropped_clip = clip_silent.cropped(x1=0, y1=y1, x2=src_w, y2=y2)

    return cropped_clip.resized(new_size=(out_w, out_h))


# ─── Layout: Ken Burns effect for B-roll ─────────────────────────────

def _build_broll_ken_burns_clip(clip: VideoFileClip) -> VideoFileClip:
    """B-roll with subtle Ken Burns (slow zoom + pan) effect.

    Instead of a static fullframe, slowly zooms from 100% to ~105% while
    panning slightly, adding cinematic movement that makes B-roll clips
    feel more produced and professional.
    Falls back to fullframe on failure.
    """
    try:
        src_w, src_h = clip.size
        out_w, out_h = OUTPUT_WIDTH, OUTPUT_HEIGHT
        duration = clip.duration or 0.0
        if duration < 1.0:
            return _build_fullframe_vertical_clip(clip)

        clip_silent = clip.without_audio() if clip.audio is not None else clip

        # Determine how the source fits in 9:16
        fg_scale = out_w / src_w
        fg_h = int(src_h * fg_scale)
        if fg_h > out_h:
            fg_scale = out_h / src_h

        # Ken Burns parameters: zoom from 1.0x to 1.05x, pan from centre
        zoom_start = 1.00
        zoom_end = 1.08
        # Pan direction: slowly drift right and up
        pan_x_start = 0.50  # centre
        pan_x_end = 0.52    # slight right drift
        pan_y_start = 0.38  # slightly above centre
        pan_y_end = 0.35    # drift up

        background = _make_blur_background(clip_silent, src_w, src_h, out_w, out_h)

        def _kb_frame(get_frame, t):
            frame = get_frame(t)
            frac = t / duration if duration > 0 else 0
            # Ease in-out
            frac = frac * frac * (3.0 - 2.0 * frac)

            zoom = zoom_start + (zoom_end - zoom_start) * frac
            pan_x = pan_x_start + (pan_x_end - pan_x_start) * frac
            pan_y = pan_y_start + (pan_y_end - pan_y_start) * frac

            # Calculate crop from source at current zoom
            crop_w = int(src_w / zoom)
            crop_h = int(src_h / zoom)
            cx = int(src_w * pan_x)
            cy = int(src_h * pan_y)

            x1 = max(0, min(src_w - crop_w, cx - crop_w // 2))
            y1 = max(0, min(src_h - crop_h, cy - crop_h // 2))
            x2 = x1 + crop_w
            y2 = y1 + crop_h

            cropped = frame[y1:y2, x1:x2]
            # Scale to fit width
            scaled_w = out_w
            scaled_h = _make_even(int(crop_h * (out_w / max(1, crop_w))))
            if scaled_h > out_h:
                scaled_h = out_h
                scaled_w = _make_even(int(crop_w * (out_h / max(1, crop_h))))

            resized = _cv2.resize(cropped, (scaled_w, scaled_h), interpolation=_cv2.INTER_LANCZOS4)

            # Centre on output canvas
            canvas = _np.zeros((out_h, out_w, 3), dtype=_np.uint8)
            ox = max(0, (out_w - scaled_w) // 2)
            oy = max(0, int((out_h - scaled_h) * 0.35))
            canvas[oy:oy + scaled_h, ox:ox + scaled_w] = resized[:scaled_h, :scaled_w]
            return canvas

        from moviepy import VideoClip

        kb_clip = VideoClip(
            lambda t: _kb_frame(clip_silent.get_frame, t),
            duration=duration,
        ).with_fps(clip.fps or 24)

        # Composite Ken Burns foreground over blurred background
        layers = [background, kb_clip.with_position((0, 0))]
        return CompositeVideoClip(layers, size=(out_w, out_h)).with_duration(duration)

    except Exception as exc:
        _warn_note(f"Ken Burns B-roll layout failed ({exc}), falling back to fullframe.")
        return _build_fullframe_vertical_clip(clip)


# ─── Master composition router ───────────────────────────────────────

_LAYOUT_ROUTER: dict[str, "Callable[[VideoFileClip], VideoFileClip]"] = {
    _CONTENT_SINGLE_SPEAKER: _build_smooth_pan_speaker_clip,
    _CONTENT_SCREEN_SHARE: _build_fullframe_vertical_clip,
    _CONTENT_SCREEN_SHARE_WITH_CAM: _build_screenshare_with_cam_clip,
    _CONTENT_MEETING_GALLERY: _build_meeting_gallery_clip,
    _CONTENT_PODCAST_DUO: _build_podcast_duo_clip,
    _CONTENT_NEWS_BROADCAST: _build_news_broadcast_clip,
    _CONTENT_BROLL: _build_broll_ken_burns_clip,
    _CONTENT_MIXED: _build_mixed_content_clip,
}


# Minimum number of rated clips per type before adaptive logic kicks in
_ADAPTIVE_MIN_SAMPLES = 5
# Approval rate below which the classifier downgrades confidence
_ADAPTIVE_LOW_APPROVAL = 0.4


def _apply_adaptive_adjustment(content_type: str, meta: dict) -> tuple[str, dict]:
    """Adjust classification based on accumulated user feedback.

    If a content type consistently gets bad ratings (approval < 40% with at
    least 5 samples), reduce its confidence.  If confidence drops below 0.3,
    fall through to mixed/fullframe as a safer choice.

    This is a lightweight, non-destructive adjustment — the underlying
    classifier logic is unchanged.
    """
    try:
        from app.analytics import get_insights
        insights = get_insights()
    except Exception:
        return content_type, meta

    type_data = insights.get("perContentType", {}).get(content_type)
    if not type_data:
        return content_type, meta

    rated = type_data.get("rated", 0)
    if rated < _ADAPTIVE_MIN_SAMPLES:
        return content_type, meta

    approval = type_data.get("approvalRate")
    if approval is None or approval >= _ADAPTIVE_LOW_APPROVAL:
        return content_type, meta

    # Penalize confidence proportionally to how poor the approval is
    original_confidence = meta.get("confidence", 0.5)
    penalty = (1.0 - approval / _ADAPTIVE_LOW_APPROVAL) * 0.4  # max 0.4 penalty
    adjusted = round(max(0.1, original_confidence - penalty), 2)
    meta["confidence"] = adjusted
    meta["adaptive_penalty"] = round(penalty, 2)
    meta["adaptive_reason"] = f"{content_type} has {approval:.0%} approval across {rated} rated clips"

    # If confidence is now very low, fall through to mixed (safer layout)
    if adjusted < 0.3 and content_type not in (_CONTENT_MIXED, _CONTENT_SINGLE_SPEAKER):
        meta["adaptive_override"] = content_type
        _debug_note(f"Adaptive override: {content_type} -> mixed (approval {approval:.0%}, {rated} samples).")
        return _CONTENT_MIXED, meta

    return content_type, meta


def _ensure_output_size(clip: VideoFileClip, label: str = "") -> VideoFileClip:
    """Guarantee the clip is exactly OUTPUT_WIDTH × OUTPUT_HEIGHT.

    If a layout builder returns a clip with the wrong dimensions (e.g. due to a
    rounding error or an unexpected source size), this corrects it without
    distortion: scale-to-fit first, then pad/crop to exact canvas size.
    In practice this should rarely trigger — it is a last-resort safety net.
    """
    w, h = clip.size
    if w == OUTPUT_WIDTH and h == OUTPUT_HEIGHT:
        return clip

    tag = f" [{label}]" if label else ""
    _warn_note(f"Layout returned {w}x{h} instead of {OUTPUT_WIDTH}x{OUTPUT_HEIGHT}{tag}; correcting.")

    # Scale to fit preserving aspect ratio
    scale = min(OUTPUT_WIDTH / w, OUTPUT_HEIGHT / h)
    new_w = _make_even(int(w * scale))
    new_h = _make_even(int(h * scale))
    scaled = clip.resized(new_size=(new_w, new_h))

    if new_w == OUTPUT_WIDTH and new_h == OUTPUT_HEIGHT:
        return scaled

    # Pad with blurred background to reach exact canvas size
    try:
        bg = _make_blur_background(scaled, new_w, new_h, OUTPUT_WIDTH, OUTPUT_HEIGHT)
        x_off = max(0, (OUTPUT_WIDTH - new_w) // 2)
        y_off = max(0, int((OUTPUT_HEIGHT - new_h) * 0.35))
        return CompositeVideoClip(
            [bg, scaled.with_position((x_off, y_off))],
            size=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
        ).with_duration(clip.duration)
    except Exception:
        # Absolute fallback — stretch to exact size (better than crashing)
        return clip.resized(new_size=(OUTPUT_WIDTH, OUTPUT_HEIGHT))


def build_vertical_master_clip(clip: VideoFileClip, audio_speaker_meta: dict | None = None) -> tuple[VideoFileClip, str, dict]:
    """Build a 9:16 vertical clip using adaptive composition.

    Classifies the visual content type, populates a face cache, then delegates
    to a specialised layout builder.  Returns ``(vertical_clip, content_type, analytics_meta)``.

    ``analytics_meta`` contains the full classifier signals (confidence, face
    stats, edge/text density, motion, etc.) and a ``layout_fallback`` flag.

    The face cache is cleared after the layout is built, ensuring no stale data
    leaks between clips.  Wrapped in a master safety net — if *anything* fails,
    falls back to a simple centre-crop so no job ever crashes.
    """
    width, height = clip.size

    try:
        source_ratio = width / height

        if abs(source_ratio - TARGET_ASPECT_RATIO) < 0.001:
            return clip.resized(new_size=(OUTPUT_WIDTH, OUTPUT_HEIGHT)), _CONTENT_SINGLE_SPEAKER, {"confidence": 1.0, "layout_fallback": False, "already_vertical": True}

        # Classify — this populates the thread-local face cache
        content_type, meta = _classify_content_type(clip)

        # Apply adaptive confidence adjustment from historical feedback
        content_type, meta = _apply_adaptive_adjustment(content_type, meta)

        label = _CONTENT_LABELS.get(content_type, content_type)
        confidence = meta.get("confidence", 0)
        _debug_note(
            f"Visual content type: {label} (confidence={confidence}). "
            f"Signals: faces={meta.get('avg_faces',0):.1f} edge={meta.get('avg_edge',0):.3f} "
            f"text={meta.get('avg_text',0):.3f} motion={meta.get('avg_motion',0):.4f} "
            f"clusters={meta.get('cx_clusters',0)} scene_transitions={meta.get('scene_change_transitions',0)}."
        )

        meta = _augment_meta_with_speaker_data(meta, content_type, clip)
        content_type, meta = _refine_content_type_with_speaker_data(content_type, meta, audio_speaker_meta)
        meta["layout_fallback"] = False

        # Route to specialised layout
        layout_fn = _LAYOUT_ROUTER.get(content_type)
        if layout_fn is not None:
            built = _ensure_output_size(layout_fn(clip), label=content_type)
            _clear_face_cache()
            return built, content_type, meta

        # Fallback — static crop (should not be reached since all types are in router)
        meta["layout_fallback"] = True
        built = _ensure_output_size(_build_static_speaker_crop(clip), label="static_fallback")
        _clear_face_cache()
        return built, content_type, meta

    except Exception as exc:
        _clear_face_cache()
        _warn_note(f"Master composition failed ({exc}), using safe centre-crop fallback.")
        fallback_meta = {"confidence": 0.0, "layout_fallback": True, "error": str(exc)}
        try:
            source_ratio = width / height
            if source_ratio > TARGET_ASPECT_RATIO:
                crop_width = min(width, _make_even(int(height * TARGET_ASPECT_RATIO)))
                x1 = max(0, (width - crop_width) // 2)
                x2 = min(width, x1 + crop_width)
                fallback = clip.cropped(x1=x1, y1=0, x2=x2, y2=height)
            else:
                crop_height = min(height, _make_even(int(width / TARGET_ASPECT_RATIO)))
                y1 = max(0, (height - crop_height) // 2)
                y2 = min(height, y1 + crop_height)
                fallback = clip.cropped(x1=0, y1=y1, x2=width, y2=y2)
            return fallback.resized(new_size=(OUTPUT_WIDTH, OUTPUT_HEIGHT)), _CONTENT_MIXED, fallback_meta
        except Exception:
            return clip.resized(new_size=(OUTPUT_WIDTH, OUTPUT_HEIGHT)), _CONTENT_MIXED, fallback_meta


def ensure_dependencies() -> None:
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg is not installed or not available in PATH. On Windows, install it with 'winget install Gyan.FFmpeg'."
    )


def _run_storage_maintenance_if_due() -> None:
    global _LAST_STORAGE_PRUNE_AT
    now = time.time()
    if now - _LAST_STORAGE_PRUNE_AT < 900:
        return
    try:
        prune_runtime_storage(dry_run=False)
        _LAST_STORAGE_PRUNE_AT = now
    except Exception:
        logger.warning("Background storage maintenance failed.", exc_info=True)


def _render_selected_clips(
    *,
    video_path: Path,
    workspace: RenderWorkspace,
    clip_candidates: list[dict],
    transcript_segments: list[dict],
    output_filename: str,
    subtitle_style: dict,
    render_settings: dict[str, str],
    progress_callback: ProgressCallback | None = None,
    observer: RunObserver | None = None,
) -> list[dict]:
    clips_output: list[dict] = []

    for index, clip_data in enumerate(clip_candidates, start=1):
        start = clip_data["start"]
        end = clip_data["end"]
        current_filename = build_clip_filename(output_filename, index, len(clip_candidates))
        temp_output_path = workspace.clips_dir / current_filename
        clip_metrics: dict[str, int | float | bool] = {
            "sourceStartSeconds": start,
            "sourceEndSeconds": end,
            "durationSeconds": round(max(0.0, end - start), 2),
        }
        clip_source = None
        clip = None
        clip_vertical = None
        clip_final = None
        audio_temp_path: Path | None = None
        subtitle_plan_path = None
        subtitle_preflight_path = None

        try:
            observer.snapshot(f"clip_{index}_start", workspace_dir=workspace.workspace_dir) if observer is not None else None
            _emit_labeled(
                progress_callback,
                "rendering",
                "CLIP_RENDER",
                f"Rendering clip {index} of {len(clip_candidates)} with a fresh source handle to reduce memory pressure...",
                observer=observer,
                clipIndex=index,
                clipStart=start,
                clipEnd=end,
            )
            clip_started_at = time.time()
            clip_source = VideoFileClip(str(video_path))
            clip = clip_source.subclipped(start, end)

            if clip.audio is not None:
                audio_started_at = time.time()
                _emit_labeled(
                    progress_callback,
                    "rendering",
                    "AUDIO",
                    f"Extracting audio segment for clip {index}...",
                    observer=observer,
                    clipIndex=index,
                )
                audio_temp_path = extract_audio_segment(video_path, start, end, workspace.audio_dir)
                clip_metrics["audioExtractSeconds"] = round(time.time() - audio_started_at, 2)

            _emit_labeled(
                progress_callback,
                "rendering",
                "SUBTITLES",
                f"Preparing subtitle timing for clip {index}...",
                observer=observer,
                clipIndex=index,
            )
            transcript_started_at = time.time()
            clip_transcript, requires_precise_fallback = extract_clip_transcript_from_segments(transcript_segments, start, end)
            if requires_precise_fallback:
                _emit_labeled(
                    progress_callback,
                    "rendering",
                    "TRANSCRIPT",
                    f"Refining subtitle timing for clip {index} from the pre-extracted audio track...",
                    observer=observer,
                    clipIndex=index,
                )
                fallback_started_at = time.time()
                clip_transcript = transcribe_audio_path_for_subtitles(audio_temp_path)
                clip_metrics["subtitleFallbackSeconds"] = round(time.time() - fallback_started_at, 2)
            clip_metrics["subtitlePrepSeconds"] = round(time.time() - transcript_started_at, 2)

            speaker_started_at = time.time()
            audio_speaker_meta = analyze_audio_speakers(audio_temp_path, clip_transcript)
            clip_metrics["speakerAnalysisSeconds"] = round(time.time() - speaker_started_at, 2)
            _emit_labeled(
                progress_callback,
                "rendering",
                "AUDIO",
                f"Speaker analysis for clip {index}: {speaker_analysis_backend_label(audio_speaker_meta)} "
                f"({audio_speaker_meta.get('audioSpeakerCount', 0)} speaker estimate).",
                observer=observer,
                clipIndex=index,
            )

            _emit_labeled(
                progress_callback,
                "rendering",
                "CLIP_RENDER",
                f"Analysing visual content for clip {index}...",
                observer=observer,
                clipIndex=index,
            )
            layout_started_at = time.time()
            clip_vertical, content_type, clip_analytics = build_vertical_master_clip(clip, audio_speaker_meta=audio_speaker_meta)
            clip_metrics["layoutBuildSeconds"] = round(time.time() - layout_started_at, 2)
            clip_analytics.update(audio_speaker_meta)
            ct_label = _CONTENT_LABELS.get(content_type, content_type)
            _emit_labeled(
                progress_callback,
                "rendering",
                "CLIP_RENDER",
                f"Clip {index} detected as {ct_label} — composing vertical frame...",
                observer=observer,
                clipIndex=index,
                contentType=content_type,
            )

            subtitle_runtime_started_at = time.time()
            subtitle_runtime = subtitles.prepare_subtitle_runtime(
                clip_vertical,
                clip_transcript.get("segments") or [],
                0,
                subtitle_style,
            )
            clip_metrics["subtitleRuntimePrepSeconds"] = round(time.time() - subtitle_runtime_started_at, 2)
            subtitle_plan = subtitle_runtime["subtitleCues"]
            subtitle_preflight_started_at = time.time()
            subtitle_preflight = subtitles.validate_prepared_subtitle_runtime(subtitle_runtime)
            clip_metrics["subtitlePreflightSeconds"] = round(time.time() - subtitle_preflight_started_at, 2)
            if KEEP_RENDER_DIAGNOSTICS:
                subtitle_plan_path = workspace.diagnostics_dir / f"clip_{index:02d}_subtitles.json"
                subtitle_plan_path.write_text(
                    json.dumps(subtitles.export_subtitle_plan(subtitle_plan), ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
                subtitle_preflight_path = workspace.diagnostics_dir / f"clip_{index:02d}_subtitle_preflight.json"
                subtitle_preflight_path.write_text(
                    json.dumps(subtitle_preflight, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
            _emit_labeled(
                progress_callback,
                "rendering",
                "SUBTITLES",
                f"Subtitle preflight passed for clip {index}.",
                observer=observer,
                clipIndex=index,
                subtitleCues=len(subtitle_plan),
            )

            subtitle_compose_started_at = time.time()
            clip_final = subtitles.create_subtitles(
                clip_vertical,
                clip_transcript.get("segments") or [],
                0,
                subtitle_style,
                clip_title=clip_data.get("title"),
                clip_reason=clip_data.get("reason"),
                prepared_runtime=subtitle_runtime,
            )
            clip_metrics["subtitleComposeSeconds"] = round(time.time() - subtitle_compose_started_at, 2)
            encode_metrics = write_high_quality_video(
                clip_final,
                temp_output_path,
                audio_path=audio_temp_path,
                render_settings=render_settings,
                render_threads=RENDER_THREADS,
            )
            clip_metrics.update(encode_metrics)
            clip_metrics["totalClipSeconds"] = round(time.time() - clip_started_at, 2)
            output_bytes = int(clip_metrics.get("outputBytes") or 0)
            clip_payload = {
                "index": index,
                "title": clip_data.get("title"),
                "reason": clip_data.get("reason"),
                "start": start,
                "end": end,
                "contentType": content_type,
                "analytics": clip_analytics,
                "outputFilename": current_filename,
                "subtitlePlanFilename": subtitle_plan_path.name if subtitle_plan_path else None,
                "subtitlePreflightFilename": subtitle_preflight_path.name if subtitle_preflight_path else None,
                "subtitleCueCount": len(subtitle_plan),
                "subtitlePreflightWarnings": 0,
                "renderMetrics": clip_metrics,
            }
            if observer is not None:
                observer.record_clip(clip_payload)
                observer.snapshot(f"clip_{index}_complete", workspace_dir=workspace.workspace_dir)
            _emit_labeled(
                progress_callback,
                "rendering",
                "SUMMARY",
                f"Clip {index} profile: layout {clip_metrics.get('layoutBuildSeconds', 0)}s, "
                f"subtitles {clip_metrics.get('subtitleComposeSeconds', 0)}s, "
                f"encode {clip_metrics.get('videoEncodeSeconds', 0)}s, "
                f"mux {clip_metrics.get('audioMuxSeconds', 0)}s, "
                f"output {round(output_bytes / (1024 * 1024), 1)} MB.",
                observer=observer,
                clipIndex=index,
                totalClipSeconds=clip_metrics.get("totalClipSeconds"),
                outputBytes=output_bytes,
            )
            clips_output.append(clip_payload)
        except Exception as clip_error:
            logger.exception("Clip %d/%d failed, continuing with remaining clips.", index, len(clip_candidates))
            _emit_labeled(
                progress_callback,
                "rendering",
                "CLIP_RENDER",
                f"Clip {index} failed ({clip_error}). Continuing with remaining clips...",
                observer=observer,
                clipIndex=index,
            )
        finally:
            if clip_final is not None:
                clip_final.close()
            if clip_vertical is not None:
                clip_vertical.close()
            if clip is not None:
                clip.close()
            if clip_source is not None:
                clip_source.close()
            if audio_temp_path is not None and audio_temp_path.exists():
                audio_temp_path.unlink(missing_ok=True)
            gc.collect()

    if not clips_output:
        raise RuntimeError("All clips failed to render. Check the logs folder for details.")

    return clips_output


def _materialize_final_clips(workspace: RenderWorkspace, clips_output: list[dict]) -> list[dict]:
    return [
        {
            **clip,
            "outputPath": str(workspace.clips_dir / clip["outputFilename"]),
            "subtitlePlanPath": str(workspace.diagnostics_dir / clip["subtitlePlanFilename"]) if clip.get("subtitlePlanFilename") else None,
            "subtitlePreflightPath": str(workspace.diagnostics_dir / clip["subtitlePreflightFilename"]) if clip.get("subtitlePreflightFilename") else None,
        }
        for clip in clips_output
    ]


def create_short_from_url(
    video_url: str,
    api_key: str,
    output_filename: str = "short_con_subs.mp4",
    base_dir: str | Path = OUTPUT_JOBS_DIR,
    job_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    subtitle_style: dict | None = None,
    clip_count: int = DEFAULT_CLIP_COUNT,
    render_profile: str = DEFAULT_RENDER_PROFILE,
) -> dict:
    video_url = validate_video_url(video_url)
    output_filename = sanitize_output_filename(output_filename)
    subtitle_style = normalize_requested_subtitle_style(subtitle_style)
    render_profile = normalize_requested_render_profile(render_profile)
    render_settings = _get_render_profile_settings(render_profile)
    clip_count = max(1, min(5, int(clip_count)))
    job_id = job_id or uuid.uuid4().hex[:10]
    pipeline_fingerprint = job_fingerprint(
        video_url=video_url,
        output_filename=output_filename,
        clip_count=clip_count,
        render_profile=render_profile,
        subtitle_style=subtitle_style,
    )
    observer = RunObserver(
        job_id=job_id,
        fingerprint=pipeline_fingerprint,
        video_url=video_url,
        output_filename=output_filename,
        render_profile=render_profile,
    )
    observer.log(
        "SUMMARY",
        "Run started.",
        clipCount=clip_count,
        renderProfile=render_profile,
        outputFilename=output_filename,
    )
    observer.snapshot("run_started")

    def observed_progress(stage: str, message: str) -> None:
        phase, detail = (message.split(" | ", 1) + [""])[:2] if " | " in message else (stage.upper(), message)
        observer.log(phase, detail)
        _emit(progress_callback, stage, message)

    _run_storage_maintenance_if_due()

    output_dir = Path(base_dir) / pipeline_fingerprint
    if REUSE_COMPLETED_RENDERS:
        existing_result = load_existing_result(output_dir, pipeline_fingerprint)
        if existing_result is not None:
            observer.mark_cache("finalResult", True)
            observer.log("CACHE", "Existing clips already match this request. Reusing previous render output.", outputDir=str(output_dir))
            _emit_labeled(
                progress_callback,
                "completed",
                "CACHE",
                "Existing clips already match this request. Reusing previous render output.",
                observer=observer,
                outputDir=str(output_dir),
            )
            existing_result["jobId"] = job_id
            existing_result["reusedExisting"] = True
            existing_result["jobFingerprint"] = pipeline_fingerprint
            existing_result["outputDir"] = str(output_dir)
            return existing_result

    with acquire_fingerprint_lock(pipeline_fingerprint, job_id=job_id, progress_callback=progress_callback):
        if REUSE_COMPLETED_RENDERS:
            existing_result = load_existing_result(output_dir, pipeline_fingerprint)
            if existing_result is not None:
                observer.mark_cache("finalResult", True)
                observer.log("CACHE", "Another identical render already finished. Reusing its completed output.", outputDir=str(output_dir))
                _emit_labeled(
                    progress_callback,
                    "completed",
                    "CACHE",
                    "Another identical render already finished. Reusing its completed output.",
                    observer=observer,
                    outputDir=str(output_dir),
                )
                existing_result["jobId"] = job_id
                existing_result["reusedExisting"] = True
                existing_result["jobFingerprint"] = pipeline_fingerprint
                existing_result["outputDir"] = str(output_dir)
                return existing_result

        ensure_dependencies()
        _emit_labeled(
            progress_callback,
            "validating",
            "SUBTITLES",
            "Checking subtitle rendering compatibility...",
            observer=observer,
        )
        subtitles.assert_subtitle_rendering_ready(subtitle_style)

        workspace = RenderWorkspace.create(fingerprint=pipeline_fingerprint, job_id=job_id, base_dir=base_dir)
        _emit_labeled(
            progress_callback,
            "validating",
            "CACHE",
            f"Using isolated temporary workspace: {workspace.workspace_dir}",
            observer=observer,
            workspaceDir=str(workspace.workspace_dir),
        )
        observer.snapshot("workspace_created", workspace_dir=workspace.workspace_dir)
        transcript_path = workspace.meta_dir / "full_transcript.txt"
        source_meta_path = workspace.meta_dir / "source_download.json"
        temp_base = workspace.source_dir / "source_video"
        phase_metrics = {
            "workspaceTempDir": str(workspace.workspace_dir),
            "outputDir": str(workspace.final_output_dir),
            "renderMode": "generate",
        }
        total_started_at = time.time()
        video_path = None
        download_info: dict = {}
        remove_source_video_at_end = False
        render_completed = False
        workspace_cleanup_attempted = False

        try:
            started_at = time.time()
            video_path, download_info, remove_source_video_at_end, source_cache_hit = resolve_source_video(video_url, temp_base, observed_progress)
            phase_metrics["downloadSeconds"] = round(time.time() - started_at, 2)
            observer.mark_cache("sourceVideo", source_cache_hit)
            observer.record_phase("source", phase_metrics["downloadSeconds"], cache=observer.cache["sourceVideo"])
            source_meta_path.write_text(json.dumps(download_info, ensure_ascii=True, indent=2), encoding="utf-8")
            observer.snapshot("after_source", workspace_dir=workspace.workspace_dir)

            started_at = time.time()
            transcript_result, transcript_cache_hit = resolve_transcript(video_url, video_path, observed_progress)
            phase_metrics["transcriptionSeconds"] = round(time.time() - started_at, 2)
            observer.mark_cache("transcript", transcript_cache_hit)
            observer.record_phase("transcript", phase_metrics["transcriptionSeconds"], cache=observer.cache["transcript"])
            transcript_segments = transcript_result.get("segments") or []
            transcript_path.write_text(f"URL: {video_url}\n{transcript_result.get('text') or ''}", encoding="utf-8")
            observer.snapshot("after_transcript", workspace_dir=workspace.workspace_dir)
            del transcript_result
            gc.collect()

            started_at = time.time()
            clip_candidates, clip_selection_cache_hit = select_clip_candidates(
                video_url=video_url,
                transcript_segments=transcript_segments,
                api_key=api_key,
                clip_count=clip_count,
                progress_callback=observed_progress,
            )
            phase_metrics["analysisSeconds"] = round(time.time() - started_at, 2)
            observer.mark_cache("clipSelection", clip_selection_cache_hit)
            observer.record_phase("clipSelection", phase_metrics["analysisSeconds"], cache=observer.cache["clipSelection"])
            observer.snapshot("after_clip_selection", workspace_dir=workspace.workspace_dir)

            video_duration = probe_video_duration(video_path)
            phase_metrics["sourceDurationSeconds"] = round(video_duration, 2)
            observer.log("SOURCE", "Source duration probed.", durationSeconds=phase_metrics["sourceDurationSeconds"])
            emit_workload_warning(video_duration, clip_count, observed_progress)
            free_bytes = ensure_disk_headroom(
                workspace.root_dir,
                video_path=video_path,
                clip_count=clip_count,
                video_duration=video_duration,
            )
            phase_metrics["freeDiskBytesBeforeRender"] = free_bytes
            observer.log("CACHE", "Disk headroom validated before render.", freeDiskBytes=free_bytes)

            clip_candidates = validate_clip_candidates(clip_candidates, video_duration, warn_callback=_warn_note)
            if not clip_candidates:
                raise ValueError("No valid clips remain after timestamp validation against video duration.")
            observer.log("CLIP_SELECTION", "Clip candidates validated against source duration.", clipCount=len(clip_candidates))

            started_at = time.time()
            temp_clips_output = _render_selected_clips(
                video_path=video_path,
                workspace=workspace,
                clip_candidates=clip_candidates,
                transcript_segments=transcript_segments,
                output_filename=output_filename,
                subtitle_style=subtitle_style,
                render_settings=render_settings,
                progress_callback=progress_callback,
                observer=observer,
            )
            phase_metrics["renderSeconds"] = round(time.time() - started_at, 2)
            observer.record_phase("render", phase_metrics["renderSeconds"], clipCount=len(temp_clips_output))
            phase_metrics["renderedClipCount"] = len(temp_clips_output)
            phase_metrics["workspaceBytesBeforePromotion"] = path_size(workspace.workspace_dir)
            observer.snapshot("before_promotion", workspace_dir=workspace.workspace_dir)
            del transcript_segments
            gc.collect()

            _emit_labeled(
                progress_callback,
                "rendering",
                "PROMOTION",
                "Promoting finished artifacts into the final output folder...",
                observer=observer,
                workspaceBytes=phase_metrics["workspaceBytesBeforePromotion"],
            )
            workspace.promote()
            observer.snapshot("after_promotion", final_output_dir=workspace.final_output_dir)

            # Auto-delete the per-job source copy now that the render is done.
            # The shared cache (outputs/cache/{hash}/source.*) is kept intact for
            # re-runs.  The original flag only covered the old temp path which no
            # longer exists after promotion; we use workspace.source_dir instead.
            _auto_source_deleted = False
            _auto_source_deleted_at: float | None = None
            _auto_delete_enabled = os.getenv("AUTO_DELETE_SOURCE_MEDIA", "1").strip() == "1"
            if remove_source_video_at_end and _auto_delete_enabled and workspace.source_dir.exists():
                try:
                    shutil.rmtree(workspace.source_dir, ignore_errors=True)
                    _auto_source_deleted = True
                    _auto_source_deleted_at = time.time()
                    logger.info("Auto-deleted source media from %s after render", workspace.source_dir)
                except Exception as _sdel_err:
                    logger.warning("Auto-delete of source media failed: %s", _sdel_err)

            clips_output = _materialize_final_clips(workspace, temp_clips_output)

            _emit_labeled(
                progress_callback,
                "completed",
                "SUMMARY",
                f"{len(clips_output)} high-quality clips are ready to download.",
                observer=observer,
                clipCount=len(clips_output),
            )
            first_clip = clips_output[0]
            now = time.time()
            phase_metrics["totalSeconds"] = round(now - total_started_at, 2)
            phase_metrics["promotedFromTemp"] = True
            phase_metrics["finalOutputBytes"] = path_size(workspace.final_output_dir)
            observer.record_phase("promotion", 0.0, outputDir=str(workspace.final_output_dir))
            _emit_labeled(
                progress_callback,
                "completed",
                "SUMMARY",
                f"Profile summary: download {phase_metrics.get('downloadSeconds', 0)}s, "
                f"transcription {phase_metrics.get('transcriptionSeconds', 0)}s, "
                f"analysis {phase_metrics.get('analysisSeconds', 0)}s, "
                f"render {phase_metrics.get('renderSeconds', 0)}s, "
                f"final size {round(int(phase_metrics.get('finalOutputBytes', 0)) / (1024 * 1024), 1)} MB.",
                observer=observer,
                metrics=phase_metrics,
            )
            result_payload = {
                "jobId": job_id,
                "jobFingerprint": pipeline_fingerprint,
                "videoUrl": video_url,
                "title": first_clip.get("title"),
                "reason": first_clip.get("reason"),
                "sourceMediaPresent": not _auto_source_deleted,
                "sourceMediaDeletedAt": _auto_source_deleted_at,
                "start": first_clip["start"],
                "end": first_clip["end"],
                "outputFilename": first_clip["outputFilename"],
                "subtitleStyle": subtitle_style,
                "outputPath": first_clip["outputPath"],
                "transcriptPath": str(workspace.meta_dir / "full_transcript.txt"),
                "sourceMetaPath": str(workspace.meta_dir / "source_download.json"),
                "sourceDownload": download_info,
                "outputDir": str(workspace.final_output_dir),
                "renderProfile": render_settings["label"],
                "renderProfileKey": render_profile,
                "clips": clips_output,
                "clipCount": len(clips_output),
                "generatedAt": now,
                "lastUsedAt": now,
                "reusedExisting": False,
                "metrics": phase_metrics,
            }
            observer.build_summary(status="completed", result_payload=result_payload, cleanup_ok=True, promotion_ok=True)
            _emit_labeled(
                progress_callback,
                "completed",
                "SUMMARY",
                f"Run summary: {observer.summary.get('totalJobSeconds')}s total, "
                f"{observer.summary.get('generatedClipCount')} generated, "
                f"{observer.summary.get('reusedClipCount')} reused, "
                f"peak workspace {round(int(observer.summary.get('peakWorkspaceBytes') or 0) / (1024 * 1024), 1)} MB.",
                observer=observer,
                summary=observer.summary,
            )
            run_report_path = observer.write_success_report(workspace.final_output_dir, result_payload)
            result_payload["runReportPath"] = str(run_report_path)
            result_payload["runSummary"] = observer.summary
            result_payload["logPath"] = str(_LOG_PATH)
            write_result_manifest(workspace.final_output_dir, result_payload)
            render_completed = True
            return result_payload
        except Exception as error:
            observer.log("SUMMARY", "Run failed.", error=str(error))
            observer.snapshot("failed", workspace_dir=workspace.workspace_dir if workspace.workspace_dir.exists() else None)
            cleanup_ok = None
            if not render_completed:
                try:
                    workspace.cleanup()
                    cleanup_ok = True
                except Exception as cleanup_error:
                    cleanup_ok = False
                    logger.warning("Workspace cleanup failed after job error.", exc_info=True)
                    observer.log("CLEANUP", "Temporary workspace cleanup failed after render error.", error=str(cleanup_error))
                workspace_cleanup_attempted = True
            failure_report_path = observer.write_failure_report(str(error), cleanup_ok=cleanup_ok)
            _emit_labeled(
                progress_callback,
                "failed",
                "SUMMARY",
                f"Failure report written to {failure_report_path}",
                observer=observer,
                failureReportPath=str(failure_report_path),
            )
            raise
        finally:
            if remove_source_video_at_end and video_path and os.path.exists(video_path):
                os.remove(video_path)
            if not render_completed and not workspace_cleanup_attempted:
                workspace.cleanup()


def build_clip_filename(output_filename: str, clip_index: int, total_clips: int) -> str:
    if total_clips <= 1:
        return output_filename

    path = Path(output_filename)
    stem = path.stem or "short_con_subs"
    suffix = path.suffix or ".mp4"
    return f"{stem}_{clip_index:02d}{suffix}"
