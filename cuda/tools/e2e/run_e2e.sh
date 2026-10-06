#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# End-to-end run of one vision_processor binary on a video file with geometry from a fresh
# wrapper backend on isolated ports; the published detections are recorded to <out>/detections.jsonl.
# Compare two runs with compare_detections.py.
#
#   VIDEO=/path/real.avi LINE_CORNERS='[[3,370],[112,70],[570,61],[747,360]]' \
#     cuda/tools/e2e/run_e2e.sh build-cuda/vision_processor /tmp/e2e/cuda
#
# Environment (defaults in brackets):
#   VIDEO (required), LINE_CORNERS (required, image pixels of the output size, field corner order)
#   OUTPUT_WIDTH/OUTPUT_HEIGHT [768/432], CAMERA_HEIGHT [2000], ROBOT_HEIGHTS [<repo>/robot-heights.yml]
#   GEOMETRY [<repo>/geometry-wrapper-lab-divB.yml], FIELD_LENGTH/FIELD_WIDTH [from GEOMETRY]
#   VISION_PORT [10998], GC_PORT [10994], HTTP_PORT [8791]  -- never use the default 10006/10003 ports
#   BACKEND_REPO [<repo>, checkout with generated wrapper_backend/proto], PYTHON [<BACKEND_REPO>/.venv/bin/python]
# The backend keeps the first calibration it receives, so every run starts its own backend.
set -u
BIN=$(realpath "$1"); OUT=$(realpath -m "$2")
REPO=$(realpath "$(dirname "$0")/../../..")
BACKEND_REPO=${BACKEND_REPO:-$REPO}
PYTHON=${PYTHON:-$BACKEND_REPO/.venv/bin/python}
VISION_PORT=${VISION_PORT:-10998}; GC_PORT=${GC_PORT:-10994}; HTTP_PORT=${HTTP_PORT:-8791}
if [ "$VISION_PORT" = 10006 ] || [ "$GC_PORT" = 10003 ]; then echo "Refusing to use the default SSL ports"; exit 1; fi

mkdir -p "$OUT/img" "$OUT/pyproto"
protoc -I "$REPO" --python_out="$OUT/pyproto" "$REPO"/proto/*.proto
cp "${ROBOT_HEIGHTS:-$REPO/robot-heights.yml}" "$OUT/robot-heights.yml"
GEOMETRY=${GEOMETRY:-$REPO/geometry-wrapper-lab-divB.yml}
sed -e "${FIELD_LENGTH:+s/field_length: .*/field_length: $FIELD_LENGTH/}" -e "${FIELD_WIDTH:+s/field_width: .*/field_width: $FIELD_WIDTH/}" "$GEOMETRY" > "$OUT/geometry.yml"
cat > "$OUT/config.yml" <<EOF
cam_id: 0
bot_heights_file: robot-heights.yml
camera:
  driver: OPENCV
  path: $(realpath "$VIDEO")
  output_width: ${OUTPUT_WIDTH:-768}
  output_height: ${OUTPUT_HEIGHT:-432}
geometry:
  camera_amount: 1
  camera_height: ${CAMERA_HEIGHT:-2000.0}
  refinement: false
  line_corners: $LINE_CORNERS
network:
  gc_ip: "224.5.23.1"
  gc_port: $GC_PORT
  vision_ip: "224.5.23.2"
  vision_port: $VISION_PORT
stream:
  active: false
debug:
  wait_for_geometry: true
EOF

cd "$OUT"
PYTHONPATH=$BACKEND_REPO "$PYTHON" -m wrapper_backend "$OUT/geometry.yml" --port "$HTTP_PORT" --vision-port "$VISION_PORT" --vision-config "$OUT/config.yml" --frontend-dir "$OUT/nofrontend" --vision-binary /nonexistent > backend.log 2>&1 &
BACKEND=$!
python3 "$REPO/cuda/tools/e2e/capture_detections.py" "$OUT/pyproto" 224.5.23.2 "$VISION_PORT" "$OUT/detections.jsonl" 15 > capture.log 2>&1 &
CAPTURE=$!
sleep 3
if ! kill -0 $BACKEND 2>/dev/null; then echo "backend failed:"; tail -3 backend.log; kill $CAPTURE; exit 1; fi

start=$(date +%s.%N)
timeout 900 "$BIN" config.yml > vp.log 2>&1
echo "vision_processor rc=$? wall=$(echo "$(date +%s.%N) - $start" | bc)s"
sleep 2
kill $CAPTURE $BACKEND 2>/dev/null
wait $CAPTURE $BACKEND 2>/dev/null
echo "frames with detections: $(wc -l < detections.jsonl)"
