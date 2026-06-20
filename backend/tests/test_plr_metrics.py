import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from plr_metrics import calculate_plr_metrics
from model_caller import ModelCaller


class PlrMetricsTests(unittest.TestCase):
    def test_initial_formulas_use_preflash_baseline_and_postflash_minimum(self):
        fps = 10.0
        frames = list(range(40))
        diameters = [50.0] * 10 + [49.0, 47.0, 44.0, 40.0, 42.0] + [45.0] * 25

        result = calculate_plr_metrics(
            frames,
            diameters,
            fps=fps,
            flash_onset_s=1.0,
            smoothing_window=1,
        )

        self.assertEqual(result["unit"], "px")
        self.assertEqual(result["baseline_diameter_px"], 50.0)
        self.assertEqual(result["min_diameter_px"], 40.0)
        self.assertEqual(result["constriction_amplitude_px"], 10.0)
        self.assertEqual(result["latency_ms"], 300.0)
        self.assertEqual(len(result["diameter_series"]), len(frames))

    def test_missing_preflash_frames_uses_documented_fallback(self):
        result = calculate_plr_metrics(
            [0, 1, 2],
            [30.0, 29.0, 28.0],
            fps=10.0,
            flash_onset_s=0.0,
            smoothing_window=1,
        )
        self.assertTrue(result["baseline_fallback_used"])

    def test_mock_model_caller_emits_mobile_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clip = root / "clips" / "led1" / "FFFFFF.mp4"
            clip.parent.mkdir(parents=True)
            clip.touch()

            caller = ModelCaller(
                clips_root=str(root / "clips"),
                results_dir=str(root / "results"),
                cropped_dir=str(root / "cropped"),
                predictions_dir=str(root / "predictions"),
                session_id="unit-test",
                mock=True,
            )
            summary = caller.run(
                {1: [str(clip)]},
                {
                    str(clip): {
                        "flash_onset_s": 1.0,
                        "clip_start_s": 0.0,
                        "clip_end_s": 4.0,
                        "hex_color": "#FFFFFF",
                    }
                },
            )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["unit"], "px")
            result = summary["results"][str(clip)]
            self.assertEqual(result["_meta"]["eye"], "Left")
            self.assertGreater(len(result["diameter_series"]), 0)
            self.assertTrue((root / "results" / "session_summary.json").is_file())

    def test_clip_failure_preserves_successful_results(self):
        class PartiallyFailingCaller(ModelCaller):
            def _analyze_clip(self, led_index, clip_path, metadata):
                if "bad" in clip_path:
                    raise RuntimeError("synthetic clip failure")
                return super()._analyze_clip(led_index, clip_path, metadata)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "good.mp4"
            bad = root / "bad.mp4"
            good.touch()
            bad.touch()
            caller = PartiallyFailingCaller(
                results_dir=str(root / "results"),
                cropped_dir=str(root / "cropped"),
                predictions_dir=str(root / "predictions"),
                mock=True,
            )
            metadata = {
                str(good): {"flash_onset_s": 1.0, "clip_end_s": 4.0},
                str(bad): {"flash_onset_s": 1.0, "clip_end_s": 4.0},
            }

            summary = caller.run({1: [str(good), str(bad)]}, metadata)

            self.assertEqual(summary["status"], "partial")
            self.assertEqual(summary["failure_count"], 1)
            self.assertEqual(summary["results"][str(good)]["status"], "ok")
            self.assertEqual(summary["results"][str(bad)]["status"], "error")


if __name__ == "__main__":
    unittest.main()
