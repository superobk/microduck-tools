#!/bin/sh
# Own lifecycle only; never calls the legacy manager or changes daemon/current.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="$ROOT/venv/bin/python"
if [ ! -x "$PYTHON" ]; then PYTHON=python3; fi
exec "$PYTHON" -m studio.manage "$@"
