#!/bin/sh
set -eu
cd /opt/microduck-tools/releases/0.6.0-py313-board1-20261103
exec /usr/bin/python3.13 -m studio.manage pair --apply --confirm EXECUTE
