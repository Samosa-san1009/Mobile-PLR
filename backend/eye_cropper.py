"""Create eye-region videos before pupil-diameter inference.

The trained models expect every input frame to already contain one eye. The
live camera may record a full face, so the default cropper splits each frame
into left/right search regions, detects the eye inside the relevant half, and
writes an eye-only video for the ONNX model.

Manual normalized ROIs are still supported through ``PLR_EYE_CROP_MODE=static``
for tests or fixed-camera fallback.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from statistics import median


DEFAULT_ROIS = {
    1: (0.0, 0.0, 0.5, 1.0),  # static fallback: left half of camera image
    2: (0.5, 0.0, 0.5, 1.0),  # static fallback: right half of camera image
}

DEFAULT_DETECTION_SEARCH_ROIS = {
    1: (0.0, 0.0, 0.5, 1.0),  # detect LED1/left eye only in left half
    2: (0.5, 0.0, 0.5, 1.0),  # detect LED2/right eye only in right half
}

EYE_LABELS = {
    1: "left",
    2: "right",
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


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    value = int(os.environ.get(name, str(default)))
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    value = float(os.environ.get(name, str(default)))
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class EyeCropper:
    def __init__(self, output_dir: str, ffmpeg_bin: str = "ffmpeg"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_bin = ffmpeg_bin
        self.rois = {
            1: _parse_roi(os.environ.get("PLR_LEFT_EYE_ROI"), DEFAULT_ROIS[1]),
            2: _parse_roi(os.environ.get("PLR_RIGHT_EYE_ROI"), DEFAULT_ROIS[2]),
        }
        self.detection_search_rois = {
            1: _parse_roi(
                os.environ.get("PLR_LEFT_EYE_DETECT_ROI"),
                DEFAULT_DETECTION_SEARCH_ROIS[1],
            ),
            2: _parse_roi(
                os.environ.get("PLR_RIGHT_EYE_DETECT_ROI"),
                DEFAULT_DETECTION_SEARCH_ROIS[2],
            ),
        }
        self.mode = os.environ.get("PLR_EYE_CROP_MODE", "detect").strip().lower()
        if self.mode not in {"detect", "static"}:
            raise ValueError("PLR_EYE_CROP_MODE must be 'detect' or 'static'")

        cascade_path = os.environ.get("PLR_EYE_CASCADE")
        if cascade_path:
            self.cascade_path = Path(cascade_path)
        else:
            self.cascade_path = None

    def crop(self, clip_path: str, led_index: int) -> tuple[str, dict]:
        if led_index not in EYE_LABELS:
            raise ValueError(f"No crop configuration for LED/eye {led_index}")
        if self.mode == "static":
            return self._crop_static_roi(clip_path, led_index)
        return self._crop_detected_eye(clip_path, led_index)

    def _crop_detected_eye(self, clip_path: str, led_index: int) -> tuple[str, dict]:
        cv2 = self._cv2()
        source = Path(clip_path)
        output = self.output_dir / f"led{led_index}_{source.stem}_eye.mp4"
        debug_output = self.output_dir / f"led{led_index}_{source.stem}_debug_boxes.mp4"
        target_eye = EYE_LABELS[led_index]

        cascade_path = self.cascade_path or Path(cv2.data.haarcascades) / "haarcascade_eye.xml"
        if not cascade_path.is_file():
            raise FileNotFoundError(f"OpenCV eye cascade not found: {cascade_path}")
        detector = cv2.CascadeClassifier(str(cascade_path))
        if detector.empty():
            raise RuntimeError(f"OpenCV could not load eye cascade: {cascade_path}")

        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            raise IOError(f"Cannot open video for eye crop: {source}")

        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 24.0
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if frame_width <= 0 or frame_height <= 0:
            cap.release()
            raise RuntimeError(f"Cannot read frame size for eye crop: {source}")

        output_size = _env_int("PLR_EYE_CROP_OUTPUT_SIZE", 224, minimum=32)
        padding = _env_float("PLR_EYE_BOX_PADDING", 0.45, minimum=0.0)
        min_eye_size = _env_int("PLR_EYE_MIN_SIZE_PX", 20, minimum=1)
        min_neighbors = _env_int("PLR_EYE_MIN_NEIGHBORS", 5, minimum=1)
        scale_factor = _env_float("PLR_EYE_SCALE_FACTOR", 1.1, minimum=1.01)
        search_roi = self._normalized_to_pixel_roi(
            self.detection_search_rois[led_index],
            frame_width,
            frame_height,
        )

        fourcc = cv2.VideoWriter_fourcc(*os.environ.get("PLR_CROP_FOURCC", "mp4v")[:4])
        writer = cv2.VideoWriter(
            str(output),
            fourcc,
            fps,
            (output_size, output_size),
        )
        if not writer.isOpened():
            cap.release()
            raise RuntimeError(f"Cannot create cropped eye video: {output}")

        save_debug_video = _env_bool("PLR_SAVE_EYE_DEBUG_VIDEO", True)
        debug_writer = None
        if save_debug_video:
            debug_writer = cv2.VideoWriter(
                str(debug_output),
                fourcc,
                fps,
                (frame_width, frame_height),
            )
            if not debug_writer.isOpened():
                writer.release()
                cap.release()
                raise RuntimeError(f"Cannot create eye debug video: {debug_output}")

        frame_count = 0
        detected_frames = 0
        reused_frames = 0
        single_eye_frames = 0
        selected_boxes: list[tuple[int, int, int, int]] = []
        previous_box = None

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                frame_count += 1

                roi_x, roi_y, roi_width, roi_height = search_roi
                search_frame = frame[roi_y : roi_y + roi_height, roi_x : roi_x + roi_width]
                gray = cv2.cvtColor(search_frame, cv2.COLOR_BGR2GRAY)
                detections = detector.detectMultiScale(
                    gray,
                    scaleFactor=scale_factor,
                    minNeighbors=min_neighbors,
                    minSize=(min_eye_size, min_eye_size),
                )
                detections = [
                    (int(x) + roi_x, int(y) + roi_y, int(width), int(height))
                    for x, y, width, height in detections
                ]
                detections = self._dedupe_boxes(detections)

                selected = self._select_best_eye_box(detections)
                if selected is not None:
                    detected_frames += 1
                    previous_box = selected
                    reused_box = False
                    if len(detections) == 1:
                        single_eye_frames += 1
                elif previous_box is not None:
                    selected = previous_box
                    reused_box = True
                    reused_frames += 1
                else:
                    if debug_writer is not None:
                        debug_frame = frame.copy()
                        self._draw_debug_overlay(
                            cv2,
                            debug_frame,
                            detections=detections,
                            search_roi=search_roi,
                            selected_box=None,
                            crop_box=None,
                            target_eye=target_eye,
                            reused_box=False,
                        )
                        debug_writer.write(debug_frame)
                    continue

                crop_box = self._expand_to_square(selected, frame_width, frame_height, padding)
                selected_boxes.append(crop_box)
                x, y, size, _ = crop_box
                if debug_writer is not None:
                    debug_frame = frame.copy()
                    self._draw_debug_overlay(
                        cv2,
                        debug_frame,
                        detections=detections,
                        search_roi=search_roi,
                        selected_box=selected,
                        crop_box=crop_box,
                        target_eye=target_eye,
                        reused_box=reused_box,
                    )
                    debug_writer.write(debug_frame)
                eye_frame = frame[y : y + size, x : x + size]
                eye_frame = cv2.resize(
                    eye_frame,
                    (output_size, output_size),
                    interpolation=cv2.INTER_LINEAR,
                )
                writer.write(eye_frame)
        finally:
            cap.release()
            writer.release()
            if debug_writer is not None:
                debug_writer.release()

        if detected_frames == 0:
            output.unlink(missing_ok=True)
            debug_output.unlink(missing_ok=True)
            raise RuntimeError(
                f"No eyes detected in {source}. Check camera view/lighting, or set "
                "PLR_EYE_CROP_MODE=static with PLR_LEFT_EYE_ROI/PLR_RIGHT_EYE_ROI."
            )
        if not selected_boxes:
            output.unlink(missing_ok=True)
            debug_output.unlink(missing_ok=True)
            raise RuntimeError(f"Eye crop produced no frames for {source}")

        metadata = {
            "source_clip": str(source),
            "cropped_clip": str(output),
            "debug_boxes_clip": str(debug_output) if save_debug_video else None,
            "crop_mode": "detect",
            "detector": "opencv_haar_eye",
            "detector_cascade": str(cascade_path),
            "selected_eye": target_eye,
            "selection_rule": (
                "LED 1 searches the left half; LED 2 searches the right half; "
                "the largest detected eye candidate in that half is selected"
            ),
            "normalized_detection_search_roi": self._normalized_roi_dict(
                self.detection_search_rois[led_index],
            ),
            "input_frame_size": {"width": frame_width, "height": frame_height},
            "output_frame_size": {"width": output_size, "height": output_size},
            "frames_seen": frame_count,
            "frames_written": len(selected_boxes),
            "detected_frames": detected_frames,
            "reused_previous_box_frames": reused_frames,
            "single_eye_detection_frames": single_eye_frames,
            "median_normalized_eye_box": self._median_normalized_box(
                selected_boxes,
                frame_width,
                frame_height,
            ),
            "mapping": "LED 1 -> left-eye model; LED 2 -> right-eye model",
        }
        return str(output), metadata

    @staticmethod
    def _draw_debug_overlay(
        cv2,
        frame,
        detections: list[tuple[int, int, int, int]],
        search_roi: tuple[int, int, int, int],
        selected_box: tuple[int, int, int, int] | None,
        crop_box: tuple[int, int, int, int] | None,
        target_eye: str,
        reused_box: bool,
    ) -> None:
        sx, sy, sw, sh = search_roi
        cv2.rectangle(
            frame,
            (sx, sy),
            (sx + sw, sy + sh),
            (255, 255, 0),
            2,
        )

        for x, y, width, height in detections:
            cv2.rectangle(
                frame,
                (x, y),
                (x + width, y + height),
                (160, 160, 160),
                1,
            )

        if selected_box is not None:
            x, y, width, height = selected_box
            selected_color = (0, 165, 255) if reused_box else (255, 0, 0)
            cv2.rectangle(
                frame,
                (x, y),
                (x + width, y + height),
                selected_color,
                2,
            )

        if crop_box is not None:
            x, y, width, height = crop_box
            cv2.rectangle(
                frame,
                (x, y),
                (x + width, y + height),
                (0, 255, 0),
                2,
            )

        label = f"{target_eye} eye"
        if crop_box is None:
            label += " - no detection"
        elif reused_box:
            label += " - reused previous box"
        cv2.putText(
            frame,
            label,
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0) if crop_box is not None else (0, 0, 255),
            2,
            cv2.LINE_AA,
        )

    @staticmethod
    def _cv2():
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError(
                "OpenCV is required for PLR_EYE_CROP_MODE=detect. Install backend "
                "requirements, or set PLR_EYE_CROP_MODE=static for manual ROI crops."
            ) from exc
        return cv2

    def _crop_static_roi(self, clip_path: str, led_index: int) -> tuple[str, dict]:
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
            "crop_mode": "static",
            "normalized_roi": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            },
            "mapping": "LED 1 -> left-eye model; LED 2 -> right-eye model",
        }
        return str(output), metadata

    @staticmethod
    def _select_eye_box(
        detections: list[tuple[int, int, int, int]],
        target_eye: str,
    ) -> tuple[int, int, int, int] | None:
        if not detections:
            return None
        ordered = sorted(detections, key=lambda box: box[0] + box[2] / 2.0)
        if target_eye == "left":
            return ordered[0]
        if target_eye == "right":
            return ordered[-1]
        raise ValueError(f"Unknown target eye: {target_eye}")

    @staticmethod
    def _select_best_eye_box(
        detections: list[tuple[int, int, int, int]],
    ) -> tuple[int, int, int, int] | None:
        if not detections:
            return None
        return max(detections, key=lambda box: box[2] * box[3])

    @staticmethod
    def _dedupe_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
        """Keep larger eye boxes when Haar returns overlapping duplicates."""
        ordered = sorted(boxes, key=lambda box: box[2] * box[3], reverse=True)
        kept: list[tuple[int, int, int, int]] = []
        for box in ordered:
            if all(EyeCropper._intersection_over_union(box, other) < 0.35 for other in kept):
                kept.append(box)
        return kept

    @staticmethod
    def _intersection_over_union(
        a: tuple[int, int, int, int],
        b: tuple[int, int, int, int],
    ) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        left = max(ax, bx)
        top = max(ay, by)
        right = min(ax + aw, bx + bw)
        bottom = min(ay + ah, by + bh)
        intersection = max(0, right - left) * max(0, bottom - top)
        if intersection == 0:
            return 0.0
        union = aw * ah + bw * bh - intersection
        return intersection / union if union else 0.0

    @staticmethod
    def _expand_to_square(
        box: tuple[int, int, int, int],
        frame_width: int,
        frame_height: int,
        padding: float,
    ) -> tuple[int, int, int, int]:
        x, y, width, height = box
        center_x = x + width / 2.0
        center_y = y + height / 2.0
        size = int(round(max(width, height) * (1.0 + padding * 2.0)))
        size = max(1, min(size, frame_width, frame_height))
        left = int(round(center_x - size / 2.0))
        top = int(round(center_y - size / 2.0))
        left = max(0, min(left, frame_width - size))
        top = max(0, min(top, frame_height - size))
        return left, top, size, size

    @staticmethod
    def _median_normalized_box(
        boxes: list[tuple[int, int, int, int]],
        frame_width: int,
        frame_height: int,
    ) -> dict:
        xs = [box[0] / frame_width for box in boxes]
        ys = [box[1] / frame_height for box in boxes]
        ws = [box[2] / frame_width for box in boxes]
        hs = [box[3] / frame_height for box in boxes]
        return {
            "x": round(float(median(xs)), 6),
            "y": round(float(median(ys)), 6),
            "width": round(float(median(ws)), 6),
            "height": round(float(median(hs)), 6),
        }

    @staticmethod
    def _normalized_to_pixel_roi(
        roi: tuple[float, float, float, float],
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int, int, int]:
        x, y, width, height = roi
        left = int(round(x * frame_width))
        top = int(round(y * frame_height))
        right = int(round((x + width) * frame_width))
        bottom = int(round((y + height) * frame_height))
        left = max(0, min(left, frame_width - 1))
        top = max(0, min(top, frame_height - 1))
        right = max(left + 1, min(right, frame_width))
        bottom = max(top + 1, min(bottom, frame_height))
        return left, top, right - left, bottom - top

    @staticmethod
    def _normalized_roi_dict(roi: tuple[float, float, float, float]) -> dict:
        x, y, width, height = roi
        return {
            "x": round(float(x), 6),
            "y": round(float(y), 6),
            "width": round(float(width), 6),
            "height": round(float(height), 6),
        }
