"""
model_caller.py
---------------
Iterates clips/led1/ and clips/led2/ and submits each clip
to the RESTful or PuReST pupillometry model running on the Pi.

Set MOCK_MODE = True to skip the real model and print fake results.
This lets you test the full pipeline on a Pi without the model running.
"""

import os
import json
import time
import random

MOCK_MODE = True   # ← set False when your real model endpoint is ready

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False


class ModelCaller:

    def __init__(
        self,
        model_url:   str = "http://localhost:8000/analyze",
        clips_root:  str = "clips",
        results_dir: str = "results",
        timeout_s:   int = 60,
        mock:        bool = MOCK_MODE,
    ):
        self.model_url   = model_url
        self.clips_root  = clips_root
        self.results_dir = results_dir
        self.timeout_s   = timeout_s
        self.mock        = mock
        os.makedirs(results_dir, exist_ok=True)

        if self.mock:
            print("  [Model] ⚠  MOCK MODE — no real model will be called.")
            print("  [Model]    Set mock=False (or MOCK_MODE=False) to use the real endpoint.\n")

    # ── public ────────────────────────────────────────────────────────────────

    def run(self, clip_paths: dict = None) -> dict:
        """
        clip_paths: optional {led_index: [path, ...]} from Segmenter.run()
        If None the clips/ folder is scanned directly.
        Returns {clip_path: result_dict}
        """
        if clip_paths is None:
            clip_paths = self._scan_clips_folder()

        all_results = {}

        for led_index in sorted(clip_paths.keys()):
            paths = clip_paths[led_index]
            print(f"\n  [Model] LED {led_index} — {len(paths)} clip(s)")

            for clip_path in paths:
                result = self._analyze_clip(led_index, clip_path)
                all_results[clip_path] = result

        self._save_summary(all_results)
        return all_results

    # ── internal ──────────────────────────────────────────────────────────────

    def _analyze_clip(self, led_index: int, clip_path: str) -> dict:
        filename  = os.path.basename(clip_path)
        safe_hex  = filename.replace(".mp4", "").split("_")[0]
        hex_color = "#" + safe_hex

        print(f"    → {filename}  (LED {led_index}, color {hex_color})")

        if self.mock:
            result = self._mock_response(led_index, hex_color, clip_path)
        else:
            result = self._real_request(led_index, hex_color, clip_path)

        result["_meta"] = {
            "led_index":  led_index,
            "hex_color":  hex_color,
            "clip_path":  clip_path,
            "timestamp":  time.time(),
            "mock":       self.mock,
        }

        self._save_clip_result(filename, result)
        return result

    def _mock_response(self, led_index: int, hex_color: str, clip_path: str) -> dict:
        """Simulate a plausible PLR model response for testing."""
        baseline   = round(random.uniform(3.5, 5.5), 3)
        constrict  = round(baseline - random.uniform(0.5, 1.5), 3)
        latency    = round(random.uniform(150, 350), 1)
        amplitude  = round(baseline - constrict, 3)
        print(f"      [MOCK] baseline={baseline}mm  constriction={constrict}mm  "
              f"latency={latency}ms  amplitude={amplitude}mm")
        return {
            "status":              "ok (mock)",
            "baseline_diameter_mm":    baseline,
            "min_diameter_mm":         constrict,
            "constriction_amplitude_mm": amplitude,
            "latency_ms":              latency,
        }

    def _real_request(self, led_index: int, hex_color: str, clip_path: str) -> dict:
        if not _REQUESTS_AVAILABLE:
            return {"error": "requests library not installed"}

        payload = {
            "video_path": os.path.abspath(clip_path),
            "led_index":  led_index,
            "hex_color":  hex_color,
        }
        try:
            resp = requests.post(self.model_url, json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            print(f"      ✗ Cannot connect to model at {self.model_url}")
            return {"error": "connection_failed"}
        except requests.exceptions.Timeout:
            print(f"      ✗ Model timed out after {self.timeout_s}s")
            return {"error": "timeout"}
        except requests.exceptions.HTTPError as e:
            print(f"      ✗ HTTP error: {e}")
            return {"error": str(e)}

    def _save_clip_result(self, filename: str, result: dict):
        base = filename.replace(".mp4", "")
        out  = os.path.join(self.results_dir, f"{base}.json")
        with open(out, "w") as f:
            json.dump(result, f, indent=2)

    def _save_summary(self, all_results: dict):
        path = os.path.join(self.results_dir, "session_summary.json")
        with open(path, "w") as f:
            json.dump({str(k): v for k, v in all_results.items()}, f, indent=2)
        print(f"\n  [Model] Summary → {path}")

    def _scan_clips_folder(self) -> dict:
        result = {}
        for led_index in (1, 2):
            folder = os.path.join(self.clips_root, f"led{led_index}")
            if not os.path.isdir(folder):
                result[led_index] = []
                continue
            result[led_index] = sorted(
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.endswith(".mp4")
            )
        return result
