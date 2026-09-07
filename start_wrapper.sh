#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
	# Use the project's virtualenv Python to ensure the correct packages
	# (protobuf, grpc_tools, etc.) are used when starting the wrapper.
	exec .venv/bin/python -m wrapper_backend "${1:-geometry-wrapper-lab-divB.yml}" "${@:2}"
else
	exec uv run python -m wrapper_backend "${1:-geometry-wrapper-lab-divB.yml}" "${@:2}"
fi
