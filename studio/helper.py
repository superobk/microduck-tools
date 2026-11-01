"""Linux root helper: fixed handoff + bounded BlueZ operations, no arbitrary shell/RPC."""

import argparse
import asyncio
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import shlex
import socket
import stat
import struct
import time

from .core import Refused

SOCKET = "/run/microduck-studio-helper.sock"
STATE = Path("/var/lib/microduck-studio-handoff")
UNITS = ("padd.service", "netpadd.service")
GUARD = "90-microduck-studio-handoff.conf"


def atomic(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def unlink_sync(path):
    path.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Handoff:
    def __init__(
        self,
        state=STATE,
        systemd=Path("/etc/systemd/system"),
        manager=Path("/var/lib/microduck-tools-manager"),
    ):
        self.state = state
        self.systemd = systemd
        self.manager = manager
        self.fd = None
        self.lock = asyncio.Lock()

    async def run(self, *args):
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 10)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise Refused(
                "HELPER_SYSTEMD", (err or out).decode(errors="replace")[-1200:]
            )
        return out.decode()

    async def props(self, unit):
        keys = (
            "Id",
            "LoadState",
            "ActiveState",
            "MainPID",
            "ControlPID",
            "Job",
            "ControlGroup",
            "ExecStart",
            "ExecCondition",
            "ExecStartPre",
            "ExecStartPost",
            "ExecStop",
            "ExecStopPost",
            "RequiredBy",
            "BoundBy",
            "ConsistsOf",
            "PropagatesStopTo",
            "UnitFileState",
        )
        raw = await self.run(
            "systemctl", "show", unit, *[v for key in keys for v in ("-p", key)]
        )
        return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)

    def guard_path(self, unit):
        return self.systemd / (unit + ".d") / GUARD

    def guard_raw(self):
        return (
            "# Studio owns only this condition; no native command is replaced.\n[Unit]\nConditionPathExists=!"
            + str(self.state / "phone.pending")
            + "\n"
        ).encode()

    def private(self):
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.state.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise Refused("HANDOFF_STATE", "交接目录不是root私有真实目录")

    async def inspect(self):
        rows = {}
        for unit in UNITS:
            p = await self.props(unit)
            if p.get("LoadState") == "not-found":
                continue
            if p.get("Id") != unit or p.get("UnitFileState", "").startswith("masked"):
                raise Refused("UNIT_UNREVIEWED", "手柄服务别名/mask未审阅")
            if any(
                p.get(k)
                for k in ("ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost")
            ):
                raise Refused("UNIT_HOOK", "存在未审阅启停钩子")
            for key in ("RequiredBy", "BoundBy", "ConsistsOf", "PropagatesStopTo"):
                if set(p.get(key, "").split()) - {"multi-user.target", *UNITS}:
                    raise Refused("UNIT_LINK", "存在未审阅服务联动")
            expected = "/opt/robot/daemon/current/bin/" + unit.removesuffix(".service")
            import re

            argv = re.findall(r"argv\[\]=(.*?)\s*;", p.get("ExecStart", ""))
            if len(argv) != 1 or shlex.split(argv[0]) != [expected]:
                raise Refused("UNIT_COMMAND", "手柄有效完整启动命令不匹配：" + unit)
            condition = re.findall(r"argv\[\]=(.*?)\s*;", p.get("ExecCondition", ""))
            if condition and (
                len(condition) != 1
                or shlex.split(condition[0]) != [expected, "--should-run"]
            ):
                raise Refused("UNIT_CONDITION", "手柄条件命令未审阅")
            path = self.guard_path(unit)
            if (
                path.is_symlink()
                or not path.is_file()
                or path.read_bytes() != self.guard_raw()
            ):
                raise Refused(
                    "GUARD_MISSING", "交接启动保护缺失或外部改变，请先本地setup"
                )
            rows[unit] = p
        return rows

    def manager_lock(self):
        self.manager.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.manager.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise Refused("MANAGER_DIR", "部署管理器锁目录不可信")
        fd = os.open(
            self.manager / "manager.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise Refused("MAINTENANCE_BUSY", "升级/维护进行中，不接管控制")
        if self.fd is not None:
            os.close(self.fd)
        self.fd = fd

    async def stopped(self, rows):
        for unit in rows:
            p = await self.props(unit)
            if (
                p.get("ActiveState") not in ("inactive", "failed")
                or p.get("MainPID") != "0"
                or p.get("ControlPID") != "0"
                or p.get("Job") not in ("", "0")
            ):
                raise Refused("PAD_NOT_STOPPED", "服务/PID/Job仍活跃：" + unit)
            group = p.get("ControlGroup", "")
            if group:
                if not group.startswith("/") or ".." in Path(group).parts:
                    raise Refused("CGROUP_PATH", "未知cgroup")
                path = Path("/sys/fs/cgroup") / group.lstrip("/")
                for file in (
                    [path / "cgroup.procs", *path.glob("**/cgroup.procs")]
                    if path.exists()
                    else []
                ):
                    if file.exists() and file.read_text().strip():
                        raise Refused("CGROUP_BUSY", "手柄cgroup仍有进程")

    async def take(self):
        async with self.lock:
            self.private()
            previous = None
            intent = self.state / "return.intent"
            if intent.exists() and not (self.state / "phone.pending").exists():
                atomic(self.state / "phone.pending", intent.read_bytes())
            if (self.state / "phone.pending").exists():
                previous = json.loads((self.state / "phone.pending").read_text())
                if (
                    previous.get("schema") != 1
                    or not isinstance(previous.get("active"), list)
                    or set(previous["active"]) - set(UNITS)
                    or len(previous["active"]) > 1
                ):
                    raise Refused("HANDOFF_RECORD", "旧交接记录损坏")
            rows = await self.inspect()
            active = [u for u, p in rows.items() if p.get("ActiveState") == "active"]
            if len(active) > 1:
                raise Refused("PAD_AMBIGUOUS", "两个手柄服务同时活跃，拒绝猜测")
            self.manager_lock()
            # Durable marker BEFORE stop: reboot cannot re-enable a pad behind a phone.
            atomic(
                self.state / "phone.pending",
                json.dumps(
                    previous or dict(schema=1, active=active, started=time.time())
                ).encode(),
            )
            for unit in rows:
                await self.run("systemctl", "stop", unit)
            await self.stopped(rows)
            return dict(paused=True, pending=True, automatic_restore=False)

    async def park(self):
        async with self.lock:
            self.private()
            rows = await self.inspect()
            intent = self.state / "return.intent"
            if intent.exists() and not (self.state / "phone.pending").exists():
                atomic(self.state / "phone.pending", intent.read_bytes())
            if not (self.state / "phone.pending").exists():
                raise Refused("NO_HANDOFF", "没有可恢复的交接记录")
            for unit in rows:
                await self.run("systemctl", "stop", unit)
            await self.stopped(rows)
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            return dict(paused=True, pending=True, automatic_restore=False)

    async def release(self):
        async with self.lock:
            self.private()
            rows = await self.inspect()
            path = self.state / "phone.pending"
            if not path.exists() and (self.state / "return.intent").exists():
                atomic(path, (self.state / "return.intent").read_bytes())
                for unit in rows:
                    await self.run("systemctl", "stop", unit)
            if not path.exists():
                return dict(pending=False, restored=[])
            saved = json.loads(path.read_text())
            if (
                saved.get("schema") != 1
                or not isinstance(saved.get("active"), list)
                or set(saved["active"]) - set(UNITS)
                or len(saved["active"]) > 1
            ):
                raise Refused("HANDOFF_RECORD", "交接记录损坏，不恢复")
            await self.stopped(rows)
            # Explicit hand-back intent is durable. Restore only the original active source.
            atomic(self.state / "return.intent", path.read_bytes())
            unlink_sync(path)
            try:
                for unit in saved["active"]:
                    await self.run("systemctl", "start", unit)
            except BaseException:
                atomic(path, json.dumps(saved).encode())
                raise
            finally:
                if self.fd is not None:
                    os.close(self.fd)
                    self.fd = None
            unlink_sync(self.state / "return.intent")
            return dict(pending=False, restored=saved["active"])

    async def status(self):
        rows = {u: await self.props(u) for u in UNITS}
        return dict(
            pending=(self.state / "phone.pending").exists()
            or (self.state / "return.intent").exists(),
            units={
                u: {k: p.get(k) for k in ("ActiveState", "MainPID", "Job")}
                for u, p in rows.items()
            },
            automatic_restore=False,
        )


class Bluetooth:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.last = []

    @staticmethod
    def unpack(objects):
        out = []
        for path, interfaces in objects.items():
            data = interfaces.get("org.bluez.Device1")
            if not data:
                continue
            obj = {k: v.value for k, v in data.items()}
            kind = obj.get("Class", 0) & 0x1FFC
            if obj.get("Icon") != "input-gaming" and kind not in (0x504, 0x508):
                continue
            out.append(
                dict(
                    path=path,
                    mac=obj.get("Address"),
                    name=obj.get("Name", "gamepad"),
                    paired=obj.get("Paired", False),
                    connected=obj.get("Connected", False),
                    trusted=obj.get("Trusted", False),
                    rssi=obj.get("RSSI"),
                )
            )
        return out[:20]

    async def operate(self, op, mac=None):
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

        async with self.lock:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            adapter = None
            discovery = False
            try:
                intro = await bus.introspect("org.bluez", "/")
                manager = bus.get_proxy_object("org.bluez", "/", intro).get_interface(
                    "org.freedesktop.DBus.ObjectManager"
                )
                objects = await manager.call_get_managed_objects()
                if op == "scan":
                    paths = [p for p, v in objects.items() if "org.bluez.Adapter1" in v]
                    if len(paths) != 1:
                        raise Refused("BT_ADAPTER", "没有唯一可核对蓝牙适配器")
                    intro = await bus.introspect("org.bluez", paths[0])
                    adapter = bus.get_proxy_object(
                        "org.bluez", paths[0], intro
                    ).get_interface("org.bluez.Adapter1")
                    await adapter.call_start_discovery()
                    discovery = True
                    await asyncio.sleep(12)
                    self.last = self.unpack(await manager.call_get_managed_objects())
                    return dict(
                        devices=[
                            {k: v for k, v in r.items() if k != "path"}
                            for r in self.last
                        ]
                    )
                rows = self.unpack(objects)
                row = next((r for r in rows if r["mac"] == mac), None)
                if not row:
                    raise Refused("BT_DEVICE", "不是已识别的游戏手柄，拒绝操作")
                intro = await bus.introspect("org.bluez", row["path"])
                device = bus.get_proxy_object(
                    "org.bluez", row["path"], intro
                ).get_interface("org.bluez.Device1")
                if op == "connect":
                    await device.call_connect()
                elif op == "disconnect":
                    await device.call_disconnect()
                else:
                    raise Refused("BT_OPERATION", "蓝牙操作未知")
                return dict(
                    submitted=True,
                    mac=mac,
                    verify="重新读取pad.status与输入，提交不代表已可控制",
                )
            finally:
                if discovery:
                    try:
                        await adapter.call_stop_discovery()
                    except Exception:
                        pass
                bus.disconnect()


async def serve():
    if os.geteuid() != 0 or not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("helper仅Linux root可启动")
    from .ipc import Identity, IPC

    identity = Identity(local=True)
    ipc = IPC()
    os.umask(0o077)
    handoff = Handoff()
    bluetooth = Bluetooth()
    allowed = pwd.getpwnam("microduck-studio").pw_uid

    async def client(reader, writer):
        try:
            peer = writer.get_extra_info("socket").getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, 12
            )
            _, uid, _ = struct.unpack("3i", peer)
            if uid not in (0, allowed):
                raise Refused("HELPER_PEER", "拒绝调用者")
            raw = await asyncio.wait_for(reader.readline(), 2)
            if len(raw) > 4096:
                raise Refused("HELPER_SIZE", "请求过大")
            request = json.loads(raw)
            if set(request) - {"op", "mac", "confirm"}:
                raise Refused("HELPER_FIELDS", "请求有额外字段")
            op = request.get("op")
            if op in ("take", "park", "return") and request.get("confirm") is not True:
                raise Refused("HELPER_CONFIRM", "缺少明确交接确认")
            if op == "take":
                result = await handoff.take()
            elif op == "park":
                result = await handoff.park()
            elif op == "return":
                result = await handoff.release()
            elif op == "status":
                result = await handoff.status()
            elif op == "identity":
                hello = await ipc.rpc("robot", "hello", {"api_version": 47})
                model = await ipc.rpc("robot", "robot.model")
                verified, calibration = await asyncio.to_thread(
                    identity.robot, hello, model
                )
                result = dict(
                    verified=verified,
                    calibration_verified=calibration,
                    hello=hello,
                    process=identity.info,
                )
            elif op == "camera_identity":
                result = dict(verified=await asyncio.to_thread(identity.camera))
            elif op in ("scan", "connect", "disconnect"):
                import re

                if op != "scan" and not re.fullmatch(
                    r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", str(request.get("mac"))
                ):
                    raise Refused("MAC", "MAC格式错误")
                result = await asyncio.wait_for(
                    bluetooth.operate(op, request.get("mac")), 25
                )
            else:
                raise Refused("HELPER_OPERATION", "操作不在白名单")
            reply = dict(ok=True, result=result)
        except Exception as e:
            reply = dict(
                ok=False, code=getattr(e, "code", "HELPER_FAILED"), error=str(e)[:1500]
            )
        try:
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    path = Path(SOCKET)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid != 0:
            raise RuntimeError("拒绝替换未知helper文件")
        path.unlink()
    server = await asyncio.start_unix_server(client, path=SOCKET, limit=4097)
    os.chmod(SOCKET, 0o660)
    os.chown(SOCKET, 0, grp.getgrnam("microduck-studio").gr_gid)
    try:
        async with server:
            await server.serve_forever()
    finally:
        if handoff.fd is not None:
            os.close(handoff.fd)
        # Deliberately no automatic hand-back, including on service stop/crash.


async def call(op, **kwargs):
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(SOCKET, limit=8193), 1
        )
        writer.write((json.dumps(dict(op=op, **kwargs)) + "\n").encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), 30)
        if len(raw) > 8192:
            raise Refused("HELPER_REPLY", "helper回复过长")
        result = json.loads(raw)
        if result.get("ok") is not True:
            raise Refused(
                result.get("code", "HELPER"), result.get("error", "helper错误")
            )
        return result["result"]
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(serve())
