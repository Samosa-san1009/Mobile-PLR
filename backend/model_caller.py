"""Run the bundled eye models and produce mobile-ready PLR results.

The ONNX inference implementation remains in ``ml_model/inference``. This
adapter supplies the missing application integration: eye cropping, model
selection, per-frame CSV output, initial PLR metrics, partial failure handling,
and a stable JSON contract for the mobile app.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import random
import time
from pathlib import Path

from eye_cropper import EyeCropper
from plr_metrics import calculate_plr_metrics


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class ModelCaller:
    def __init__(
        self,
        clips_root: str = "clips",
        results_dir: str = "results",
        cropped_dir: str = "cropped",
        predictions_dir: str = "predictions",
        model_dir: str | None = None,
        session_id: str | None = None,
        mock: bool | None = None,
        progress_callback=None,
        logger: logging.Logger | None = None,
    ):
        self.clips_root = Path(clips_root)
        self.results_dir = Path(results_dir)
        self.cropped_dir = Path(cropped_dir)
        self.predictions_dir = Path(predictions_dir)
        self.session_id = session_id
        self.mock = _env_flag("PLR_MOCK_MODE", False) if mock is None else mock
        self.progress_callback = progress_callback
        self.logger = logger or logging.getLogger(__name__)

        repo_root = Path(__file__).resolve().parent.parent
        self.inference_script = repo_root / "ml_model" / "inference" / "inference_onnx.py"
        self.model_dir = Path(model_dir) if model_dir else (
            repo_root / "ml_model" / "inference" / "model_weights"
        )

        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.cropped_dir.mkdir(parents=True, exist_ok=True)
        self.predictions_dir.mkdir(parents=True, exist_ok=True)

        self._inference_module = None
        self._sessions = {}
        self.cropper = EyeCropper(str(self.cropped_dir))

        if self.mock:
            self.logger.warning("PLR_MOCK_MODE is enabled; predictions are simulated")

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
                self.logger.exception("Inference failed for %s", clip_path)
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

    def _analyze_clip(self, led_index: int, clip_path: str, metadata: dict) -> dict:
        meta = self._build_meta(led_index, clip_path, metadata)
        if self.mock:
            metrics = self._mock_metrics(metadata)
            return {"status": "ok", **metrics, "_meta": {**meta, "mock": True}}

        cropped_path, crop_meta = self.cropper.crop(clip_path, led_index)
        inference = self._load_inference_module()
        session = self._model_session(led_index)
        frame_predictions = inference.run_inference_on_clip(
            Path(cropped_path),
            session,
            batch_size=int(os.environ.get("PLR_INFERENCE_BATCH_SIZE", "4")),
        )

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
                "model": "left_eye_int8.onnx" if led_index == 1 else "right_eye_int8.onnx",
                "mock": False,
            },
        }

    def _load_inference_module(self):
        if self._inference_module is not None:
            return self._inference_module
        if not self.inference_script.is_file():
            raise FileNotFoundError(f"Inference script not found: {self.inference_script}")
        spec = importlib.util.spec_from_file_location("plr_inference_onnx", self.inference_script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._inference_module = module
        return module

    def _model_session(self, led_index: int):
        if led_index in self._sessions:
            return self._sessions[led_index]
        inference = self._load_inference_module()
        filename = "left_eye_int8.onnx" if led_index == 1 else "right_eye_int8.onnx"
        model_path = self.model_dir / filename
        if not model_path.is_file():
            raise FileNotFoundError(f"ONNX model not found: {model_path}")
        session = inference.create_session(
            str(model_path),
            num_threads=int(os.environ.get("PLR_INFERENCE_THREADS", "4")),
        )
        self._sessions[led_index] = session
        return session

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
    def _mock_metrics(metadata: dict) -> dict:
        fps = 24.0
        flash_onset_s = float(metadata.get("flash_onset_s", 1.0))
        duration_s = max(4.0, float(metadata.get("clip_end_s", 4.0)) - float(metadata.get("clip_start_s", 0.0)))
        frames = list(range(int(duration_s * fps)))
        baseline = random.uniform(35.0, 55.0)
        diameters = []
        for frame in frames:
            time_s = frame / fps
            if time_s < flash_onset_s:
                value = baseline + random.uniform(-0.4, 0.4)
            else:
                elapsed = time_s - flash_onset_s
                constriction = min(10.0, elapsed * 8.0) if elapsed < 1.25 else max(0.0, 10.0 - (elapsed - 1.25) * 3.0)
                value = baseline - constriction + random.uniform(-0.25, 0.25)
            diameters.append(value)
        return calculate_plr_metrics(frames, diameters, fps, flash_onset_s)

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
