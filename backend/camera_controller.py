"""
camera_controller.py
--------------------
IR Pi Camera Module (CSI ribbon) on Raspberry Pi 4, via picamera2.

Designed for a passively cooled Pi 4 with a single IR cam that
heats up quickly:
  • low resolution (640x480) and modest fps (24)
  • H.264 hardware encoder via libcamera (no CPU re-encode)
  • camera is only powered up while record_session() is running
    and is fully released as soon as the flash sequence ends

A small mock recorder is provided automatically when picamera2
is not available (dev machines, CI), so the rest of the pipeline
remains testable.
"""

import os
import time
import threading

try:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder
    from picamera2.outputs import FfmpegOutput
    _PICAM_AVAILABLE = True
except ImportError:
    _PICAM_AVAILABLE = False


class CameraController:
    """
    Starts the IR camera, records a single continuous video,
    and exposes offset_of() so the segmenter can convert absolute
    timestamps into video-relative offsets.
    """

    def __init__(self, output_dir: str = "recordings",
                 fps: int = 24, resolution: tuple = (640, 480)):
        self.output_dir = output_dir
        self.fps        = fps
        self.resolution = resolution

        self._picam   = None
        self._encoder = None
        self._output  = None

        self.recording_start_time: float = None
        self.output_path: str = None
        self._mock_thread = None
        self._mock_stop = threading.Event()

        os.makedirs(self.output_dir, exist_ok=True)

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> str:
        """Power up camera, begin recording. Returns the output file path."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.output_path = os.path.join(
            self.output_dir, f"full_recording_{timestamp}.mp4"
        )

        if _PICAM_AVAILABLE:
            self._start_picamera()
        else:
            self._start_mock()

        print(f"  [Camera] Recording started → {self.output_path}")
        return self.output_path

    def stop(self):
        """Stop recording and fully release the camera ASAP (heat management)."""
        if _PICAM_AVAILABLE and self._picam is not None:
            try:
                self._picam.stop_recording()
            finally:
                try:
                    self._picam.close()
                except Exception:
                    pass
                self._picam = None
                self._encoder = None
                self._output = None
        else:
            self._mock_stop.set()
            if self._mock_thread:
                self._mock_thread.join(timeout=2)

        print(f"  [Camera] Recording saved   → {self.output_path}")

    def offset_of(self, unix_timestamp: float) -> float:
        if self.recording_start_time is None:
            raise RuntimeError("Recording has not started yet.")
        return max(0.0, unix_timestamp - self.recording_start_time)

    # ── picamera2 path ────────────────────────────────────────────────────────

    def _start_picamera(self):
        self._picam = Picamera2()

        # Video configuration tuned for low heat / low CPU.
        # The IR module typically reports as a normal sensor — IR cut handled
        # at the optical filter, not in software.
        video_cfg = self._picam.create_video_configuration(
            main={"size": self.resolution, "format": "RGB888"},
            controls={"FrameRate": float(self.fps)},
        )
        self._picam.configure(video_cfg)

        self._encoder = H264Encoder(bitrate=2_000_000)         # 2 Mbps is enough at 640x480
        self._output  = FfmpegOutput(self.output_path)         # MP4 container via ffmpeg

        self._picam.start_recording(self._encoder, self._output)
        self.recording_start_time = time.time()

    # ── mock path (no Pi camera available) ────────────────────────────────────

    def _start_mock(self):
        """Create an empty placeholder file and record a wall-clock start time."""
        self.recording_start_time = time.time()
        self._mock_stop.clear()

        def _writer():
            with open(self.output_path, "wb") as f:
                f.write(b"")
            self._mock_stop.wait()

        self._mock_thread = threading.Thread(target=_writer, daemon=True)
        self._mock_thread.start()
        print("  [Camera] (mock) picamera2 not available — placeholder file only.")
