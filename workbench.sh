#!/bin/sh
# Independent component; never invokes the robot upgrade manager.
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
if [ -x .venv-workbench/bin/python ]; then
    WB_PYTHON=.venv-workbench/bin/python
elif command -v python3.11 >/dev/null 2>&1; then
    WB_PYTHON=python3.11
else
    WB_PYTHON=python3
fi
exec "$WB_PYTHON" -B -m servo_workbench "$@"
