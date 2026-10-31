#!/bin/sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$DIR/tool/tools/duck-manager.py" ]; then
  ENGINE="$DIR/tool/tools/duck-manager.py"
else
  ENGINE="$DIR/../../tools/duck-manager.py"
fi
exec python3 "$ENGINE" --bundle "$DIR" menu "$@"
