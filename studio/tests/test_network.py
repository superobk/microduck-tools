"""Transport, occupied ports and network-only rollback without touching native services."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from studio import manage, network, server


class EndpointTests(unittest.TestCase):
    def test_default_lan_http_and_optional_ssh_https(self):
        cfg = network.settings("192.168.1.56")
        self.assertEqual(cfg, dict(listen="0.0.0.0", port=8790, transport="lan-http",
                                   public_origin="http://192.168.1.56:8790"))
        self.assertIsNone(network.tls_context(cfg))
        self.assertEqual(network.settings("192.168.1.56", transport="ssh-loopback-http")["listen"], "127.0.0.1")
        self.assertEqual(network.settings("192.168.1.56", transport="https")["public_origin"], "https://192.168.1.56:8790")

    def test_existing_https_configuration_without_transport_is_supported(self):
        self.assertEqual(network.validate(dict(listen="0.0.0.0", port=8443,
                                               public_origin="https://192.168.1.56:8443")), "https")

    def test_reserved_or_invalid_new_ports_are_refused(self):
        for port in (8443, 8765, 8766, 80, 65536, True, "8790"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                network.settings("192.168.1.56", port)

    def test_origin_is_not_a_wildcard_whitelist(self):
        cfg = network.settings("192.168.1.56")
        for origin in ("http://0.0.0.0:8790", "http://192.168.1.56:8791",
                       "https://192.168.1.56:8790", "http://user@192.168.1.56:8790",
                       "http://192.168.1.56:8790/", "http://evil.invalid:8790"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                network.validate({**cfg, "public_origin": origin})

    def test_invalid_address_or_implicit_plaintext_is_refused(self):
        for address in ("0.0.0.0", "224.0.0.1", "255.255.255.255", "::1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                network.settings(address)
        cfg = network.settings("192.168.1.56")
        cfg.pop("transport")
        with self.assertRaises(ValueError):
            network.validate(cfg)

    def test_real_busy_listener_is_preserved(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, "已占用"):
                network.ensure_port_available(port)
            self.assertEqual(listener.getsockname()[1], port)

    def test_server_live_http_does_not_load_a_certificate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(network.settings("192.168.1.56")))
            with patch("sys.argv", ["studio", "--config", str(path)]), patch.object(
                server, "create_app", return_value=object()
            ), patch("aiohttp.web.run_app") as run, contextlib.redirect_stdout(io.StringIO()):
                server.main()
            self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
            self.assertEqual(run.call_args.kwargs["port"], 8790)
            self.assertIsNone(run.call_args.kwargs["ssl_context"])

    def test_server_legacy_https_loads_saved_certificate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(dict(listen="0.0.0.0", port=8444,
                public_origin="https://192.168.1.56:8444", cert="saved.crt", key="saved.key")))
            with patch("sys.argv", ["studio", "--config", str(path)]), patch.object(
                server, "create_app", return_value=object()
            ), patch("aiohttp.web.run_app") as run, patch("ssl.SSLContext") as tls, contextlib.redirect_stdout(io.StringIO()):
                server.main()
            tls.return_value.load_cert_chain.assert_called_once_with("saved.crt", "saved.key")
            self.assertIs(run.call_args.kwargs["ssl_context"], tls.return_value)


class NetworkChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.base = self.root / "addon"
        self.cfg = self.root / "etc/config.json"
        self.unit = self.root / "systemd/microduck-studio.service"
        self.journal = manage.Journal(self.root / "journal")
        self.old = dict(listen="127.0.0.1", port=8790, transport="ssh-loopback-http",
                       public_origin="http://127.0.0.1:8790", controls_enabled=False, camera_enabled=False)
        self.journal.write(self.cfg, json.dumps(self.old).encode(), uid=os.geteuid(), gid=os.getegid())
        self.journal.write(self.unit, b"ORIGINAL FROZEN STUDIO UNIT", uid=os.geteuid(), gid=os.getegid())
        self.journal.data.update(phase="installed", release=str(self.base / "releases/0.6.0"))
        self.journal.save()
        write = self.journal.write
        self.journal.write = lambda p, raw, mode=0o644, uid=0, gid=0: write(p, raw, mode, os.geteuid(), os.getegid())
        self.commands = []
        def mapped(path):
            p = Path(path)
            if str(p) == "/etc/systemd/system":
                return self.unit.parent
            return self.unit if str(p) == "/etc/systemd/system/microduck-studio.service" else p
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(manage, "BASE", self.base))
        self.stack.enter_context(patch.object(manage, "CONFIG", self.cfg))
        self.stack.enter_context(patch.object(manage, "Path", side_effect=mapped))
        self.stack.enter_context(patch.object(manage, "verify_release", return_value={}))
        self.stack.enter_context(patch.object(manage, "run", side_effect=lambda *args: self.commands.append(args)))
        self.stack.enter_context(patch.object(network, "ensure_port_available"))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.args = SimpleNamespace(server_ip="192.168.1.56", port=8790, transport="lan-http", controls=None, camera=None)

    def tearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    def test_existing_frozen_runtime_gets_managed_launcher_and_one_command_pair(self):
        manage.configure_network(self.args, self.journal)
        cfg = json.loads(self.cfg.read_text())
        self.assertEqual(cfg["listen"], "0.0.0.0")
        self.assertFalse(cfg["controls_enabled"])
        self.assertFalse(cfg["camera_enabled"])
        self.assertIn("launchers/studio-http.py", self.unit.read_text())
        self.assertTrue((self.base / "pair-code.sh").stat().st_mode & 0o100)
        self.journal.verify()
        self.assertEqual(self.commands, [("systemctl", "stop", "microduck-studio.service"), ("systemctl", "daemon-reload")])

    def test_network_rollback_restores_config_and_unit_only(self):
        manage.configure_network(self.args, self.journal)
        manage.rollback_network(self.journal)
        self.assertEqual(json.loads(self.cfg.read_text()), self.old)
        self.assertEqual(self.unit.read_bytes(), b"ORIGINAL FROZEN STUDIO UNIT")
        self.assertTrue((self.base / "pair-code.sh").exists())
        self.journal.verify()
        self.assertFalse(any("robotd" in str(c) or "tofd" in str(c) for c in self.commands))

    def test_network_rollback_preserves_later_feature_configuration(self):
        manage.configure_network(self.args, self.journal)
        cfg = json.loads(self.cfg.read_text())
        cfg["camera_enabled"] = True
        self.journal.update_config(self.cfg, json.dumps(cfg).encode())
        manage.rollback_network(self.journal)
        cfg = json.loads(self.cfg.read_text())
        self.assertTrue(cfg["camera_enabled"])
        self.assertFalse(cfg["controls_enabled"])
        self.assertEqual(cfg["public_origin"], self.old["public_origin"])

    def test_new_busy_port_fails_before_stopping_studio(self):
        self.args.port = 8791
        with patch.object(network, "ensure_port_available", side_effect=RuntimeError("busy")):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                manage.configure_network(self.args, self.journal)
        self.assertEqual(self.commands, [])
        self.assertEqual(json.loads(self.cfg.read_text()), self.old)

    def test_same_port_still_busy_after_stop_keeps_configuration_unchanged(self):
        with patch.object(network, "ensure_port_available", side_effect=RuntimeError("busy")):
            with self.assertRaises(RuntimeError):
                manage.configure_network(self.args, self.journal)
        self.assertEqual(json.loads(self.cfg.read_text()), self.old)
        manage.rollback_network(self.journal)
        self.journal.verify()

    def test_interrupt_after_config_write_requires_rollback_and_never_replays(self):
        update = self.journal.update_config
        def fail_after_config(path, raw):
            update(path, raw)
            if path == self.cfg:
                raise OSError("simulated interruption")
        with patch.object(self.journal, "update_config", side_effect=fail_after_config):
            with self.assertRaises(OSError):
                manage.configure_network(self.args, self.journal)
        with self.assertRaisesRegex(RuntimeError, "未完成"):
            manage.configure_network(self.args, self.journal)
        manage.rollback_network(self.journal)
        self.assertEqual(json.loads(self.cfg.read_text()), self.old)
        self.assertEqual(self.unit.read_bytes(), b"ORIGINAL FROZEN STUDIO UNIT")

    def test_interrupt_during_atomic_config_replace_can_restore_before_bytes(self):
        atomic = manage.atomic
        def fail(path, raw):
            if path == self.cfg:
                raise OSError("before replace")
            return atomic(path, raw)
        with patch.object(manage, "atomic", side_effect=fail):
            with self.assertRaises(OSError):
                manage.configure_network(self.args, self.journal)
        self.assertEqual(self.journal.data["phase"], "configuring")
        manage.rollback_network(self.journal)
        self.journal.verify()
        self.assertEqual(json.loads(self.cfg.read_text()), self.old)

    def test_network_rollback_cannot_resurrect_an_uninstalled_service(self):
        manage.configure_network(self.args, self.journal)
        self.journal.data["phase"] = "rolled_back"
        self.commands.clear()
        with self.assertRaisesRegex(RuntimeError, "已卸载"):
            manage.rollback_network(self.journal)
        self.assertEqual(self.commands, [])

    def test_foreign_config_edit_is_not_overwritten(self):
        self.cfg.write_text("foreign")
        with self.assertRaisesRegex(RuntimeError, "外部修改"):
            manage.configure_network(self.args, self.journal)
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
