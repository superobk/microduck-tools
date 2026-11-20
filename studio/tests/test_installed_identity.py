import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from studio import manage


class InstalledIdentityTests(unittest.TestCase):
    def test_fresh_install_loads_api47_and_api68_profiles_and_tracks_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            (assets / "assets").mkdir(parents=True)
            (assets / "assets/asset-manifest.json").write_text(
                json.dumps({"ready": True, "files": []}), encoding="utf-8"
            )
            journal = manage.Journal(root / "journal")
            with patch.object(manage, "BASE", root / "installed"), patch.object(
                manage, "run", return_value=SimpleNamespace(stdout="")
            ):
                release = manage.prepare_release(journal, assets, root / "wheels")
                manage.verify_release(release)
                profile_file = release / "calibration/common/daemon_profiles.py"
                spec = importlib.util.spec_from_file_location("installed_profiles", profile_file)
                profiles = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(profiles)
                for version, api in (("0.18.3", 47), ("0.23.0", 68)):
                    meta = profiles.profiles()[version]
                    self.assertIsNotNone(profiles.match({
                        "daemon_version": version, "api_version": api,
                        "revision": meta["build_revision"],
                    }))
                saved = json.loads((release / "STUDIO-RELEASE.json").read_text())
                rel = "calibration/daemon-v0.23.0/native-build.json"
                self.assertIn(rel, {row["path"] for row in saved["source"]})
                (release / rel).write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "哈希"):
                    manage.verify_release(release)


if __name__ == "__main__":
    unittest.main()
