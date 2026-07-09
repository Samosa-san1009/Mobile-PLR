#!/usr/bin/env bash
# test_pypupilext_build.sh
# ---------------------------------------------------------------
# Run this ON THE PI. Attempts to build & smoke-test PyPupilEXT and
# reports clearly whether it worked, so you don't have to sit and
# watch a multi-hour build to find out.
#
# LICENSE NOTE, READ FIRST: PyPupilEXT and the pupil-detection
# algorithms it wraps (PuRe, PuReST, ElSe, ExCuSe, Starburst,
# Swirski2D) are GPL-3.0, and the pupil-detection functionality
# itself is licensed by the upstream project for ACADEMIC /
# NON-COMMERCIAL USE ONLY. Confirm that's acceptable for your use
# case before investing time in this -- your existing PuRe/PuReST
# binary is built from the same algorithm family and license terms,
# so check that too if you haven't. https://github.com/openPupil/PyPupilEXT
#
# EXPECTATIONS: no prebuilt ARM/aarch64 wheel exists as of writing --
# this compiles OpenCV, Eigen, Boost, TBB, Ceres-Solver, glog, gflags
# etc. from source via vcpkg. Budget several GB of disk and a long
# compile time on a Pi 4. This script does NOT try to shortcut that --
# it just runs it and tells you clearly whether it succeeded.
# ---------------------------------------------------------------
set -euo pipefail

LOG_DIR="$HOME/pypupilext_build_test"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/build_$(date +%Y%m%d_%H%M%S).log"

echo "== PyPupilEXT build test =="
echo "Log: $LOG_FILE"
echo "Started: $(date)"
df -h "$HOME" | tee -a "$LOG_FILE"
echo ""

START_TS=$(date +%s)

fail() {
    echo ""
    echo "❌ FAILED at step: $1"
    echo "   See $LOG_FILE for the full build log."
    exit 1
}

echo "-- Checking prerequisites --" | tee -a "$LOG_FILE"
for tool in git cmake g++ python3 pip3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing required tool: $tool" | tee -a "$LOG_FILE"
        fail "prerequisite check ($tool missing)"
    fi
done
echo "All prerequisites present." | tee -a "$LOG_FILE"

echo "-- Cloning PyPupilEXT --" | tee -a "$LOG_FILE"
cd "$LOG_DIR"
if [ ! -d "PyPupilEXT" ]; then
    git clone --recursive https://github.com/openPupil/PyPupilEXT.git >>"$LOG_FILE" 2>&1 \
        || fail "git clone"
fi
cd PyPupilEXT

echo "-- Setting up a clean venv --" | tee -a "$LOG_FILE"
python3 -m venv "$LOG_DIR/venv" >>"$LOG_FILE" 2>&1 || fail "venv creation"
# shellcheck disable=SC1091
source "$LOG_DIR/venv/bin/activate"
pip install --upgrade pip >>"$LOG_FILE" 2>&1 || fail "pip upgrade"

echo "-- Building (this is the long part -- go make coffee) --" | tee -a "$LOG_FILE"
BUILD_START=$(date +%s)
pip install -v . >>"$LOG_FILE" 2>&1 || fail "pip install (build)"
BUILD_END=$(date +%s)
echo "Build step took $((BUILD_END - BUILD_START))s" | tee -a "$LOG_FILE"

echo "-- Running smoke test (PuRe on a synthetic image) --" | tee -a "$LOG_FILE"
python3 - <<'PYEOF' >>"$LOG_FILE" 2>&1 || fail "smoke test import/run"
import numpy as np
import cv2
import pypupilext as pp

img = (np.random.rand(240, 320) * 255).astype(np.uint8)
cv2.circle(img, (160, 120), 25, 30, -1)  # synthetic dark "pupil" blob

pure = pp.PuRe()
result = pure.runWithConfidence(img)
print("PuRe ran OK. Result:", result)
PYEOF

END_TS=$(date +%s)
echo ""
echo "✅ SUCCESS — PyPupilEXT built and ran on this machine."
echo "   Total time: $((END_TS - START_TS))s"
du -sh "$LOG_DIR" | tee -a "$LOG_FILE"
echo ""
echo "If this is worth pursuing given the non-commercial license note"
echo "above, the next step is swapping purest_caller.py's subprocess"
echo "+ CSV round-trip for direct in-process pp.PuRe()/pp.PuReST() calls"
echo "-- that also unlocks frame-by-frame detection for the live"
echo "/preview bounding box (see camera_service.set_frame_annotator)."
