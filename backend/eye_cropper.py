"""Create eye-region videos before pupil-diameter inference.

The trained models expect every input frame to already contain one eye. The
live camera records a wider scene, so clips are cropped with configurable
normalized ROIs before they are passed to the existing inference script.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


DEFAULT_ROIS = {
    1: (0.0, 0.0, 0.5, 1.0),  # left half of camera image
    2: (0.5, 0.0, 0.5, 1.0),  # right half of camera image
}


def _parse_roi(value: str | None, default: tuple[float, float, float, float]):
    if not value:
        return default
    try:
        roi = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise ValueError(f"Invalid ROI {value!r}; expected x,y,width,height") from exc
    if len(roi) != 4:
        raise ValueError(f"Invalid ROI {value!r}; expected four comma-separated values")
    x, y, width, height = roi
    if min(x, y, width, height) < 0 or width <= 0 or height <= 0:
        raise ValueError(f"ROI values must be non-negative and dimensions positive: {roi}")
    if x + width > 1.0 or y + height > 1.0:
        raise ValueError(f"Normalized ROI must stay inside the frame: {roi}")
    return roi


class EyeCropper:
    def __init__(self, output_dir: str, ffmpeg_bin: str = "ffmpeg"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_bin = ffmpeg_bin
        self.rois = {
            1: _parse_roi(os.environ.get("PLR_LEFT_EYE_ROI"), DEFAULT_ROIS[1]),
            2: _parse_roi(os.environ.get("PLR_RIGHT_EYE_ROI"), DEFAULT_ROIS[2]),
        }

    def crop(self, clip_path: str, led_index: int) -> tuple[str, dict]:
        if led_index not in self.rois:
            raise ValueError(f"No crop ROI configured for LED/eye {led_index}")

        source = Path(clip_path)
        output = self.output_dir / f"led{led_index}_{source.stem}_eye.mp4"
        x, y, width, height = self.rois[led_index]

        # Truncation to even dimensions keeps H.264 encoders happy.
        crop_filter = (
            f"crop="
            f"trunc(iw*{width}/2)*2:"
            f"trunc(ih*{height}/2)*2:"
            f"trunc(iw*{x}/2)*2:"
            f"trunc(ih*{y}/2)*2"
        )
        codec = os.environ.get("PLR_CROP_VIDEO_CODEC", "libx264")
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i", str(source),
            "-vf", crop_filter,
            "-an",
            "-c:v", codec,
            "-preset", "veryfast",
            "-crf", "18",
            str(output),
        ]
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode != 0:
            error = completed.stderr.decode(errors="replace")
            raise RuntimeError(f"Eye crop failed for {source}:\n{error}")

        metadata = {
            "source_clip": str(source),
            "cropped_clip": str(output),
            "normalized_roi": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            },
            "mapping": "LED 1 -> left-eye model; LED 2 -> right-eye model",
        }
        return str(output), metadata
