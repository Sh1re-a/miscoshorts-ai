from __future__ import annotations

import json
import secrets
from pathlib import Path

from moviepy import ColorClip, VideoFileClip
from PIL import Image

from app.shorts_service import write_high_quality_video
from app import subtitles
from app.paths import OUTPUTS_DIR


DEFAULT_VIDEO_SIZE = (1080, 1920)
DEFAULT_CUES = [
    {
        "start": 0.0,
        "end": 0.7,
        "text": "I THINK MORE",
        "words": ["I", "THINK", "MORE"],
        "highlightIndex": 1,
        "highlight": "THINK",
    },
    {
        "start": 0.7,
        "end": 1.4,
        "text": "YOU MEAN INTO",
        "words": ["YOU", "MEAN", "INTO"],
        "highlightIndex": 2,
        "highlight": "INTO",
    },
    {
        "start": 1.4,
        "end": 2.1,
        "text": "DIDN'T HE?",
        "words": ["DIDN'T", "HE?"],
        "highlightIndex": 0,
        "highlight": "DIDN'T",
    },
    {
        "start": 2.1,
        "end": 2.9,
        "text": "BROTHER 100%",
        "words": ["BROTHER", "100%"],
        "highlightIndex": 1,
        "highlight": "100%",
    },
    {
        "start": 2.9,
        "end": 3.8,
        "text": "THE 1800S WERE",
        "words": ["THE", "1800S", "WERE"],
        "highlightIndex": 1,
        "highlight": "1800S",
    },
]
DEFAULT_HEADER = {
    "title": "Michael Jackson, Macklemore, and the Debate",
    "reason": "Premium subtitle diagnostic with calm motion, soft panel, and subtle gradients.",
}
PREVIEW_ROOT = OUTPUTS_DIR / "_preview"


def _build_preview_segments(cues):
    segments = []
    for cue in cues:
        words = cue.get("words") or cue["text"].split()
        duration = max(0.18, (cue["end"] - cue["start"]) / max(1, len(words)))
        word_entries = []
        current_start = cue["start"]
        for word in words:
            word_end = min(cue["end"], current_start + duration)
            word_entries.append({"word": word, "start": current_start, "end": word_end})
            current_start = word_end

        segments.append(
            {
                "start": cue["start"],
                "end": cue["end"],
                "text": cue["text"],
                "words": word_entries,
            }
        )

    return segments


def _save_frame(frame_array, output_path):
    Image.fromarray(frame_array).save(output_path)


def generate_preview_bundle(
    *,
    subtitle_style: dict | None = None,
    title: str | None = None,
    reason: str | None = None,
    cues: list[dict] | None = None,
    video_size: tuple[int, int] = DEFAULT_VIDEO_SIZE,
) -> dict:
    preview_id = secrets.token_hex(8)
    output_dir = PREVIEW_ROOT / preview_id
    output_dir.mkdir(parents=True, exist_ok=True)

    resolved_cues = cues or DEFAULT_CUES
    resolved_header = {
        "title": (title or DEFAULT_HEADER["title"]).strip(),
        "reason": (reason or DEFAULT_HEADER["reason"]).strip(),
    }

    previews = subtitles.create_subtitle_preview_frames(
        video_size,
        resolved_cues,
        subtitle_style=subtitle_style,
    )

    preview_frames: list[dict] = []
    for cue_index, preview in enumerate(previews, start=1):
        frame_paths = {}
        for frame in preview["frames"]:
            background = frame["background"]
            filename = f"cue_{cue_index:02d}_{background}.png"
            image_path = output_dir / filename
            frame["image"].save(image_path)
            frame_paths[background] = filename

        preview_frames.append(
            {
                "cue": cue_index,
                "text": preview["text"],
                "start": preview["start"],
                "end": preview["end"],
                "width": preview["width"],
                "height": preview["height"],
                "position": preview["position"],
                "frames": frame_paths,
            }
        )

    header_clip = ColorClip(size=video_size, color=(18, 22, 29)).with_duration(3.0)
    header_layers = subtitles.create_top_description_overlay(
        header_clip,
        resolved_header["title"],
        resolved_header["reason"],
        subtitle_style,
    )
    header_images: list[str] = []
    try:
        for index, layer in enumerate(header_layers, start=1):
            frame = layer.get_frame(0.2)
            filename = f"header_{index:02d}.png"
            _save_frame(frame, output_dir / filename)
            header_images.append(filename)
    finally:
        for layer in header_layers:
            try:
                layer.close()
            except Exception:
                pass
        header_clip.close()

    manifest = {
        "previewId": preview_id,
        "title": resolved_header["title"],
        "reason": resolved_header["reason"],
        "subtitleStyle": subtitles.normalize_subtitle_style(subtitle_style),
        "videoSize": {"width": video_size[0], "height": video_size[1]},
        "headerImages": header_images,
        "subtitleFrames": preview_frames,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    output_dir = OUTPUTS_DIR / "subtitle_preview"
    output_dir.mkdir(parents=True, exist_ok=True)

    previews = subtitles.create_subtitle_preview_frames(DEFAULT_VIDEO_SIZE, DEFAULT_CUES)
    report = []

    for cue_index, preview in enumerate(previews, start=1):
        frame_paths = {}
        for frame in preview["frames"]:
            background = frame["background"]
            image_path = output_dir / f"cue_{cue_index:02d}_{background}.png"
            frame["image"].save(image_path)
            frame_paths[background] = str(image_path)

        report.append(
            {
                "cue": cue_index,
                "text": preview["text"],
                "start": preview["start"],
                "end": preview["end"],
                "width": preview["width"],
                "height": preview["height"],
                "position": preview["position"],
                "frames": frame_paths,
            }
        )

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    preview_segments = _build_preview_segments(DEFAULT_CUES)
    video_duration = max(cue["end"] for cue in DEFAULT_CUES) + 0.6
    white_clip = ColorClip(size=DEFAULT_VIDEO_SIZE, color=(245, 245, 245)).with_duration(video_duration)
    rendered_clip = subtitles.create_subtitles(
        white_clip,
        preview_segments,
        0,
        clip_title=DEFAULT_HEADER["title"],
        clip_reason=DEFAULT_HEADER["reason"],
    )

    pre_encode_times = [0.35, 0.95, 1.75]
    pre_encode_paths = []
    for index, timestamp in enumerate(pre_encode_times, start=1):
        image_path = output_dir / f"pre_encode_{index:02d}.png"
        _save_frame(rendered_clip.get_frame(timestamp), image_path)
        pre_encode_paths.append(str(image_path))

    encoded_video_path = output_dir / "diagnostic_probe.mp4"
    write_high_quality_video(rendered_clip, encoded_video_path)
    rendered_clip.close()
    white_clip.close()

    encoded_clip = VideoFileClip(str(encoded_video_path))
    post_encode_paths = []
    for index, timestamp in enumerate(pre_encode_times, start=1):
        image_path = output_dir / f"post_encode_{index:02d}.png"
        _save_frame(encoded_clip.get_frame(timestamp), image_path)
        post_encode_paths.append(str(image_path))
    encoded_clip.close()

    diagnostics_path = output_dir / "video_probe.json"
    diagnostics_path.write_text(
        json.dumps(
            {
                "header": DEFAULT_HEADER,
                "preEncodeFrames": pre_encode_paths,
                "postEncodeFrames": post_encode_paths,
                "encodedVideo": str(encoded_video_path),
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Wrote subtitle preview report to {report_path}")
    for entry in report:
        print(f"Cue {entry['cue']:02d}: {entry['text']} -> {entry['frames']}")
    print(f"Wrote encoded diagnostics to {diagnostics_path}")


if __name__ == "__main__":
    main()
