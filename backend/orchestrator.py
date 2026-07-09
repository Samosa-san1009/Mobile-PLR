"""
orchestrator.py
---------------
Drives the flash session for whichever mode was selected in the wizard.

  DUAL          — both LEDs fire simultaneously on separate threads,
                  same color, same duration. One shared queue pair.

  LEFT→RIGHT    — LED1 (left) fires, inner pause, LED2 (right) fires.
                  Repeats for N rounds. Each side has its own queue pair.

  RIGHT→LEFT    — LED2 (right) fires, inner pause, LED1 (left) fires.
                  Same structure, reversed order.

Queue contents after run():
  hex_queue_1   →  hex codes fired by LED 1, in order
  ts_queue_1    →  (t_on, t_off) tuples for LED 1, in order
  hex_queue_2   →  hex codes fired by LED 2, in order
  ts_queue_2    →  (t_on, t_off) tuples for LED 2, in order
"""

import queue
import time
import threading

from led_controller    import LedController
from camera_controller import CameraController


class Orchestrator:

    def __init__(self, config: dict):
        self.config = config
        self.mode   = config["mode"]
        self.sched  = config["schedule"]

        # 4 queues — always present regardless of mode
        self.hex_queue_1: queue.Queue = queue.Queue()
        self.ts_queue_1:  queue.Queue = queue.Queue()
        self.hex_queue_2: queue.Queue = queue.Queue()
        self.ts_queue_2:  queue.Queue = queue.Queue()

        self.leds   = LedController(config)
        cam_cfg     = config.get("camera", {})
        self.camera = CameraController(
            output_dir=cam_cfg.get("output_dir", "recordings"),
            fps=cam_cfg.get("fps"),
            resolution=cam_cfg.get("resolution"),
        )

    # ── public ────────────────────────────────────────────────────────────────

    def run(self) -> str:
        """Start camera, run flash sequence, stop camera. Returns recording path."""
        print(f"\n  [Orchestrator] Mode: {self.mode.replace('_', ' ').upper()}")
        print("  [Orchestrator] Starting camera...")
        recording_path = self.camera.start()
        # The initial PLR formulas use one second of pre-flash baseline.
        time.sleep(float(self.config.get("pre_roll_s", 1.0)))
        initial_delay_s = float(self.sched.get("initial_delay_ms", 0)) / 1000.0
        if initial_delay_s > 0:
            print(f"  [Orchestrator] Waiting {initial_delay_s:.1f}s before first flash...")
            time.sleep(initial_delay_s)

        print("  [Orchestrator] Beginning flash sequence...\n")

        if self.mode == "dual":
            self._run_dual()
        elif self.mode == "left_to_right":
            self._run_sequential(first_led=1, second_led=2)
        elif self.mode == "right_to_left":
            self._run_sequential(first_led=2, second_led=1)

        # Keep recording long enough to observe constriction after the final flash.
        time.sleep(float(self.config.get("post_roll_s", 3.0)))

        print("\n  [Orchestrator] Flash sequence complete. Stopping camera...")
        self.camera.stop()
        self.leds.cleanup()
        self._print_queue_summary()
        return recording_path

    # ── DUAL mode ─────────────────────────────────────────────────────────────

    def _run_dual(self):
        """
        Both LEDs fire the same color at the same time.
        Uses two threads so GPIO writes happen in parallel.
        Both hex_queue_1 and hex_queue_2 are filled identically.
        """
        flashes = self.sched["flashes"]
        gap_s   = self.sched["gap_ms"] / 1000.0
        total   = len(flashes)

        for i, flash in enumerate(flashes):
            hex_color    = flash["hex"]
            duration_ms  = flash["duration_ms"]
            intensity    = flash.get("intensity_pct", 100.0)

            print(
                f"  DUAL  flash {i+1}/{total}"
                f"   color={hex_color}   duration={duration_ms}ms"
                f"   intensity={intensity:.0f}%"
            )

            # Enqueue for both LEDs before firing
            self.hex_queue_1.put(hex_color)
            self.hex_queue_2.put(hex_color)

            # Fire both LEDs simultaneously via threads
            t_on1, t_off1 = None, None
            t_on2, t_off2 = None, None

            def fire_led1():
                nonlocal t_on1, t_off1
                t_on1, t_off1 = self.leds.led1.flash(hex_color, duration_ms, intensity)

            def fire_led2():
                nonlocal t_on2, t_off2
                t_on2, t_off2 = self.leds.led2.flash(hex_color, duration_ms, intensity)

            t1 = threading.Thread(target=fire_led1)
            t2 = threading.Thread(target=fire_led2)
            t1.start(); t2.start()
            t1.join();  t2.join()

            self.ts_queue_1.put((t_on1, t_off1))
            self.ts_queue_2.put((t_on2, t_off2))

            if i < total - 1:
                print(f"  ··· gap {self.sched['gap_ms']}ms")
                time.sleep(gap_s)

    # ── SEQUENTIAL modes (left→right and right→left) ──────────────────────────

    def _run_sequential(self, first_led: int, second_led: int):
        """
        Each round:
          first_led flashes → (optional) inner pause → second_led flashes
        If schedule["active_led"] is set, only that LED fires each round
        (single-eye mode coming from the mobile app).
        Color may cycle through schedule["colors_cycle"] across rounds.
        """
        rounds       = self.sched["rounds"]
        duration_ms  = self.sched["duration_ms"]
        intensity    = self.sched.get("intensity_pct", 100.0)
        inner_pause  = self.sched["inner_pause_ms"] / 1000.0
        gap_s        = self.sched["gap_ms"] / 1000.0
        active_led   = self.sched.get("active_led")             # 1, 2, or None
        colors_cycle = self.sched.get("colors_cycle") or [self.sched["hex"]]

        first_label  = f"LED {first_led} ({'Left ' if first_led == 1 else 'Right'})"
        second_label = f"LED {second_led} ({'Left ' if second_led == 1 else 'Right'})"

        first_hq,  first_tq  = self._queues_for(first_led)
        second_hq, second_tq = self._queues_for(second_led)

        for round_i in range(1, rounds + 1):
            hex_color = colors_cycle[(round_i - 1) % len(colors_cycle)]
            print(f"\n  Round {round_i}/{rounds}   color={hex_color}   "
                  f"intensity={intensity:.0f}%")

            # ── first side ──
            if active_led in (None, first_led):
                print(f"    {first_label}   duration={duration_ms}ms")
                first_hq.put(hex_color)
                t_on, t_off = self.leds.get_led(first_led).flash(
                    hex_color, duration_ms, intensity
                )
                first_tq.put((t_on, t_off))

            # ── inner pause (only meaningful when both sides fire) ──
            if active_led is None:
                print(f"    ··· inner pause {self.sched['inner_pause_ms']}ms")
                time.sleep(inner_pause)

            # ── second side ──
            if active_led in (None, second_led):
                print(f"    {second_label}   duration={duration_ms}ms")
                second_hq.put(hex_color)
                t_on, t_off = self.leds.get_led(second_led).flash(
                    hex_color, duration_ms, intensity
                )
                second_tq.put((t_on, t_off))

            if round_i < rounds:
                print(f"    ··· gap {self.sched['gap_ms']}ms")
                time.sleep(gap_s)

    def _queues_for(self, led_index: int):
        """Return (hex_queue, ts_queue) for the given LED index."""
        if led_index == 1:
            return self.hex_queue_1, self.ts_queue_1
        return self.hex_queue_2, self.ts_queue_2

    # ── summary ───────────────────────────────────────────────────────────────

    def _print_queue_summary(self):
        print("\n  ── Queue contents ──────────────────────────────────")

        def _show(label, hq, tq):
            hexes = list(hq.queue)
            times = list(tq.queue)
            if not hexes:
                print(f"\n  {label}  (empty)")
                return
            print(f"\n  {label}")
            for hex_color, (t_on, t_off) in zip(hexes, times):
                duration   = (t_off - t_on) * 1000
                offset_on  = self.camera.offset_of(t_on)
                offset_off = self.camera.offset_of(t_off)
                print(
                    f"    {hex_color}  "
                    f"actual={duration:.1f}ms  "
                    f"video={offset_on:.3f}s → {offset_off:.3f}s"
                )

        _show("LED 1 (Left) ", self.hex_queue_1, self.ts_queue_1)
        _show("LED 2 (Right)", self.hex_queue_2, self.ts_queue_2)
        print()
