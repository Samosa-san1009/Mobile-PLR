"""Run the classical PuRe/PuReST pupil-detection binary in place of the
ResNet ONNX regression model.

This is a **drop-in replacement** for ``ModelCaller`` (model_caller.py):
same constructor kwargs, same ``.run()`` return shape, same per-clip JSON
and ``session_summary.json`` files. Swap the import in server.py / main.py
and nothing else has to change.

Why this exists
----------------
The ONNX pipeline (ml_model/inference/inference_onnx.py) is a trained
ResNet regressor: each frame -> one predicted pupil diameter, learned from
labelled training video.

PuRe / PuReST (~/pupil_detection_new, built from
pupil-detection-methods/PuRe.cpp + PuReST.cpp) is a *classical* CV
algorithm: it fits an ellipse to the pupil boundary directly in every
frame, no training data or model weights involved. It's already compiled
and working on this Pi (see wrapper_2.py / analysis.py), so this module
just gives Mobile-PLR's orchestrator/server the same call contract for it
that it already has for the ONNX model.

Known constraints of the existing PupilDetection binary
---------------------------------------------------------
Confirmed from wrapper_2.py: the binary takes **no CLI arguments**. It
always reads from a fixed input path and always writes a fixed output CSV
path. wrapper_2.py works around this by copying the eye video into that
fixed slot before each run and copying the CSV back out afterward -- this
module does the same thing. That means:

  * Two clips cannot be processed concurrently through this class. That's
    fine here: ModelCaller.run() (and this class) process jobs in a plain
    sequential for-loop already, never in parallel.
  * If you ever rebuild main.cpp in pupil-detection-methods to accept
    ``--input`` / ``--output`` args, delete ``_run_pupil_detection`` below
    and call the binary with real per-clip paths instead of copying files;
    everything else in this class stays the same.

CSV contract (confirmed from analysis.py, which reads this binary's
output directly): columns ``Frame``, ``Valid``, ``Confidence``,
``Diameter_px`` (plus whatever else PuRe/PuReST adds -- we only use these
four).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import pandas as pd

from eye_cropper import EyeCropper
from plr_metrics import calculate_plr_metrics


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class PuReCaller:
    """Same public surface as ModelCaller, backed by the PuRe/PuReST binary."""

    def __init__(
        self,
        clips_root: str = "clips",
        results_dir: str = "results",
        cropped_dir: str = "cropped",
        predictions_dir: str = "predictions",
        model_dir: str | None = None,   # accepted, unused: keeps kwargs identical to ModelCaller
        session_id: str | None = None,
        mock: bool | None = None,       # accepted, unused: PuReST has no mock mode
        progress_callback=None,
        logger: logging.Logger | None = None,
    ):
        self.clips_root = Path(clips_root)
        self.results_dir = Path(results_dir)
        self.cropped_dir = Path(cropped_dir)
        self.predictions_dir = Path(predictions_dir)
        self.session_id = session_id
        self.progress_callback = progress_callback
        self.logger = logger or logging.getLogger(__name__)
        self.mock = False  # kept so _save_summary()'s "mock" field stays truthful

        # ---- PupilDetection binary + its fixed I/O slots -------------------
        # Override any of these with env vars if your paths differ from the
        # layout in your shell history (~/pupil_detection_new/...).
        self.pupil_bin = Path(os.environ.get(
            "PLR_PUPIL_BIN",
            str(Path.home() / "pupil_detection_new" / "build" / "PupilDetection"),
        ))
        self.pupil_input_video = Path(os.environ.get(
            "PLR_PUPIL_INPUT",
            str(Path.home() / "pupil_detection_new" / "videos" / "input" / "output_left_eye.mp4"),
        ))
        self.pupil_output_csv = Path(os.environ.get(
            "PLR_PUPIL_OUTPUT",
            str(Path.home() / "pupil_detection_new" / "videos" / "output" / "output_left_eye.csv"),
        ))
        self.conf_threshold = float(os.environ.get("PLR_PUPIL_CONF_THRESHOLD", "0.70"))

        if not self.pupil_bin.is_file():
            raise FileNotFoundError(f"PupilDetection binary not found: {self.pupil_bin}")

        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.cropped_dir.mkdir(parents=True, exist_ok=True)
        self.predictions_dir.mkdir(parents=True, exist_ok=True)
        self.pupil_input_video.parent.mkdir(parents=True, exist_ok=True)

        self.cropper = EyeCropper(str(self.cropped_dir))

    # -- Public API, identical to ModelCaller -----------------------------

    def run(self, clip_paths: dict | None = None, clip_metadata: dict | None = None) -> dict:
        if clip_paths is None:
            clip_paths = self._scan_clips_folder()
        clip_metadata = clip_metadata or {}

        jobs = [
            (led_index, clip_path)
            for led_index in sorted(clip_paths)
            for clip_path in clip_paths[led_index]
        ]
        results = {}
        failures = 0

        for completed, (led_index, clip_path) in enumerate(jobs, start=1):
            metadata = clip_metadata.get(clip_path, {})
            try:
                result = self._analyze_clip(led_index, clip_path, metadata)
            except Exception as exc:
                failures += 1
                self.logger.exception("PuRe/PuReST inference failed for %s", clip_path)
                result = {
                    "status": "error",
                    "error": str(exc),
                    "unit": "px",
                    "_meta": self._build_meta(led_index, clip_path, metadata),
                }
            results[clip_path] = result
            self._save_clip_result(led_index, clip_path, result)
            if self.progress_callback:
                self.progress_callback(completed, len(jobs), clip_path, result["status"])

        succeeded = len(jobs) - failures
        if not jobs or succeeded == 0:
            status = "error"
        elif failures:
            status = "partial"
        else:
            status = "ok"

        summary = {
            "schema_version": 2,
            "status": status,
            "unit": "px",
            "session_id": self.session_id,
            "mock": self.mock,
            "formula_note": (
                "Initial formulas: baseline is the median smoothed pre-flash "
                "diameter; minimum is the lowest smoothed post-flash diameter; "
                "amplitude is baseline minus minimum; latency is flash onset "
                "to the minimum. These definitions require clinical validation."
            ),
            "result_count": len(jobs),
            "failure_count": failures,
            "results": results,
        }
        self._save_summary(summary)
        return summary

    # -- Per-clip pipeline ---------------------------------------------------

    def _analyze_clip(self, led_index: int, clip_path: str, metadata: dict) -> dict:
        meta = self._build_meta(led_index, clip_path, metadata)

        cropped_path, crop_meta = self.cropper.crop(clip_path, led_index)
        frame_predictions = self._run_pupil_detection(cropped_path)

        prediction_path = self.predictions_dir / f"led{led_index}_{Path(clip_path).stem}.csv"
        frame_predictions.to_csv(prediction_path, index=False)

        fps = self._video_fps(cropped_path)
        metrics = calculate_plr_metrics(
            frame_predictions["frame"].tolist(),
            frame_predictions["pred_diameter_px"].tolist(),
            fps=fps,
            flash_onset_s=float(metadata.get("flash_onset_s", 1.0)),
            smoothing_window=int(os.environ.get("PLR_SMOOTHING_WINDOW", "5")),
        )
        return {
            "status": "ok",
            **metrics,
            "_meta": {
                **meta,
                **crop_meta,
                "prediction_csv": str(prediction_path),
                "model": "PuReST",
                "mock": False,
            },
        }

    def _run_pupil_detection(self, cropped_video_path: str) -> pd.DataFrame:
        """Copy the cropped clip into PupilDetection's fixed input slot,
        run the binary, and read its fixed output CSV back.

        Not safe to call concurrently for two clips -- ``run()`` above only
        ever calls this sequentially, so that's fine as written.
        """
        shutil.copy(cropped_video_path, self.pupil_input_video)

        if self.pupil_output_csv.exists():
            self.pupil_output_csv.unlink()

        result = subprocess.run(
            [str(self.pupil_bin)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"PupilDetection exited {result.returncode}: {result.stderr.strip()[:500]}"
            )

        if not self.pupil_output_csv.is_file():
            raise FileNotFoundError(
                f"PupilDetection did not produce {self.pupil_output_csv}"
            )

        raw = pd.read_csv(self.pupil_output_csv)
        for col in ("Frame", "Valid", "Confidence", "Diameter_px"):
            if col not in raw.columns:
                raise ValueError(
                    f"PupilDetection output is missing expected column {col!r}: "
                    f"got {list(raw.columns)}"
                )

        clean = raw[(raw["Valid"] == 1) & (raw["Confidence"] >= self.conf_threshold)].copy()
        if clean.empty:
            raise ValueError(
                f"No valid, confident ({self.conf_threshold:.2f}+) pupil "
                "detections in this clip"
            )

        return (
            pd.DataFrame({
                "frame": clean["Frame"].astype(int).values,
                "pred_diameter_px": clean["Diameter_px"].astype(float).values,
            })
            .sort_values("frame")
            .reset_index(drop=True)
        )

    # -- Helpers, unchanged from ModelCaller ----------------------------------

    @staticmethod
    def _video_fps(video_path: str) -> float:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS))
        finally:
            cap.release()
        return fps if fps > 0 else 24.0

    @staticmethod
    def _build_meta(led_index: int, clip_path: str, metadata: dict) -> dict:
        filename = Path(clip_path).name
        return {
            **metadata,
            "led_index": led_index,
            "eye": "Left" if led_index == 1 else "Right",
            "hex_color": metadata.get("hex_color", "#" + filename.split("_")[0].replace(".mp4", "")),
            "clip_path": clip_path,
            "timestamp": time.time(),
        }

    def _save_clip_result(self, led_index: int, clip_path: str, result: dict):
        output = self.results_dir / f"led{led_index}_{Path(clip_path).stem}.json"
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    def _save_summary(self, summary: dict):
        output = self.results_dir / "session_summary.json"
        output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        self.logger.info("Session summary written to %s", output)

    def _scan_clips_folder(self) -> dict:
        result = {}
        for led_index in (1, 2):
            folder = self.clips_root / f"led{led_index}"
            result[led_index] = sorted(str(path) for path in folder.glob("*.mp4")) if folder.is_dir() else []
        return result
