"""
segmenter.py
------------
Reads the 4 queues produced by the Orchestrator and cuts the
full continuous recording into per-flash clips using ffmpeg.

Output structure:
    clips/
        led1/
            #FF0000.mp4
            #00FF00.mp4
            ...
        led2/
            #FF00FF.mp4
            ...

If the same hex color appears more than once for a given LED,
clips are numbered:  #FF0000_1.mp4, #FF0000_2.mp4, ...
"""

import os
import queue
import subprocess
import collections


class Segmenter:

    def __init__(
        self,
        recording_path: str,
        camera_controller,          # CameraController instance (for offset_of())
        hex_queue_1: queue.Queue,
        ts_queue_1:  queue.Queue,
        hex_queue_2: queue.Queue,
        ts_queue_2:  queue.Queue,
        clips_root:  str = "clips",
        padding_s:   float = 0.1,   # seconds of extra footage before/after each flash
    ):
        self.recording_path   = recording_path
        self.camera           = camera_controller
        self.hex_queue_1      = hex_queue_1
        self.ts_queue_1       = ts_queue_1
        self.hex_queue_2      = hex_queue_2
        self.ts_queue_2       = ts_queue_2
        self.clips_root       = clips_root
        self.padding_s        = padding_s

    # ── public API ────────────────────────────────────────────────────────────

    def run(self):
        """
        Drain all 4 queues and cut clips for both LEDs.
        Returns dict mapping led_index → list of output paths.
        """
        results = {}

        for led_index, hq, tq in (
            (1, self.hex_queue_1, self.ts_queue_1),
            (2, self.hex_queue_2, self.ts_queue_2),
        ):
            out_dir = os.path.join(self.clips_root, f"led{led_index}")
            os.makedirs(out_dir, exist_ok=True)

            paths = self._cut_led(led_index, hq, tq, out_dir)
            results[led_index] = paths

        print("\n  [Segmenter] All clips saved.")
        return results

    # ── internal ──────────────────────────────────────────────────────────────

    def _cut_led(
        self,
        led_index: int,
        hq: queue.Queue,
        tq: queue.Queue,
        out_dir: str,
    ) -> list:
        """Drain one LED's queues and cut its clips."""
        hexes  = []
        stamps = []

        while not hq.empty() and not tq.empty():
            hexes.append(hq.get())
            stamps.append(tq.get())

        if not hexes:
            print(f"  [Segmenter] LED {led_index}: no entries in queue, skipping.")
            return []

        # Track duplicates so filenames stay unique
        seen_counts = collections.defaultdict(int)
        output_paths = []

        for hex_color, (t_on, t_off) in zip(hexes, stamps):
            # Convert absolute timestamps → video offsets
            start_s = max(0.0, self.camera.offset_of(t_on)  - self.padding_s)
            end_s   =          self.camera.offset_of(t_off) + self.padding_s
            duration_s = end_s - start_s

            # Build a filesystem-safe filename from hex code
            safe_hex = hex_color.lstrip("#")            # remove the '#'
            seen_counts[safe_hex] += 1
            count = seen_counts[safe_hex]

            if count == 1:
                # First time we see this color — no suffix yet,
                # but we may need to rename later if a duplicate appears.
                # Simplest: always use suffix when count >= 1.
                filename = f"{safe_hex}.mp4"
            else:
                # Rename previous clip to _1 on first duplicate
                if count == 2:
                    old = os.path.join(out_dir, f"{safe_hex}.mp4")
                    new = os.path.join(out_dir, f"{safe_hex}_1.mp4")
                    if os.path.exists(old):
                        os.rename(old, new)
                        # Update the tracked path in output_paths
                        if old in output_paths:
                            output_paths[output_paths.index(old)] = new
                filename = f"{safe_hex}_{count}.mp4"

            out_path = os.path.join(out_dir, filename)

            print(
                f"  [Segmenter] LED {led_index}  {hex_color}"
                f"  {start_s:.3f}s → {end_s:.3f}s"
                f"  → {out_path}"
            )

            self._ffmpeg_cut(start_s, duration_s, out_path)
            output_paths.append(out_path)

        return output_paths

    def _ffmpeg_cut(self, start_s: float, duration_s: float, out_path: str):
        """
        Use ffmpeg to cut a segment from the full recording.
        -ss before -i = fast seek (keyframe accurate enough for our use case).
        -c copy = no re-encode, very fast.
        """
        cmd = [
            "ffmpeg",
            "-y",                           # overwrite without asking
            "-ss", f"{start_s:.6f}",        # seek to start
            "-i", self.recording_path,      # input file
            "-t", f"{duration_s:.6f}",      # duration to extract
            "-c", "copy",                   # stream copy, no re-encode
            "-avoid_negative_ts", "make_zero",
            out_path,
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")
            raise RuntimeError(
                f"ffmpeg failed for {out_path}:\n{err}"
            )
