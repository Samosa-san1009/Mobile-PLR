"""
server.py
---------
Lightweight HTTP server (Flask) that lets the Eyezer mobile app
drive the PLR pipeline on the Pi 4.

Lifecycle (designed for a cooler-less Pi 4 + IR cam that heats up):

    1. App POSTs /session  → config received, pipeline thread starts
    2. Orchestrator runs flashes + records IR video
    3. Camera is released and LEDs cleaned up
    4. Segmenter cuts clips
    5. EyeCropper creates one-eye model inputs
    6. ModelCaller runs bundled INT8 ONNX inference
    7. Results and logs are saved in the unique session directory
    8. HTTP server remains available for status/results and future sessions

Endpoints:
    GET  /health              → liveness
    POST /session             → start a new session (JSON body, mobile payload)
    GET  /status              → current pipeline stage + progress
    GET  /results             → session_summary.json (after completion)

Only one session at a time. Concurrent POSTs return 409.
"""

import json
import logging
import os
import re
import time
import threading
import traceback
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify

from config_adapter import adapt
from orchestrator   import Orchestrator
from segmenter      import Segmenter
from model_caller   import ModelCaller


app = Flask(__name__)

# ── shared session state (single-session server) ─────────────────────────────
STATE = {
    "stage":        "idle",      # idle | recording | segmenting | inference | done | error
    "started_at":   None,
    "ended_at":     None,
    "error":        None,
    "summary_path": None,
    "summary":      None,
    "session_id":   None,
    "session_dir":  None,
    "log_path":     None,
    "progress":     None,
}
_state_lock = threading.Lock()
_session_thread: threading.Thread = None


def _set_stage(stage, **extras):
    with _state_lock:
        STATE["stage"] = stage
        STATE.update(extras)
        print(f"  [server] stage → {stage}")


# ── pipeline runner ──────────────────────────────────────────────────────────

def _session_paths(config: dict):
    participant = config.get("participant", {})
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(participant.get("name") or "anon")).strip("_")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    session_id = f"{stamp}_{safe_name or 'anon'}"
    default_root = Path(__file__).resolve().parent.parent / "sessions"
    root = Path(os.environ.get("PLR_SESSIONS_ROOT", default_root))
    session_dir = root / session_id
    paths = {
        "session_id": session_id,
        "session_dir": session_dir,
        "recordings": session_dir / "recordings",
        "clips": session_dir / "clips",
        "cropped": session_dir / "cropped",
        "predictions": session_dir / "predictions",
        "results": session_dir / "results",
        "log": session_dir / "session.log",
    }
    for key, path in paths.items():
        if key not in {"session_id", "log"}:
            path.mkdir(parents=True, exist_ok=True)
    return paths


def _session_logger(session_id: str, log_path: Path):
    logger = logging.getLogger(f"plr.session.{session_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    return logger


def _run_pipeline(config: dict, paths: dict):
    """Run capture, segmentation, cropping, inference, and result persistence."""
    orchestrator = None
    logger = _session_logger(paths["session_id"], paths["log"])
    try:
        logger.info("Session started: %s", paths["session_id"])
        logger.info("Configuration: %s", json.dumps(config, sort_keys=True))
        _set_stage(
            "recording",
            started_at=time.time(),
            session_id=paths["session_id"],
            session_dir=str(paths["session_dir"]),
            log_path=str(paths["log"]),
            progress=None,
        )

        config["camera"]["output_dir"] = str(paths["recordings"])
        orchestrator = Orchestrator(config)
        recording_path = orchestrator.run()       # blocks: camera + flashes
        # Camera + GPIO are already released inside orchestrator.run()

        _set_stage("segmenting")

        segmenter = Segmenter(
            recording_path=recording_path,
            camera_controller=orchestrator.camera,
            hex_queue_1=orchestrator.hex_queue_1,
            ts_queue_1=orchestrator.ts_queue_1,
            hex_queue_2=orchestrator.hex_queue_2,
            ts_queue_2=orchestrator.ts_queue_2,
            clips_root=str(paths["clips"]),
            pre_flash_s=1.0,
            post_flash_s=3.0,
        )
        clip_paths = segmenter.run()

        _set_stage("inference", progress={"completed": 0, "total": sum(map(len, clip_paths.values()))})

        def report_progress(completed, total, clip_path, status):
            logger.info(
                "Inference progress %s/%s: %s (%s)",
                completed, total, clip_path, status,
            )
            _set_stage(
                "inference",
                progress={
                    "completed": completed,
                    "total": total,
                    "clip": clip_path,
                    "clip_status": status,
                },
            )

        caller = ModelCaller(
            clips_root=str(paths["clips"]),
            results_dir=str(paths["results"]),
            cropped_dir=str(paths["cropped"]),
            predictions_dir=str(paths["predictions"]),
            session_id=paths["session_id"],
            progress_callback=report_progress,
            logger=logger,
        )
        summary = caller.run(clip_paths, segmenter.clip_metadata)

        summary_path = paths["results"] / "session_summary.json"
        metadata_path = paths["session_dir"] / "session_metadata.json"
        metadata_path.write_text(json.dumps({
            "session_id": paths["session_id"],
            "participant": config.get("participant", {}),
            "config": config,
            "summary_path": str(summary_path),
            "log_path": str(paths["log"]),
        }, indent=2), encoding="utf-8")

        _set_stage(
            "done",
            ended_at=time.time(),
            summary_path=str(summary_path),
            summary=summary,
            session_status=summary["status"],
            progress=None,
        )
        logger.info("Session completed with status=%s", summary["status"])

    except Exception as e:
        traceback.print_exc()
        logger.exception("Session pipeline failed")
        try:
            if orchestrator is not None:
                orchestrator.leds.cleanup()
                orchestrator.camera.stop()
        except Exception:
            pass
        _set_stage("error", error=str(e), ended_at=time.time())
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


# ── endpoints ────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "stage": STATE["stage"]})


@app.route("/session", methods=["POST"])
def start_session():
    global _session_thread

    with _state_lock:
        if STATE["stage"] not in ("idle", "done", "error"):
            return jsonify({
                "error":  "session already running",
                "stage":  STATE["stage"],
            }), 409

    payload = request.get_json(silent=True) or {}
    try:
        config = adapt(payload)
    except Exception as e:
        return jsonify({"error": f"bad payload: {e}"}), 400

    paths = _session_paths(config)

    # Reset state
    with _state_lock:
        STATE.update({
            "stage": "starting",
            "started_at": None,
            "ended_at":   None,
            "error":      None,
            "summary":    None,
            "summary_path": None,
            "session_id": paths["session_id"],
            "session_dir": str(paths["session_dir"]),
            "log_path": str(paths["log"]),
            "progress": None,
        })

    _session_thread = threading.Thread(
        target=_run_pipeline, args=(config, paths), daemon=True
    )
    _session_thread.start()

    return jsonify({"ok": True, "stage": "starting"}), 202


@app.route("/status", methods=["GET"])
def status():
    with _state_lock:
        payload = {key: value for key, value in STATE.items() if key != "summary"}
        return jsonify(payload)


@app.route("/results", methods=["GET"])
def results():
    summary_path = STATE.get("summary_path")
    if not summary_path or not os.path.exists(summary_path):
        return jsonify({"error": "not ready", "stage": STATE["stage"]}), 404
    with open(summary_path) as f:
        return jsonify(json.load(f))


# ── entry point ──────────────────────────────────────────────────────────────

def main():
    # The server remains alive and responsive while ONNX inference runs.
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
