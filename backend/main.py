"""
main.py
-------
Entry point for the PLR Pupillometry system.

Run:
    python main.py

Flow:
    1. Terminal wizard → collect config
    2. Orchestrator    → camera + LED flashes → 4 queues filled
    3. Segmenter       → cut full video into per-flash clips
    4. ModelCaller     → submit clips to RESTful/PuReST model
"""

import sys
import traceback

from config_wizard   import run_wizard
from orchestrator    import Orchestrator
from segmenter       import Segmenter
from model_caller    import ModelCaller


def main():
    # ── 1. Configure ──────────────────────────────────────────────────────────
    try:
        config = run_wizard()
    except KeyboardInterrupt:
        print("\n\n  Interrupted during configuration. Exiting.")
        sys.exit(0)

    # ── 2. Flash session ──────────────────────────────────────────────────────
    orchestrator = Orchestrator(config)

    try:
        recording_path = orchestrator.run()
    except KeyboardInterrupt:
        print("\n\n  Interrupted during recording. Cleaning up GPIO...")
        orchestrator.leds.cleanup()
        orchestrator.camera.stop()
        sys.exit(1)
    except Exception:
        print("\n  Error during flash session:")
        traceback.print_exc()
        orchestrator.leds.cleanup()
        sys.exit(1)

    # ── 3. Segment video ──────────────────────────────────────────────────────
    print("\n  ── Segmenting video ────────────────────────────────")

    segmenter = Segmenter(
        recording_path=recording_path,
        camera_controller=orchestrator.camera,
        hex_queue_1=orchestrator.hex_queue_1,
        ts_queue_1=orchestrator.ts_queue_1,
        hex_queue_2=orchestrator.hex_queue_2,
        ts_queue_2=orchestrator.ts_queue_2,
        clips_root="clips",
        padding_s=0.1,        # 100ms padding before and after each flash
    )

    try:
        clip_paths = segmenter.run()
    except Exception:
        print("\n  Error during segmentation:")
        traceback.print_exc()
        sys.exit(1)

    # ── 4. Model inference ────────────────────────────────────────────────────
    print("\n  ── Running model inference ─────────────────────────")

    caller = ModelCaller(
        model_url="http://localhost:8000/analyze",   # adjust to your model's endpoint
        clips_root="clips",
        results_dir="results",
    )

    try:
        results = caller.run(clip_paths)
    except Exception:
        print("\n  Error during model inference:")
        traceback.print_exc()
        sys.exit(1)

    # ── Done ──────────────────────────────────────────────────────────────────
    print("\n  ╔══════════════════════════════════════════════════╗")
    print("  ║             Session complete ✓                   ║")
    print("  ╚══════════════════════════════════════════════════╝")

    total_clips = sum(len(v) for v in clip_paths.values())
    print(f"\n  Full recording : {recording_path}")
    print(f"  Clips saved    : {total_clips}  (clips/led1/  +  clips/led2/)")
    print(f"  Results        : results/session_summary.json\n")


if __name__ == "__main__":
    main()
