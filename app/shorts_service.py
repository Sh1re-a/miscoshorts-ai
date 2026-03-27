from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import yt_dlp
from moviepy import CompositeVideoClip, ImageClip, VideoFileClip

try:
    from faster_whisper import WhisperModel as _FasterWhisperModel
    _FASTER_WHISPER_AVAILABLE = True
except Exception:
    _FasterWhisperModel = None
    _FASTER_WHISPER_AVAILABLE = False

try:
    import whisper
    _OPENAI_WHISPER_AVAILABLE = True
except Exception:
    whisper = None
    _OPENAI_WHISPER_AVAILABLE = False

try:
    import cv2 as _cv2
    import numpy as _np
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

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

from app import gemini_analyzer, subtitles
from app.paths import OUTPUTS_DIR, OUTPUT_CACHE_DIR, OUTPUT_JOBS_DIR


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
YTDLP_MAX_HEIGHT = max(1080, int(os.getenv("YTDLP_MAX_HEIGHT", "4320")))
DOWNLOAD_FORMAT = os.getenv(
    "YTDLP_FORMAT",
    f"bestvideo*[height<={YTDLP_MAX_HEIGHT}]+bestaudio/best[height<={YTDLP_MAX_HEIGHT}]/best",
)
DOWNLOAD_FORMAT_SORT = [
    field.strip()
    for field in os.getenv("YTDLP_FORMAT_SORT", "res,fps,hdr:12,vcodec:h264,acodec:aac").split(",")
    if field.strip()
]
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "distil-large-v3,large-v3,turbo,base")
WHISPER_BACKEND = os.getenv("WHISPER_BACKEND", "auto").strip().lower() or "auto"
RENDER_THREADS = max(4, min(12, os.cpu_count() or 4))
DEFAULT_CLIP_COUNT = 3
DEFAULT_RENDER_PROFILE = os.getenv("DEFAULT_RENDER_PROFILE", "studio").strip().lower() or "studio"
CACHE_ENABLED = os.getenv("LOCAL_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
SPEAKER_DIARIZATION_MODE = os.getenv("SPEAKER_DIARIZATION_MODE", "auto").strip().lower() or "auto"
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
GEMINI_FIELD_PATTERN = re.compile(r"^(TITLE|START|END|REASON)\s*:\s*(.+?)\s*$", re.IGNORECASE)
GEMINI_CLIP_PATTERN = re.compile(r"^CLIP\s+\d+\s*:?\s*$", re.IGNORECASE)

_whisper_model_cache: dict[str, object] = {}
_whisper_model_lock = threading.Lock()
_pyannote_pipeline_cache: object | None = None
_pyannote_pipeline_lock = threading.Lock()


def _emit(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback is not None:
        callback(stage, message)


def normalize_requested_render_profile(render_profile: str | None) -> str:
    normalized = (render_profile or DEFAULT_RENDER_PROFILE).strip().lower()
    if normalized not in RENDER_PROFILES:
        raise ValueError(f"renderProfile must be one of: {', '.join(sorted(RENDER_PROFILES))}")
    return normalized


def _get_render_profile_settings(render_profile: str) -> dict[str, str]:
    return dict(RENDER_PROFILES[normalize_requested_render_profile(render_profile)])


def _get_speaker_diarization_token() -> str:
    return (
        os.getenv("PYANNOTE_AUTH_TOKEN")
        or os.getenv("HUGGINGFACE_ACCESS_TOKEN")
        or os.getenv("HF_TOKEN")
        or ""
    ).strip()


def _should_use_pyannote() -> bool:
    if SPEAKER_DIARIZATION_MODE == "heuristic":
        return False
    if SPEAKER_DIARIZATION_MODE == "pyannote":
        return True
    return bool(_get_speaker_diarization_token())


def _load_pyannote_pipeline() -> object | None:
    global _pyannote_pipeline_cache
    if not _should_use_pyannote():
        return None

    with _pyannote_pipeline_lock:
        if _pyannote_pipeline_cache is not None:
            return _pyannote_pipeline_cache

        token = _get_speaker_diarization_token()
        if not token:
            return None

        try:
            import torch
            from pyannote.audio import Pipeline
        except Exception:
            return None

        try:
            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-community-1",
                token=token,
            )
            if hasattr(pipeline, "to"):
                device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                pipeline.to(device)
            _pyannote_pipeline_cache = pipeline
            return pipeline
        except Exception:
            return None


def _speaker_analysis_backend_label(audio_meta: dict) -> str:
    provider = str(audio_meta.get("audioSpeakerProvider") or "none")
    if provider == "pyannote":
        return "Pyannote diarization"
    if provider == "heuristic":
        return "Local heuristic diarization"
    return "Speaker analysis unavailable"


def _make_even(value: float) -> int:
    return max(2, int(round(value / 2) * 2))


def _video_cache_key(video_url: str) -> str:
    return hashlib.sha1(video_url.encode("utf-8")).hexdigest()[:16]


def _cache_dir_for_url(video_url: str) -> Path:
    return OUTPUT_CACHE_DIR / _video_cache_key(video_url)


def _find_cached_video(video_url: str) -> Path | None:
    if not CACHE_ENABLED:
        return None

    cache_dir = _cache_dir_for_url(video_url)
    matches = sorted(cache_dir.glob("source.*"))
    for match in matches:
        if match.is_file():
            return match
    return None


def _restore_cached_video(video_url: str, destination_base: Path) -> Path | None:
    cached_video = _find_cached_video(video_url)
    if cached_video is None:
        return None

    destination_path = destination_base.with_suffix(cached_video.suffix)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached_video, destination_path)
    return destination_path


def _store_cached_video(video_url: str, video_path: Path) -> None:
    if not CACHE_ENABLED or not video_path.exists():
        return

    cache_dir = _cache_dir_for_url(video_url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for existing in cache_dir.glob("source.*"):
        existing.unlink(missing_ok=True)
    shutil.copy2(video_path, cache_dir / f"source{video_path.suffix.lower()}")


def _load_cached_transcript(video_url: str) -> dict | None:
    if not CACHE_ENABLED:
        return None

    transcript_path = _cache_dir_for_url(video_url) / "transcript.json"
    if not transcript_path.exists():
        return None

    try:
        return json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _store_cached_transcript(video_url: str, transcript: dict) -> None:
    if not CACHE_ENABLED:
        return

    cache_dir = _cache_dir_for_url(video_url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _resolve_downloaded_video_path(destination_base: Path) -> Path:
    preferred_extensions = (".mp4", ".mkv", ".mov", ".webm")
    for extension in preferred_extensions:
        candidate = destination_base.with_suffix(extension)
        if candidate.exists():
            return candidate

    matches = sorted(destination_base.parent.glob(f"{destination_base.name}.*"))
    if matches:
        return matches[0]

    raise FileNotFoundError("yt-dlp completed without producing a video file.")


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

    if not parsed.path or parsed.path == "/":
        raise ValueError("The YouTube URL appears to be incomplete.")

    return normalized_url


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


def get_render_fps(clip: VideoFileClip) -> int:
    return max(24, round(clip.fps or 24))


def _load_cascades() -> "tuple[_cv2.CascadeClassifier, _cv2.CascadeClassifier]":
    """Lazy-load Haar cascades into module-level singletons (once per process)."""
    global _FRONTAL_CASCADE, _PROFILE_CASCADE
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
    if not _CV2_AVAILABLE:
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
    if not _CV2_AVAILABLE:
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
    if not _CV2_AVAILABLE:
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
        print(f"  ⚠️  Podcast duo layout failed ({exc}), falling back to fullframe")
        return _build_fullframe_vertical_clip(clip)


def _detect_duo_face_positions(clip: VideoFileClip) -> tuple[int, int] | None:
    """Find the x centres of two face clusters (left speaker, right speaker).
    Uses face cache if available to avoid redundant detection.
    """
    cache = _get_face_cache()
    if cache is not None and cache.populated and cache.duo_positions is not None:
        return cache.duo_positions

    if not _CV2_AVAILABLE:
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
        print(f"  ⚠️  Meeting gallery layout failed ({exc}), falling back to fullframe")
        return _build_fullframe_vertical_clip(clip)


def _detect_face_bbox(clip: VideoFileClip) -> tuple[int, int, int, int] | None:
    """Return the bounding box (x1, y1, x2, y2) covering all detected faces.
    Uses face cache if available.
    """
    cache = _get_face_cache()
    if cache is not None and cache.populated and cache.face_bbox is not None:
        return cache.face_bbox

    if not _CV2_AVAILABLE:
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
        print(f"  ⚠️  News broadcast layout failed ({exc}), falling back to fullframe")
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
        print(f"  ⚠️  Smooth pan layout failed ({exc}), falling back to static crop")
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
        print(f"  ⚠️  Ken Burns B-roll layout failed ({exc}), falling back to fullframe")
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
        print(f"  🔄 Adaptive override: {content_type} → mixed (approval {approval:.0%}, {rated} samples)")
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
    print(f"  ⚠️  Layout returned {w}×{h} instead of {OUTPUT_WIDTH}×{OUTPUT_HEIGHT}{tag} — correcting...")

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
        print(f"  📐 Visual content type: {label} (confidence={confidence})")
        print(f"     Signals: faces={meta.get('avg_faces',0):.1f}  edge={meta.get('avg_edge',0):.3f}  "
              f"text={meta.get('avg_text',0):.3f}  motion={meta.get('avg_motion',0):.4f}  "
              f"clusters={meta.get('cx_clusters',0)}  scene_transitions={meta.get('scene_change_transitions',0)}")

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
        print(f"  ⚠️  Master composition failed ({exc}), using safe centre-crop fallback")
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


def write_high_quality_video(
    clip: VideoFileClip,
    output_path: str | Path,
    audio_path: Path | None = None,
    render_profile: str = DEFAULT_RENDER_PROFILE,
) -> None:
    """Write the clip to disk.

    When *audio_path* is supplied the render is two-pass:
      1. Write video-only (no MoviePy audio reader involved).
      2. Mux the pre-extracted WAV in via a direct FFmpeg subprocess call.

    This completely bypasses MoviePy's FFMPEG_AudioReader whose ``proc``
    attribute can become ``None`` during long renders, causing:
      AttributeError: 'NoneType' object has no attribute 'stdout'
    """
    fps = get_render_fps(clip)
    # Predictable GOP: keyframe every 1 s for precise seeking (YouTube Shorts).
    keyint = str(fps * 1)
    keyint_min = str(fps)
    output_path = Path(output_path)
    render_settings = _get_render_profile_settings(render_profile)

    base_ffmpeg_params = [
        "-crf", render_settings["video_crf"],
        "-b:v", render_settings["video_bitrate"],
        "-maxrate", render_settings["video_maxrate"],
        "-bufsize", render_settings["video_bufsize"],
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level:v", "4.2",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-g", keyint,
        "-keyint_min", keyint_min,
        "-sc_threshold", "0",
        "-sws_flags", "lanczos+accurate_rnd+full_chroma_int",
        "-x264-params", render_settings.get("x264_params", "aq-mode=3:aq-strength=0.9:deblock=-1,-1"),
    ]

    if audio_path is not None and Path(audio_path).exists():
        # Pass 1: write video-only — no audio reader whatsoever
        video_only_path = output_path.with_suffix(".videoonly.mp4")
        try:
            clip.write_videofile(
                str(video_only_path),
                codec="libx264",
                audio=False,
                fps=fps,
                threads=RENDER_THREADS,
                preset=render_settings["video_preset"],
                ffmpeg_params=base_ffmpeg_params,
                logger=None,
            )
            # Pass 2: mux audio with direct FFmpeg — never touches MoviePy readers
            ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
            mux_proc = subprocess.run(
                [
                    ffmpeg_bin, "-y",
                    "-i", str(video_only_path),
                    "-i", str(audio_path),
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", render_settings["audio_bitrate"],
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-movflags", "+faststart",
                    "-shortest",
                    str(output_path),
                ],
                capture_output=True,
                timeout=300,
            )
            if mux_proc.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg audio mux failed (rc={mux_proc.returncode}): "
                    f"{mux_proc.stderr.decode(errors='replace')[:500]}"
                )
        finally:
            if video_only_path.exists():
                video_only_path.unlink(missing_ok=True)
    else:
        # No pre-extracted audio — write directly (silent or with MoviePy audio)
        clip.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac" if clip.audio is not None else None,
            fps=fps,
            audio_bitrate=render_settings["audio_bitrate"],
            threads=RENDER_THREADS,
            preset=render_settings["video_preset"],
            ffmpeg_params=base_ffmpeg_params,
            logger=None,
        )


def _extract_audio_segment(
    video_path: Path,
    start: float,
    end: float,
    temp_dir: Path,
) -> Path | None:
    """Pre-extract an audio segment to a WAV file using a direct FFmpeg call.

    Returns the WAV path on success, or None if the source has no audio or
    FFmpeg fails.  Using a standalone WAV file avoids MoviePy's chained
    FFMPEG_AudioReader whose ``proc`` can be None under complex clip chains,
    causing ``AttributeError: 'NoneType' object has no attribute 'stdout'``.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return None

    out_path = temp_dir / f"audio_tmp_{start:.3f}_{end:.3f}.wav"
    duration = end - start
    try:
        proc = subprocess.run(
            [
                ffmpeg_bin, "-y",
                "-ss", f"{start:.6f}",
                "-t", f"{duration:.6f}",
                "-i", str(video_path),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "44100",
                "-ac", "2",
                str(out_path),
            ],
            capture_output=True,
            timeout=120,
        )
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            return None
        return out_path
    except Exception:
        return None


def ensure_dependencies() -> None:
    if shutil.which("ffmpeg"):
        return

    raise EnvironmentError(
        "FFmpeg is not installed or not available in PATH. On Windows, install it with 'winget install Gyan.FFmpeg'."
    )


def _get_whisper_model_candidates() -> list[str]:
    return [candidate.strip() for candidate in WHISPER_MODEL.split(",") if candidate.strip()] or ["base"]


def _normalize_faster_whisper_result(segments, info) -> dict:
    normalized_segments = []
    full_text_parts: list[str] = []

    for segment in segments:
        text = (segment.text or "").strip()
        words = []
        for word in getattr(segment, "words", None) or []:
            if word.start is None or word.end is None:
                continue
            words.append(
                {
                    "word": (word.word or "").strip(),
                    "start": float(word.start),
                    "end": float(word.end),
                }
            )

        normalized_segments.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
                "words": words,
            }
        )
        if text:
            full_text_parts.append(text)

    language = getattr(info, "language", None)
    return {
        "text": " ".join(full_text_parts).strip(),
        "segments": normalized_segments,
        "language": language,
    }


def _load_faster_whisper_model() -> tuple[str, object]:
    if not _FASTER_WHISPER_AVAILABLE:
        raise RuntimeError("faster-whisper is not installed.")

    last_error = None
    with _whisper_model_lock:
        for model_name in _get_whisper_model_candidates():
            cache_key = f"faster::{model_name}"
            cached_model = _whisper_model_cache.get(cache_key)
            if cached_model is not None:
                return model_name, cached_model

            try:
                model = _FasterWhisperModel(model_name, device="auto", compute_type="auto")
                _whisper_model_cache[cache_key] = model
                return model_name, model
            except Exception as error:
                last_error = error

    raise RuntimeError("Could not load any configured faster-whisper model.") from last_error


def _load_openai_whisper_model() -> tuple[str, object]:
    if not _OPENAI_WHISPER_AVAILABLE or whisper is None:
        raise RuntimeError("openai-whisper is not installed.")

    last_error = None
    with _whisper_model_lock:
        for model_name in _get_whisper_model_candidates():
            cache_key = f"openai::{model_name}"
            cached_model = _whisper_model_cache.get(cache_key)
            if cached_model is not None:
                return model_name, cached_model

            try:
                model = whisper.load_model(model_name)
                _whisper_model_cache[cache_key] = model
                return model_name, model
            except Exception as error:
                last_error = error

    raise RuntimeError("Could not load any configured Whisper model.") from last_error


def load_whisper_model() -> tuple[str, str, object]:
    backend_order = {
        "auto": ["faster-whisper", "openai-whisper"],
        "faster-whisper": ["faster-whisper", "openai-whisper"],
        "openai-whisper": ["openai-whisper"],
    }.get(WHISPER_BACKEND, ["faster-whisper", "openai-whisper"])

    last_error = None
    for backend in backend_order:
        try:
            if backend == "faster-whisper":
                model_name, model = _load_faster_whisper_model()
                return backend, model_name, model
            model_name, model = _load_openai_whisper_model()
            return backend, model_name, model
        except Exception as error:
            last_error = error

    raise RuntimeError("Could not load any configured transcription backend.") from last_error


def transcribe_media(media_path: Path, *, word_timestamps: bool) -> dict:
    backend, model_name, model = load_whisper_model()
    if backend == "faster-whisper":
        try:
            segments, info = model.transcribe(
                str(media_path),
                beam_size=5,
                word_timestamps=word_timestamps,
                vad_filter=True,
                condition_on_previous_text=False,
                temperature=0.0,
            )
            return _normalize_faster_whisper_result(list(segments), info)
        except Exception as error:
            if WHISPER_BACKEND == "faster-whisper":
                raise RuntimeError("faster-whisper transcription failed.") from error
            # fallback to openai-whisper
            if _OPENAI_WHISPER_AVAILABLE:
                backend, model_name, model = "openai-whisper", *_load_openai_whisper_model()
            else:
                raise RuntimeError("faster-whisper transcription failed.") from error

    transcribe_options = {
        "fp16": False,
        "verbose": False,
        "condition_on_previous_text": False,
        "temperature": 0.0,
    }
    if word_timestamps:
        transcribe_options["word_timestamps"] = True

    try:
        return model.transcribe(str(media_path), **transcribe_options)
    except TypeError:
        fallback_options = dict(transcribe_options)
        fallback_options.pop("word_timestamps", None)
        return model.transcribe(str(media_path), **fallback_options)
    except Exception as error:
        if model_name != "base" and whisper is not None:
            with _whisper_model_lock:
                _whisper_model_cache.pop(f"openai::{model_name}", None)
                base_model = _whisper_model_cache.get("openai::base")
                if base_model is None:
                    base_model = whisper.load_model("base")
                    _whisper_model_cache["openai::base"] = base_model
            fallback_options = dict(transcribe_options)
            try:
                return base_model.transcribe(str(media_path), **fallback_options)
            except TypeError:
                fallback_options.pop("word_timestamps", None)
                return base_model.transcribe(str(media_path), **fallback_options)
        raise RuntimeError("Whisper transcription failed.") from error


def transcribe_video_fast(video_path: Path) -> dict:
    return transcribe_media(video_path, word_timestamps=True)


def transcribe_clip_for_subtitles(clip: VideoFileClip, output_dir: Path, clip_index: int) -> dict:
    if clip.audio is None:
        return {"text": "", "segments": []}

    audio_path = output_dir / f"clip_audio_{clip_index:02d}.wav"
    try:
        clip.audio.write_audiofile(
            str(audio_path),
            fps=16000,
            nbytes=2,
            ffmpeg_params=["-ac", "1"],
            logger=None,
        )
        return transcribe_media(audio_path, word_timestamps=True)
    finally:
        if audio_path.exists():
            audio_path.unlink()


def _load_wav_mono(audio_path: Path) -> tuple[int, "_np.ndarray"] | None:
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            raw = wav_file.readframes(frame_count)
    except (wave.Error, OSError):
        return None

    if sample_width != 2:
        return None

    audio = _np.frombuffer(raw, dtype=_np.int16).astype(_np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    audio /= 32768.0
    return frame_rate, audio


def _extract_audio_features(samples: "_np.ndarray", sample_rate: int) -> list[float]:
    if len(samples) == 0:
        return [0.0] * 6

    window = samples - float(samples.mean())
    rms = float(_np.sqrt(_np.mean(window ** 2)))
    zcr = float(_np.mean(_np.abs(_np.diff(_np.signbit(window).astype(_np.int8)))))

    spectrum = _np.abs(_np.fft.rfft(window * _np.hanning(len(window))))
    freqs = _np.fft.rfftfreq(len(window), d=1.0 / sample_rate)
    spec_sum = float(spectrum.sum()) or 1.0
    centroid = float((freqs * spectrum).sum() / spec_sum)
    cumulative = _np.cumsum(spectrum)
    rolloff = float(freqs[min(len(freqs) - 1, int(_np.searchsorted(cumulative, spec_sum * 0.85)))])
    peak_freq = float(freqs[int(_np.argmax(spectrum))]) if len(freqs) else 0.0
    flatness = float(_np.exp(_np.mean(_np.log(spectrum + 1e-9))) / (_np.mean(spectrum + 1e-9) + 1e-9))

    return [rms, zcr, centroid / 4000.0, rolloff / 4000.0, peak_freq / 4000.0, flatness]


def _cluster_audio_segments(feature_rows: "_np.ndarray") -> tuple[list[int], float]:
    if len(feature_rows) < 4:
        return [0] * len(feature_rows), 0.0

    centroids = _np.array([feature_rows[0], feature_rows[-1]], dtype=_np.float32)
    labels = _np.zeros(len(feature_rows), dtype=_np.int32)

    for _ in range(10):
        distances = _np.linalg.norm(feature_rows[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = distances.argmin(axis=1)
        if _np.array_equal(labels, new_labels):
            break
        labels = new_labels
        for cluster_index in range(2):
            members = feature_rows[labels == cluster_index]
            if len(members) > 0:
                centroids[cluster_index] = members.mean(axis=0)

    counts = [_np.sum(labels == 0), _np.sum(labels == 1)]
    if min(counts) < 2:
        return labels.tolist(), 0.0

    separation = float(_np.linalg.norm(centroids[0] - centroids[1]))
    intra = 0.0
    for cluster_index in range(2):
        members = feature_rows[labels == cluster_index]
        intra += float(_np.linalg.norm(members - centroids[cluster_index], axis=1).mean())
    confidence = separation / max(0.05, intra)
    return labels.tolist(), round(confidence, 3)


def _smooth_speaker_labels(labels: list[int]) -> list[int]:
    if len(labels) < 3:
        return labels

    smoothed = list(labels)
    for _ in range(2):
        updated = list(smoothed)
        for index in range(1, len(smoothed) - 1):
            prev_label = smoothed[index - 1]
            next_label = smoothed[index + 1]
            if prev_label == next_label and smoothed[index] != prev_label:
                updated[index] = prev_label
        smoothed = updated
    return smoothed


def _merge_audio_assignments(assignments: list[dict]) -> list[dict]:
    if not assignments:
        return []

    merged: list[dict] = []
    for assignment in sorted(assignments, key=lambda item: (float(item.get("start") or 0.0), float(item.get("end") or 0.0))):
        start = round(float(assignment.get("start") or 0.0), 3)
        end = round(max(start, float(assignment.get("end") or start)), 3)
        speaker = str(assignment.get("speaker") or "S1")
        if merged and merged[-1]["speaker"] == speaker and start - float(merged[-1]["end"]) <= 0.35:
            merged[-1]["end"] = end
            continue
        merged.append({"start": start, "end": end, "speaker": speaker})
    return merged


def _build_audio_speaker_summary(assignments: list[dict]) -> dict:
    merged = _merge_audio_assignments(assignments)
    if not merged:
        return {
            "audioSpeakerCount": 0,
            "audioSpeakerSwitches": 0,
            "audioDominantSpeaker": None,
            "audioDominantShare": 0.0,
            "audioTurnDensity": 0.0,
            "audioSpeakerAssignments": [],
        }

    totals: dict[str, float] = {}
    switches = 0
    previous_speaker = None
    for assignment in merged:
        speaker = str(assignment["speaker"])
        duration = max(0.0, float(assignment["end"]) - float(assignment["start"]))
        totals[speaker] = totals.get(speaker, 0.0) + duration
        if previous_speaker is not None and previous_speaker != speaker:
            switches += 1
        previous_speaker = speaker

    total_duration = max(0.001, sum(totals.values()))
    dominant_speaker, dominant_duration = max(totals.items(), key=lambda item: item[1])
    turn_density = switches / max(0.25, total_duration / 60.0)

    return {
        "audioSpeakerCount": len(totals),
        "audioSpeakerSwitches": switches,
        "audioDominantSpeaker": dominant_speaker,
        "audioDominantShare": round(dominant_duration / total_duration, 3),
        "audioTurnDensity": round(turn_density, 3),
        "audioSpeakerAssignments": merged,
    }


def _analyze_audio_speakers_pyannote(audio_path: Path) -> dict | None:
    pipeline = _load_pyannote_pipeline()
    if pipeline is None:
        return None

    try:
        diarization = pipeline(str(audio_path))
    except Exception:
        return None

    assignments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        speaker_label = str(speaker)
        assignments.append(
            {
                "start": round(float(turn.start), 3),
                "end": round(float(turn.end), 3),
                "speaker": speaker_label,
            }
        )

    if not assignments:
        return None

    summary = _build_audio_speaker_summary(assignments)
    summary["audioSpeakerConfidence"] = 0.92 if int(summary["audioSpeakerCount"]) > 1 else 0.75
    summary["audioSpeakerProvider"] = "pyannote"
    return summary


def analyze_audio_speakers(audio_path: Path | None, transcript: dict) -> dict:
    if audio_path is None or not audio_path.exists():
        return {"audioSpeakerCount": 0, "audioSpeakerSwitches": 0, "audioSpeakerConfidence": 0.0, "audioSpeakerProvider": "none"}

    pyannote_result = _analyze_audio_speakers_pyannote(audio_path)
    if pyannote_result is not None:
        return pyannote_result

    loaded = _load_wav_mono(audio_path)
    if loaded is None:
        return {"audioSpeakerCount": 0, "audioSpeakerSwitches": 0, "audioSpeakerConfidence": 0.0, "audioSpeakerProvider": "none"}

    sample_rate, audio = loaded
    segment_rows: list[dict] = []
    for index, segment in enumerate(transcript.get("segments") or []):
        try:
            start = max(0.0, float(segment.get("start") or 0.0))
            end = max(start + 0.01, float(segment.get("end") or 0.0))
        except (TypeError, ValueError):
            continue

        if end - start < 0.45:
            continue

        start_idx = int(start * sample_rate)
        end_idx = min(len(audio), int(end * sample_rate))
        samples = audio[start_idx:end_idx]
        if len(samples) < int(sample_rate * 0.35):
            continue

        segment_rows.append(
            {
                "index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "features": _extract_audio_features(samples, sample_rate),
            }
        )

    if len(segment_rows) < 4:
        return {"audioSpeakerCount": 1, "audioSpeakerSwitches": 0, "audioSpeakerConfidence": 0.0, "audioSpeakerProvider": "heuristic"}

    feature_matrix = _np.array([row["features"] for row in segment_rows], dtype=_np.float32)
    means = feature_matrix.mean(axis=0)
    stds = feature_matrix.std(axis=0) + 1e-6
    normalized = (feature_matrix - means) / stds
    labels, confidence = _cluster_audio_segments(normalized)
    labels = _smooth_speaker_labels(labels)

    switches = 0
    for prev, curr in zip(labels, labels[1:]):
        if prev != curr:
            switches += 1

    speaker_count = 2 if confidence >= 1.15 and switches >= 1 else 1
    assignments = []
    for row, label in zip(segment_rows, labels):
        assignments.append(
            {
                "start": row["start"],
                "end": row["end"],
                "speaker": f"S{label + 1 if speaker_count > 1 else 1}",
            }
        )

    summary = _build_audio_speaker_summary(assignments)
    summary["audioSpeakerCount"] = speaker_count
    summary["audioSpeakerSwitches"] = int(summary["audioSpeakerSwitches"] or 0) if speaker_count > 1 else 0
    summary["audioSpeakerConfidence"] = confidence if speaker_count > 1 else round(confidence * 0.4, 3)
    summary["audioSpeakerProvider"] = "heuristic"
    if speaker_count <= 1:
        summary["audioDominantSpeaker"] = "S1"
        summary["audioDominantShare"] = 1.0 if summary["audioSpeakerAssignments"] else 0.0
    return summary


def _slice_segment_words(words: list[dict], clip_start_time: float, clip_end_time: float) -> list[dict]:
    clip_duration = max(0.0, clip_end_time - clip_start_time)
    sliced_words: list[dict] = []

    for word in words:
        raw_text = (word.get("word") or "").strip()
        raw_start = word.get("start")
        raw_end = word.get("end")
        if not raw_text or raw_start is None or raw_end is None:
            continue

        try:
            word_start = float(raw_start)
            word_end = float(raw_end)
        except (TypeError, ValueError):
            continue

        if word_end <= clip_start_time or word_start >= clip_end_time:
            continue

        relative_start = max(0.0, word_start - clip_start_time)
        relative_end = min(clip_duration, word_end - clip_start_time)
        if relative_end <= relative_start:
            relative_end = min(clip_duration, relative_start + 0.12)

        sliced_words.append(
            {
                "word": raw_text,
                "start": relative_start,
                "end": relative_end,
            }
        )

    return sliced_words


def extract_clip_transcript_from_full(full_transcript: dict, clip_start_time: float, clip_end_time: float) -> tuple[dict, bool]:
    clip_duration = max(0.0, clip_end_time - clip_start_time)
    clip_segments: list[dict] = []
    transcript_text_parts: list[str] = []
    requires_precise_fallback = False

    for raw_segment in full_transcript.get("segments") or []:
        raw_start = raw_segment.get("start")
        raw_end = raw_segment.get("end")
        if raw_start is None or raw_end is None:
            continue

        try:
            segment_start = float(raw_start)
            segment_end = float(raw_end)
        except (TypeError, ValueError):
            continue

        if segment_end <= clip_start_time or segment_start >= clip_end_time:
            continue

        relative_start = max(0.0, segment_start - clip_start_time)
        relative_end = min(clip_duration, segment_end - clip_start_time)
        if relative_end <= relative_start:
            continue

        sliced_words = _slice_segment_words(raw_segment.get("words") or [], clip_start_time, clip_end_time)
        if sliced_words:
            segment_text = " ".join(word["word"] for word in sliced_words)
        else:
            segment_text = (raw_segment.get("text") or "").strip()
            if segment_start < clip_start_time or segment_end > clip_end_time:
                requires_precise_fallback = True

        if not segment_text and not sliced_words:
            continue

        clipped_segment = {
            "start": relative_start,
            "end": relative_end,
            "text": segment_text,
        }
        if sliced_words:
            clipped_segment["words"] = sliced_words

        clip_segments.append(clipped_segment)
        if segment_text:
            transcript_text_parts.append(segment_text)

    return {
        "text": " ".join(transcript_text_parts).strip(),
        "segments": clip_segments,
    }, requires_precise_fallback


def _summarize_download_info(info: dict | None) -> dict:
    if not info:
        return {}

    requested = info.get("requested_formats") or []
    video_format = next((fmt for fmt in requested if fmt.get("vcodec") not in {None, "none"}), info)
    audio_format = next((fmt for fmt in requested if fmt.get("acodec") not in {None, "none"}), info)

    width = video_format.get("width") or info.get("width")
    height = video_format.get("height") or info.get("height")
    fps = video_format.get("fps") or info.get("fps")
    source_summary = {
        "id": info.get("id"),
        "title": info.get("title"),
        "webpageUrl": info.get("webpage_url") or info.get("original_url"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "container": info.get("ext"),
        "width": width,
        "height": height,
        "fps": fps,
        "videoCodec": video_format.get("vcodec") or info.get("vcodec"),
        "audioCodec": audio_format.get("acodec") or info.get("acodec"),
        "videoBitrate": video_format.get("tbr") or info.get("tbr"),
        "audioBitrate": audio_format.get("abr") or info.get("abr"),
    }
    return {
        key: value
        for key, value in source_summary.items()
        if value is not None and value != "" and value != []
    }


def _format_download_quality_label(download_info: dict) -> str:
    if not download_info:
        return "Source download complete."

    dimensions = "x".join(
        str(int(value))
        for value in (download_info.get("width"), download_info.get("height"))
        if value is not None
    )
    fps = download_info.get("fps")
    codec = download_info.get("videoCodec")
    audio = download_info.get("audioCodec")
    parts = [part for part in [dimensions, f"{fps}fps" if fps else None, codec, audio] if part]
    if not parts:
        return "Source download complete."
    return f"Source download complete: {' / '.join(parts)}."


def download_video(url: str, destination_base: Path, progress_callback: ProgressCallback | None = None) -> tuple[Path, dict]:
    last_reported: dict[str, int] = {"pct": -1}

    def _ydl_progress(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes") or 0
        if total > 0:
            pct = int(downloaded / total * 100)
            # Report at most every 10%
            if pct >= last_reported["pct"] + 10:
                last_reported["pct"] = pct
                speed = d.get("speed") or 0
                speed_mb = speed / 1_048_576 if speed else 0
                eta = d.get("eta") or 0
                msg = f"Downloading... {pct}%"
                if speed_mb >= 0.1:
                    msg += f"  ({speed_mb:.1f} MB/s"
                    if eta:
                        msg += f", ~{eta}s left"
                    msg += ")"
                _emit(progress_callback, "downloading", msg)
        elif d.get("info_dict"):
            # No size info yet — just confirm it started
            if last_reported["pct"] < 0:
                last_reported["pct"] = 0
                _emit(progress_callback, "downloading", "Downloading video… (size unknown)")

    ydl_opts = {
        "format": DOWNLOAD_FORMAT,
        "format_sort": DOWNLOAD_FORMAT_SORT,
        "outtmpl": str(destination_base.with_suffix(".%(ext)s")),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "check_formats": True,
        "concurrent_fragment_downloads": 4,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 3,
        "progress_hooks": [_ydl_progress],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    download_info = _summarize_download_info(info)
    _emit(progress_callback, "downloading", _format_download_quality_label(download_info))
    return _resolve_downloaded_video_path(destination_base), download_info


def _extract_gemini_clip_blocks(text: str) -> list[dict[str, str]]:
    clips: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if GEMINI_CLIP_PATTERN.match(line):
            if current:
                clips.append(current)
            current = {}
            continue

        match = GEMINI_FIELD_PATTERN.match(line)
        if not match:
            continue

        key = match.group(1).lower()
        value = match.group(2).strip()

        if key == "title" and current.get("title") and current.get("start") and current.get("end"):
            clips.append(current)
            current = {}

        current[key] = value

    if current:
        clips.append(current)

    return clips


def _parse_gemini_float(raw_value: str | None, field_name: str) -> float:
    if raw_value is None or not raw_value.strip():
        raise ValueError(f"Gemini response is missing {field_name.upper()}.")

    try:
        return float(raw_value.strip())
    except ValueError as error:
        raise ValueError(f"Gemini returned an invalid {field_name.upper()} value: {raw_value!r}") from error


def _normalize_gemini_clip(raw_clip: dict[str, str]) -> dict:
    start = _parse_gemini_float(raw_clip.get("start"), "start")
    end = _parse_gemini_float(raw_clip.get("end"), "end")
    if end <= start:
        raise ValueError(f"Gemini returned an invalid clip interval: start={start}, end={end}")

    title = (raw_clip.get("title") or "").strip() or None
    reason = (raw_clip.get("reason") or "").strip() or None
    return {
        "title": title,
        "start": start,
        "end": end,
        "reason": reason,
    }


def parse_gemini_response(text: str) -> dict:
    clips = parse_gemini_responses(text)
    if not clips:
        raise ValueError("Gemini did not return any valid clip intervals.")
    return clips[0]


def parse_gemini_responses(text: str) -> list[dict]:
    raw_clips = _extract_gemini_clip_blocks(text)
    normalized: list[dict] = []
    seen_ranges: set[tuple[int, int]] = set()
    parse_errors: list[str] = []

    for raw_clip in raw_clips:
        try:
            clip = _normalize_gemini_clip(raw_clip)
        except ValueError as error:
            parse_errors.append(str(error))
            continue

        key = (round(clip["start"] * 10), round(clip["end"] * 10))
        if key in seen_ranges:
            continue

        seen_ranges.add(key)
        normalized.append(clip)

    if normalized:
        return normalized

    if parse_errors:
        raise ValueError(parse_errors[0])

    return []


def create_output_dir(base_dir: str | Path = OUTPUT_JOBS_DIR, job_id: str | None = None) -> tuple[str, Path]:
    job_id = job_id or uuid.uuid4().hex[:10]
    output_dir = Path(base_dir) / job_id
    (output_dir / "source").mkdir(parents=True, exist_ok=True)
    (output_dir / "audio").mkdir(parents=True, exist_ok=True)
    (output_dir / "clips").mkdir(parents=True, exist_ok=True)
    (output_dir / "meta").mkdir(parents=True, exist_ok=True)
    (output_dir / "diagnostics").mkdir(parents=True, exist_ok=True)
    return job_id, output_dir


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

    ensure_dependencies()
    _emit(progress_callback, "validating", "Checking subtitle rendering compatibility...")
    subtitles.assert_subtitle_rendering_ready(subtitle_style)

    job_id, output_dir = create_output_dir(base_dir=base_dir, job_id=job_id)
    source_dir = output_dir / "source"
    audio_dir = output_dir / "audio"
    meta_dir = output_dir / "meta"
    clips_dir = output_dir / "clips"
    diagnostics_dir = output_dir / "diagnostics"
    transcript_path = meta_dir / "full_transcript.txt"
    source_meta_path = meta_dir / "source_download.json"
    temp_base = source_dir / "source_video"

    video_path = None
    source_video = None
    clip = None
    clip_vertical = None
    clip_final = None
    download_info: dict = {}

    try:
        video_path = _restore_cached_video(video_url, temp_base)
        if video_path is not None:
            _emit(progress_callback, "downloading", "Reusing cached source video from a previous local run...")
        else:
            _emit(progress_callback, "downloading", "Downloading the highest-quality source video and audio from YouTube...")
            video_path, download_info = download_video(video_url, temp_base, progress_callback)
            _store_cached_video(video_url, video_path)

        if video_path is not None:
            if not download_info:
                download_info = {"webpageUrl": video_url}
            download_info["localPath"] = str(video_path)
            source_meta_path.write_text(json.dumps(download_info, ensure_ascii=True, indent=2), encoding="utf-8")

        result = _load_cached_transcript(video_url)
        if result is not None:
            _emit(progress_callback, "transcribing", "Reusing cached transcript from a previous local run...")
        else:
            _emit(progress_callback, "transcribing", f"Transcribing full video once with Whisper ({_get_whisper_model_candidates()[0]}) for analysis and subtitles...")
            result = transcribe_video_fast(video_path)
            _store_cached_transcript(video_url, result)
            _emit(progress_callback, "transcribing", f"Transcription complete. Found {len(result.get('segments') or [])} segments.")

        transcript_path.write_text(
            f"URL: {video_url}\n{result['text']}", encoding="utf-8"
        )

        clip_count = max(1, min(5, int(clip_count)))
        _emit(progress_callback, "analyzing", f"Asking Gemini for the best {clip_count} clips...")
        analysis = gemini_analyzer.find_viral_clips(result["segments"], api_key, clip_count=clip_count)
        clip_candidates = parse_gemini_responses(analysis)
        if not clip_candidates:
            clip_candidates = [parse_gemini_response(analysis)]

        clip_candidates = clip_candidates[:clip_count]

        # Validate clip timestamps against actual video duration
        clips_output = []
        source_video = VideoFileClip(str(video_path))
        video_duration = source_video.duration or 0.0

        valid_candidates = []
        for cd in clip_candidates:
            s, e = cd["start"], cd["end"]
            # Clamp endpoints to video boundaries
            s = max(0.0, min(s, video_duration - 1.0))
            e = max(s + 5.0, min(e, video_duration))
            if e - s < 20.0:
                print(f"  ⚠️  Skipping clip {cd.get('title', 'unknown')}: too short after clamping ({e - s:.1f}s < 20 s minimum)")
                continue
            cd["start"] = round(s, 2)
            cd["end"] = round(e, 2)
            valid_candidates.append(cd)

        if not valid_candidates:
            raise ValueError("No valid clips remain after timestamp validation against video duration.")

        clip_candidates = valid_candidates

        for index, clip_data in enumerate(clip_candidates, start=1):
            start = clip_data["start"]
            end = clip_data["end"]
            current_filename = build_clip_filename(output_filename, index, len(clip_candidates))
            output_path = clips_dir / current_filename

            _emit(progress_callback, "rendering", f"Rendering clip {index} of {len(clip_candidates)}...")
            clip = source_video.subclipped(start, end)

            # Pre-extract audio to a WAV file via direct FFmpeg.
            # Audio is muxed in by write_high_quality_video (2-pass) so that
            # MoviePy's FFMPEG_AudioReader is never used during the render,
            # preventing the proc=None crash on long clips.
            audio_temp_path: Path | None = None
            if clip.audio is not None:
                audio_temp_path = _extract_audio_segment(video_path, start, end, audio_dir)

            _emit(progress_callback, "rendering", f"Preparing subtitle timing for clip {index}...")
            clip_transcript, requires_precise_fallback = extract_clip_transcript_from_full(result, start, end)
            if requires_precise_fallback:
                _emit(progress_callback, "rendering", f"Refining subtitle timing for clip {index}...")
                clip_transcript = transcribe_clip_for_subtitles(clip, audio_dir, index)

            audio_speaker_meta = analyze_audio_speakers(audio_temp_path, clip_transcript)
            _emit(
                progress_callback,
                "rendering",
                f"Speaker analysis for clip {index}: {_speaker_analysis_backend_label(audio_speaker_meta)} "
                f"({audio_speaker_meta.get('audioSpeakerCount', 0)} speaker estimate).",
            )

            _emit(progress_callback, "rendering", f"Analysing visual content for clip {index}...")
            clip_vertical, content_type, clip_analytics = build_vertical_master_clip(clip, audio_speaker_meta=audio_speaker_meta)
            clip_analytics.update(audio_speaker_meta)
            ct_label = _CONTENT_LABELS.get(content_type, content_type)
            _emit(progress_callback, "rendering", f"Clip {index} detected as {ct_label} — composing vertical frame...")

            subtitle_plan = subtitles.build_subtitle_plan(
                clip_transcript.get("segments") or [],
                0,
                clip_vertical.duration,
            )
            subtitle_plan_path = diagnostics_dir / f"clip_{index:02d}_subtitles.json"
            subtitle_plan_path.write_text(
                json.dumps(subtitles.export_subtitle_plan(subtitle_plan), ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            subtitle_preflight = subtitles.validate_subtitle_plan_renderability(
                clip_vertical.size,
                subtitle_plan,
                subtitle_style,
            )
            subtitle_preflight_path = diagnostics_dir / f"clip_{index:02d}_subtitle_preflight.json"
            subtitle_preflight_path.write_text(
                json.dumps(subtitle_preflight, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            _emit(progress_callback, "rendering", f"Subtitle preflight passed for clip {index}.")

            clip_final = subtitles.create_subtitles(
                clip_vertical,
                clip_transcript.get("segments") or [],
                0,
                subtitle_style,
                clip_title=clip_data.get("title"),
                clip_reason=clip_data.get("reason"),
            )
            write_high_quality_video(
                clip_final,
                output_path,
                audio_path=audio_temp_path,
                render_profile=render_profile,
            )

            clips_output.append(
                {
                    "index": index,
                    "title": clip_data.get("title"),
                    "reason": clip_data.get("reason"),
                    "start": start,
                    "end": end,
                    "contentType": content_type,
                    "analytics": clip_analytics,
                    "outputFilename": current_filename,
                    "outputPath": str(output_path),
                    "subtitlePlanPath": str(subtitle_plan_path),
                    "subtitlePlan": subtitles.export_subtitle_plan(subtitle_plan),
                    "subtitlePreflightPath": str(subtitle_preflight_path),
                    "subtitlePreflight": subtitle_preflight,
                }
            )

            clip_final.close()
            clip_final = None
            if audio_temp_path is not None and audio_temp_path.exists():
                audio_temp_path.unlink(missing_ok=True)
                audio_temp_path = None
            clip_vertical.close()
            clip_vertical = None
            clip.close()
            clip = None

        _emit(progress_callback, "completed", f"{len(clips_output)} high-quality clips are ready to download.")
        first_clip = clips_output[0]
        return {
            "jobId": job_id,
            "videoUrl": video_url,
            "title": first_clip.get("title"),
            "reason": first_clip.get("reason"),
            "start": first_clip["start"],
            "end": first_clip["end"],
            "outputFilename": first_clip["outputFilename"],
            "subtitleStyle": subtitle_style,
            "outputPath": first_clip["outputPath"],
            "transcriptPath": str(transcript_path),
            "sourceMetaPath": str(source_meta_path),
            "sourceDownload": download_info,
            "outputDir": str(output_dir),
            "renderProfile": render_settings["label"],
            "renderProfileKey": render_profile,
            "clips": clips_output,
            "clipCount": len(clips_output),
        }
    finally:
        if clip_final is not None:
            clip_final.close()
        if source_video is not None:
            source_video.close()
        if clip_vertical is not None:
            clip_vertical.close()
        if clip is not None:
            clip.close()
        if video_path and os.path.exists(video_path):
            os.remove(video_path)


def build_clip_filename(output_filename: str, clip_index: int, total_clips: int) -> str:
    if total_clips <= 1:
        return output_filename

    path = Path(output_filename)
    stem = path.stem or "short_con_subs"
    suffix = path.suffix or ".mp4"
    return f"{stem}_{clip_index:02d}{suffix}"
