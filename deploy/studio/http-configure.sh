#!/bin/sh
# Configure only an already installed Studio; leave it stopped for an explicit start.
set -eu
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: sudo ./deploy/studio/http-configure.sh BOARD_IPV4 [PORT]" >&2
    exit 2
fi
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec "$ROOT/tools/studio.sh" configure --transport lan-http --server-ip "$1" --port "${2:-8790}" --apply --confirm EXECUTE
