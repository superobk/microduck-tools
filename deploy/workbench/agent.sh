#!/bin/sh
set -eu
[ "$#" -eq 1 ] && [ "$1" = "--stdio" ] || { echo 'only --stdio is allowed' >&2; exit 2; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
# Isolated mode excludes PYTHONPATH, user packages and the caller's working directory.
exec "$ROOT/.venv-workbench/bin/python" -I -B -c 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));sys.argv=["servo-agent","agent","--stdio"];runpy.run_module("servo_workbench",run_name="__main__")' "$ROOT"
