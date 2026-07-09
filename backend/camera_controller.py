"""
camera_controller.py
--------------------
Same external interface as before (start() / stop() / offset_of() /
output_path / recording_start_time) -- orchestrator.py and segmenter.py
need ZERO changes -- but now backed by camera_service's persistent
Picamera2 instance instead of opening a fresh one per session. That's
what makes the always-on alignment preview possible: the camera is
never closed between sessions, so the on-monitor preview window keeps
running the whole
time the server is up, including mid-session.

CHANGES FROM THE ORIGINAL VERSION:
  - resolution/fps are no longer hardcoded to 640x480@24 -- pass them
    explicitly, or via PLR_CAMERA_RESOLUTION / PLR_CAMERA_FPS env vars,
    or config_adapter.py now threads a "camera": {"resolution": [...],
    "fps": ...} block through from the mobile payload if the app sends
    one. Falls back to the old 640x480@24 defaults if nothing is set.
  - stop() now verifies the output file actually exists and has content
    before declaring success, instead of unconditionally printing
    "Recording saved" -- this was silently masking a real camera
    pipeline failure in the log you shared (buffer queue errors meant
    no file was ever written, but the old code declared success anyway
    and the crash only surfaced 2 stages later in the segmenter with a
    confusing "No such file or directory").
"""

import os
import time

import camera_service

_PICAM_AVAILABLE = camera_service._PICAM_AVAILABLE


class CameraController:
    def __init__(self, output_dir: str = "recordings",
                 fps: int = None, resolution: tuple = None):
        self.output_dir = output_dir
        self.fps = fps
        self.resolution = tuple(resolution) if resolution else None

        self.recording_start_time: float = None
        self.output_path: str = None

        os.makedirs(self.output_dir, exist_ok=True)

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> str:
        """Begin recording the main stream. Returns the output file path."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.output_path = os.path.join(
            self.output_dir, f"full_recording_{timestamp}.mp4"
        )

        if _PICAM_AVAILABLE:
            self.recording_start_time = camera_service.begin_recording(
                self.output_path, resolution=self.resolution, fps=self.fps
            )
        else:
            self._start_mock()

        print(f"  [Camera] Recording started → {self.output_path}")
        return self.output_path

    def stop(self):
        """Stop recording. The camera itself stays open (preview keeps
        running) -- only the main-stream encoder is stopped."""
        if _PICAM_AVAILABLE:
            camera_service.end_recording()

        if (
            not self.output_path
            or not os.path.exists(self.output_path)
            or os.path.getsize(self.output_path) == 0
        ):
            raise RuntimeError(
                f"Recording failed — no valid video at {self.output_path}. "
                f"Check the camera_service preview loop / dmesg for driver errors."
            )

        print(f"  [Camera] Recording saved   → {self.output_path}")

    def offset_of(self, unix_timestamp: float) -> float:
        if self.recording_start_time is None:
            raise RuntimeError("Recording has not started yet.")
        return max(0.0, unix_timestamp - self.recording_start_time)

    # ── mock path (no Pi camera available, e.g. dev machine / CI) ──────────────

    def _start_mock(self):
        self.recording_start_time = time.time()
        with open(self.output_path, "wb") as f:
            f.write(b"")
        print("  [Camera] (mock) picamera2 not available — placeholder file only.")
