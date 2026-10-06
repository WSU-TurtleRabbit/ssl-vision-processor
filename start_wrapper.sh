#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Usage: ./start_wrapper.sh [geometry.yml] [wrapper flags...]
#   e.g. ./start_wrapper.sh geometry-wrapper-lab-divB.yml --vision-config config-pi-cam.yml
# The geometry file must use the wrapper format (`optional_field_lines:`);
# the legacy geom_publisher files (`default_lines:`) are rejected.
#
# vision_processor supervision (see wrapper_backend/README.md):
#   --start-vision           start vision_processor with the backend (and stop it
#                            on exit); without it, use Start in the UI or
#                            POST /api/vision/start
#   --vision-binary PATH     default: build/vision_processor of this repo
#   --camera-token-file F    Pi camera remote-control token (default: .camera-token
#                            in this repo if present; fallback $PI_CAMERA_TOKEN)
#   --vision-cwd DIR         default: the --vision-config file's directory (img/
#                            and bot_heights_file resolve there)
#   e.g. ./start_wrapper.sh geometry-wrapper-lab-divB.yml --vision-config config-pi-cam.yml --start-vision
if [ -x .venv/bin/python ]; then
	# Use the project's virtualenv Python to ensure the correct packages
	# (protobuf, grpc_tools, etc.) are used when starting the wrapper.
	exec .venv/bin/python -m wrapper_backend "${1:-geometry-wrapper-lab-divB.yml}" "${@:2}"
else
	exec uv run python -m wrapper_backend "${1:-geometry-wrapper-lab-divB.yml}" "${@:2}"
fi
