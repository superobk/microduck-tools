#!/usr/bin/env python3
"""Managed transport launcher for both frozen and new Studio releases."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("studio_network", Path(__file__).with_name("network.py"))
    network = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(network)
    cfg = json.loads(args.config.read_text())
    if cfg.get("demo", False) is not False:
        raise ValueError("已安装服务不允许切换模拟数据")
    context = network.tls_context(cfg)
    sys.path.insert(0, str(args.release.resolve()))
    from studio.server import create_app
    from aiohttp import web
    print("Studio LIVE / " + network.validate(cfg) + " / " + cfg["public_origin"], flush=True)
    web.run_app(create_app(cfg), host=cfg["listen"], port=cfg["port"],
                ssl_context=context, access_log=None)


if __name__ == "__main__":
    main()
