"""
camera_service.py
------------------
Persistent camera with a LOCAL on-screen preview window on the Pi's own
attached monitor, using Picamera2's built-in Qt/OpenGL preview -- not a
browser MJPEG stream. This is meant for "look at the physically attached
screen to align eyes," not "pull up a phone/laptop browser."

One Picamera2 instance stays open for the whole server lifetime, with
two streams:
  main  -> full-res stream, encoded to H264/mp4 only while a PLR session
           is actively recording (same pipeline segmenter.py/plr_metrics.py
           already expect -- nothing downstream changes)
  lores -> smaller stream, continuously rendered to the monitor via
           Picamera2.start_preview(Preview.QTGL), live the whole time the
           server is running, including mid-session.

REQUIRES an X11 desktop session on the Pi (confirmed you're running
LXDE/X11, not console-only) AND the process needs permission to draw to
that display. See "HOW TO LAUNCH server.py" in the README -- this is the
single most likely thing to trip this up, more likely than any Python
bug below.

System packages needed (apt, not pip) if not already present:
    sudo apt install -y python3-pyqt5 python3-pyqt5.qtopengl python3-opengl

BOUNDING BOX OVERLAY:
call set_frame_annotator(fn) with a callable(frame_gray) -> (x, y, w, h)
or None. It's composited live on top of the preview window via
Picamera2.set_overlay(), an RGBA numpy array Picamera2 draws over the
video feed. Left as a no-op (no box, no extra CPU use) until you wire
in a detector.

NOT YET TESTED ON REAL HARDWARE -- things worth checking on the Pi:
  - QTGL needs GPU/EGL support; if it errors on start, this code falls
    back to the plain Preview.QT (software-rendered, works everywhere
    X11 does, just less smooth). If that ALSO fails, it's almost
    certainly the DISPLAY/XAUTHORITY issue described in the README, not
    a code bug.
  - CPU/GPU headroom: main-stream H264 recording + preview rendering +
    LED PWM (see led_controller.py) all at once on a Pi 4 is a lot --
    watch for preview lag or dropped recording frames during a real
    session.
"""

import os
import threading
import time

import numpy as np

try:
    from picamera2 import Picamera2, Preview
    from picamera2.encoders import H264Encoder
    from picamera2.outputs import FfmpegOutput
    _PICAM_AVAILABLE = True
except ImportError:
    _PICAM_AVAILABLE = False


def _parse_resolution(env_var: str, default: str) -> tuple:
    text = os.environ.get(env_var, default)
    w, h = text.lower().split("x")
    return (int(w), int(h))


DEFAULT_MAIN_RESOLUTION = _parse_resolution("PLR_CAMERA_RESOLUTION", "640x480")
DEFAULT_FPS = int(os.environ.get("PLR_CAMERA_FPS", "24"))
DEFAULT_PREVIEW_RESOLUTION = _parse_resolution("PLR_PREVIEW_RESOLUTION", "800x600")
# QTGL = hardware-accelerated (preferred). Falls back to QT automatically
# if QTGL fails to start. Override with PLR_PREVIEW_BACKEND=QT to force it.
PREVIEW_BACKEND = os.environ.get("PLR_PREVIEW_BACKEND", "QTGL").upper()
ANNOTATOR_FPS = float(os.environ.get("PLR_ANNOTATOR_FPS", "10"))

_lock = threading.RLock()
_picam = None
_recording = False
_frame_annotator = None
_annotator_thread = None
_annotator_stop = threading.Event()


def set_frame_annotator(fn):
    """fn: callable(frame_gray_ndarray) -> (x, y, w, h) | None, or None to disable."""
    global _frame_annotator, _annotator_thread
    _frame_annotator = fn
    if fn is not None and _picam is not None and _annotator_thread is None:
        _start_annotator_loop()


def ensure_started(resolution=None, fps=None):
    """Idempotent: opens the camera + preview window once. Safe to call at
    server startup so the monitor shows a live feed immediately, and safe
    to call again from CameraController.start()."""
    global _picam, _annotator_thread

    with _lock:
        if _picam is not None:
            return _picam
        if not _PICAM_AVAILABLE:
            raise RuntimeError("picamera2 not available on this system")

        main_res = tuple(resolution) if resolution else DEFAULT_MAIN_RESOLUTION
        cam_fps = fps or DEFAULT_FPS

        _picam = Picamera2()
        video_cfg = _picam.create_video_configuration(
            main={"size": main_res, "format": "RGB888"},
            lores={"size": DEFAULT_PREVIEW_RESOLUTION, "format": "YUV420"},
            controls={"FrameRate": float(cam_fps)},
            encode="main",
            display="lores",   # <- tells Picamera2 which stream feeds the preview window
        )
        _picam.configure(video_cfg)

        backend = Preview.QTGL if PREVIEW_BACKEND == "QTGL" else Preview.QT
        try:
            _picam.start_preview(backend)
            print(f"  [CameraService] Preview window started ({backend}).")
        except Exception as e:
            print(f"  [CameraService] {backend} preview failed ({e}); trying Preview.QT fallback.")
            try:
                _picam.start_preview(Preview.QT)
            except Exception as e2:
                _picam = None
                raise RuntimeError(
                    f"Could not open a preview window on the monitor: {e2}. "
                    f"This is almost always a DISPLAY/XAUTHORITY problem -- "
                    f"see the README's 'HOW TO LAUNCH server.py' section."
                ) from e2

        _picam.start()
        time.sleep(0.5)  # let AE/AWB settle

        if _frame_annotator is not None:
            _start_annotator_loop()

        print(
            f"  [CameraService] Camera open. main={main_res}@{cam_fps}fps  "
            f"preview={DEFAULT_PREVIEW_RESOLUTION} on local monitor"
        )
        return _picam


def _start_annotator_loop():
    global _annotator_thread
    _annotator_stop.clear()
    _annotator_thread = threading.Thread(target=_annotator_loop, daemon=True)
    _annotator_thread.start()


def _annotator_loop():
    """Periodically runs the registered annotator on the lores feed and
    pushes a bounding-box overlay onto the live preview window."""
    w, h = DEFAULT_PREVIEW_RESOLUTION
    while not _annotator_stop.is_set():
        if _frame_annotator is None:
            time.sleep(0.2)
            continue
        try:
            frame = _picam.capture_array("lores")   # planar YUV420
            gray = frame[:h, :w]                     # Y plane == grayscale, no conversion cost
            box = _frame_annotator(gray)
        except Exception as e:
            box = None
            print(f"  [CameraService] annotator error: {e}")

        overlay = np.zeros((h, w, 4), dtype=np.uint8)
        if box is not None:
            x, y, bw, bh = box
            t = 2  # box line thickness in px
            overlay[y:y + t, x:x + bw] = (0, 255, 0, 255)
            overlay[max(y, y + bh - t):y + bh, x:x + bw] = (0, 255, 0, 255)
            overlay[y:y + bh, x:x + t] = (0, 255, 0, 255)
            overlay[y:y + bh, max(x, x + bw - t):x + bw] = (0, 255, 0, 255)

        try:
            _picam.set_overlay(overlay)
        except Exception as e:
            print(f"  [CameraService] set_overlay failed: {e}")

        time.sleep(1.0 / ANNOTATOR_FPS)


def begin_recording(output_path: str, resolution=None, fps=None) -> float:
    """Starts writing the main stream to output_path without disturbing
    the on-screen preview. Returns the wall-clock recording start time."""
    global _recording
    with _lock:
        picam = ensure_started(resolution=resolution, fps=fps)
        if _recording:
            raise RuntimeError("A recording is already in progress.")
        encoder = H264Encoder(bitrate=2_000_000)
        output = FfmpegOutput(output_path)
        picam.start_recording(encoder, output, name="main")
        _recording = True
        return time.time()


def end_recording():
    global _recording
    with _lock:
        if _recording:
            try:
                _picam.stop_recording()
            finally:
                _recording = False


def shutdown():
    """Fully releases the camera and closes the preview window. Only call
    this on server shutdown -- calling it between sessions would kill the
    always-on preview, which defeats the point of this module."""
    global _picam, _annotator_thread
    with _lock:
        _annotator_stop.set()
        if _annotator_thread:
            _annotator_thread.join(timeout=2)
        if _picam is not None:
            try:
                _picam.stop_preview()
            except Exception:
                pass
            try:
                _picam.stop()
                _picam.close()
            except Exception:
                pass
            _picam = None
        print("  [CameraService] Camera fully released.")
