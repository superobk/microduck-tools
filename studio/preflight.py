"""Read-only checks for the delivered Studio runtime, before account/service changes."""

import hashlib
import grp
import importlib.util
import json
from pathlib import Path
import platform
import re
import shutil
import sys


def check(assets, wheels, require_target=True, lock=None):
    from .manage import verify_assets

    checks = []

    def row(name, ok, detail):
        checks.append(dict(item=name, ok=bool(ok), detail=detail))

    target = platform.system() == "Linux" and platform.machine() in ("arm64", "aarch64")
    row(
        "目标系统",
        target if require_target else True,
        platform.system() + " / " + platform.machine(),
    )
    row(
        "Python ABI",
        sys.version_info[:2] == (3, 11) if require_target else True,
        platform.python_version() + "；板端离线wheel要求3.11",
    )
    if require_target:
        for name in ("systemctl", "systemd-analyze", "openssl", "useradd", "usermod"):
            row(name, shutil.which(name) is not None, shutil.which(name) or "未安装")
        try:
            grp.getgrnam("robot")
            row("原生socket访问组", True, "robot组存在；不放宽Unix socket权限")
        except KeyError:
            row("原生socket访问组", False, "缺少robot组，请先核对原生部署")
        row(
            "venv / ensurepip",
            all(importlib.util.find_spec(x) is not None for x in ("venv", "ensurepip")),
            "只检查模块，不创建环境",
        )
    try:
        manifest = verify_assets(Path(assets))
        required = {
            "assets/three/three.module.js",
            "assets/three/three.core.js",
            "assets/transformers/transformers.min.js",
            "assets/transformers/ort-wasm-simd-threaded.jsep.mjs",
            "assets/transformers/ort-wasm-simd-threaded.jsep.wasm",
        }
        if not required.issubset({r["path"] for r in manifest["files"]}):
            raise ValueError("浏览器/ORT必需文件不完整")
        row(
            "浏览器与模型资产",
            True,
            "逐文件SHA一致；模型就绪=" + str(manifest.get("model_ready", False)),
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        row("浏览器与模型资产", False, str(e))
    try:
        lock = (
            Path(lock)
            if lock is not None
            else Path(__file__).with_name("requirements-linux-arm64.lock")
        )
        available = {}
        for path in Path(wheels).glob("*.whl"):
            if path.is_symlink():
                raise ValueError("wheel不能是链接")
            name, version = path.name.split("-")[:2]
            available.setdefault((name.lower().replace("_", "-"), version), []).append(
                path
            )
        count = 0
        for line in lock.read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(
                r"([\w.-]+)==([\w.+-]+) --hash=sha256:([0-9a-f]{64})", line
            )
            if not match:
                raise ValueError("锁文件格式不支持")
            name, version, expected = match.groups()
            candidates = available.get((name.lower().replace("_", "-"), version), [])
            if not any(
                hashlib.sha256(p.read_bytes()).hexdigest() == expected
                for p in candidates
            ):
                raise ValueError("缺失或哈希不符：" + name + "==" + version)
            count += 1
        row(
            "离线依赖",
            True,
            str(count) + "个固定wheel哈希一致；目标动态加载在setup内另验",
        )
    except (OSError, ValueError) as e:
        row("离线依赖", False, str(e))
    return dict(
        ok=all(c["ok"] for c in checks),
        checks=checks,
        side_effects=False,
        hardware="NOT_RUN",
    )
