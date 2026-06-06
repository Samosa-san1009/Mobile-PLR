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
                              HTTP server stops listening
                                         │
                                         ▼
                                   ModelCaller
                                         │
                              results/session_summary.json
ResultsScreen (RN) ──GET /results───────┘   (+ cached to Downloads/eyezer/)
```

## Pi side — running the server

```bash
cd device/backend
pip3 install -r requirements.txt
sudo apt install -y python3-picamera2 python3-libcamera ffmpeg
python3 server.py             # listens on 0.0.0.0:5000
```

The server is **single-session**: after the pipeline completes and results
are written, the process exits to free CPU/RAM. Re-launch it (or run it
under systemd with `Restart=always`) before the next session.

## GPIO + wiring — set per device

Pins and common-anode flags are placeholders in `led_controller.py`:

```python
DEFAULT_PINS = {
    "led1_r": 17, "led1_g": 27, "led1_b": 22,    # ← edit
    "led2_r": 23, "led2_g": 24, "led2_b": 25,    # ← edit
}
DEFAULT_COMMON_ANODE = {"led1": False, "led2": False}   # ← edit per LED
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
  "eye":        "Left" | "Right" | "Both",
  "color":      "Red"  | "Green" | "Blue" | "Yellow" | "White" | "All",
  "iterations": 3,
  "duration":   1.0,
  "delay":      1.0,
  "intensity":  80
}
```

Translation rules (`config_adapter.py`):

| Mobile field | Backend mapping |
|---|---|
| `eye = Left`  | `mode = left_to_right`, `active_led = 1` (LED2 skipped) |
| `eye = Right` | `mode = right_to_left`, `active_led = 2` (LED1 skipped) |
| `eye = Both`  | `mode = dual` (both LEDs fire together) |
| `color = All` | cycles R, G, B, Y, W across rounds |
| `intensity`   | PWM duty cycle in `RGBLed.flash()` |
| `duration` s  | flash on-time in ms |
| `delay` s     | gap between flashes / rounds |

## Mobile — set the Pi address

Edit `eyezer/mobile/api.js`:

```js
export const PI_BASE_URL = 'http://eyezer.local:5000';   // ← your Pi
```

If mDNS isn't available on your LAN, use the Pi's IP directly.

## Pi 4 thermal budget (single IR cam, no cooler)

- PWM frequency held at 200 Hz (low CPU, no visible flicker).
- Camera resolution capped at 640×480 @ 24 fps with H.264 HW encoder.
- Camera and GPIO are released as soon as flashes finish.
- HTTP server stops accepting work before model inference starts so the
  Pi has all cores free for the pupillometry model.
