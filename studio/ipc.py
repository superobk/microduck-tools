"""Reviewed Unix-socket clients. Never opens a robot device or starts a native service."""

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import socket
import struct
import sys
import time

from .core import JOINTS, Refused, compact_tof

ROOT = Path(__file__).resolve().parents[1]
SOCKETS = {
    "robot": "/run/robotd.sock",
    "config": "/run/configd.sock",
    "updater": "/run/updaterd.sock",
    "tof": "/run/tofd/tof.sock",
    "pad": "/run/padd/pad.sock",
}
READS = {
    "robot": {
        "hello",
        "robot.health",
        "robot.model",
        "robot.policies",
        "robot.skills",
        "pad.bindings",
    },
    "config": {"pad.status", "system.services", "system.logs"},
    "updater": {"policy.search", "policy.check"},
}
WRITES = {
    "robot": {
        "robot.move",
        "robot.stop",
        "robot.enable",
        "robot.do",
        "robot.sound",
        "robot.loadPolicy",
        "robot.setSkill",
        "robot.removeSkill",
        "pad.bind",
    },
    "config": {"pad.pair", "pad.forget"},
    "updater": {"policy.fetch"},
}


def decode(line):
    if len(line) > 262144 or not line.endswith(b"\n"):
        raise Refused("IPC_FRAME", "IPC帧过长或不完整")
    return json.loads(
        line, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("非有限JSON"))
    )


class IPC:
    def __init__(self, paths=None):
        self.paths = {**SOCKETS, **(paths or {})}

    async def rpc(self, service, method, params=None, write=False, timeout=1):
        if method not in (WRITES if write else READS).get(service, set()):
            raise Refused("RPC_ALLOWLIST", "方法不在当前操作白名单")
        writer = None

        async def exchange():
            nonlocal writer
            reader, writer = await asyncio.open_unix_connection(
                self.paths[service], limit=262145
            )
            writer.write(
                (
                    json.dumps(
                        dict(jsonrpc="2.0", id=1, method=method, params=params or {}),
                        allow_nan=False,
                    )
                    + "\n"
                ).encode()
            )
            await writer.drain()
            reply = decode(await reader.readline())
            if reply.get("id") != 1 or reply.get("error") is not None:
                raise Refused(
                    "IPC_REFUSED", str(reply.get("error") or "RPC ID不匹配")[:1500]
                )
            if not isinstance(reply.get("result"), dict):
                raise Refused("IPC_RESULT", "RPC结果必须为对象")
            result = reply["result"]
            if result.get("accepted") is False:
                raise Refused(
                    "INTENT_REFUSED", str(result.get("reason") or result)[:1500]
                )
            return result

        try:
            return await asyncio.wait_for(exchange(), timeout)
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    async def subscribe(self, service, method, params=None):
        permitted = {
            ("robot", "robot.subscribe"),
            ("tof", "tof.stream"),
            ("pad", "pad.input"),
        }
        if (service, method) not in permitted:
            raise Refused("STREAM_ALLOWLIST", "流不在白名单")
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(self.paths[service], limit=262145), 1
        )
        try:
            writer.write(
                (
                    json.dumps(
                        dict(jsonrpc="2.0", id=1, method=method, params=params or {})
                    )
                    + "\n"
                ).encode()
            )
            await writer.drain()
            while True:
                line = await asyncio.wait_for(reader.readline(), 3)
                if not line:
                    raise ConnectionError(service + "流结束")
                obj = decode(line)
                if obj.get("error"):
                    raise Refused("STREAM_REFUSED", str(obj["error"])[:500])
                if obj.get("id") == 1:
                    yield ("status", obj.get("result") or {})
                elif obj.get("method"):
                    yield (obj["method"], obj.get("params") or {})
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


class Identity:
    def __init__(self, local=False):
        self.hashes = {}
        self.info = {}
        self.local = local

    def helper_proof(self, op):
        from .helper import SOCKET

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(8)
            client.connect(SOCKET)
            client.sendall((json.dumps(dict(op=op)) + "\n").encode())
            raw = bytearray()
            while not raw.endswith(b"\n"):
                part = client.recv(8192 - len(raw))
                if not part:
                    raise Refused("IDENTITY_REPLY", "身份助手已断开")
                raw.extend(part)
                if not raw or len(raw) >= 8192:
                    raise Refused("IDENTITY_REPLY", "身份助手回复不完整/过大")
            data = json.loads(raw)
            if not data.get("ok"):
                raise Refused("IDENTITY_HELPER", data.get("error", "核验失败"))
            return data["result"]

    def executable(self, unit, sha):
        import subprocess

        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        pid = int(result.stdout.strip())
        path = Path(f"/proc/{pid}/exe")
        info = path.stat()
        key = (pid, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if key not in self.hashes:
            with path.open("rb") as f:
                self.hashes[key] = hashlib.file_digest(f, "sha256").hexdigest()
            while len(self.hashes) > 4:
                self.hashes.pop(next(iter(self.hashes)))
        actual = self.hashes[key]
        self.info = dict(pid=pid, exe=os.readlink(path), sha256=actual)
        return actual == sha

    def robot(self, hello, model):
        if not self.local:
            proof = self.helper_proof("identity")
            self.info = proof.get("process", {})
            return bool(proof.get("verified") and proof.get("hello") == hello), bool(
                proof.get("calibration_verified") and proof.get("hello") == hello
            )
        if platform.system() != "Linux" or platform.machine() not in (
            "aarch64",
            "arm64",
        ):
            return False, False
        known = json.loads((ROOT / "deploy/manual-upgrade/inputs.json").read_text())
        native = hello.get("revision") == known["native_revision"]
        official = hello.get("revision") == known["base_revision"]
        if (
            hello.get("api_version") != 47
            or hello.get("daemon_version") != "0.18.3"
            or model.get("joint_names") != JOINTS
            or not (native or official)
        ):
            return False, False
        expected = known["native" if native else "official"]["robotd"]
        if not self.executable("robotd.service", expected):
            return False, False
        if official:
            # An official daemon cannot apply custom calibration. Never approve a hidden pointer.
            import tomllib

            base = tomllib.loads(Path("/etc/robot/robotd.toml").read_text())
            user = Path("/var/lib/robot/robotd.toml")
            override = tomllib.loads(user.read_text()) if user.exists() else {}
            pointer = override.get("bus", {}).get(
                "calibration_path", base.get("bus", {}).get("calibration_path", "")
            )
            return True, not bool(pointer)
        web = ROOT / "calibration/integration-0.16.3/web/calibration_web.py"
        if "studio_legacy_verify" not in sys.modules:
            spec = importlib.util.spec_from_file_location("studio_legacy_verify", web)
            module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(web.parent))
            spec.loader.exec_module(module)
            sys.modules["studio_legacy_verify"] = module
        module = sys.modules["studio_legacy_verify"]
        receipt = json.loads(Path("/run/robotd/calibration-effective.json").read_text())
        config = Path("/etc/robot/robotd.toml")
        ok, _ = module.verify_native_receipt(receipt, config, "daemon-v0.18.3")
        active, _ = module.valid_active_file(receipt, demo=False)
        params = module.cal.config_layers.load(config, "daemon-v0.18.3")
        pointer = params.get("bus", {}).get("calibration_path")
        if not pointer:
            return True, False
        saved = module.cal.parse_calibration(pointer, "daemon-v0.18.3")
        saved = {**saved, **module.cal.effective_gyro(saved)}
        good = (
            ok
            and active
            and receipt.get("pid") == self.info["pid"]
            and receipt.get("calibration_path") == pointer
            and not module.state_delta(saved, receipt)
        )
        return True, bool(good)

    def camera(self):
        if not self.local:
            return self.helper_proof("camera_identity").get("verified") is True
        known = json.loads((ROOT / "deploy/manual-upgrade/inputs.json").read_text())
        if not self.executable("mediad.service", known["official"]["mediad"]):
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect("/run/mediad/media.sock")
            pid, uid, _ = struct.unpack(
                "3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            # Official mediad runs as User=mediad. Bind the endpoint to its actual
            # systemd PID and real UID; root is required for the verifier, not the camera.
            status = Path(f'/proc/{self.info["pid"]}/status').read_text()
            actual_uid = int(
                next(
                    line for line in status.splitlines() if line.startswith("Uid:")
                ).split()[1]
            )
            if pid != self.info["pid"] or uid != actual_uid:
                return False
            client.sendall(
                b'{"jsonrpc":"2.0","id":1,"method":"hello","params":{"api_version":47}}\n'
            )
            raw = bytearray()
            while not raw.endswith(b"\n"):
                part = client.recv(4097 - len(raw))
                if not part or len(raw) + len(part) > 4096:
                    return False
                raw.extend(part)
            data = json.loads(raw)
            hello = data.get("result", {})
            return (
                data.get("id") == 1
                and hello.get("api_version") == 47
                and hello.get("daemon_version") == "0.18.3"
                and hello.get("revision") == known["base_revision"]
            )


class Broker:
    def __init__(self, store, ipc=None, identity=None):
        self.store = store
        self.ipc = ipc or IPC()
        self.identity = identity or Identity()
        self.tasks = []
        self.pad_wanted = False
        self.pad_task = None
        self.tof_status = {}
        self.last_hello = None
        self.last_pid = None

    def error(self, where, error):
        message = (where + ": " + str(error))[:600]
        if not self.store.errors or self.store.errors[-1] != message:
            self.store.errors.append(message)

    async def stream(self, service, method, params=None):
        while True:
            try:
                async for kind, obj in self.ipc.subscribe(service, method, params):
                    if service == "robot" and kind == "robot.state":
                        self.store.ingest(obj)
                    elif service == "tof":
                        if kind == "status":
                            self.tof_status = obj
                        elif kind == "tof.frame":
                            self.store.ingest_tof(obj)
                    elif service == "pad" and kind == "pad.report":
                        # Retain latest bounded input only, not a history or IMU batch stream.
                        if (
                            obj.get("report") in ("attached", "detached", "frame")
                            and len(json.dumps(obj)) < 12000
                        ):
                            self.store.pad_input = obj
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.error(service, e)
                if service == "robot":
                    self.store.identity_verified = False
                await asyncio.sleep(2)

    async def poll(self):
        last_pad = 0
        last_proof = 0
        while True:
            start = time.monotonic()
            try:
                hello = await self.ipc.rpc("robot", "hello", {"api_version": 47})
                if hello != self.last_hello:
                    self.store.reset()
                    self.store.hello = hello
                    self.last_hello = hello
                    last_proof = 0
                    self.store.model = await self.ipc.rpc("robot", "robot.model")
                health = await self.ipc.rpc("robot", "robot.health")
                self.store.health = health
                self.store.health_at = self.store.clock()
                self.store.policies = await self.ipc.rpc("robot", "robot.policies")
                if start - last_proof >= 5:
                    try:
                        verified, calibration = await asyncio.to_thread(
                            self.identity.robot, hello, self.store.model
                        )
                        pid = self.identity.info.get("pid")
                        if (
                            self.last_pid is not None
                            and pid is not None
                            and pid != self.last_pid
                        ):
                            self.store.reset()
                            self.store.hello = hello
                            self.store.model = await self.ipc.rpc(
                                "robot", "robot.model"
                            )
                        self.last_pid = pid
                        self.store.identity_verified = verified
                        self.store.calibration_verified = calibration
                    except Exception as e:
                        self.store.identity_verified = False
                        self.store.calibration_verified = False
                        self.error("identity", e)
                    last_proof = start
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.store.identity_verified = False
                self.error("health", e)
            if start - last_pad >= 2:
                try:
                    self.store.pad = await self.ipc.rpc("config", "pad.status")
                except Exception as e:
                    self.error("pad.status", e)
                last_pad = start
            await asyncio.sleep(max(0.05, 1 - (time.monotonic() - start)))

    async def start(self):
        if self.tasks:
            return
        self.tasks = [
            asyncio.create_task(self.stream("robot", "robot.subscribe", {"hz": 5})),
            asyncio.create_task(self.stream("tof", "tof.stream")),
            asyncio.create_task(self.poll()),
        ]

    async def inputs(self, wanted):
        self.pad_wanted = bool(wanted)
        if wanted and self.tasks and self.pad_task is None:
            self.pad_task = asyncio.create_task(self.stream("pad", "pad.input"))
        elif not wanted and self.pad_task is not None:
            self.pad_task.cancel()
            await asyncio.gather(self.pad_task, return_exceptions=True)
            self.pad_task = None
            self.store.pad_input = None

    async def stop(self):
        await self.inputs(False)
        tasks = self.tasks
        self.tasks = []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
