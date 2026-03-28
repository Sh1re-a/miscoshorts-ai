from __future__ import annotations

import os
import threading
import wave
from pathlib import Path

from moviepy import VideoFileClip

from app.paths import MODEL_CACHE_DIR
from app.runtime import configure_logging, load_local_env

load_local_env()
logger, _LOG_PATH = configure_logging("transcription")

try:
    import numpy as _np
except ImportError:
    _np = None

_FasterWhisperModel = None
_FASTER_WHISPER_AVAILABLE: bool | None = None
whisper = None
_OPENAI_WHISPER_AVAILABLE: bool | None = None
_whisper_model_cache: dict[str, object] = {}
_whisper_model_lock = threading.Lock()
_pyannote_pipeline_cache: object | None = None
_pyannote_pipeline_lock = threading.Lock()

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "distil-large-v3,large-v3")
WHISPER_BACKEND = os.getenv("WHISPER_BACKEND", "auto").strip().lower() or "auto"
WHISPER_MODEL_CACHE_DIR = Path(os.getenv("WHISPER_MODEL_CACHE_DIR", str(MODEL_CACHE_DIR / "whisper")))
SPEAKER_DIARIZATION_MODE = os.getenv("SPEAKER_DIARIZATION_MODE", "auto").strip().lower() or "auto"


def ensure_faster_whisper_available() -> bool:
    global _FasterWhisperModel, _FASTER_WHISPER_AVAILABLE
    if _FASTER_WHISPER_AVAILABLE is not None:
        return _FASTER_WHISPER_AVAILABLE

    try:
        from faster_whisper import WhisperModel as faster_whisper_model
    except Exception:
        _FasterWhisperModel = None
        _FASTER_WHISPER_AVAILABLE = False
    else:
        _FasterWhisperModel = faster_whisper_model
        _FASTER_WHISPER_AVAILABLE = True
    return _FASTER_WHISPER_AVAILABLE


def ensure_openai_whisper_available() -> bool:
    global whisper, _OPENAI_WHISPER_AVAILABLE
    if _OPENAI_WHISPER_AVAILABLE is not None:
        return _OPENAI_WHISPER_AVAILABLE

    try:
        import whisper as whisper_module
    except Exception:
        whisper = None
        _OPENAI_WHISPER_AVAILABLE = False
    else:
        whisper = whisper_module
        _OPENAI_WHISPER_AVAILABLE = True
    return _OPENAI_WHISPER_AVAILABLE


def get_speaker_diarization_token() -> str:
    return (
        os.getenv("PYANNOTE_AUTH_TOKEN")
        or os.getenv("HUGGINGFACE_ACCESS_TOKEN")
        or os.getenv("HF_TOKEN")
        or ""
    ).strip()


def should_use_pyannote() -> bool:
    if SPEAKER_DIARIZATION_MODE == "heuristic":
        return False
    if SPEAKER_DIARIZATION_MODE == "pyannote":
        return True
    return bool(get_speaker_diarization_token())


def load_pyannote_pipeline() -> object | None:
    global _pyannote_pipeline_cache
    if not should_use_pyannote():
        return None

    with _pyannote_pipeline_lock:
        if _pyannote_pipeline_cache is not None:
            return _pyannote_pipeline_cache

        token = get_speaker_diarization_token()
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


def speaker_analysis_backend_label(audio_meta: dict) -> str:
    provider = str(audio_meta.get("audioSpeakerProvider") or "none")
    if provider == "pyannote":
        return "Pyannote diarization"
    if provider == "heuristic":
        return "Local heuristic diarization"
    return "Speaker analysis unavailable"


def get_whisper_model_candidates() -> list[str]:
    configured = [candidate.strip() for candidate in WHISPER_MODEL.split(",") if candidate.strip()]
    candidates: list[str] = []
    for candidate in configured:
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates or ["distil-large-v3", "large-v3"]


def whisper_cache_contains_files() -> bool:
    if not WHISPER_MODEL_CACHE_DIR.exists():
        return False
    try:
        return any(path.is_file() for path in WHISPER_MODEL_CACHE_DIR.rglob("*"))
    except OSError:
        return False


def _get_whisper_fallback_candidates(current_model: str) -> list[str]:
    return [candidate for candidate in get_whisper_model_candidates() if candidate != current_model]


def _format_transcription_backend_error(error: Exception) -> str:
    details = str(error).strip() or error.__class__.__name__
    lowered = details.lower()
    if "no space left on device" in lowered or "disk full" in lowered:
        return (
            f"Speech model setup failed because the disk is full. Free up space and try again. "
            f"The local model cache lives in {WHISPER_MODEL_CACHE_DIR}."
        )
    if "permission denied" in lowered:
        return (
            f"Speech model setup could not write to {WHISPER_MODEL_CACHE_DIR}. "
            "Check folder permissions and try again."
        )
    return (
        f"Speech model setup failed in the local cache at {WHISPER_MODEL_CACHE_DIR}. "
        f"Details: {details}"
    )


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
    if not ensure_faster_whisper_available():
        raise RuntimeError("faster-whisper is not installed.")

    last_error = None
    WHISPER_MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _whisper_model_lock:
        for model_name in get_whisper_model_candidates():
            cache_key = f"faster::{model_name}"
            cached_model = _whisper_model_cache.get(cache_key)
            if cached_model is not None:
                return model_name, cached_model

            try:
                model = _FasterWhisperModel(
                    model_name,
                    device="auto",
                    compute_type="auto",
                    download_root=str(WHISPER_MODEL_CACHE_DIR),
                    local_files_only=False,
                )
                _whisper_model_cache[cache_key] = model
                logger.info("Prepared faster-whisper model %s", model_name)
                return model_name, model
            except Exception as error:
                last_error = error

    raise RuntimeError(_format_transcription_backend_error(last_error or RuntimeError("Unknown faster-whisper error."))) from last_error


def _load_openai_whisper_model() -> tuple[str, object]:
    if not ensure_openai_whisper_available() or whisper is None:
        raise RuntimeError("openai-whisper is not installed.")

    last_error = None
    WHISPER_MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _whisper_model_lock:
        for model_name in get_whisper_model_candidates():
            cache_key = f"openai::{model_name}"
            cached_model = _whisper_model_cache.get(cache_key)
            if cached_model is not None:
                return model_name, cached_model

            try:
                model = whisper.load_model(model_name, download_root=str(WHISPER_MODEL_CACHE_DIR))
                _whisper_model_cache[cache_key] = model
                logger.info("Prepared openai-whisper model %s", model_name)
                return model_name, model
            except Exception as error:
                last_error = error

    raise RuntimeError(_format_transcription_backend_error(last_error or RuntimeError("Unknown whisper error."))) from last_error


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

    raise RuntimeError(_format_transcription_backend_error(last_error or RuntimeError("Unknown transcription backend error."))) from last_error


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
            if ensure_openai_whisper_available():
                backend, model_name, model = "openai-whisper", *_load_openai_whisper_model()
                logger.warning("Falling back to openai-whisper after faster-whisper failed.")
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
        if whisper is not None:
            with _whisper_model_lock:
                _whisper_model_cache.pop(f"openai::{model_name}", None)
                WHISPER_MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                for fallback_name in _get_whisper_fallback_candidates(model_name):
                    fallback_key = f"openai::{fallback_name}"
                    fallback_model = _whisper_model_cache.get(fallback_key)
                    if fallback_model is None:
                        try:
                            fallback_model = whisper.load_model(fallback_name, download_root=str(WHISPER_MODEL_CACHE_DIR))
                            _whisper_model_cache[fallback_key] = fallback_model
                        except Exception:
                            continue
                    fallback_options = dict(transcribe_options)
                    try:
                        logger.warning("Retrying transcription with fallback whisper model %s", fallback_name)
                        return fallback_model.transcribe(str(media_path), **fallback_options)
                    except TypeError:
                        fallback_options.pop("word_timestamps", None)
                        return fallback_model.transcribe(str(media_path), **fallback_options)
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
    if _np is None:
        return None

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
    pipeline = load_pyannote_pipeline()
    if pipeline is None:
        return None

    try:
        diarization = pipeline(str(audio_path))
    except Exception:
        return None

    assignments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        assignments.append(
            {
                "start": round(float(turn.start), 3),
                "end": round(float(turn.end), 3),
                "speaker": str(speaker),
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
