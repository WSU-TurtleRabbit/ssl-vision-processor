#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# The operator UI reads the camera config to show driver, resolution,
# thresholds and colour references. Defaulting to config.yml showed nothing,
# because that file is the documented template with every value commented out.
VISION_CONFIG="${VISION_CONFIG:-config-robocup-lab.yml}"

if [ -x .venv/bin/python ]; then
	# Use the project's virtualenv Python to ensure the correct packages
	# (protobuf, grpc_tools, etc.) are used when starting the wrapper.
	exec .venv/bin/python -m wrapper_backend "${1:-geometry-wrapper-lab-divB.yml}" --vision-config "$VISION_CONFIG" "${@:2}"
else
	exec uv run python -m wrapper_backend "${1:-geometry-wrapper-lab-divB.yml}" --vision-config "$VISION_CONFIG" "${@:2}"
fi
