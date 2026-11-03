#!/usr/bin/env python3
"""Live read-only Studio on loopback HTTP, accessed only via an SSH forward."""
import argparse
import json
from pathlib import Path
import sys


def validate_config(cfg):
    port=cfg.get("port")
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError("HTTP port must be1024..65535")
    if cfg.get("listen")!="127.0.0.1" or cfg.get("public_origin")!=f"http://127.0.0.1:{port}":
        raise ValueError("SSH HTTP mode requires exact127.0.0.1 bind and origin")
    if cfg.get("transport")!="ssh-loopback-http" or cfg.get("demo",False) is not False:
        raise ValueError("Explicit live SSH HTTP transport is required")
    if cfg.get("controls_enabled") is not False or cfg.get("camera_enabled") is not False:
        raise ValueError("SSH HTTP display mode keeps controls and Camera disabled")
    return cfg


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--release",type=Path,required=True)
    args=parser.parse_args()
    cfg=validate_config(json.loads(args.config.read_text()))
    sys.path.insert(0,str(args.release.resolve()))
    from studio.server import create_app
    from aiohttp import web
    print("Studio LIVE / SSH LOOPBACK HTTP / READ ONLY "+cfg["public_origin"],flush=True)
    web.run_app(create_app(cfg),host="127.0.0.1",port=cfg["port"],
                ssl_context=None,access_log=None)


if __name__=="__main__":
    main()
