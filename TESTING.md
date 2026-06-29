# Integrated inference testing

The repository includes two levels of tests. No Raspberry Pi hardware is
required for the first level. A real eye video can be added later for the
second level.

## 1. Metric unit tests

From the repository root:

```bash
cd ~/Documents/Mobile-PLR
source .venv/bin/activate
python -m unittest discover -s backend/tests -p "test_*.py"
```

These tests verify the initial PLR formulas and JSON-ready time-series output.
They also verify all three terminal control modes, including the default three
dual flashes, optional additional flashes, default three sequential rounds,
and Left→Right / Right→Left execution order.

The backend contract tests also verify that the mobile `controlMode` payload
creates equivalent Dual, Left→Right, and Right→Left schedules without the
legacy single-eye `active_led` behavior.

## 2. Video integration test

This exercises the complete non-hardware path:

```text
input MP4 → detected/static eye crop → INT8 ONNX inference
          → per-frame CSV → PLR metrics → session_summary.json
```

Place a full-face test video anywhere on the Pi, then run:

```bash
cd ~/Documents/Mobile-PLR
source .venv/bin/activate

python backend/tests/run_video_integration_test.py \
  --video /path/to/full-face-video.mp4 \
  --eye left \
  --flash-onset 1.0
```

The default crop mode is `detect`, which uses OpenCV eye detection on each
frame. For `--eye left`, detection is limited to the left half of the frame.
For `--eye right`, detection is limited to the right half. The largest detected
eye candidate in that half is selected.

For an already-eye-cropped video, or to bypass detection with a fixed camera
ROI, use static mode:

```bash
python backend/tests/run_video_integration_test.py \
  --video /path/to/test-eye-video.mp4 \
  --eye left \
  --crop-mode static \
  --roi 0,0,1,1 \
  --flash-onset 1.0
```

For a wider face/camera video where detection is not reliable, supply a
normalized static crop:

```bash
python backend/tests/run_video_integration_test.py \
  --video /path/to/wide-video.mp4 \
  --eye left \
  --crop-mode static \
  --roi 0.05,0.2,0.4,0.6 \
  --flash-onset 1.0
```

Outputs are written under `test_artifacts/video_integration/`:

- `cropped/`: the exact video passed to the model
- `cropped/*_debug_boxes.mp4`: full-frame annotated debug videos for detected
  crops
- `predictions/`: frame-by-frame pupil diameter CSV in pixels
- `results/session_summary.json`: mobile-facing result contract

Inspect the cropped MP4 first. The model was trained on frames where the full
frame is already an eye region; a poor detection box or ROI invalidates the
prediction. For detected crops, also inspect `session_summary.json` metadata
such as `detected_frames`, `reused_previous_box_frames`, and
`single_eye_detection_frames`. In debug videos, cyan is the half-frame search
region, green is the final crop box, blue is the selected detected eye, gray
boxes are other detections, and orange means the previous frame's box was
reused.

## Contract-only mock test

To test result generation without loading ONNX Runtime:

```bash
python backend/tests/run_video_integration_test.py \
  --video /path/to/test-eye-video.mp4 \
  --eye left \
  --mock
```

Mock mode does not validate model accuracy. It only validates orchestration,
metrics, output storage, and the mobile JSON contract.

## Future accuracy fixture

When a validated video becomes available, record:

- expected eye and ROI
- frame rate
- flash-onset timestamp
- expected approximate baseline/minimum diameter ranges
- expected latency range

Then add it as a local/non-Git fixture and compare
`predictions/*.csv` and `session_summary.json` against those expected ranges.
