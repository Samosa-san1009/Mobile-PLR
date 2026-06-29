#!/usr/bin/env python3
"""Run crop + bundled ONNX inference + PLR metrics on a supplied test video."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from model_caller import ModelCaller


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate the integrated Mobile-PLR inference pipeline with one video."
    )
    parser.add_argument("--video", required=True, help="Path to an MP4 containing a full face or visible eye.")
    parser.add_argument("--eye", choices=["left", "right"], required=True)
    parser.add_argument(
        "--roi",
        default=None,
        help="Normalized x,y,width,height crop used only with --crop-mode static.",
    )
    parser.add_argument(
        "--crop-mode",
        choices=["detect", "static"],
        default="detect",
        help="detect uses OpenCV eye detection; static uses --roi. Default: detect.",
    )
    parser.add_argument("--flash-onset", type=float, default=1.0, help="Flash onset within video, seconds.")
    parser.add_argument("--mock", action="store_true", help="Exercise JSON/metrics without ONNX inference.")
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "test_artifacts" / "video_integration"),
        help="Folder for test outputs.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    source = Path(args.video).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Video not found: {source}")

    output = Path(args.output).expanduser().resolve()
    clips = output / "clips"
    results = output / "results"
    cropped = output / "cropped"
    predictions = output / "predictions"
    eye_index = 1 if args.eye == "left" else 2
    clip_dir = clips / f"led{eye_index}"
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip_path = clip_dir / source.name
    shutil.copy2(source, clip_path)

    os.environ["PLR_EYE_CROP_MODE"] = args.crop_mode
    if args.crop_mode == "static":
        roi_env = "PLR_LEFT_EYE_ROI" if eye_index == 1 else "PLR_RIGHT_EYE_ROI"
        os.environ[roi_env] = args.roi or "0,0,1,1"
    os.environ["PLR_MOCK_MODE"] = "1" if args.mock else "0"

    caller = ModelCaller(
        clips_root=str(clips),
        results_dir=str(results),
        cropped_dir=str(cropped),
        predictions_dir=str(predictions),
        session_id="manual-video-test",
    )
    metadata = {
        str(clip_path): {
            "led_index": eye_index,
            "eye": args.eye.title(),
            "hex_color": "#FFFFFF",
            "flash_onset_s": args.flash_onset,
            "clip_start_s": 0.0,
            "clip_end_s": 4.0,
        }
    }
    summary = caller.run({eye_index: [str(clip_path)]}, metadata)
    print(json.dumps({
        "status": summary["status"],
        "summary": str(results / "session_summary.json"),
        "cropped_video_dir": str(cropped),
        "prediction_csv_dir": str(predictions),
    }, indent=2))
    if summary["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
