# Eyezer Mobile ↔ Pi Backend Integration

End-to-end flow:

```
ConfigScreen (RN)  ──POST /session──►  server.py
                                         │
                                         ▼
                              Orchestrator → IR cam + LEDs
                                         │
                                         ▼
                                    Segmenter
                                         │
                                         ▼
                                    EyeCropper
                                         │
                                         ▼
                              INT8 ONNX ModelCaller
                                         │
                           sessions/<id>/results/session_summary.json
ResultsScreen (RN) ──GET /results───────┘   (+ cached to Downloads/eyezer/)
```

## Pi side — running the server

```bash
cd ~/Documents/Mobile-PLR
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
sudo apt install -y python3-picamera2 python3-libcamera ffmpeg
cd backend
python3 server.py             # listens on 0.0.0.0:5000
```

The server permits one active session at a time and remains running during
inference and after results are written.

## GPIO + wiring — set per device

Pins and common-anode flags are placeholders in `led_controller.py`:

```python
DEFAULT_CONFIG = {
    "led1_common_anode": False,
    "led1_r": 17, "led1_g": 27, "led1_b": 22,    # ← edit
    "led2_common_anode": True,
    "led2_r": 23, "led2_g": 24, "led2_b": 25,    # ← edit
}
```

Either edit the file or pass overrides in the mobile payload:

```json
{
  "gpio": {"led1_r": 5, "led1_g": 6, ...},
  "common_anode": {"led1": true, "led2": false}
}
```

Two LEDs of mixed wiring (one CA, one CC) are supported — set the dict form.

## IR Pi Camera Module (RPi 4)

`camera_controller.py` uses **picamera2** with the H.264 hardware encoder
(2 Mbps, 640×480 @ 24 fps). The camera is opened only during a session and
fully released the moment the flash sequence ends — important on a Pi 4
without a cooler because the IR module heats up quickly.

Falls back to a mock file on dev machines without picamera2.

## Mobile payload shape

```json
{
  "participant": {"name":"...", "age":30, "sex":"M"},
  "controlMode": "dual" | "left_to_right" | "right_to_left",
  "intensity": 80,
  "schedule": {}
}
```

Translation rules (`config_adapter.py`):

| Mobile field | Backend mapping |
|---|---|
| `controlMode = dual` | Both LEDs flash simultaneously; `schedule.flashes` carries each RGB hex and duration |
| `controlMode = left_to_right` | LED 1, inner pause, LED 2 for each round |
| `controlMode = right_to_left` | LED 2, inner pause, LED 1 for each round |
| `intensity` / `schedule.intensity` | 0–100% value carried through the API; current HIGH/LOW GPIO driver does not physically dim without PWM |
| `schedule.initialBreak` | Break before the first flash; capped at 120 seconds |
| `schedule.gap` | Break between flashes/rounds; minimum 3 seconds for analysis, capped at 120 seconds |
| `schedule.innerPause` | Pause between eyes in sequential modes; capped at 120 seconds |

Dual schedule example:

```json
{
  "controlMode": "dual",
  "intensity": 80,
  "schedule": {
    "flashes": [
      {"hex": "#FF0000", "duration": 1.0},
      {"hex": "#00FF00", "duration": 1.0},
      {"hex": "#0000FF", "duration": 1.0}
    ],
    "initialBreak": 5,
    "gap": 3,
    "intensity": 80
  }
}
```

Sequential schedule example:

```json
{
  "controlMode": "left_to_right",
  "schedule": {
    "rounds": 3,
    "hex": "#FFFFFF",
    "duration": 1.0,
    "initialBreak": 5,
    "innerPause": 1.0,
    "gap": 3,
    "intensity": 80
  }
}
```

## Mobile — set the Pi address

Edit `eyezer/mobile/api.js`:

```js
export const PI_BASE_URL = 'http://eyezer.local:5000';   // ← your Pi
```

If mDNS isn't available on your LAN, use the Pi's IP directly.

## Pi 4 thermal budget (single IR cam, no cooler)

- LEDs use direct GPIO HIGH/LOW output with safe OFF initialization.
- Camera resolution capped at 640×480 @ 24 fps with H.264 HW encoder.
- Camera and GPIO are released as soon as flashes finish.
- ONNX Runtime uses four inference threads by default.

## Model crop and result contract

The trained model does not crop an eye; it expects the complete frame to
already be an eye region. The backend therefore crops each segmented clip
before ONNX inference.

Default mode is `PLR_EYE_CROP_MODE=detect`. The backend runs OpenCV Haar eye
detection on every frame, but constrains detection to the relevant half first:
LED 1 / the left-eye ONNX model searches the left half, while LED 2 / the
right-eye ONNX model searches the right half. It selects the largest detected
eye candidate in that half, expands the eye box, and writes a 224×224 eye-only
video. Detection counts and the median normalized eye box are stored in result
metadata. The cropper also writes `*_debug_boxes.mp4` full-frame videos beside
the cropped model inputs; cyan is the detection search region, green is the
final crop box, blue is the selected detected eye, gray boxes are other
detections, and orange means a previous box was reused. Disable these with
`PLR_SAVE_EYE_DEBUG_VIDEO=0`.

Manual ROI mode is still available with `PLR_EYE_CROP_MODE=static`:

- LED 1 uses `PLR_LEFT_EYE_ROI` and the left-eye ONNX model.
- LED 2 uses `PLR_RIGHT_EYE_ROI` and the right-eye ONNX model.

Static ROIs use normalized `x,y,width,height` values. Defaults split the
camera frame into left and right halves. Inspect each session's `cropped/`
videos first if predictions are flat.

Results use pixels and include aggregate metrics plus the full per-frame
diameter series. See the root README for the initial formulas and mock-mode
environment variables.
