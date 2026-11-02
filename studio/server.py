"""Optional authenticated Studio gateway. Demo never creates native clients or helper calls."""

import argparse
import asyncio
from collections import deque
import contextlib
import hashlib
import json
import os
from pathlib import Path
import resource
import secrets
import ssl
import struct
import time
import urllib.parse

from aiohttp import web, ClientSession, ClientTimeout, WSMsgType
from .core import Store, Lease, Environment, Refused, SCENES
from .diagnostics import Diagnostics, scrub
from .ipc import Broker, ROOT
from . import helper

PREFIX = "/api/studio/v1"
DEFAULTS = dict(
    demo=False,
    controls_enabled=False,
    camera_enabled=False,
    listen="127.0.0.1",
    port=8443,
    public_origin="https://127.0.0.1:8443",
    state_dir="/var/lib/microduck-studio",
    asset_dir=str(ROOT / "studio/vendor"),
    model_dir=str(ROOT / "studio/models"),
    legacy_port=8765,
    camera_port=8080,
)
LEGACY_GET = {
    "/api/status",
    "/api/camera/status",
    "/api/camera/frame",
    "/api/capabilities",
    "/api/export",
    "/servo-health.js",
}
LEGACY_POST = {
    "/api/preview",
    "/api/save",
    "/api/camera/preview",
    "/api/camera/save",
    "/api/camera/apply",
}
LEGACY_SERVO = {"status", "history", "maintenance", "export", "config", "baseline"}


class Snapshot:
    def __init__(self, app):
        self.app = app
        self.frame = None
        self.busy = False
        self.retry = 0.0
        self.interval = 10.0
        self.error = None
        self.bytes = 0

    async def fetch(self):
        app = self.app
        now = time.monotonic()
        if app.config["demo"] or not app.config["camera_enabled"]:
            raise Refused("CAMERA_DISABLED", "相机功能未启用；不会启动mediad")
        if self.busy:
            raise Refused("CAMERA_BUSY", "已有快照在途")
        if now < self.retry:
            if self.frame and now - self.frame["at"] < 60:
                return self.frame
            raise Refused("CAMERA_INTERVAL", "快照限流中，请等待")
        if app.store.state and any(
            abs(x) > 0.4 for x in (app.store.state["imu"]["gyro"] or [0, 0, 0])
        ):
            raise Refused("CAMERA_MOTION", "明显晃动，暂缓环境取帧")
        self.busy = True
        self.retry = now + self.interval
        try:
            if not await asyncio.to_thread(app.broker.identity.camera):
                raise Refused("CAMERA_IDENTITY", "mediad实际身份未核验")
            url = f"http://127.0.0.1:{app.config['camera_port']}/frame"
            started = time.monotonic()
            async with app.http.get(
                url, allow_redirects=False, timeout=ClientTimeout(total=4)
            ) as response:
                if response.status != 200:
                    raise Refused("CAMERA_HTTP", "快照HTTP " + str(response.status))
                raw = bytearray()
                async for part in response.content.iter_chunked(65536):
                    raw.extend(part)
                    if len(raw) > 3 * 1024 * 1024:
                        raise Refused("CAMERA_SIZE", "PNG超过3MiB，已停止接收")
            if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
                raise Refused("CAMERA_FORMAT", "不是有界PNG")
            width, height = struct.unpack(">II", raw[16:24])
            if not 1 <= width <= 1920 or not 1 <= height <= 1920:
                raise Refused("CAMERA_DIMENSIONS", "图像尺寸超出首版预算")
            now = time.monotonic()
            self.interval = max(10.0, min(60.0, len(raw) / 65536))
            self.retry = now + self.interval
            self.frame = dict(
                id=secrets.token_hex(12),
                at=now,
                png=bytes(raw),
                width=width,
                height=height,
                request_ms=round((now - started) * 1000),
                capture_timestamp=None,
            )
            self.bytes += len(raw)
            self.error = None
            return self.frame
        except Exception as e:
            self.error = str(e)
            raise
        finally:
            self.busy = False

    def status(self):
        f = self.frame
        return dict(
            enabled=self.app.config["camera_enabled"],
            frame_id=f["id"] if f else None,
            age_ms=round((time.monotonic() - f["at"]) * 1000) if f else None,
            interval_s=self.interval,
            bytes_total=self.bytes,
            error=self.error,
            exposure_time_known=False,
        )


class Studio:
    def __init__(self, config=None, store=None, broker=None, helper_call=None):
        self.config = {**DEFAULTS, **(config or {})}
        self.store = store or Store()
        self.broker = broker or Broker(self.store)
        self.helper_call = helper_call or helper.call
        self.lease = Lease()
        self.environment = Environment()
        self.diagnostics = Diagnostics()
        self.sessions = {}
        self.sockets = {}
        self.attempts = {}
        self.camera = Snapshot(self)
        self.http = None
        self.task = None
        self.grace = None
        self.mutation = asyncio.Lock()
        self.jobs = {}
        self.last_error = None
        self.helper_state = {}
        self.last_helper = 0
        self.last_cpu = time.process_time()
        self.last_resource = time.monotonic()
        self.cpu_percent = 0.0
        self.handoff_busy = False
        self.helper_task = None
        self.helper_at = None
        self.last_write = 0.0
        self.bytes_sent = 0
        self.started = time.monotonic()
        self.asset_manifest = self.load_asset_manifest()

    def load_asset_manifest(self):
        path = Path(self.config["asset_dir"]) / "asset-manifest.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {"files": [], "model_sha": None, "ready": False}

    def live_context(self, ctx):
        if self.config["demo"] or ctx["mode"] != "live":
            raise Refused("SIMULATION_ISOLATION", "演练不会调用真实机器人/助手")

    def control(self, ctx, movement=False):
        self.live_context(ctx)
        if not ctx.get("visible", True):
            raise Refused("PAGE_HIDDEN", "页面不可见，禁止控制")
        if not self.config["controls_enabled"]:
            raise Refused("CONTROL_DISABLED", "真实控制默认关闭，需本地setup明确启用")
        self.store.ready(movement=movement)

    def idle(self, ctx):
        self.control(ctx)
        if self.lease.owner != ctx["id"]:
            raise Refused("LEASE_REQUIRED", "先接管并停车，才能写配置/配对")
        if any(abs(x) > 0.001 for x in self.lease.value) or self.lease.last is not None:
            raise Refused("NOT_PARKED", "先释放移动输入")
        if self.store.state and any(
            abs(x) > 0.01
            for x in (self.store.state["movement"]["applied"] or [999] * 3)
        ):
            raise Refused("ROBOT_MOVING", "实际应用速度尚未归零")

    async def stop_motion(self, reason, release=False):
        had = self.lease.owner is not None
        self.lease.value = [0.0, 0.0, 0.0]
        self.lease.last = None
        self.lease.armed = False
        self.last_error = reason
        if had and not self.config["demo"]:
            try:
                await self.broker.ipc.rpc(
                    "robot", "robot.stop", write=True, timeout=0.5
                )
            except Exception as e:
                self.store.errors.append("stop: " + str(e)[:400])
        if release:
            self.lease.clear()

    def snapshot(self, mode=None):
        if self.config["demo"] or mode == "simulation":
            data = dict(
                source="simulation",
                epoch="demo",
                state=None,
                tof=None,
                health={},
                ages={},
                identity={"verified": False},
                policies={},
                pad={},
                errors=[],
            )
            data.update(
                control=dict(
                    occupied=False,
                    enabled=False,
                    armed=False,
                    source=None,
                    requested=[0, 0, 0],
                    reason="独立演练",
                ),
                environment=dict(theme="garden", fresh=False, estimated=True),
                camera=dict(enabled=False),
                helper={},
                hardware_acceptance="NOT_RUN",
            )
            return data
        else:
            data = self.store.snapshot()
        data.update(
            control={
                **self.lease.status(),
                "enabled": self.config["controls_enabled"],
                "reason": self.last_error,
            },
            environment=self.environment.status(),
            camera=self.camera.status(),
            helper=self.helper_state,
            hardware_acceptance="NOT_RUN",
        )
        return data

    async def tick(self):
        try:
            while True:
                start = time.monotonic()
                if self.lease.expire():
                    await self.stop_motion("输入超过300ms，已停车；重新归中才能继续")
                if self.lease.owner and not self.handoff_busy:
                    try:
                        self.store.ready(movement=self.lease.last is not None)
                    except Refused as e:
                        await self.stop_motion(e.code + ": " + str(e), release=True)
                    if self.helper_at is not None and start - self.helper_at > 2.5:
                        await self.stop_motion("助手健康超过2.5秒", release=True)
                    if start - self.last_helper > 1 and (
                        self.helper_task is None or self.helper_task.done()
                    ):
                        self.last_helper = start

                        async def check_helper():
                            try:
                                self.helper_state = await asyncio.wait_for(
                                    self.helper_call("status"), 2
                                )
                                self.helper_at = time.monotonic()
                                if not self.helper_state.get("pending") or any(
                                    p.get("MainPID") not in ("0", None)
                                    for p in self.helper_state.get("units", {}).values()
                                ):
                                    await self.stop_motion(
                                        "手柄暂停条件失效", release=True
                                    )
                            except Exception as e:
                                await self.stop_motion(
                                    "助手不可用: " + str(e), release=True
                                )

                        self.helper_task = asyncio.create_task(check_helper())
                active = any(
                    c["mode"] == "live" and c["visible"] for c in self.sockets.values()
                )
                if active and not self.config["demo"]:
                    self.grace = None
                    await self.broker.start()
                    await self.broker.inputs(
                        any(
                            c["mode"] == "live"
                            and c["visible"]
                            and c.get("pad_details")
                            for c in self.sockets.values()
                        )
                    )
                elif self.broker.tasks:
                    if self.grace is None:
                        self.grace = start
                    if start - self.grace > 3 and not self.lease.owner:
                        await self.broker.stop()
                if start - self.last_resource > 5:
                    self.cpu_percent = (
                        (time.process_time() - self.last_cpu)
                        / (start - self.last_resource)
                        * 100
                    )
                    self.last_cpu = time.process_time()
                    self.last_resource = start
                await asyncio.sleep(max(0.005, 0.05 - (time.monotonic() - start)))
        finally:
            await self.stop_motion("Studio退出", release=True)
            await self.broker.stop()

    async def start(self, app):
        self.http = ClientSession(trust_env=False)
        self.task = asyncio.create_task(self.tick())

    async def cleanup(self, app):
        await self.stop_motion("Studio关闭", release=True)
        for ctx in list(self.sockets.values()):
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ctx["ws"].close(), 0.5)
        for job in list(self.jobs.values()):
            if job.get("task"):
                job["task"].cancel()
        await asyncio.gather(
            *(j["task"] for j in self.jobs.values() if j.get("task")),
            return_exceptions=True,
        )
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.helper_task:
            self.helper_task.cancel()
            await asyncio.gather(self.helper_task, return_exceptions=True)
        if self.http:
            await self.http.close()

    async def ws_command(self, ctx, data):
        op = data.get("op")
        if op == "details":
            ctx["pad_details"] = data.get("pad") is True
            return dict(pad_details=ctx["pad_details"])
        if op == "mode":
            if data.get("mode") not in ("live", "simulation"):
                raise Refused("MODE", "模式未知")
            if self.config["demo"] and data["mode"] == "live":
                raise Refused("DEMO_ONLY", "此进程仅演练")
            if self.lease.owner == ctx["id"]:
                await self.stop_motion("切换模式", release=True)
            if self.environment.owner == ctx["id"]:
                self.environment.owner = None
            ctx["mode"] = data["mode"]
            return dict(mode=ctx["mode"])
        if op == "visible":
            ctx["visible"] = data.get("visible") is True
            if not ctx["visible"] and self.environment.owner == ctx["id"]:
                self.environment.owner = None
            if not ctx["visible"] and self.lease.owner == ctx["id"]:
                await self.stop_motion("页面不可见", release=True)
            return dict(visible=ctx["visible"])
        if op == "stop":
            if self.lease.owner != ctx["id"]:
                raise Refused("LEASE_REQUIRED", "没有本连接控制权")
            await self.stop_motion("用户停车")
            return dict(submitted=True, executed=False)
        if op == "claim":
            self.control(ctx)
            if data.get("confirm") is not True:
                raise Refused("CONFIRM", "确认现场、暂停手柄服务并接管")
            if self.lease.owner:
                raise Refused("CONTROL_BUSY", "控制端已占用")
            # Gate lease before awaiting helper, so simultaneous claims cannot both stop pads.
            self.lease.claim(ctx["id"], data.get("source"))
            self.handoff_busy = True
            try:
                self.helper_state = await self.helper_call("take", confirm=True)
                self.helper_at = time.monotonic()
            except BaseException:
                self.lease.clear()
                raise
            finally:
                self.handoff_busy = False
            return dict(acquired=True, neutral_required=True)
        if op == "return":
            self.live_context(ctx)
            if data.get("confirm") is not True:
                raise Refused("CONFIRM", "请确认全部按键释放、摇杆居中后交还")
            if self.lease.owner not in (None, ctx["id"]):
                raise Refused("CONTROL_BUSY", "其他控制端持有控制权")
            await self.stop_motion("明确交还手柄", release=True)
            self.helper_state = await self.helper_call("return", confirm=True)
            return self.helper_state
        if op == "park":
            self.live_context(ctx)
            if data.get("confirm") is not True:
                raise Refused("CONFIRM", "需要明确恢复暂停状态")
            if self.lease.owner not in (None, ctx["id"]):
                raise Refused("CONTROL_BUSY", "其他控制端占用")
            await self.stop_motion("恢复暂停", release=True)
            self.helper_state = await self.helper_call("park", confirm=True)
            return self.helper_state
        if op == "switch_source":
            self.control(ctx)
            if self.lease.owner != ctx["id"] or self.lease.last is not None:
                raise Refused("NOT_PARKED", "先停车再切换输入")
            from .core import INPUTS

            if data.get("source") not in INPUTS:
                raise Refused("INPUT_SOURCE", "来源未知")
            self.lease.source = data["source"]
            self.lease.seq = -1
            self.lease.armed = False
            return dict(source=self.lease.source, neutral_required=True)
        if op == "input":
            self.control(ctx, movement=data.get("held") is True)
            if self.mutation.locked():
                raise Refused("CONFIG_BUSY", "配置操作进行中，移动暂停")
            was_held = self.lease.last is not None
            values = self.lease.submit(
                ctx["id"],
                data.get("seq"),
                data.get("source"),
                data.get("held"),
                data.get("values"),
            )
            if data.get("held"):
                result = await self.broker.ipc.rpc(
                    "robot",
                    "robot.move",
                    dict(zip(("vx", "vy", "vyaw"), values)),
                    write=True,
                    timeout=0.2,
                )
                return dict(submitted=True, executed=False)
            if was_held:
                await self.broker.ipc.rpc(
                    "robot", "robot.stop", write=True, timeout=0.2
                )
            return dict(submitted=True, executed=False)
        if op == "analysis":
            self.live_context(ctx)
            if not ctx["visible"]:
                raise Refused("PAGE_HIDDEN", "后台暂停分析")
            if not self.config["camera_enabled"]:
                raise Refused("CAMERA_DISABLED", "本地配置未启用相机感知")
            if data.get("enabled") is True:
                self.environment.claim(ctx["id"])
            elif self.environment.owner == ctx["id"]:
                self.environment.owner = None
            return self.environment.status()
        if op == "analysis_ping":
            self.live_context(ctx)
            if not ctx["visible"]:
                raise Refused("PAGE_HIDDEN", "后台暂停分析")
            if self.environment.owner != ctx["id"]:
                raise Refused("ANALYSIS_OWNER", "本连接不持有分析权")
            self.environment.heartbeat = time.monotonic()
            return dict(ok=True)
        if op == "semantic":
            self.live_context(ctx)
            if not ctx["visible"]:
                raise Refused("PAGE_HIDDEN", "后台不接收分析结果")
            theme = self.environment.submit(
                ctx["id"], data, self.camera.frame, self.asset_manifest.get("model_sha")
            )
            return dict(theme=theme, display_only=True)
        raise Refused("OPERATION", "操作不在Studio白名单")


@web.middleware
async def secure(request, handler):
    studio = request.app["studio"]
    cfg = studio.config
    try:
        if (
            request.path.startswith(PREFIX)
            and request.content_length
            and request.content_length > (2*1024*1024 if request.path == PREFIX + '/servo-workbench/reports' else 48000)
        ):
            raise Refused("REQUEST_SIZE", "Studio请求超过48KB")
        origin = cfg["public_origin"]
        expected = urllib.parse.urlsplit(origin)
        if request.host != expected.netloc:
            raise Refused("HOST", "Host不在配置白名单")
        incoming = request.headers.get("Origin")
        if incoming and incoming != origin:
            raise Refused("ORIGIN", "跨站请求被拒绝")
        if request.method not in ("GET", "HEAD") and incoming != origin:
            raise Refused("ORIGIN", "写操作要求同源Origin")
        public = request.path in (
            "/",
            "/studio/",
            "/api/studio/v1/pair",
        ) or request.path.startswith("/static/")
        if not public:
            token = request.cookies.get("studio_session")
            session = studio.sessions.get(token)
            if not session or session["expires"] < time.monotonic():
                raise Refused("AUTH", "请使用本地一次性配对码登录")
            request["session"] = session
            if (
                request.method not in ("GET", "HEAD")
                and request.path.startswith(PREFIX)
                and not secrets.compare_digest(
                    request.headers.get("X-Studio-CSRF", ""), session["csrf"]
                )
            ):
                raise Refused("CSRF", "CSRF校验失败")
        response = await handler(request)
    except Refused as e:
        response = web.json_response(
            dict(error=str(e), code=e.code),
            status=(
                401
                if e.code == "AUTH"
                else 409 if e.code not in ("HOST", "ORIGIN", "CSRF") else 403
            ),
        )
    except web.HTTPException:
        raise
    except (ValueError, TypeError, KeyError) as e:
        response = web.json_response(
            dict(error=str(e)[:500], code="INVALID_REQUEST"), status=400
        )
    except Exception as e:
        studio.last_error = scrub(e)
        response = web.json_response(
            dict(error=scrub(e), code="UNAVAILABLE"), status=503
        )
    if not isinstance(response, web.WebSocketResponse):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = (
            "private, max-age=86400"
            if request.path.startswith("/models/")
            else "no-store"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'none'"
        )
        response.headers["Permissions-Policy"] = (
            "accelerometer=(self), gyroscope=(self), magnetometer=(), camera=(), microphone=()"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def pair(request):
    studio = request.app["studio"]
    key = request.remote or "unknown"
    now = time.monotonic()
    if key not in studio.attempts:
        if len(studio.attempts) >= 64:
            studio.attempts = {
                k: v for k, v in studio.attempts.items() if v and now - v[-1] < 60
            }
        if len(studio.attempts) >= 64:
            raise Refused("PAIR_LIMIT", "配对请求过多")
        studio.attempts[key] = deque(maxlen=5)
    attempts = studio.attempts[key]
    if len(attempts) == 5 and now - attempts[0] < 60:
        raise Refused("PAIR_RATE", "每分钟最多5次配对尝试")
    attempts.append(now)
    body = await request.json()
    code = str(body.get("code", ""))
    path = Path(studio.config["state_dir"]) / "pairing.json"
    record = json.loads(path.read_text())
    if (
        record.get("used")
        or time.time() > record["expires"]
        or not secrets.compare_digest(
            hashlib.sha256(code.encode()).hexdigest(), record["sha256"]
        )
    ):
        raise Refused("PAIR_CODE", "配对码错误、过期或已使用")
    record["used"] = True
    helper.atomic(path, json.dumps(record).encode())
    studio.sessions = {k: v for k, v in studio.sessions.items() if v["expires"] > now}
    if len(studio.sessions) >= 32:
        raise Refused("SESSION_LIMIT", "会话达到上限，稍后重试")
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    session = dict(id=secrets.token_hex(12), csrf=csrf, expires=now + 8 * 3600)
    studio.sessions[token] = session
    response = web.json_response(dict(ok=True, csrf=csrf, demo=studio.config["demo"]))
    response.set_cookie(
        "studio_session",
        token,
        secure=studio.config["public_origin"].startswith("https:"),
        httponly=True,
        samesite="Strict",
        max_age=8 * 3600,
    )
    return response


async def status(request):
    studio = request.app["studio"]
    return web.json_response(
        dict(
            **studio.snapshot(),
            csrf=request["session"]["csrf"],
            demo=studio.config["demo"],
            scenes=SCENES,
            assets=studio.asset_manifest,
        )
    )


async def websocket(request):
    studio = request.app["studio"]
    if request.headers.get("Origin") != studio.config["public_origin"]:
        raise Refused("ORIGIN", "WebSocket须同源")
    if len(studio.sockets) >= 2:
        raise Refused("CLIENT_LIMIT", "最多两个活跃显示端")
    ws = web.WebSocketResponse(heartbeat=5, max_msg_size=4096, compress=False)
    await ws.prepare(request)
    ident = secrets.token_hex(12)
    ctx = dict(
        id=ident,
        ws=ws,
        session=request["session"]["id"],
        mode="simulation" if studio.config["demo"] else "live",
        visible=True,
    )
    studio.sockets[ident] = ctx
    await ws.send_json(dict(type="welcome", connection_id=ident, mode=ctx["mode"]))

    async def publish():
        last_tof = None
        last_health = 0
        tokens = 16384.0
        at = time.monotonic()
        while True:
            if not ctx["visible"]:
                await asyncio.sleep(0.5)
                continue
            now = time.monotonic()
            data = studio.snapshot(ctx["mode"])
            data["control"]["you"] = studio.lease.owner == ctx["id"]
            # Different presentation rates share one native subscription and one current cache.
            if (
                studio.store.tof_hz != 5
                and last_tof is not None
                and now - last_tof < 0.5
            ):
                data.pop("tof", None)
            else:
                last_tof = now
            if now - last_health < 1:
                for k in ("health", "policies", "pad", "errors", "identity"):
                    data.pop(k, None)
            else:
                last_health = now
            raw = json.dumps(
                dict(type="frame", data=data),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            tokens = min(16384.0, tokens + (now - at) * 16384)
            at = now
            if len(raw.encode()) <= tokens:
                tokens -= len(raw.encode())
                await asyncio.wait_for(ws.send_str(raw), 0.5)
                studio.bytes_sent += len(raw.encode())
            await asyncio.sleep(0.2)

    async def bounded_publish():
        try:
            await publish()
        except (asyncio.TimeoutError, ConnectionError, RuntimeError):
            await ws.close(code=1008, message=b"slow display")

    publisher = asyncio.create_task(bounded_publish())
    slot = None
    wake = asyncio.Event()

    async def reply(data, result=None, error=None):
        message = dict(type="reply", id=data.get("id"), result=result)
        if error is not None:
            message = dict(
                type="error",
                id=data.get("id"),
                code=getattr(error, "code", "REQUEST"),
                error=scrub(error),
            )
        await asyncio.wait_for(ws.send_json(message), 0.5)

    async def consume_input():
        nonlocal slot
        while True:
            await wake.wait()
            wake.clear()
            if slot is None:
                continue
            data, received = slot
            slot = None
            try:
                if time.monotonic() - received > 0.15:
                    raise Refused("INPUT_OLD", "等待过久的输入已丢弃；不会刷新移动心跳")
                result = await studio.ws_command(ctx, data)
                await reply(data, result)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if studio.lease.owner == ident:
                    await studio.stop_motion(
                        getattr(e, "code", "INPUT") + ": " + str(e)
                    )
                await reply(data, error=e)

    consumer = asyncio.create_task(consume_input())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = {}
            try:
                data = json.loads(
                    msg.data,
                    parse_constant=lambda v: (_ for _ in ()).throw(
                        ValueError("非法JSON")
                    ),
                )
                if not isinstance(data, dict):
                    data = {}
                    raise ValueError("对象请求")
                if data.get("op") == "input":
                    if slot is not None:
                        await reply(
                            slot[0], dict(dropped=True, submitted=False, executed=False)
                        )
                    slot = (data, time.monotonic())
                    wake.set()
                    continue
                # A stop or source/lifecycle operation cannot be followed by an old queued move.
                if data.get("op") in (
                    "stop",
                    "return",
                    "park",
                    "mode",
                    "visible",
                    "switch_source",
                    "claim",
                ):
                    slot = None
                    wake.clear()
                    consumer.cancel()
                    await asyncio.gather(consumer, return_exceptions=True)
                    consumer = asyncio.create_task(consume_input())
                result = await studio.ws_command(ctx, data)
                await reply(data, result)
            except Exception as e:
                await reply(data, error=e)
    finally:
        consumer.cancel()
        publisher.cancel()
        await asyncio.gather(consumer, publisher, return_exceptions=True)
        studio.sockets.pop(ident, None)
        if studio.lease.owner == ident:
            await studio.stop_motion("控制连接断开", release=True)
        if studio.environment.owner == ident:
            studio.environment.owner = None
    return ws


async def context(request, body):
    studio = request.app["studio"]
    ctx = studio.sockets.get(body.get("connection_id"))
    if not ctx or ctx["session"] != request["session"]["id"]:
        raise Refused("CONNECTION", "需要本会话的活跃连接")
    return studio, ctx


def read_mode(request):
    studio = request.app["studio"]
    if studio.config["demo"]:
        return "simulation"
    ctx = studio.sockets.get(request.query.get("connection_id"))
    if not ctx or ctx["session"] != request["session"]["id"]:
        raise Refused("CONNECTION", "读取须来自本会话活跃连接，以区分实体/演练")
    return ctx["mode"]


async def query(request):
    studio = request.app["studio"]
    if read_mode(request) == "simulation":
        return web.json_response(
            dict(
                source="simulation",
                policies={},
                skills={"skills": []},
                bindings={"bindings": []},
                pad={"pads": []},
                hardware_acceptance="NOT_RUN",
            )
        )
    queries = (
        ("robot", "robot.policies"),
        ("robot", "robot.skills"),
        ("robot", "pad.bindings"),
        ("config", "pad.status"),
    )
    results = await asyncio.gather(
        *[studio.broker.ipc.rpc(service, method) for service, method in queries],
        return_exceptions=True,
    )
    return web.json_response(
        {
            key: dict(error=scrub(value)) if isinstance(value, Exception) else value
            for key, value in zip(("policies", "skills", "bindings", "pad"), results)
        }
    )


def validate_action(op, p):
    if not isinstance(p, dict):
        raise Refused("PARAMS", "参数必须为对象")
    limits = {
        "enable": {"on"},
        "skill": {"skill"},
        "sound": {"tag"},
        "load_policy": {"slot", "path"},
        "set_skill": {"name", "path", "duration", "command"},
        "remove_skill": {"name"},
        "bind": {"button", "skill"},
        "pair": {"mac", "timeout_seconds"},
        "forget": {"mac"},
        "fetch": {"repo", "revision", "file"},
        "scan": set(),
        "connect": {"mac"},
        "disconnect": {"mac"},
    }
    if op not in limits or set(p) - limits[op]:
        raise Refused("ACTION_FIELDS", "操作/参数不在白名单")
    if any(
        isinstance(v, str) and (len(v) > 500 or "\x00" in v or "\n" in v)
        for v in p.values()
    ):
        raise Refused("STRING", "字段过长或含控制符")
    if op == "enable" and type(p.get("on")) is not bool:
        raise Refused("ENABLE", "on必须为布尔值")
    if op == "sound" and p.get("tag") not in (
        "alarm",
        "greet",
        "inquire",
        "peck",
        "chirp",
        "coo",
    ):
        raise Refused("SOUND", "只允许已核对的一次性声音")
    if op in ("load_policy", "set_skill") and p.get("path") is not None:
        path = Path(str(p["path"]))
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.suffix != ".onnx"
            or not str(path).startswith(("/opt/robot/", "/var/lib/robot/"))
        ):
            raise Refused("MODEL_PATH", "模型只允许机器人模型目录中的绝对.onnx路径")
    import re

    for key in ("name", "skill"):
        if (
            key in p
            and p[key] is not None
            and not re.fullmatch(r"[a-zA-Z0-9_-]{0,64}", str(p[key]))
        ):
            raise Refused("SKILL_NAME", "动作名称非法")
    if op == "bind" and p.get("button") not in ("a", "b", "x", "y", "lb", "rb"):
        raise Refused("BUTTON", "按键不在0.18.3可绑定列表")
    if (
        op in ("pair", "forget", "connect", "disconnect")
        and p.get("mac") is not None
        and not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", str(p["mac"]))
    ):
        raise Refused("MAC", "MAC格式非法")
    if op == "pair" and (
        "timeout_seconds" in p
        and (
            type(p["timeout_seconds"]) is not int or not 1 <= p["timeout_seconds"] <= 60
        )
    ):
        raise Refused("PAIR_TIMEOUT", "配对窗口须为1–60秒")
    if op == "fetch" and not re.fullmatch(r"[\w.-]+/[\w.-]+", str(p.get("repo", ""))):
        raise Refused("HUB_REPO", "需要明确org/name")
    if op == "set_skill":
        from .core import number, vector

        if (
            not p.get("name")
            or not number(p.get("duration"))
            or not 0.1 <= p["duration"] <= 60
        ):
            raise Refused("SKILL_DURATION", "需名称和0.1–60秒持续时间")
        cmd = vector(p.get("command", [0, 0, 0]), 3)
        if cmd is None or any(
            abs(x) > limit for x, limit in zip(cmd, (0.1, 0.05, 0.4))
        ):
            raise Refused("SKILL_COMMAND", "动作命令超出初始限速")
    if op == "load_policy" and p.get("slot") not in (
        "walk",
        "stand",
        "sitstand",
        "ground_pick",
        "kick_left",
        "kick_right",
        "roulade",
    ):
        raise Refused("SLOT", "槽位未核对")
    # JSON finite enforcement applies recursively to vectors and numeric skill parameters.
    json.dumps(p, allow_nan=False)


async def action(request):
    body = await request.json()
    studio, ctx = await context(request, body)
    studio.idle(ctx)
    if body.get("confirm") is not True:
        raise Refused("CONFIRM", "请明确确认操作及影响")
    op = body.get("operation")
    params = body.get("params", {})
    validate_action(op, params)
    if studio.mutation.locked():
        raise Refused("CONFIG_BUSY", "另一个配置/配对操作进行中")
    if len(studio.jobs) >= 16:
        studio.jobs = {
            k: v for k, v in studio.jobs.items() if v.get("state") == "running"
        }
    if len(studio.jobs) >= 16:
        raise Refused("JOB_LIMIT", "操作队列达到上限")
    ident = secrets.token_hex(10)
    job = dict(
        state="running",
        operation=op,
        source="live",
        automatic_rollback=False,
        session=request["session"]["id"],
    )
    studio.jobs[ident] = job

    async def run():
        async with studio.mutation:
            try:
                await studio.stop_motion("配置操作先停车")
                studio.idle(
                    ctx
                )  # Recheck after scheduling: a disconnected owner cannot leave queued writes.
                if op in ("scan", "connect", "disconnect"):
                    result = await studio.helper_call(op, **params)
                else:
                    methods = {
                        "enable": ("robot", "robot.enable"),
                        "skill": ("robot", "robot.do"),
                        "sound": ("robot", "robot.sound"),
                        "load_policy": ("robot", "robot.loadPolicy"),
                        "set_skill": ("robot", "robot.setSkill"),
                        "remove_skill": ("robot", "robot.removeSkill"),
                        "bind": ("robot", "pad.bind"),
                        "pair": ("config", "pad.pair"),
                        "forget": ("config", "pad.forget"),
                        "fetch": ("updater", "policy.fetch"),
                    }
                    service, method = methods[op]
                    result = await studio.broker.ipc.rpc(
                        service,
                        method,
                        params,
                        write=True,
                        timeout=65 if op in ("pair", "fetch") else 12,
                    )
                job.update(
                    state="completed",
                    result=result,
                    verify="提交/接受不等于运动已完成；请核对实际反馈",
                )
                if op not in (
                    "scan",
                    "connect",
                    "disconnect",
                    "pair",
                    "forget",
                    "fetch",
                ):
                    job["readback"] = await studio.broker.ipc.rpc(
                        "robot", "robot.policies"
                    )
                    if op == "bind":
                        job["bindings"] = await studio.broker.ipc.rpc(
                            "robot", "pad.bindings"
                        )
                    if op in ("set_skill", "remove_skill"):
                        job["skills"] = await studio.broker.ipc.rpc(
                            "robot", "robot.skills"
                        )
            except Exception as e:
                job.update(
                    state="failed",
                    error=scrub(e),
                    code=getattr(e, "code", "ACTION_FAILED"),
                    next="查看详细诊断；保留部分结果，明确恢复本次变更，不盲目重复提交",
                )

    job["task"] = asyncio.create_task(run())
    return web.json_response(dict(job_id=ident, state="running"), status=202)


async def jobs(request):
    studio = request.app["studio"]
    job = studio.jobs.get(request.match_info["id"])
    if not job or job["session"] != request["session"]["id"]:
        raise Refused("JOB_UNKNOWN", "没有本会话操作")
    return web.json_response(
        {k: v for k, v in job.items() if k not in ("task", "session")}
    )


async def diagnostics(request):
    studio = request.app["studio"]
    extra = []
    if studio.camera.error:
        extra.append(("CAMERA", studio.camera.error, studio.camera.status()))
    if studio.last_error:
        extra.append(("CONTROL", studio.last_error, studio.lease.status()))
    if not studio.environment.status()["fresh"]:
        extra.append(
            (
                "VISION",
                studio.environment.status()["reason"],
                studio.environment.status(),
            )
        )
    mode = read_mode(request)
    report = studio.diagnostics.evaluate(studio.store, extra) if mode == "live" else {}
    if mode == "simulation":
        report = dict(
            version=1,
            source="simulation",
            playbooks=__import__("studio.diagnostics", fromlist=["BOOK"]).BOOK,
            issues=[],
            hardware_acceptance="NOT_RUN",
            message="演练诊断由本地故障案例生成，未查询真实硬件",
        )
    if request.query.get("export") == "1":
        report.update(
            state=studio.snapshot(mode),
            exported_at=time.time(),
            credentials_included=False,
        )
        return web.Response(
            text=json.dumps(report, ensure_ascii=False, indent=2),
            content_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="studio-evidence.json"'
            },
        )
    return web.json_response(report)


async def logs(request):
    studio = request.app["studio"]
    unit = request.query.get("unit", "robotd")
    if unit not in ("robotd", "mediad", "tofd", "padd", "netpadd", "configd"):
        raise Refused("LOG_UNIT", "日志服务不在白名单")
    if read_mode(request) == "simulation":
        return web.json_response(
            dict(source="simulation", lines=["DEMO: no native log read"])
        )
    result = await studio.broker.ipc.rpc(
        "config", "system.logs", dict(unit=unit, lines=100, boot=0)
    )
    result["lines"] = [scrub(x) for x in result.get("lines", [])[:100]]
    return web.json_response(result)


async def camera(request):
    studio = request.app["studio"]
    ctx = studio.sockets.get(request.query.get("connection_id"))
    if (
        not ctx
        or ctx["session"] != request["session"]["id"]
        or studio.environment.owner != ctx["id"]
    ):
        raise Refused("ANALYSIS_OWNER", "先在此显示端明确启用环境分析")
    studio.live_context(ctx)
    if not ctx["visible"]:
        raise Refused("PAGE_HIDDEN", "后台不取帧")
    frame = await studio.camera.fetch()
    return web.Response(
        body=frame["png"],
        content_type="image/png",
        headers={
            "X-Frame-Id": frame["id"],
            "X-Frame-Age-Ms": str(round((time.monotonic() - frame["at"]) * 1000)),
            "X-Next-Frame-Seconds": str(studio.camera.interval),
        },
    )


async def model(request):
    data = request.app["studio"].store.model
    return web.json_response(
        {
            k: data.get(k)
            for k in (
                "asset",
                "joint_names",
                "tof_beams",
                "tof_fov_deg",
                "trunk_height_m",
            )
            if k in data
        }
    )


async def resources(request):
    studio = request.app["studio"]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if os.uname().sysname == "Linux":
        rss *= 1024
    return web.json_response(
        dict(
            cpu_single_core_percent=studio.cpu_percent,
            rss_high_water_bytes=rss,
            bytes_sent=studio.bytes_sent,
            clients=len(studio.sockets),
            cache_limit_bytes=2 * 1024 * 1024,
            camera_cache_limit_bytes=3 * 1024 * 1024,
            hardware_acceptance="NOT_RUN",
            camera=studio.camera.status(),
            native_services_included_in_this_process=False,
        )
    )


async def static(request):
    studio = request.app["studio"]
    kind = request.match_info.get("kind", "static")
    relative = request.match_info.get("path", "index.html")
    directory = (
        Path(__file__).parent / "static"
        if kind == "static"
        else Path(
            studio.config["asset_dir"]
            if kind == "assets"
            else studio.config["model_dir"]
        )
    )
    if kind == "models" and relative.split("/", 1)[0] == studio.asset_manifest.get(
        "model_revision"
    ):
        relative = relative.split("/", 1)[1] if "/" in relative else ""
    path = directory / relative
    if (
        ".." in Path(relative).parts
        or path.is_symlink()
        or not path.resolve().is_relative_to(directory.resolve())
        or not path.is_file()
    ):
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def legacy(request):
    studio = request.app["studio"]
    path = request.path
    if studio.config["demo"]:
        raise Refused("DEMO_LEGACY", "演练不连接旧Web；请使用独立旧DEMO入口")
    if path == "/calibration/":
        upstream = "/"
    elif (
        path in LEGACY_GET
        and request.method == "GET"
        or path in LEGACY_POST
        and request.method == "POST"
    ):
        upstream = path
    elif (
        path.startswith("/api/servo-health/")
        and path.rsplit("/", 1)[-1] in LEGACY_SERVO
    ):
        upstream = path
    else:
        raise web.HTTPNotFound()
    origin = f"http://127.0.0.1:{studio.config['legacy_port']}"
    headers = {"Host": urllib.parse.urlsplit(origin).netloc, "Origin": origin}
    if request.headers.get("X-Calibration-CSRF"):
        headers["X-Calibration-CSRF"] = request.headers["X-Calibration-CSRF"]
    if request.headers.get("Content-Type"):
        headers["Content-Type"] = request.headers["Content-Type"]
    if path == "/api/camera/frame":
        frame = await studio.camera.fetch()
        return web.Response(body=frame["png"], content_type="image/png")
    data = await request.read() if request.method == "POST" else None
    async with studio.http.request(
        request.method,
        origin
        + upstream
        + ("?" + request.query_string if request.query_string else ""),
        data=data,
        headers=headers,
        allow_redirects=False,
        timeout=ClientTimeout(total=6),
    ) as response:
        raw = bytearray()
        async for part in response.content.iter_chunked(65536):
            raw.extend(part)
            if len(raw) > 3 * 1024 * 1024:
                raise Refused("LEGACY_SIZE", "旧Web回复超过网关预算")
        raw = bytes(raw)
        content = response.headers.get("Content-Type", "application/json")
        if upstream == "/" and response.status == 200:
            raw = raw.replace(
                b"</head>",
                b'<link rel="stylesheet" href="/static/calibration-mobile.css"><script defer src="/static/calibration-mobile.js"></script></head>',
            )
        return web.Response(
            body=raw, status=response.status, headers={"Content-Type": content}
        )


async def workbench_reports(request):
    """Historical file exchange only; no controller, serial access or live acceptance."""
    from servo_workbench.reports import validate
    from servo_workbench.profiles import Refused as ReportRefused, digest
    directory = Path(request.app['studio'].config['state_dir']) / 'servo-workbench-reports'

    def exchange(value=None):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if value is not None:
            try:
                report = validate(value)
            except ReportRefused as error:
                raise Refused(error.code, str(error)) from error
            ident = digest(report)
            target = directory / (ident + '.json')
            import tempfile
            with tempfile.NamedTemporaryFile(dir=directory, delete=False, mode='w') as f:
                json.dump(report, f, ensure_ascii=False, allow_nan=False)
                f.flush(); os.fsync(f.fileno())
                temporary = Path(f.name)
            temporary.replace(target)
            return dict(id=ident, historical=True, live=False, hardware_acceptance=False)
        ident = request.query.get('id')
        if ident:
            if len(ident)!=64 or any(c not in '0123456789abcdef' for c in ident):
                raise Refused('REPORT_ID', '报告ID错误')
            return validate(json.loads((directory / (ident+'.json')).read_text()))
        return [dict(id=p.stem) for p in sorted(directory.glob('*.json'))]

    value = None
    if request.method == 'POST':
        raw = bytearray()
        async for part in request.content.iter_chunked(65536):
            raw.extend(part)
            if len(raw)>2*1024*1024:
                raise Refused('REPORT_SIZE', '报告超过2MiB')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise Refused('REPORT_SCHEMA', '报告必须是对象')
    return web.json_response(await asyncio.to_thread(exchange, value))


def create_app(config=None, store=None, broker=None, helper_call=None):
    studio = Studio(config, store, broker, helper_call)
    app = web.Application(middlewares=[secure], client_max_size=1048576)
    app["studio"] = studio
    app.on_startup.append(studio.start)
    app.on_cleanup.append(studio.cleanup)

    async def page(request):
        return web.FileResponse(Path(__file__).parent / "static/index.html")

    async def root(request):
        raise web.HTTPFound("/studio/")

    app.router.add_get("/", root)
    app.router.add_get("/studio/", page)
    async def report_page(request):
        return web.FileResponse(Path(__file__).parent / 'static/workbench-reports.html')
    app.router.add_get('/servo-workbench/', report_page)
    app.router.add_get(PREFIX + '/servo-workbench/reports', workbench_reports)
    app.router.add_post(PREFIX + '/servo-workbench/reports', workbench_reports)
    app.router.add_post(PREFIX + "/pair", pair)
    app.router.add_get(PREFIX + "/status", status)
    app.router.add_get(PREFIX + "/stream", websocket)
    app.router.add_get(PREFIX + "/query", query)
    app.router.add_post(PREFIX + "/action", action)
    app.router.add_get(PREFIX + "/jobs/{id}", jobs)
    app.router.add_get(PREFIX + "/diagnostics", diagnostics)
    app.router.add_get(PREFIX + "/logs", logs)
    app.router.add_get(PREFIX + "/camera", camera)
    app.router.add_get(PREFIX + "/resources", resources)
    app.router.add_get(PREFIX + "/model", model)
    for kind in ("static", "assets", "models"):
        app.router.add_get("/{kind:" + kind + "}/{path:.*}", static)
    app.router.add_route("*", "/calibration/", legacy)
    app.router.add_route("*", "/api/{tail:.*}", legacy)
    app.router.add_get("/servo-health.js", legacy)
    return app


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--port", type=int)
    p.add_argument("--assets", type=Path)
    p.add_argument("--models", type=Path)
    p.add_argument("--state-dir", type=Path)
    a = p.parse_args()
    cfg = {**DEFAULTS, **(json.loads(a.config.read_text()) if a.config else {})}
    if a.demo:
        cfg["demo"] = True
        cfg["controls_enabled"] = False
        cfg["camera_enabled"] = False
    if a.port is not None:
        cfg["port"] = a.port
    for key, value in [
        ("asset_dir", a.assets),
        ("model_dir", a.models),
        ("state_dir", a.state_dir),
    ]:
        if value:
            cfg[key] = str(value)
    if cfg["demo"]:
        cfg["listen"] = "127.0.0.1"
        cfg["public_origin"] = f"http://127.0.0.1:{cfg['port']}"
    context = None
    if not cfg["demo"]:
        if not a.config:
            raise SystemExit("真实服务须使用本地setup生成的HTTPS配置，默认不启用控制")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cfg["cert"], cfg["key"])
    print(
        "Studio "
        + (
            "DEMO / NO HARDWARE"
            if cfg["demo"]
            else "LIVE / CONTROL " + str(cfg["controls_enabled"])
        )
        + " "
        + cfg["public_origin"],
        flush=True,
    )
    web.run_app(
        create_app(cfg),
        host=cfg["listen"],
        port=cfg["port"],
        ssl_context=context,
        access_log=None,
    )


if __name__ == "__main__":
    main()
