#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=python3
if command -v python3.11 >/dev/null 2>&1; then PYTHON=python3.11; fi
exec "$PYTHON" "$ROOT/installer/assistant.py" --kit "$ROOT" "$@"
