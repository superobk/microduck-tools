"""ABI tests use mocks only; never execute a board command."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from studio import manage, preflight, python_runtime


class PythonRuntimeTests(unittest.TestCase):
    def native(self, version, implementation="CPython", gil=0, machine="aarch64"):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for target, value in (
                ("studio.manage.os.geteuid", 0),
                ("studio.manage.platform.system", "Linux"),
                ("studio.manage.platform.machine", machine),
                ("studio.python_runtime.platform.python_implementation", implementation),
                ("studio.python_runtime.sysconfig.get_config_var", gil),
                ("studio.manage.shutil.which", "/fixture/bin/tool"),
            ):
                stack.enter_context(patch(target, return_value=value))
            stack.enter_context(patch("studio.python_runtime.sys.version_info", version))
            manage.native_gate()

    def test_native_313_is_allowed_without_a_311_interpreter(self):
        self.native((3, 13, 5))

    def test_native_311_remains_allowed(self):
        self.native((3, 11, 9))

    def test_other_minor_versions_stay_blocked(self):
        for version in ((3, 12, 0), (3, 14, 0)):
            with self.subTest(version=version), self.assertRaises(RuntimeError):
                self.native(version)

    def test_free_threaded_313_is_not_the_cp313_wheel_abi(self):
        with self.assertRaises(RuntimeError):
            self.native((3, 13, 5), gil=1)

    def test_pypy_cannot_reuse_cpython_wheels(self):
        with self.assertRaises(RuntimeError):
            self.native((3, 13, 5), implementation="PyPy")

    def test_non_arm64_is_still_blocked(self):
        with self.assertRaises(RuntimeError):
            self.native((3, 13, 5), machine="x86_64")

    def test_lock_selection_follows_interpreter_minor(self):
        for version, name in (
            ((3, 11, 9), "requirements-linux-arm64.lock"),
            ((3, 13, 5), "requirements-linux-arm64-py313.lock"),
        ):
            with patch("studio.python_runtime.sys.version_info", version):
                self.assertEqual(python_runtime.requirements_lock().name, name)

    def test_313_rejects_311_wheel_bytes_before_install(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            assets = root / "assets"; assets.mkdir()
            wheels = root / "wheels"; wheels.mkdir()
            wheel = wheels / "fixture-1-cp311-cp311-manylinux2014_aarch64.whl"
            wheel.write_bytes(b"legacy ABI bytes")
            lock = root / "py313.lock"
            lock.write_text("fixture==1 --hash=sha256:" +
                            hashlib.sha256(b"313 ABI bytes").hexdigest() + "\n")
            required = ["assets/three/three.module.js", "assets/three/three.core.js",
                        "assets/transformers/transformers.min.js",
                        "assets/transformers/ort-wasm-simd-threaded.jsep.mjs",
                        "assets/transformers/ort-wasm-simd-threaded.jsep.wasm"]
            with patch.object(manage, "verify_assets",
                              return_value={"files": [{"path": p} for p in required]}), \
                 patch("studio.preflight.requirements_lock", return_value=lock):
                report = preflight.check(assets, wheels, require_target=False)
            self.assertFalse(report["ok"])
            self.assertFalse(report["side_effects"])
            self.assertTrue(any(r["item"] == "离线依赖" and not r["ok"]
                                for r in report["checks"]))

    def test_hashes_of_legacy_and_313_native_wheels_do_not_overlap(self):
        old = python_runtime.requirements_lock((3, 11)).read_text()
        new = python_runtime.requirements_lock((3, 13)).read_text()
        def hashes(raw):
            return {line.split("==")[0]: line.split("sha256:")[1]
                    for line in raw.splitlines() if "--hash" in line}
        a, b = hashes(old), hashes(new)
        self.assertEqual(set(a), set(b))
        for name in ("aiohttp", "frozenlist", "multidict", "propcache", "yarl"):
            self.assertNotEqual(a[name], b[name])


    def asset_report(self, split):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "assets/three").mkdir(parents=True)
            (root / "assets/three/three.module.js").write_text(
                "export {Object3D} from './three.core.js';" if split else
                "export class Object3D {}")
            required = ["assets/three/three.module.js",
                        "assets/transformers/transformers.min.js",
                        "assets/transformers/ort-wasm-simd-threaded.jsep.mjs",
                        "assets/transformers/ort-wasm-simd-threaded.jsep.wasm"]
            wheels = root / "wheels"; wheels.mkdir()
            lock = root / "empty.lock"; lock.write_text("# fixture\n")
            with patch.object(manage, "verify_assets",
                              return_value={"files": [{"path": p} for p in required]}):
                return preflight.check(root, wheels, require_target=False, lock=lock)

    def test_self_contained_r170_does_not_require_nonexistent_core(self):
        self.assertTrue(self.asset_report(False)["ok"])

    def test_split_three_without_core_is_rejected(self):
        report = self.asset_report(True)
        self.assertFalse(report["ok"])
        self.assertTrue(any(r["item"] == "浏览器与模型资产" and not r["ok"]
                            for r in report["checks"]))

if __name__ == "__main__":
    unittest.main()
