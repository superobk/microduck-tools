#!/bin/sh
# Run locally as root, or through ssh -t. Never put this command in a log collector.
set -eu
exec /usr/bin/python3 -I - <<'PY'
import json
import os
from pathlib import Path
import subprocess
if os.geteuid() != 0:
    raise SystemExit("请以root或sudo生成访问码")
state = Path("/var/lib/microduck-studio-install/install.json")
saved = json.loads(state.read_text())
release = Path(saved["release"])
base = Path("/opt/microduck-tools/studio/releases")
if saved.get("phase") != "installed" or release.is_symlink() or not release.resolve().is_relative_to(base):
    raise SystemExit("安装登记未就绪，请先查看Studio状态")
# Use the installed venv and immutable release, not a hard-coded Python minor/source checkout.
code = "import sys;sys.path.insert(0,sys.argv[1]);from studio.manage import main;sys.argv=['studio','pair','--apply','--confirm','EXECUTE'];main()"
cfg = json.loads(Path("/etc/microduck-studio/config.json").read_text())
print("访问页面：" + cfg["public_origin"] + "/studio/", flush=True)
subprocess.run([str(release / "venv/bin/python"), "-I", "-c", code, str(release)], check=True)
PY
