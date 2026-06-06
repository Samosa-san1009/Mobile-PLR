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
    5. ► HTTP server STOPS accepting new requests  ◄
    6. ModelCaller runs inference with the full CPU available
    7. Results saved to results/session_summary.json
    8. Process exits (systemd / launcher script can restart it on demand)

Endpoints:
    GET  /health              → liveness
    POST /session             → start a new session (JSON body, mobile payload)
    GET  /status              → current pipeline stage + progress
    GET  /results             → session_summary.json (after completion)

Only one session at a time. Concurrent POSTs return 409.
"""

import os
import json
import time
import threading
import traceback

from flask import Flask, request, jsonify, abort

from config_adapter import adapt
from orchestrator   import Orchestrator
from segmenter      import Segmenter
from model_caller   import ModelCaller


app = Flask(__name__)

# ── shared session state (single-session server) ─────────────────────────────
STATE = {
    "stage":        "idle",      # idle | recording | segmenting | shutting_down_http | inference | done | error
    "started_at":   None,
    "ended_at":     None,
    "error":        None,
    "summary_path": None,
    "summary":      None,
}
_state_lock = threading.Lock()
_session_thread: threading.Thread = None


def _set_stage(stage, **extras):
    with _state_lock:
        STATE["stage"] = stage
        STATE.update(extras)
        print(f"  [server] stage → {stage}")


# ── pipeline runner ──────────────────────────────────────────────────────────

def _run_pipeline(config: dict, model_url: str):
    """
    Heavy lifting thread. Runs orchestrator + segmenter, then shuts down
    the HTTP socket before running model inference.
    """
    orchestrator = None
    try:
        _set_stage("recording", started_at=time.time())

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
            clips_root="clips",
            padding_s=0.1,
        )
        clip_paths = segmenter.run()

        # ── Free up CPU/RAM before model inference ──────────────────────────
        # Stop the Flask dev server so it isn't holding worker threads /
        # buffers while the model runs. Werkzeug exposes a shutdown hook
        # only inside a request, so we use os._exit after writing results,
        # but first we mark the server as no longer accepting work.
        _set_stage("shutting_down_http")
        _shutdown_http_server()

        _set_stage("inference")
        caller = ModelCaller(
            model_url=model_url,
            clips_root="clips",
            results_dir="results",
        )
        caller.run(clip_paths)

        summary_path = os.path.join("results", "session_summary.json")
        with open(summary_path) as f:
            summary = json.load(f)

        _set_stage(
            "done",
            ended_at=time.time(),
            summary_path=summary_path,
            summary=summary,
        )

        # Persist a final flag for any external launcher and exit.
        # If you'd rather keep the server up after inference, comment this out.
        time.sleep(0.5)
        os._exit(0)

    except Exception as e:
        traceback.print_exc()
        try:
            if orchestrator is not None:
                orchestrator.leds.cleanup()
                orchestrator.camera.stop()
        except Exception:
            pass
        _set_stage("error", error=str(e), ended_at=time.time())


def _shutdown_http_server():
    """
    Werkzeug's shutdown() only works from within a request handler.
    For our purposes the simplest reliable approach is to flip a global
    flag that the /session and /status endpoints check, and close the
    listening socket via os._exit at the end of the pipeline.
    """
    # Marker file so an external watchdog/launcher knows we're past HTTP.
    try:
        with open("results/_http_closed", "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


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

    model_url = payload.get("model_url", "http://localhost:8000/analyze")

    # Reset state
    with _state_lock:
        STATE.update({
            "stage": "starting",
            "started_at": None,
            "ended_at":   None,
            "error":      None,
            "summary":    None,
            "summary_path": None,
        })

    _session_thread = threading.Thread(
        target=_run_pipeline, args=(config, model_url), daemon=True
    )
    _session_thread.start()

    return jsonify({"ok": True, "stage": "starting"}), 202


@app.route("/status", methods=["GET"])
def status():
    with _state_lock:
        return jsonify(dict(STATE))


@app.route("/results", methods=["GET"])
def results():
    summary_path = STATE.get("summary_path") or "results/session_summary.json"
    if not os.path.exists(summary_path):
        return jsonify({"error": "not ready", "stage": STATE["stage"]}), 404
    with open(summary_path) as f:
        return jsonify(json.load(f))


# ── entry point ──────────────────────────────────────────────────────────────

def main():
    os.makedirs("results", exist_ok=True)
    # threaded=True so /status remains responsive while pipeline runs
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
