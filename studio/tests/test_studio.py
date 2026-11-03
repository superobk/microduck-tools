import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import CookieJar
from studio.core import Store, Lease, Environment, Refused, JOINTS
from studio.diagnostics import Diagnostics
from studio.helper import Handoff, UNITS
from studio.ipc import IPC, Broker
from studio.manage import Journal, verify_assets
from studio.server import create_app, Studio, validate_action, PREFIX


async def async_sleep(*a, **k):
    await asyncio.sleep(10)


class Clock:
    def __init__(self):
        self.now = 1.0

    def __call__(self):
        return self.now

    def advance(self, n):
        self.now += n


def state(stamp=1000000000):
    return dict(
        t_ns=stamp,
        imu=dict(quat=[1, 0, 0, 0], gyro=[0, 0, 0]),
        joints=[0] * 15,
        targets=[0] * 15,
        velocities=[0] * 15,
        currents_ma=[10] * 15,
        safety=dict(fallen=False, limp=False, picked_up=False, gravity=[0, 0, -1]),
        move=dict(requested=[0, 0, 0], applied=[0, 0, 0]),
        odom=dict(position=[0, 0, 0.12], yaw=0),
        policy="stand",
        loop=dict(hz=50, missed=0),
    )


def ready(clock=None):
    s = Store(clock or __import__("time").monotonic)
    s.ingest(state())
    s.health = {"healthy": True, "imu": {"ready": True, "consecutive_stale_blocks": 0}}
    s.health_at = s.clock()
    s.policies = dict(enabled=True, homed=True)
    s.identity_verified = s.calibration_verified = True
    return s


class CoreTests(unittest.TestCase):
    def test_duplicate_does_not_refresh(self):
        c = Clock()
        s = ready(c)
        c.advance(0.7)
        self.assertFalse(s.ingest(state()))
        self.assertEqual(s.age(s.state_at), 700)
        with self.assertRaisesRegex(Refused, "600ms"):
            s.ready()

    def test_timestamp_backwards_invalidates_proof(self):
        s = ready()
        epoch = s.epoch
        s.ingest(state(1))
        self.assertNotEqual(epoch, s.epoch)
        self.assertFalse(s.identity_verified)

    def test_daemon_restart_with_increasing_monotonic_time_resets_health(self):
        s = ready()
        first = state(2_000_000_000)
        first["t"] = 100
        s.ingest(first)
        epoch = s.epoch
        restarted = state(3_000_000_000)
        restarted["t"] = 0.2
        s.ingest(restarted)
        self.assertNotEqual(s.epoch, epoch)
        self.assertFalse(s.identity_verified)
        self.assertIsNone(s.health_at)
        self.assertFalse(s.policies)

    def test_missing_vectors_are_unknown(self):
        for key in ("joints", "targets", "velocities", "currents_ma"):
            for bad in ([], [0] * 14, [float("nan")] * 15, None):
                raw = state()
                raw[key] = bad
                s = Store()
                s.ingest(raw)
                self.assertIsNone(s.state[key])

    def test_safety_and_no_policy_not_bypassed(self):
        for key in ("fallen", "limp", "picked_up"):
            s = ready()
            s.state["safety"][key] = None
            with self.assertRaises(Refused):
                s.ready(True)
        s = ready()
        s.policies["enabled"] = False
        with self.assertRaisesRegex(Refused, "no-policy"):
            s.ready()

    def test_health_age_and_stale_blocks(self):
        c = Clock()
        s = ready(c)
        s.health["imu"]["consecutive_stale_blocks"] = 25
        with self.assertRaises(Refused):
            s.ready()
        s.health["imu"]["consecutive_stale_blocks"] = 0
        c.advance(3.1)
        s.ingest(state(2_000_000_000))
        with self.assertRaisesRegex(Refused, "健康信息"):
            s.ready()

    def test_lease_neutral_sequence_timeout(self):
        c = Clock()
        l = Lease(c)
        l.claim("one", "keyboard")
        with self.assertRaisesRegex(Refused, "归中"):
            l.submit("one", 1, "keyboard", True, [0.1, 0, 0])
        l.submit("one", 2, "keyboard", False, [0, 0, 0])
        c.advance(0.05)
        l.submit("one", 3, "keyboard", True, [0.1, 0, 0])
        with self.assertRaises(Refused):
            l.submit("one", 3, "keyboard", True, [0.1, 0, 0])
        c.advance(0.301)
        self.assertTrue(l.expire())
        self.assertFalse(l.armed)
        self.assertEqual(l.value, [0, 0, 0])

    def test_lease_rejects_cross_source_and_rate(self):
        c = Clock()
        l = Lease(c)
        l.claim("one", "touch")
        l.submit("one", 1, "touch", False, [0, 0, 0])
        for owner, source in [("two", "touch"), ("one", "gamepad")]:
            with self.assertRaises(Refused):
                l.submit(owner, 2, source, True, [0, 0, 0])
        with self.assertRaises(Refused):
            l.submit("one", 3, "touch", True, [0.2, 0, 0])
        c.advance(0.05)
        l.submit("one", 4, "touch", True, [0.1, 0, 0])
        with self.assertRaises(Refused):
            l.submit("one", 5, "touch", True, [0.1, 0, 0])

    def test_environment_two_distinct_frames_and_dwell(self):
        c = Clock()
        e = Environment(c)
        e.claim("a")

        def submit(frame, label="greenery", score=0.8):
            return e.submit(
                "a",
                dict(
                    frame_id=frame,
                    model_sha="hash",
                    label=label,
                    support=score,
                    margin=0.3,
                ),
                dict(id=frame, at=c()),
                "hash",
            )

        self.assertEqual(submit("one"), "garden")
        self.assertEqual(submit("one"), "garden")
        self.assertEqual(submit("two"), "forest")
        submit("3", "snow")
        self.assertEqual(submit("4", "snow"), "forest")
        c.advance(31)
        e.claim("a")
        self.assertEqual(submit("5", "snow"), "snow")
        c.advance(61)
        self.assertFalse(e.status()["fresh"])

    def test_environment_expired_wrong_model_and_low_support(self):
        c = Clock()
        e = Environment(c)
        e.claim("a")
        r = dict(frame_id="1", model_sha="h", label="snow", support=0.5, margin=0.4)
        f = dict(id="1", at=c())
        self.assertEqual(e.submit("a", r, f, "h"), "garden")
        r["model_sha"] = "x"
        with self.assertRaises(Refused):
            e.submit("a", r, f, "h")
        c.advance(16)
        r["model_sha"] = "h"
        with self.assertRaises(Refused):
            e.submit("a", r, f, "h")

    def test_camera_identity_accepts_official_nonroot_uid_and_rejects_wrong_peer(self):
        from studio.ipc import Identity, ROOT
        from unittest.mock import MagicMock
        import struct

        known = json.loads((ROOT / "deploy/manual-upgrade/inputs.json").read_text())
        identity = Identity(local=True)
        identity.info = {"pid": 123}
        client = MagicMock()
        client.__enter__.return_value = client
        client.getsockopt.return_value = struct.pack("3i", 123, 1001, 1001)
        client.recv.return_value = (
            json.dumps(
                dict(
                    id=1,
                    result=dict(
                        api_version=47,
                        daemon_version="0.18.3",
                        revision=known["base_revision"],
                    ),
                )
            )
            + "\n"
        ).encode()
        original = Path.read_text

        def read(path, *a, **kw):
            return (
                "Name: mediad\nUid:\t1001\t1001\t1001\t1001\n"
                if str(path) == "/proc/123/status"
                else original(path, *a, **kw)
            )

        with patch.object(identity, "executable", return_value=True), patch(
            "studio.ipc.socket.socket", return_value=client
        ), patch("studio.ipc.socket.SO_PEERCRED", 17, create=True), patch.object(
            Path, "read_text", read
        ):
            self.assertTrue(identity.camera())
            client.getsockopt.return_value = struct.pack("3i", 123, 0, 0)
            self.assertFalse(identity.camera())
            client.getsockopt.return_value = struct.pack("3i", 999, 1001, 1001)
            self.assertFalse(identity.camera())
        client.connect.assert_called_with("/run/mediad/media.sock")

    def test_diagnostics_historical_temperature_labeled(self):
        s = ready()
        s.health["motors"] = dict(max_c=70, hottest="left_knee")
        issues = Diagnostics().evaluate(s)["issues"]
        item = next(x for x in issues if x["code"] == "TEMPERATURE")
        self.assertIn("年龄未知", item["raw"])
        self.assertFalse(item["automatic_repair"])
        self.assertTrue(item["verification"])

    def test_actions_disallow_arbitrary_commands(self):
        for op, params in [
            ("shell", {"cmd": "reboot"}),
            ("load_policy", {"slot": "walk", "path": "/etc/evil.onnx"}),
            ("set_skill", {"name": "test", "duration": 1, "command": [1, 0, 0]}),
            ("bind", {"button": "start"}),
            ("sound", {"tag": "wheee"}),
        ]:
            with self.assertRaises(Refused):
                validate_action(op, params)
        validate_action("load_policy", {"slot": "walk"})
        validate_action("bind", {"button": "a"})
        validate_action(
            "set_skill", {"name": "test", "duration": 1, "command": [0, 0, 0]}
        )

    def test_installer_journal_refuses_drift_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d).resolve() / "state")
            p = Path(d).resolve() / "file"
            j.write(p, b"ours", uid=os.geteuid(), gid=os.getegid())
            p.write_bytes(b"external")
            with self.assertRaises(RuntimeError):
                j.rollback()
            self.assertEqual(p.read_bytes(), b"external")
            p.write_bytes(b"ours")
            j.rollback()
            j.rollback()
            self.assertFalse(p.exists())

    def test_interrupted_file_intent_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "state")
            with (
                patch("studio.manage.atomic", side_effect=lambda path, raw: None)
                if False
                else __import__("contextlib").nullcontext()
            ):
                # Durable intent can exist without the target after a power loss.
                j.data["files"] = [
                    dict(
                        path=str(Path(d) / "unwritten"),
                        sha256=hashlib.sha256(b"new").hexdigest(),
                        mode=0o644,
                        uid=os.geteuid(),
                        gid=os.getegid(),
                    )
                ]
                j.save()
            Journal(j.state).rollback()
            self.assertEqual(Journal(j.state).data["phase"], "rolled_back")


class FakeHandoff(Handoff):
    def __init__(self, root):
        super().__init__(root / "handoff", root / "systemd", root / "manager")
        self.rows = {
            u: dict(
                Id=u,
                LoadState="loaded",
                ActiveState="active" if i == 0 else "inactive",
                MainPID="5" if i == 0 else "0",
                ControlPID="0",
                Job="",
                ControlGroup="",
                UnitFileState="enabled",
                ExecStart="{ path=/opt/robot/daemon/current/bin/"
                + u[:-8]
                + " ; argv[]=/opt/robot/daemon/current/bin/"
                + u[:-8]
                + " ; }",
            )
            for i, u in enumerate(UNITS)
        }
        self.calls = []
        self.fail = None
        for u in UNITS:
            p = self.guard_path(u)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self.guard_raw())

    async def props(self, unit):
        return self.rows[unit].copy()

    def manager_lock(self):
        pass

    async def run(self, *args):
        self.calls.append(args)
        if self.fail == args[1]:
            raise RuntimeError("injected failure")
        if args[1] == "stop":
            self.rows[args[2]].update(ActiveState="inactive", MainPID="0")
        if args[1] == "start":
            self.rows[args[2]].update(ActiveState="active", MainPID="5")
        return ""


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = FakeHandoff(Path(self.tmp.name))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_record_survives_stop_failure_then_resume(self):
        self.h.fail = "stop"
        with self.assertRaises(RuntimeError):
            await self.h.take()
        self.assertTrue((self.h.state / "phone.pending").exists())
        self.h.fail = None
        await self.h.take()
        self.assertEqual(
            json.loads((self.h.state / "phone.pending").read_text())["active"],
            [UNITS[0]],
        )
        await self.h.release()
        self.assertEqual(self.h.rows[UNITS[0]]["ActiveState"], "active")
        self.assertEqual(self.h.rows[UNITS[1]]["ActiveState"], "inactive")

    async def test_failed_restore_keeps_guard(self):
        await self.h.take()
        self.h.fail = "start"
        with self.assertRaises(RuntimeError):
            await self.h.release()
        self.assertTrue((self.h.state / "phone.pending").exists())
        self.h.fail = None
        await self.h.park()
        await self.h.release()
        self.assertFalse((self.h.state / "phone.pending").exists())

    async def test_crash_after_return_intent_recovers_explicitly(self):
        await self.h.take()
        path = self.h.state / "phone.pending"
        (self.h.state / "return.intent").write_bytes(path.read_bytes())
        path.unlink()
        self.assertTrue((await self.h.status())["pending"])
        await self.h.take()
        self.assertTrue(path.exists())
        await self.h.release()

    async def test_no_unknown_hooks_or_two_active(self):
        self.h.rows[UNITS[1]].update(ActiveState="active")
        with self.assertRaises(Refused):
            await self.h.take()
        self.h.rows[UNITS[1]].update(ActiveState="inactive", ExecStop="unreviewed")
        with self.assertRaises(Refused):
            await self.h.take()
        self.assertFalse(self.h.calls)

    async def test_no_automatic_handback_on_park(self):
        await self.h.take()
        await self.h.park()
        self.assertTrue((self.h.state / "phone.pending").exists())
        self.assertFalse(any(c[1] == "start" for c in self.h.calls))


class FakeIPC:
    def __init__(self):
        self.calls = []

    async def rpc(self, *a, **kw):
        self.calls.append((a, kw))
        return dict(accepted=True)


class FakeBroker:
    def __init__(self):
        self.tasks = []
        self.ipc = FakeIPC()
        self.identity = object()

    async def start(self):
        pass

    async def inputs(self, wanted):
        pass

    async def stop(self):
        pass


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_workbench_import_is_historical_and_cannot_call_hardware(self):
        from servo_workbench.store import Store as WorkStore
        from servo_workbench.engine import Engine
        from servo_workbench.simulation import SimBus
        with tempfile.TemporaryDirectory() as d:
            s=WorkStore(d);e=Engine(s,SimBus());e.create('hd1910')
            report=e.report();s.close()
        report['hardware_acceptance']=True;report['session']['hardware_acceptance']=True
        # >48KB evidence is accepted on this route only.
        report['jobs']=[dict(evidence='x'*60000)]
        url=PREFIX+'/servo-workbench/reports'
        r=await self.client.post(url,json=report,headers=self.headers)
        self.assertEqual(r.status,200,await r.text());ident=(await r.json())['id']
        r=await self.client.get(url+'?id='+ident)
        value=await r.json()
        self.assertFalse(value['live']);self.assertFalse(value['hardware_acceptance'])
        self.assertFalse(value['session']['hardware_acceptance'])
        self.assertEqual(len(value['jobs'][0]['evidence']),60000)
        self.helper.assert_not_awaited();self.assertEqual(self.broker.ipc.calls,[])
        r=await self.client.post(url,json=report,headers={'Origin':self.origin})
        self.assertEqual(r.status,403)
        r=await self.client.post(PREFIX+'/action',json={'extra':'x'*60000},headers=self.headers)
        self.assertEqual(r.status,409)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.broker = FakeBroker()
        self.helper = AsyncMock(return_value=dict(pending=True, units={}))
        self.app = create_app(
            dict(demo=True, state_dir=self.tmp.name, public_origin="http://localhost"),
            broker=self.broker,
            helper_call=self.helper,
        )
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.origin = str(self.server.make_url("")).rstrip("/")
        self.app["studio"].config["public_origin"] = self.origin
        self.client = TestClient(self.server, cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.token = "test-only-not-real"
        self.app["studio"].sessions[self.token] = dict(
            id="session", csrf="csrf", expires=__import__("time").monotonic() + 100
        )
        self.client.session.cookie_jar.update_cookies({"studio_session": self.token})
        self.headers = {"Origin": self.origin, "X-Studio-CSRF": "csrf"}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_origin_auth_csrf_and_allowlist(self):
        r = await self.client.post(
            PREFIX + "/action", json={}, headers={"Origin": "https://evil.invalid"}
        )
        self.assertEqual(r.status, 403)
        r = await self.client.post(
            PREFIX + "/action", json={}, headers={"Origin": self.origin}
        )
        self.assertEqual(r.status, 403)
        r = await self.client.get(PREFIX + "/status")
        self.assertEqual(r.status, 200)
        r = await self.client.get("/models/../../etc/passwd")
        self.assertNotEqual(r.status, 200)

    async def test_demo_all_native_paths_are_inert(self):
        for path in ("query", "logs", "diagnostics", "resources", "model"):
            r = await self.client.get(PREFIX + "/" + path)
            self.assertEqual(r.status, 200)
        ws = await self.client.ws_connect(
            PREFIX + "/stream", headers={"Origin": self.origin}
        )
        await ws.receive_json()
        await ws.send_json(dict(id=1, op="claim", source="touch", confirm=True))
        messages = []
        while True:
            data = await ws.receive_json()
            messages.append(data)
            if data.get("id") == 1:
                break
        self.assertEqual(messages[-1]["type"], "error")
        self.assertFalse(self.broker.ipc.calls)
        self.helper.assert_not_awaited()
        await ws.close()

    async def test_two_client_limit_and_origin(self):
        from aiohttp import WSServerHandshakeError

        with self.assertRaises(WSServerHandshakeError):
            await self.client.ws_connect(
                PREFIX + "/stream", headers={"Origin": "http://evil.invalid"}
            )
        a = await self.client.ws_connect(
            PREFIX + "/stream", headers={"Origin": self.origin}
        )
        b = await self.client.ws_connect(
            PREFIX + "/stream", headers={"Origin": self.origin}
        )
        with self.assertRaises(WSServerHandshakeError):
            await self.client.ws_connect(
                PREFIX + "/stream", headers={"Origin": self.origin}
            )
        await a.close()
        await b.close()

    async def test_pair_code_used_once_and_rate_limited(self):
        from studio.helper import atomic

        atomic(
            Path(self.tmp.name) / "pairing.json",
            json.dumps(
                dict(
                    sha256=hashlib.sha256(b"code").hexdigest(),
                    used=False,
                    expires=__import__("time").time() + 60,
                )
            ).encode(),
        )
        r = await self.client.post(
            PREFIX + "/pair", json={"code": "code"}, headers={"Origin": self.origin}
        )
        self.assertEqual(r.status, 200)
        r = await self.client.post(
            PREFIX + "/pair", json={"code": "code"}, headers={"Origin": self.origin}
        )
        self.assertNotEqual(r.status, 200)

    async def test_live_racing_claim_and_disconnect_stop(self):
        s = self.app["studio"]
        s.config.update(demo=False, controls_enabled=True)
        s.store = ready()
        ctx = dict(id="one", mode="live", visible=True)
        await s.ws_command(ctx, dict(op="claim", source="touch", confirm=True))
        with self.assertRaises(Refused):
            await s.ws_command(
                dict(ctx, id="two"), dict(op="claim", source="touch", confirm=True)
            )
        await s.ws_command(
            ctx, dict(op="input", seq=1, source="touch", held=False, values=[0, 0, 0])
        )
        await asyncio.sleep(0.05)
        await s.ws_command(
            ctx, dict(op="input", seq=2, source="touch", held=True, values=[0.1, 0, 0])
        )
        await s.ws_command(ctx, dict(op="visible", visible=False))
        self.assertIsNone(s.lease.owner)
        self.assertEqual(
            [c[0][1] for c in self.broker.ipc.calls], ["robot.move", "robot.stop"]
        )
        self.assertFalse(any(c[0][1] == "robot.enable" for c in self.broker.ipc.calls))

    async def test_watchdog_is_not_blocked_by_helper_poll(self):
        s = self.app["studio"]
        s.config.update(demo=False, controls_enabled=True)
        s.store = ready()
        s.lease.claim("one", "touch")
        s.lease.armed = True
        s.lease.last = s.lease.clock() - 0.31
        s.lease.value = [0.1, 0, 0]
        s.helper_call = async_sleep
        await asyncio.sleep(0.12)
        self.assertIsNone(s.lease.last)
        self.assertTrue(any(c[0][1] == "robot.stop" for c in self.broker.ipc.calls))

    async def test_input_failure_does_not_report_execution(self):
        s = self.app["studio"]
        s.config.update(demo=False, controls_enabled=True)
        s.store = ready()
        ctx = dict(id="one", mode="live", visible=True)
        await s.ws_command(ctx, dict(op="claim", source="touch", confirm=True))
        await s.ws_command(
            ctx, dict(op="input", seq=1, source="touch", held=False, values=[0, 0, 0])
        )
        await asyncio.sleep(0.05)
        r = await s.ws_command(
            ctx, dict(op="input", seq=2, source="touch", held=True, values=[0.1, 0, 0])
        )
        self.assertFalse(r["executed"])
        self.assertTrue(r["submitted"])

    async def test_latest_input_mailbox_does_not_queue_motion_after_stop(self):
        s = self.app["studio"]
        s.config.update(demo=False, controls_enabled=True)
        s.store = ready()
        s.handoff_busy = True
        ws = await self.client.ws_connect(
            PREFIX + "/stream", headers={"Origin": self.origin}
        )
        welcome = await ws.receive_json()
        ident = welcome["connection_id"]
        s.lease.claim(ident, "touch")
        s.lease.armed = True

        async def slow_rpc(service, method, *a, **kw):
            self.broker.ipc.calls.append(((service, method), kw))
            if method == "robot.move":
                await asyncio.sleep(0.1)
            return dict(accepted=True)

        self.broker.ipc.rpc = slow_rpc
        for i in range(1, 12):
            await ws.send_json(
                dict(
                    id=i,
                    op="input",
                    seq=i,
                    source="touch",
                    held=True,
                    values=[0.1, 0, 0],
                )
            )
        await ws.send_json(dict(id=99, op="stop"))
        while True:
            reply = await asyncio.wait_for(ws.receive_json(), 2)
            if reply.get("id") == 99:
                break
        await asyncio.sleep(0.2)
        methods = [c[0][1] for c in self.broker.ipc.calls]
        self.assertEqual(methods[-1], "robot.stop")
        self.assertLess(methods.count("robot.move"), 3)
        await ws.close()

    async def test_simulation_in_live_process_does_not_read_native_or_export_live(self):
        s = self.app["studio"]
        s.config.update(demo=False)
        s.store = ready()
        s.broker = FakeBroker()
        ws = await self.client.ws_connect(
            PREFIX + "/stream", headers={"Origin": self.origin}
        )
        welcome = await ws.receive_json()
        ident = welcome["connection_id"]
        s.sockets[ident]["mode"] = "simulation"
        for path in ("query", "logs", "diagnostics?export=1"):
            sep = "&" if "?" in path else "?"
            r = await self.client.get(
                PREFIX + "/" + path + sep + "connection_id=" + ident
            )
            self.assertEqual(r.status, 200)
            body = await r.json()
            self.assertEqual(body["source"], "simulation")
            if "state" in body:
                self.assertIsNone(body["state"]["state"])
        self.assertFalse(s.broker.ipc.calls)
        self.helper.assert_not_awaited()
        await ws.close()

    async def test_legacy_proxy_preserves_csrf_and_caps(self):
        async def old(request):
            if request.path == "/":
                return __import__("aiohttp").web.Response(
                    text="<html><head></head></html>", content_type="text/html"
                )
            if request.method == "POST":
                self.assertEqual(
                    request.headers.get("X-Calibration-CSRF"), "legacy-token"
                )
                return __import__("aiohttp").web.json_response(dict(saved=True))
            return __import__("aiohttp").web.json_response(
                dict(capabilities=["imu", "camera"])
            )

        from aiohttp import web

        old_app = web.Application()
        old_app.router.add_route("*", "/{path:.*}", old)
        old_server = TestServer(old_app)
        await old_server.start_server()
        try:
            s = self.app["studio"]
            s.config.update(demo=False, legacy_port=old_server.port)
            page = await self.client.get("/calibration/")
            html = await page.text()
            self.assertIn("calibration-mobile.js", html)
            self.assertIn("calibration-mobile.css", html)
            r = await self.client.get("/api/capabilities")
            self.assertEqual(r.status, 200)
            r = await self.client.post(
                "/api/save",
                json={"test": True},
                headers={"Origin": self.origin, "X-Calibration-CSRF": "legacy-token"},
            )
            self.assertEqual(r.status, 200)
            self.assertTrue((await r.json())["saved"])
        finally:
            await old_server.close()


class AdditionalTests(unittest.IsolatedAsyncioTestCase):
    async def test_ipc_only_reviewed_methods_and_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "robot.sock")
            seen = []

            async def serve(reader, writer):
                request = json.loads(await reader.readline())
                seen.append(request)
                writer.write(
                    (
                        json.dumps(
                            dict(
                                jsonrpc="2.0",
                                id=request["id"],
                                result=dict(accepted=False, reason="no-policy"),
                            )
                        )
                        + "\n"
                    ).encode()
                )
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_unix_server(serve, path=path)
            ipc = IPC({"robot": path})
            try:
                with self.assertRaisesRegex(Refused, "no-policy"):
                    await ipc.rpc("robot", "robot.enable", {"on": True}, write=True)
                with self.assertRaises(Refused):
                    await ipc.rpc("robot", "robot.enable", {"on": True})
                with self.assertRaises(Refused):
                    await ipc.rpc("robot", "reboot", write=True)
                self.assertEqual(len(seen), 1)
            finally:
                server.close()
                await server.wait_closed()

    async def test_broker_has_one_subscription_per_shared_source(self):
        store = Store()
        ipc = FakeIPC()
        ipc.subscribe = AsyncMock()
        b = Broker(store, ipc)
        b.stream = AsyncMock(side_effect=async_sleep)
        b.poll = AsyncMock(side_effect=async_sleep)
        await b.start()
        tasks = list(b.tasks)
        await b.start()
        self.assertEqual(tasks, b.tasks)
        self.assertEqual(len(tasks), 3)
        await b.stop()
        self.assertFalse(b.tasks)

    async def test_hidden_analysis_does_not_refresh_lease(self):
        s = Studio(dict(demo=False, camera_enabled=True), broker=FakeBroker())
        s.environment.claim("a")
        ctx = dict(id="a", mode="live", visible=False)
        for op in ("analysis_ping", "semantic", "analysis"):
            with self.assertRaises(Refused):
                await s.ws_command(ctx, dict(op=op, enabled=True))

    async def test_bad_snapshot_vectors_and_unknown_pose(self):
        from studio.core import compact_tof, compact_state

        for bad in (
            {},
            {"rows": 4, "cols": 4},
            {
                "rows": 8,
                "cols": 8,
                "distance_mm": [1] * 64,
                "status": [float("nan")] * 64,
            },
        ):
            with self.assertRaises(Refused):
                compact_tof(bad)
        raw = state()
        raw["imu"]["quat"] = [0, 0, 0, 0]
        raw["safety"]["gravity"] = [float("inf"), 0, 0]
        s = compact_state(raw)
        self.assertIsNone(s["imu"]["quat"])
        self.assertIsNone(s["safety"]["gravity"])

    async def test_journal_configuration_crash_accepts_only_known_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            j = Journal(root / "state")
            p = root / "config"
            j.write(p, b"old", uid=os.geteuid(), gid=os.getegid())
            j.data["phase"] = "installed"
            j.save()
            j.update_config(p, b"new")
            j.verify()
            self.assertEqual(p.read_bytes(), b"new")
            row = j.data["files"][0]
            row["before_sha"] = row["sha256"]
            row["sha256"] = hashlib.sha256(b"pending").hexdigest()
            j.data["phase"] = "configuring"
            j.save()
            Journal(j.state).rollback()
            self.assertFalse(p.exists())

    async def test_journal_missing_installed_file_is_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            j = Journal(root / "state")
            p = root / "config"
            j.write(p, b"old", uid=os.geteuid(), gid=os.getegid())
            j.data["phase"] = "installed"
            j.save()
            p.unlink()
            with self.assertRaises(RuntimeError):
                j.verify()

    async def test_real_sigkill_install_intent_can_be_rolled_back(self):
        import subprocess, sys, time

        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            script = (
                "from studio.manage import Journal; from pathlib import Path; import os,signal; r=Path("
                + repr(str(root))
                + "); j=Journal(r/'state'); j.write(r/'owned',b'owned',uid=os.geteuid(),gid=os.getegid()); (r/'ready').touch(); signal.pause()"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2]
            )
            try:
                deadline = time.monotonic() + 3
                while not (root / "ready").exists() and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                self.assertTrue((root / "ready").exists())
                proc.kill()
                proc.wait(timeout=3)
                Journal(root / "state").rollback()
                self.assertFalse((root / "owned").exists())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=3)

    async def test_real_sigkill_after_handoff_marker_never_auto_restores(self):
        import subprocess, sys, time

        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            script = """import asyncio,sys
from pathlib import Path
sys.path.insert(0,'studio/tests')
from test_studio import FakeHandoff
r=Path(sys.argv[1]);h=FakeHandoff(r)
async def block(*args):
 (r/'ready').touch()
 await asyncio.Event().wait()
h.run=block
asyncio.run(h.take())
"""
            proc = subprocess.Popen(
                [sys.executable, "-c", script, str(root)],
                cwd=Path(__file__).resolve().parents[2],
            )
            try:
                deadline = time.monotonic() + 3
                while not (root / "ready").exists() and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                self.assertTrue((root / "ready").exists())
                proc.kill()
                proc.wait(timeout=3)
                saved = json.loads((root / "handoff/phone.pending").read_text())
                self.assertEqual(saved["active"], [UNITS[0]])
                resumed = FakeHandoff(root)
                await resumed.take()
                self.assertFalse(any(c[1] == "start" for c in resumed.calls))
                await resumed.release()
                self.assertEqual(resumed.rows[UNITS[0]]["ActiveState"], "active")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=3)

    async def test_resource_slice_and_helper_have_no_native_restart_commands(self):
        root = Path(__file__).resolve().parents[2]
        unit = (root / "deploy/studio/microduck-studio.slice").read_text()
        self.assertIn("CPUQuota=20%", unit)
        self.assertIn("MemoryMax=96M", unit)
        for path in (root / "deploy/studio").glob("*.service"):
            text = path.read_text()
            self.assertNotIn("Restart=always", text)
            self.assertNotIn("ExecStart=/opt/robot", text)

    async def test_installer_setup_rollback_does_not_touch_native_service(self):
        from studio import manage
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            assets = root / "assets"
            (assets / "assets").mkdir(parents=True)
            (assets / "assets/asset-manifest.json").write_text(
                json.dumps(dict(ready=True, files=[]))
            )
            commands = []

            def run(*argv):
                argv = list(map(str, argv))
                commands.append(argv)
                for flag in ("-keyout", "-out"):
                    if argv[0] == "openssl" and flag in argv:
                        Path(argv[argv.index(flag) + 1]).write_bytes(b"FAKE TEST CERT")
                return SimpleNamespace(
                    stdout="not-found" if argv[:2] == ["systemctl", "show"] else "test"
                )

            handoff = Handoff(root / "handoff", root / "systemd", root / "manager")
            user = SimpleNamespace(
                pw_uid=os.geteuid(),
                pw_gid=os.getegid(),
                pw_dir="/var/lib/microduck-studio",
                pw_shell="/usr/sbin/nologin",
            )
            # Map only systemd destinations into the temp tree. No real system command is run.
            j = Journal(root / "journal")
            original = j.write

            def write(path, raw, mode=0o644, uid=0, gid=0):
                path = Path(path)
                if str(path).startswith("/etc/systemd/system/"):
                    path = root / "systemd" / path.relative_to("/etc/systemd/system")
                return original(path, raw, mode, os.geteuid(), os.getegid())

            j.write = write
            with patch(
                "studio.preflight.check", return_value=dict(ok=True)
            ), patch.object(manage, "BASE", root / "addon"), patch.object(
                manage, "CONFIG", root / "config/config.json"
            ), patch.object(
                manage, "run", side_effect=run
            ), patch.object(
                manage.pwd, "getpwnam", return_value=user
            ), patch(
                "grp.getgrnam", return_value=object()
            ), patch.object(
                manage, "Handoff", return_value=handoff
            ), patch.object(
                manage, "requirements_lock",
                return_value=Path(manage.__file__).with_name("requirements-linux-arm64-py313.lock")
            ):
                manage.setup(
                    SimpleNamespace(
                        server_ip="192.168.9.9",
                        assets=assets,
                        wheels=root / "wheels",
                        enable_controls=False,
                        enable_camera=False,
                    ),
                    j,
                )
            self.assertEqual(j.data["phase"], "installed")
            release=Path(j.data['release'])
            pip_calls = [c for c in commands if "pip" in c and "install" in c]
            self.assertEqual(len(pip_calls), 1)
            self.assertIn("--require-hashes", pip_calls[0])
            self.assertEqual(Path(pip_calls[0][-1]).name, "requirements-linux-arm64-py313.lock")
            self.assertFalse((release/'servo_workbench/transport.py').exists())
            import subprocess,sys
            isolated=subprocess.run([sys.executable,'-I','-B','-c',
                "import sys;sys.path.insert(0,sys.argv[1]);from servo_workbench.reports import validate;from servo_workbench.profiles import layout;r=validate(dict(schema='microduck-servo-workbench/v1',session=dict(kind='hd1910',slots=[dict(x,state='pending') for x in layout('microduck')])));assert r['historical'];assert 'serial' not in sys.modules;assert 'servo_workbench.transport' not in sys.modules",str(release)],capture_output=True,text=True)
            self.assertEqual(isolated.returncode,0,isolated.stderr)
            j.verify()
            j.rollback()
            self.assertFalse((root / "config/config.json").exists())
            self.assertTrue((root / "addon/releases/0.6.0").exists())
            self.assertFalse(
                any(
                    c[:2] == ["systemctl", "start"]
                    or c[:2] == ["systemctl", "stop"]
                    or c[:2] == ["systemctl", "restart"]
                    for c in commands
                )
            )


if __name__ == "__main__":
    unittest.main()
