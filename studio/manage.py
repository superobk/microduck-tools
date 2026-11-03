"""Standalone add-on lifecycle. Never installs, starts or replaces robotd/legacy Tools."""

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from . import VERSION
from .helper import atomic, Handoff, UNITS, call
from .ipc import ROOT
from .python_runtime import supported_abi, requirements_lock

BASE = Path("/opt/microduck-tools/studio")
STATE = Path("/var/lib/microduck-studio-install")
CONFIG = Path("/etc/microduck-studio/config.json")
SERVICES = ("microduck-studio.service", "microduck-studio-helper.service")


def run(*args):
    try:
        return subprocess.run(
            [str(a) for a in args],
            check=True,
            text=True,
            capture_output=True,
            timeout=180,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()[-2000:]
        raise RuntimeError(
            str(args[0]) + " 执行失败（" + str(error.returncode) + "）：" + detail
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            str(args[0]) + " 等待超过180秒；先查看status和未完成登记，不重复安装。"
        ) from error


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_assets(root):
    root = Path(root)
    manifest = json.loads((root / "assets/asset-manifest.json").read_text())
    if not manifest.get("ready"):
        raise RuntimeError("3D依赖包未准备好")
    for row in manifest["files"]:
        path = root / row["path"]
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or not path.is_file()
            or digest(path) != row["sha256"]
        ):
            raise RuntimeError("资产哈希不匹配: " + row["path"])
    return manifest


class Journal:
    """Write intent before replace; rollback accepts only the recorded before or after bytes."""

    def __init__(self, state=STATE):
        self.state = state
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (
            state.is_symlink()
            or state.stat().st_uid != os.geteuid()
            or state.stat().st_mode & 0o077
        ):
            raise RuntimeError("安装登记目录不可信")
        self.path = state / "install.json"
        self.data = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else dict(schema=1, phase="new", files=[])
        )

    def save(self):
        atomic(self.path, json.dumps(self.data, indent=2).encode())

    def write(self, path, raw, mode=0o644, uid=0, gid=0):
        path = Path(path)
        if path.is_symlink() or path.exists():
            raise RuntimeError("拒绝覆盖已有文件，请先status/rollback: " + str(path))
        if any(p.is_symlink() for p in path.parents):
            raise RuntimeError("父目录含symlink: " + str(path))
        row = dict(
            path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(),
            mode=mode,
            uid=uid,
            gid=gid,
        )
        self.data["files"].append(row)
        self.save()
        atomic(path, raw)
        os.chown(path, uid, gid)
        os.chmod(path, mode)

    def verify(self):
        for row in self.data["files"]:
            p = Path(row["path"])
            if p.is_symlink():
                raise RuntimeError("登记文件被替换为链接: " + str(p))
            if not p.exists() and self.data["phase"] == "installed":
                raise RuntimeError("登记文件缺失: " + str(p))
            if p.exists():
                st = p.stat()
                if digest(p) not in (
                    {row["sha256"], row.get("before_sha")}
                    if self.data["phase"] == "configuring"
                    else {row["sha256"]}
                ) or (
                    self.data["phase"] == "installed"
                    and (
                        st.st_uid != row["uid"]
                        or st.st_gid != row["gid"]
                        or st.st_mode & 0o777 != row["mode"]
                    )
                ):
                    raise RuntimeError("外部修改，保留文件并停止回退: " + str(p))

    def update_config(self, path, raw):
        self.verify()
        row = next(r for r in self.data["files"] if r["path"] == str(path))
        self.data["phase"] = "configuring"
        row["before_sha"] = row["sha256"]
        row["sha256"] = hashlib.sha256(raw).hexdigest()
        self.save()
        atomic(path, raw)
        os.chown(path, row["uid"], row["gid"])
        os.chmod(path, row["mode"])
        self.data["phase"] = "installed"
        row.pop("before_sha")
        self.save()

    def rollback(self):
        self.verify()
        for row in reversed(self.data["files"]):
            p = Path(row["path"])
            p.unlink(missing_ok=True)
            if p.parent.exists():
                fd = os.open(p.parent, os.O_RDONLY)
                os.fsync(fd)
                os.close(fd)
        self.data["phase"] = "rolled_back"
        self.save()


def native_gate():
    if (
        os.geteuid() != 0
        or platform.system() != "Linux"
        or platform.machine() not in ("aarch64", "arm64")
    ):
        raise RuntimeError("真实安装仅Linux ARM64 root；本机请用demo")
    if not supported_abi():
        raise RuntimeError(
            "本交付支持CPython3.11或3.13 ARM64普通GIL版本；需要匹配的离线wheel和锁文件"
        )
    for tool in ("systemctl", "openssl", "useradd", "usermod"):
        shutil.which(tool) or (_ for _ in ()).throw(RuntimeError("缺少 " + tool))


def cfg_read():
    return json.loads(CONFIG.read_text())


def issue_pair(state, owner=None):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    code = secrets.token_urlsafe(12)
    atomic(
        state / "pairing.json",
        json.dumps(
            dict(
                sha256=hashlib.sha256(code.encode()).hexdigest(),
                expires=time.time() + 300,
                used=False,
            )
        ).encode(),
    )
    if owner:
        os.chown(state, owner.pw_uid, owner.pw_gid)
        os.chown(state / "pairing.json", owner.pw_uid, owner.pw_gid)
    print("一次性登录码（5分钟有效，请勿写入登记或截图）：" + code, flush=True)


def verify_release(release):
    release = Path(release)
    if release.is_symlink():
        raise RuntimeError("版本目录变为链接")
    saved = json.loads((release / "STUDIO-RELEASE.json").read_text())
    for row in saved["files"]:
        path = release / row["path"]
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(release.resolve())
            or not path.is_file()
            or digest(path) != row["sha256"]
        ):
            raise RuntimeError("版本文件哈希不一致: " + row["path"])
    return saved


def prepare_release(journal, assets, wheels):
    # Release is immutable after completion; incomplete copies are never made current.
    release = BASE / "releases" / VERSION
    if release.exists():
        stamp = release / "STUDIO-RELEASE.json"
        if not stamp.is_file():
            raise RuntimeError("保留了中断的版本目录；recover会归档它，然后重新setup")
        saved = verify_release(release)
        for row in saved["source"]:
            if digest(ROOT / row["path"]) != row["sha256"]:
                raise RuntimeError("同版本源码不同，不重用既有版本目录")
        verify_assets(release / "runtime-assets")
        journal.data["release"] = str(release)
        journal.data["release_ready"] = True
        journal.save()
        return release
    release.mkdir(parents=True)
    journal.data["release"] = str(release)
    journal.save()
    selections = [
        "studio",
        "calibration/common",
        "calibration/integration-0.16.3",
        "deploy/manual-upgrade/inputs.json",
        "deploy/studio",
        "tools/studio.sh",
        "servo_workbench/__init__.py",
        "servo_workbench/profiles.py",
        "servo_workbench/reports.py",
    ]
    for rel in selections:
        src = ROOT / rel
        dst = release / rel
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "node_modules", "vendor", "models"
                ),
            )
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    shutil.copytree(assets, release / "runtime-assets")
    run(sys.executable, "-m", "venv", release / "venv")
    run(
        release / "venv/bin/python",
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        wheels,
        "--require-hashes",
        "-r",
        requirements_lock(),
    )
    run(release / "venv/bin/python", "-c", "import aiohttp, dbus_next, studio.server")
    files = [
        p
        for p in release.rglob("*")
        if p.is_file()
        and "venv" not in p.relative_to(release).parts
        and "__pycache__" not in p.parts
    ]
    rows = [
        dict(path=str(p.relative_to(release)), sha256=digest(p)) for p in sorted(files)
    ]
    source = [row for row in rows if (ROOT / row["path"]).is_file()]
    atomic(
        release / "STUDIO-RELEASE.json",
        json.dumps(dict(files=rows, source=source)).encode(),
    )
    journal.data["release_ready"] = True
    journal.save()
    return release


def setup(args, journal):
    if journal.data["phase"] not in ("new", "rolled_back"):
        raise RuntimeError("已有安装/中断登记；先status，再recover或rollback")
    ip = ipaddress.ip_address(args.server_ip)
    if ip.is_unspecified or ip.is_multicast or str(ip) == "255.255.255.255":
        raise RuntimeError("需要实际固定IPv4，不能使用监听通配/组播/广播地址")
    address = str(ip)
    if ":" in address:
        raise RuntimeError("首版部署仅审阅IPv4 HTTPS地址")
    if not args.assets or not args.wheels:
        raise RuntimeError("setup需要--assets和--wheels离线目录")
    from .preflight import check

    preflight = check(args.assets, args.wheels)
    if not preflight["ok"]:
        raise RuntimeError(
            "安装前检查失败："
            + "；".join(
                c["item"] + "：" + c["detail"]
                for c in preflight["checks"]
                if not c["ok"]
            )
        )
    manifest = verify_assets(args.assets)
    run(sys.executable, "-m", "venv", "--help")
    if any(
        (Path("/var/lib/microduck-studio-handoff") / n).exists()
        for n in ("phone.pending", "return.intent")
    ):
        raise RuntimeError("有未结束手柄交接，先明确hand-back")
    # Fail before creating an account/venv when names or managed paths already exist.
    handoff = Handoff()
    planned = [
        CONFIG,
        *[CONFIG.parent / n for n in ("ca.key", "ca.crt", "server.key", "server.crt")],
        *[handoff.guard_path(u) for u in UNITS],
        *[
            Path("/etc/systemd/system") / u
            for u in (*SERVICES, "microduck-studio.slice")
        ],
    ]
    for path in planned:
        if path.exists() or path.is_symlink():
            raise RuntimeError("已有同名文件，先status/recover，未覆盖：" + str(path))
    for unit in SERVICES:
        load = run(
            "systemctl", "show", unit, "-p", "LoadState", "--value"
        ).stdout.strip()
        if load != "not-found":
            raise RuntimeError("已有服务或状态未知，未覆盖：" + unit + " / " + load)
    try:
        import grp

        grp.getgrnam("robot")
    except KeyError:
        raise RuntimeError("缺少daemon部署的robot组，不创建Studio账号")
    try:
        user = pwd.getpwnam("microduck-studio")
    except KeyError:
        run(
            "useradd",
            "--system",
            "--user-group",
            "--home-dir",
            "/var/lib/microduck-studio",
            "--shell",
            "/usr/sbin/nologin",
            "microduck-studio",
        )
        user = pwd.getpwnam("microduck-studio")
    if (
        user.pw_uid == 0
        or user.pw_dir != "/var/lib/microduck-studio"
        or user.pw_shell not in ("/usr/sbin/nologin", "/sbin/nologin")
    ):
        raise RuntimeError("已有studio账号用途不匹配")
    run("usermod", "-a", "-G", "robot", "microduck-studio")
    journal.data = dict(
        schema=1, phase="preparing", files=[], assets=manifest, created=time.time()
    )
    journal.save()
    release = prepare_release(journal, args.assets, args.wheels)
    # Keep keys out of source/runtime archives; generated only on the target.
    with tempfile.TemporaryDirectory(prefix="studio-cert-") as temporary:
        tmp = Path(temporary)
        run(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-keyout",
            tmp / "ca.key",
            "-out",
            tmp / "ca.crt",
            "-days",
            "3650",
            "-subj",
            "/CN=Microduck Local Studio CA",
        )
        run(
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            tmp / "server.key",
            "-out",
            tmp / "server.csr",
            "-subj",
            "/CN=Microduck Studio",
        )
        (tmp / "extensions").write_text(
            "subjectAltName=IP:"
            + address
            + ",IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
        )
        run(
            "openssl",
            "x509",
            "-req",
            "-in",
            tmp / "server.csr",
            "-CA",
            tmp / "ca.crt",
            "-CAkey",
            tmp / "ca.key",
            "-CAcreateserial",
            "-out",
            tmp / "server.crt",
            "-days",
            "365",
            "-extfile",
            tmp / "extensions",
        )
        for name, mode, gid in [
            ("ca.key", 0o600, 0),
            ("ca.crt", 0o644, 0),
            ("server.key", 0o640, user.pw_gid),
            ("server.crt", 0o644, 0),
        ]:
            journal.write(CONFIG.parent / name, (tmp / name).read_bytes(), mode, 0, gid)
    cfg = dict(
        listen="0.0.0.0",
        port=8443,
        public_origin=f"https://{address}:8443",
        controls_enabled=args.enable_controls,
        camera_enabled=args.enable_camera,
        state_dir="/var/lib/microduck-studio",
        asset_dir=str(release / "runtime-assets/assets"),
        model_dir=str(release / "runtime-assets/models"),
        cert=str(CONFIG.parent / "server.crt"),
        key=str(CONFIG.parent / "server.key"),
        legacy_port=8765,
        camera_port=8080,
    )
    journal.write(CONFIG, json.dumps(cfg, indent=2).encode(), 0o640, 0, user.pw_gid)
    handoff.private()
    handoff.manager.mkdir(mode=0o700, parents=True, exist_ok=True)
    for unit in UNITS:
        journal.write(handoff.guard_path(unit), handoff.guard_raw())
    for name in (*SERVICES, "microduck-studio.slice"):
        raw = (
            (ROOT / "deploy/studio" / name)
            .read_text()
            .replace("@RELEASE@", str(release))
            .encode()
        )
        journal.write(Path("/etc/systemd/system") / name, raw)
    run(
        "systemd-analyze",
        "verify",
        *[Path("/etc/systemd/system") / s for s in SERVICES],
    )
    run("systemctl", "daemon-reload")
    journal.data["phase"] = "installed"
    journal.save()
    print("已安装独立Studio；尚未启动服务。旧Tools、robotd和原管理器会话未改写。")
    print(
        "HTTPS入口: "
        + cfg["public_origin"]
        + "/studio/；信任证书: "
        + str(CONFIG.parent / "ca.crt")
    )
    print(
        run(
            "openssl",
            "x509",
            "-in",
            CONFIG.parent / "ca.crt",
            "-noout",
            "-fingerprint",
            "-sha256",
        ).stdout.strip()
    )


def rollback_install(journal):
    if any(
        (Path("/var/lib/microduck-studio-handoff") / n).exists()
        for n in ("phone.pending", "return.intent")
    ):
        raise RuntimeError("有手柄交接记录；先明确hand-back，不能删除启动保护")
    journal.verify()
    registered = {row["path"] for row in journal.data["files"]}
    for unit in SERVICES:
        path = Path("/etc/systemd/system") / unit
        if str(path) not in registered or not path.exists():
            continue
        load = run(
            "systemctl", "show", unit, "-p", "LoadState", "--value"
        ).stdout.strip()
        if load == "not-found":
            continue
        if load not in ("loaded", "error", "bad-setting", "masked", "merged"):
            raise RuntimeError("自有服务加载状态未知，停止恢复：" + unit)
        run("systemctl", "stop", unit)
    journal.rollback()
    release = Path(journal.data.get("release", "/nonexistent"))
    if release.exists() and not (release / "STUDIO-RELEASE.json").exists():
        release.rename(
            release.with_name(release.name + "-interrupted-" + str(time.time_ns()))
        )
    run("systemctl", "daemon-reload")
    print("已撤销独立Studio服务/配置；版本与证据保留，robotd和旧Tools未切换。")


def status_report():
    result = dict(
        version=VERSION,
        installed=False,
        files_verified=False,
        phase="not_installed",
        ok=True,
        units={},
        settings={},
        problems=[],
        busy=False,
        hardware="NOT_RUN",
        next="setup",
    )
    result["handoff_pending"] = any(
        (Path("/var/lib/microduck-studio-handoff") / n).exists()
        for n in ("phone.pending", "return.intent")
    )
    lock_fd = None
    try:
        if not STATE.joinpath("install.json").exists():
            return result
        info = STATE.lstat()
        if STATE.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError("安装登记目录不是当前root私有目录")
        lock_path = STATE / "lifecycle.lock"
        if lock_path.exists():
            lock_fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                result.update(busy=True, phase="busy", next="wait")
                return result
        journal = Journal(STATE)
        result.update(phase=journal.data["phase"], release=journal.data.get("release"))
        journal.verify()
        if journal.data.get("release_ready"):
            verify_release(journal.data["release"])
        result["files_verified"] = True
        result["installed"] = journal.data["phase"] == "installed"
        if CONFIG.exists():
            cfg = cfg_read()
            result["settings"] = {
                k: cfg.get(k)
                for k in ("public_origin", "controls_enabled", "camera_enabled")
            }
        if platform.system() == "Linux":
            raw = run(
                "systemctl",
                "show",
                *SERVICES,
                "microduck-studio.slice",
                "-p",
                "Id",
                "-p",
                "ActiveState",
                "-p",
                "MainPID",
                "-p",
                "CPUQuotaPerSecUSec",
                "-p",
                "MemoryHigh",
                "-p",
                "MemoryMax",
                "-p",
                "MemoryCurrent",
            ).stdout
            for block in raw.strip().split("\n\n"):
                values = dict(
                    line.split("=", 1) for line in block.splitlines() if "=" in line
                )
                if values.get("Id"):
                    result["units"][values["Id"]] = values
        result["handoff_pending"] = any(
            (Path("/var/lib/microduck-studio-handoff") / n).exists()
            for n in ("phone.pending", "return.intent")
        )
        result["next"] = (
            "recover"
            if not result["installed"]
            else (
                "hand-back"
                if result["handoff_pending"]
                else (
                    "pair"
                    if result["units"].get(SERVICES[0], {}).get("ActiveState")
                    == "active"
                    else "start"
                )
            )
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        subprocess.SubprocessError,
    ) as e:
        result.update(ok=False, next="review")
        result["problems"].append(str(e))
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
    return result


def status(as_json=False):
    report = status_report()
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(
        "Studio "
        + VERSION
        + " | "
        + {
            "not_installed": "未安装",
            "installed": "已安装",
            "preparing": "安装未完成",
            "configuring": "配置未完成",
            "rolled_back": "已回退",
        }.get(report["phase"], report["phase"])
    )
    if report.get("busy"):
        print("正在执行安装/配置，等待原任务；不要运行recover或重复安装。")
    print(
        "登记检查："
        + (
            "任务运行中，完成后复核"
            if report.get("busy")
            else (
                "异常：" + "；".join(report["problems"])
                if not report["ok"]
                else "文件哈希一致" if report.get("files_verified") else "尚无安装登记"
            )
        )
    )
    if report["settings"]:
        cfg = report["settings"]
        print("界面：" + str(cfg.get("public_origin")) + "/studio/")
        print(
            "磁盘开关：控制="
            + str(cfg.get("controls_enabled"))
            + " / Camera="
            + str(cfg.get("camera_enabled"))
            + "；网页另核对运行值"
        )
    for name, unit in report["units"].items():
        print(
            "  "
            + name
            + "："
            + unit.get("ActiveState", "未知")
            + " / PID "
            + unit.get("MainPID", "未知")
        )
    if report.get("handoff_pending"):
        print("手柄：待明确交还，不自动恢复")
    print("下一步：" + report["next"] + " | 板端验收 NOT_RUN")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=[
            "doctor",
            "setup",
            "start",
            "status",
            "stop",
            "rollback",
            "recover",
            "pair",
            "hand-back",
            "configure",
            "demo",
        ],
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm", default="")
    p.add_argument("--server-ip")
    p.add_argument("--assets", type=Path)
    p.add_argument("--wheels", type=Path)
    p.add_argument("--enable-controls", action="store_true")
    p.add_argument("--enable-camera", action="store_true")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--state-dir", type=Path)
    p.add_argument("--controls", choices=["on", "off"])
    p.add_argument("--camera", choices=["on", "off"])
    args = p.parse_args()
    if args.command == "demo":
        from .server import create_app
        from aiohttp import web

        directory = args.state_dir or Path(tempfile.mkdtemp(prefix="studio-demo-"))
        issue_pair(directory)
        asset = args.assets or ROOT / "studio/vendor"
        base = dict(
            demo=True,
            state_dir=str(directory),
            listen="127.0.0.1",
            port=args.port,
            public_origin=f"http://127.0.0.1:{args.port}",
            asset_dir=str(asset / "assets" if (asset / "assets").exists() else asset),
            model_dir=str(asset / "models"),
        )
        print(base["public_origin"] + "/studio/ | SIMULATION / NO HARDWARE", flush=True)
        web.run_app(create_app(base), host="127.0.0.1", port=args.port, access_log=None)
        return
    if args.command == "doctor":
        from .preflight import check

        result = (
            check(args.assets, args.wheels)
            if args.assets and args.wheels
            else dict(ok=False, checks=[], error="需要离线资产/wheel目录")
        )
        print(
            json.dumps(result, ensure_ascii=False, indent=2)
            if args.json
            else "安装检查："
            + ("通过" if result["ok"] else "未通过")
            + "\n"
            + "\n".join(
                ("[通过] " if c["ok"] else "[待处理] ") + c["item"] + "：" + c["detail"]
                for c in result["checks"]
            )
        )
        if not result["ok"]:
            raise SystemExit(1)
        return
    if args.command == "status":
        status(args.json)
        return
    if not args.apply:
        print(
            "预览: "
            + args.command
            + "；没有修改。审阅指南后添加 --apply --confirm "
            + ("NEUTRAL" if args.command == "hand-back" else "EXECUTE")
        )
        return
    if args.confirm != ("NEUTRAL" if args.command == "hand-back" else "EXECUTE"):
        raise RuntimeError("缺少明确执行确认；手柄交还还须现场全部按键释放")
    native_gate()
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "lifecycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = Journal()
        if args.command == "setup":
            if not args.server_ip:
                raise RuntimeError("需要--server-ip板端固定局域网地址")
            setup(args, journal)
        elif args.command in ("recover", "rollback"):
            rollback_install(journal)
        elif args.command == "hand-back":
            run("systemctl", "start", SERVICES[1])
            print(
                json.dumps(
                    asyncio.run(call("return", confirm=True)), ensure_ascii=False
                )
            )
        elif args.command == "configure":
            if journal.data["phase"] != "installed":
                raise RuntimeError("安装状态不完整，先recover")
            journal.verify()
            cfg = cfg_read()
            if args.controls is None and args.camera is None:
                raise RuntimeError("指定--controls on/off 或 --camera on/off")
            if args.controls is not None:
                cfg["controls_enabled"] = args.controls == "on"
            if args.camera is not None:
                cfg["camera_enabled"] = args.camera == "on"
            run("systemctl", "stop", SERVICES[0])
            journal.update_config(CONFIG, json.dumps(cfg, indent=2).encode())
            print(
                "独立配置已更新；Studio保持停止。明确start后生效，不自动恢复运动/手柄。"
            )
        elif args.command == "pair":
            issue_pair(cfg_read()["state_dir"], pwd.getpwnam("microduck-studio"))
        elif args.command == "start":
            if journal.data["phase"] != "installed":
                raise RuntimeError("安装不完整，先recover并重新setup")
            journal.verify()
            verify_release(journal.data["release"])
            run("systemctl", "start", *reversed(SERVICES))
            print(
                "Studio已启动（非开机自启）；使用pair生成登录码；未启用动力或恢复手柄"
            )
        elif args.command == "stop":
            run("systemctl", "stop", SERVICES[0])
            print("Studio已停止；助手及启动保护保留用于明确hand-back；无自动手柄恢复")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("停止: " + str(error), file=sys.stderr)
        raise SystemExit(1)
