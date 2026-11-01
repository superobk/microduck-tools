import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from installer.assistant import (
    Assistant,
    Kit,
    Refused,
    choose_next,
    operation_lock,
    query,
)
from studio.preflight import check
from studio import manage


def robot(stage, release="0.16.3"):
    return dict(
        mode="板端状态",
        stage=stage,
        actual=dict(
            release=release, official_integrity=release == "0.18.3", pending=False
        ),
        operation=None,
        last_error=None,
    )


def studio(phase="not_installed", active=False):
    return dict(
        ok=True,
        phase=phase,
        installed=phase == "installed",
        handoff_pending=False,
        units={
            "microduck-studio.service": {
                "ActiveState": "active" if active else "inactive"
            }
        },
    )


class FakeKit:
    def verify(self):
        return {}

    def manager(self, *args):
        return ["frozen-manager", *args]

    def studio(self, *args):
        return ["studio", *args]

    def assets(self):
        return [
            "--assets",
            "/kit/studio/runtime-assets",
            "--wheels",
            "/kit/studio/wheels",
        ]


class FlowTests(unittest.TestCase):
    def app(self, r, s=None):
        self.r = r
        self.s = s or studio()
        self.calls = []

        def reader(command):
            return (
                self.r
                if command[0] == "frozen-manager"
                else dict(ok=True, checks=[]) if "doctor" in command else self.s
            )

        return Assistant(
            FakeKit(),
            reader=reader,
            runner=lambda c: self.calls.append(c),
            target=lambda: True,
        )

    def test_known_upgrade_stages(self):
        for stage, step in [
            ("待备份", "backup"),
            ("已备份，待安装", "install"),
            ("官方已安装，待配置校正", "configure"),
            ("已配置，待启动", "start"),
        ]:
            with self.subTest(stage=stage):
                self.assertEqual(choose_next(robot(stage), studio())["step"], step)

    def test_no_hardware_receipt_does_not_trigger_restart_loop(self):
        r = robot("进程已启动，等待真实回执", "0.18.3")
        self.assertEqual(choose_next(r, studio())["step"], "studio-install")
        self.assertEqual(choose_next(r, studio("installed"))["step"], "studio-start")
        self.assertEqual(choose_next(r, studio("installed", True))["step"], "done")

    def test_external_valid_0183_allows_only_addon_not_adoption(self):
        r = robot("检测到外部已安装版本，尚未接管", "0.18.3")
        self.assertEqual(choose_next(r, studio())["step"], "studio-install")
        r["actual"]["official_integrity"] = False
        self.assertEqual(choose_next(r, studio())["step"], "review")

    def test_official_rollback_or_cancel_is_never_automatically_undone(self):
        for stage in (
            "官方模式（未加载校正）",
            "本次维护已撤销，仍为旧版",
            "unrecognized",
        ):
            self.assertEqual(
                choose_next(robot(stage, "0.18.3"), studio())["step"], "review"
            )

    def test_ongoing_and_interrupted_are_distinct(self):
        r = robot("正在执行：install")
        r["operation"] = {"name": "install", "status": "running"}
        self.assertEqual(choose_next(r, studio())["step"], "busy")
        r["stage"] = "中断/未完成：install"
        self.assertEqual(choose_next(r, studio())["step"], "recover")
        r["operation"] = None
        r["actual"]["pending"] = True
        self.assertEqual(choose_next(r, studio())["step"], "busy")

    def test_handoff_and_active_studio_block_robot_upgrade(self):
        r = robot("已备份，待安装")
        s = studio("installed", True)
        self.assertEqual(choose_next(r, s)["step"], "studio-stop")
        s["handoff_pending"] = True
        self.assertEqual(choose_next(r, s)["step"], "handoff")

    def test_incomplete_studio_or_drift_is_not_reinstalled(self):
        for phase in ("preparing", "configuring"):
            self.assertEqual(
                choose_next(robot("待备份"), studio(phase))["step"], "studio-recover"
            )
        s = studio("installed")
        s.update(ok=False, problems=["external edit"])
        self.assertEqual(choose_next(robot("待备份"), s)["step"], "review")

    def test_preview_has_no_mutation_or_confirmation(self):
        app = self.app(robot("待备份"))
        with patch(
            "installer.assistant.require_confirmation",
            side_effect=AssertionError("no prompt"),
        ):
            app.execute("next")
            app.utility("studio-rollback")
        self.assertFalse(self.calls)

    def test_next_stops_after_exactly_one_upgrade_step(self):
        app = self.app(robot("待备份"))
        app.execute("next", True)
        self.assertEqual(
            self.calls,
            [["frozen-manager", "check"], ["frozen-manager", "backup", "--apply"]],
        )
        self.assertFalse(any("install" in c for c in self.calls))

    def test_check_failure_cannot_submit_backup_or_install(self):
        app = self.app(robot("待备份"))

        def fail(c):
            self.calls.append(c)
            raise Refused("library missing")

        app.runner = fail
        with self.assertRaises(Refused):
            app.execute("next", True)
        self.assertEqual(self.calls, [["frozen-manager", "check"]])

    def test_no_replay_after_mutation_failure(self):
        app = self.app(robot("已备份，待安装"))
        app.runner = MagicMock(side_effect=Refused("interrupted"))
        with self.assertRaises(Refused):
            app.execute("next", True)
        app.runner.assert_called_once_with(["frozen-manager", "install", "--apply"])

    def test_state_change_refuses_stale_step(self):
        app = self.app(robot("官方已安装，待配置校正"))
        with self.assertRaises(Refused):
            app.execute("install", True)
        self.assertFalse(self.calls)

    def test_studio_install_resolves_paths_without_enabling_control(self):
        app = self.app(robot("进程已启动，等待真实回执", "0.18.3"))
        app.execute("next", True, "192.168.1.56", "EXECUTE")
        c = self.calls[0]
        self.assertIn("--assets", c)
        self.assertIn("--wheels", c)
        self.assertNotIn("--enable-controls", c)
        self.assertNotIn("--enable-camera", c)
        self.assertEqual(c[0:2], ["studio", "setup"])

    def test_bad_ip_and_missing_confirmation_do_not_mutate(self):
        app = self.app(robot("进程已启动，等待真实回执", "0.18.3"))
        for ip in ("0.0.0.0", "224.1.1.1", "::1", "bad"):
            with self.assertRaises(ValueError):
                app.execute("next", True, ip, "EXECUTE")
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(Refused):
                app.execute("next", True, "192.168.1.56")
        self.assertFalse(self.calls)

    def test_doctor_failure_precedes_installation(self):
        app = self.app(robot("进程已启动，等待真实回执", "0.18.3"))
        base = app.reader
        app.reader = lambda c: (
            dict(ok=False, checks=[dict(item="wheel", ok=False, detail="missing")])
            if "doctor" in c
            else base(c)
        )
        with self.assertRaisesRegex(Refused, "missing"):
            app.execute("next", True, "192.168.1.56", "EXECUTE")
        self.assertFalse(self.calls)

    def test_configuration_preserves_explicit_scope_and_does_not_start(self):
        app = self.app(
            robot("校正已加载；插件和硬件分别查看", "0.18.3"), studio("installed", True)
        )
        app.configure("off", "on", True, "EXECUTE")
        self.assertEqual(
            self.calls,
            [
                [
                    "studio",
                    "configure",
                    "--controls",
                    "off",
                    "--camera",
                    "on",
                    "--apply",
                    "--confirm",
                    "EXECUTE",
                ]
            ],
        )

    def test_rollback_targets_are_not_conflated(self):
        app = self.app(robot("已备份，待安装"))
        app.utility("robot-official", True)
        self.assertEqual(
            self.calls[-1],
            ["frozen-manager", "rollback", "--to", "official", "--apply"],
        )
        app.utility("studio-rollback", True, "EXECUTE")
        self.assertEqual(
            self.calls[-1], ["studio", "rollback", "--apply", "--confirm", "EXECUTE"]
        )

    def test_native_recovery_blocked_during_phone_handoff(self):
        app = self.app(robot("已备份，待安装"), studio("installed"))
        self.s["handoff_pending"] = True
        with self.assertRaises(Refused):
            app.utility("robot-recover", True)
        self.assertFalse(self.calls)

    def test_wrong_host_cannot_launch_step(self):
        app = self.app(robot("已备份，待安装"))
        app.target = lambda: False
        with self.assertRaises(Refused):
            app.execute("next", True)
        self.assertFalse(self.calls)

    def test_private_operation_lock_excludes_second_writer(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve() / "state"
            with operation_lock(root):
                with self.assertRaises(Refused):
                    with operation_lock(root):
                        pass

    def test_kit_rejects_corrupted_or_escaped_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            p = root / "upgrade/tool/VERSION"
            p.parent.mkdir(parents=True)
            p.write_text("0.5.0")
            record = {
                "schema": "microduck-install-kit/v1",
                "files": [
                    dict(
                        path="upgrade/tool/VERSION",
                        sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                    )
                ],
            }
            (root / "KIT-MANIFEST.json").write_text(json.dumps(record))
            Kit(root).verify()
            p.write_text("0.6.0")
            with self.assertRaises(Refused):
                Kit(root).verify()
            record["files"][0]["path"] = "../outside"
            (root / "KIT-MANIFEST.json").write_text(json.dumps(record))
            with self.assertRaises(Refused):
                Kit(root).verify()

    def test_studio_lifecycle_lock_reports_busy_without_probing_services(self):
        import fcntl

        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            journal = manage.Journal(root)
            journal.data["phase"] = "preparing"
            journal.save()
            with (root / "lifecycle.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with patch.object(manage, "STATE", root), patch.object(
                    manage, "run"
                ) as run:
                    report = manage.status_report()
                    self.assertTrue(report["busy"])
                    self.assertFalse(report["files_verified"])
                    run.assert_not_called()
                    self.assertEqual(
                        choose_next(robot("待备份"), report)["step"], "busy"
                    )

    def test_interruption_does_not_kill_or_replay_child_transaction(self):
        from installer.assistant import run_child

        process = MagicMock()
        process.wait.side_effect = KeyboardInterrupt
        with patch("installer.assistant.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(Refused, "不能重复install"):
                run_child(["frozen-manager", "install", "--apply"])
        process.kill.assert_not_called()
        process.terminate.assert_not_called()
        process.wait.assert_called_once()

    def test_recover_before_units_exist_does_not_stop_missing_services(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            j = manage.Journal(root / "journal")
            owned = root / "config"
            j.write(owned, b"ours", uid=os.geteuid(), gid=os.getegid())
            j.data["phase"] = "preparing"
            j.save()
            with patch.object(manage, "run") as run:
                manage.rollback_install(j)
            self.assertEqual(j.data["phase"], "rolled_back")
            self.assertFalse(owned.exists())
            run.assert_called_once_with("systemctl", "daemon-reload")

    def test_structured_doctor_failure_retains_explanation(self):
        with patch(
            "installer.assistant.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [],
                1,
                json.dumps(
                    dict(
                        ok=False,
                        checks=[dict(item="Python", ok=False, detail="3.11 needed")],
                    )
                ),
                "unused",
            ),
        ):
            self.assertFalse(query(["doctor"])["ok"])


class PreflightTests(unittest.TestCase):
    def test_missing_assets_and_wheels_are_reported_without_creating_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            r = check(
                root / "missing-assets", root / "missing-wheels", require_target=False
            )
            self.assertFalse(r["ok"])
            self.assertFalse(list(root.iterdir()))
            self.assertFalse(r["side_effects"])

    def test_assets_and_locked_wheels_detect_partial_or_corrupt_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            assets = root / "assets"
            wheels = root / "wheels"
            wheels.mkdir()
            paths = [
                "assets/three/three.module.js",
                "assets/three/three.core.js",
                "assets/transformers/transformers.min.js",
                "assets/transformers/ort-wasm-simd-threaded.jsep.mjs",
                "assets/transformers/ort-wasm-simd-threaded.jsep.wasm",
            ]
            rows = []
            for name in paths:
                p = assets / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"fixture")
                rows.append(
                    dict(path=name, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                )
            (assets / "assets/asset-manifest.json").write_text(
                json.dumps(dict(ready=True, files=rows))
            )
            wheel = wheels / "fixture-1-py3-none-any.whl"
            wheel.write_bytes(b"locked wheel fixture")
            lock = root / "lock"
            lock.write_text(
                "fixture==1 --hash=sha256:"
                + hashlib.sha256(wheel.read_bytes()).hexdigest()
                + "\n"
            )
            self.assertTrue(check(assets, wheels, False, lock)["ok"])
            wheel.write_bytes(b"corrupt")
            self.assertFalse(check(assets, wheels, False, lock)["ok"])
            (assets / paths[0]).unlink()
            self.assertFalse(check(assets, wheels, False, lock)["ok"])

    def test_setup_preflight_precedes_account_or_copy(self):
        from types import SimpleNamespace

        with patch(
            "studio.preflight.check",
            return_value=dict(
                ok=False, checks=[dict(item="wheel", detail="missing", ok=False)]
            ),
        ), patch.object(manage, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "missing"):
                manage.setup(
                    SimpleNamespace(
                        server_ip="192.168.1.56",
                        assets=Path("/none"),
                        wheels=Path("/none"),
                    ),
                    SimpleNamespace(data={"phase": "new"}),
                )
            run.assert_not_called()

    def test_existing_unknown_service_blocks_before_account_creation(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            args = SimpleNamespace(server_ip="192.168.1.56", assets=root, wheels=root)
            with patch(
                "studio.preflight.check", return_value=dict(ok=True)
            ), patch.object(manage, "verify_assets", return_value={}), patch.object(
                manage, "CONFIG", root / "config.json"
            ), patch.object(
                manage, "run", return_value=SimpleNamespace(stdout="loaded")
            ) as run, patch.object(
                manage.pwd, "getpwnam"
            ) as account:
                with self.assertRaisesRegex(RuntimeError, "已有服务"):
                    manage.setup(args, SimpleNamespace(data={"phase": "new"}))
                account.assert_not_called()
                self.assertTrue(
                    all(
                        c.args[0] not in ("useradd", "usermod")
                        for c in run.call_args_list
                    )
                )

    def test_unfinished_handback_blocks_standalone_setup_before_account(self):
        from types import SimpleNamespace

        original = Path.exists

        def exists(path):
            return (
                True
                if str(path) == "/var/lib/microduck-studio-handoff/return.intent"
                else original(path)
            )

        with patch("studio.preflight.check", return_value=dict(ok=True)), patch.object(
            manage, "verify_assets", return_value={}
        ), patch.object(manage, "run"), patch.object(
            Path, "exists", exists
        ), patch.object(
            manage.pwd, "getpwnam"
        ) as account:
            with self.assertRaisesRegex(RuntimeError, "交接"):
                manage.setup(
                    SimpleNamespace(
                        server_ip="192.168.1.56",
                        assets=Path("/none"),
                        wheels=Path("/none"),
                    ),
                    SimpleNamespace(data={"phase": "new"}),
                )
            account.assert_not_called()

    def test_subprocess_error_reports_real_loader_message(self):
        with patch(
            "studio.manage.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                1, ["python"], stderr="ImportError: libexample.so missing"
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "libexample.so"):
                manage.run("python", "-c", "import module")


if __name__ == "__main__":
    unittest.main()
