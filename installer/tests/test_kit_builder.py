import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "kit_builder", ROOT / "tools/build-install-kit.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuilderTests(unittest.TestCase):
    def archive(self, path, name, raw=b"fixed", kind=None):
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo(name)
            info.mode = 0o755
            if kind:
                info.type = kind
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(raw)
                tar.addfile(info, io.BytesIO(raw))

    def test_extracts_only_reviewed_prefix_and_preserves_executable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "a.tar.gz"
            self.archive(src, "old/bundle/duck.sh")
            builder.extract(src, root / "kit", ("old", "bundle"))
            self.assertEqual((root / "kit/duck.sh").read_bytes(), b"fixed")
            self.assertTrue((root / "kit/duck.sh").stat().st_mode & 0o111)

    def test_rejects_traversal_links_and_special_files(self):
        for name, kind in [
            ("old/bundle/../../escape", None),
            ("old/bundle/link", tarfile.SYMTYPE),
            ("old/bundle/fifo", tarfile.FIFOTYPE),
        ]:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                src = root / "a.tar.gz"
                self.archive(src, name, kind=kind)
                with self.assertRaises(ValueError):
                    builder.extract(src, root / "out", ("old", "bundle"))

    def test_missing_prefix_cannot_produce_empty_valid_kit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "a.tar.gz"
            self.archive(src, "wrong/prefix")
            with self.assertRaises(ValueError):
                builder.extract(src, root / "out", ("old", "bundle"))

    def test_changed_frozen_archive_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "wrong"
            src.write_bytes(b"not frozen")
            with self.assertRaisesRegex(ValueError, "frozen"):
                builder.build(src, src, root / "out", "0" * 40)
            self.assertFalse((root / "out").exists())

    def test_existing_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(ValueError):
                builder.build(root / "missing", root / "missing", root, "0" * 40)

    def test_manifest_hash_or_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / "data"
            p.write_bytes(b"good")
            record = {
                "files": [
                    {"path": "data", "sha256": hashlib.sha256(b"good").hexdigest()}
                ]
            }
            (root / "manifest.json").write_text(json.dumps(record))
            builder.verify_manifest(root, "manifest.json")
            p.write_bytes(b"bad")
            with self.assertRaises(ValueError):
                builder.verify_manifest(root, "manifest.json")
            record["files"][0]["path"] = "../outside"
            (root / "manifest.json").write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                builder.verify_manifest(root, "manifest.json")


if __name__ == "__main__":
    unittest.main()
