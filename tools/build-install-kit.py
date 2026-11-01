#!/usr/bin/env python3
"""Assemble a NEW kit from frozen upgrade + Studio archives; never install on a target."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FROZEN_SHA = "d17bc4444d8c3fd817e2ed212bdb7001330680ad0fbe97fe30ec5417135d8790"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract(archive, destination, prefix):
    """Only directories and regular files below the exact reviewed prefix."""
    found = 0
    with tarfile.open(archive) as tar:
        for member in tar:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or member.issym()
                or member.islnk()
            ):
                raise ValueError("archive has unsafe paths/links")
            if tuple(path.parts[: len(prefix)]) != tuple(prefix):
                continue
            relative = Path(*path.parts[len(prefix) :])
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError("archive contains special file")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            target.chmod(member.mode & 0o755)
            found += 1
    if not found:
        raise ValueError("expected archive prefix absent")


def verify_manifest(root, name):
    record = json.loads((root / name).read_text())
    for row in record["files"]:
        path = root / row["path"]
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or sha(path) != row["sha256"]
        ):
            raise ValueError("payload hash mismatch: " + row["path"])
    return record


def build(upgrade, studio, out, commit):
    if out.exists():
        raise ValueError("choose a NEW output directory")
    if sha(upgrade) != FROZEN_SHA:
        raise ValueError("upgrade archive is not frozen 0.5.0")
    out.mkdir(parents=True)
    root = out / "microduck-install-kit-0.6.0"
    root.mkdir()
    extract(upgrade, root / "upgrade", ("microduck-tools-manager-20261031", "bundle"))
    extract(studio, root / "studio", ("microduck-studio-0.6.0",))
    studio_manifest = verify_manifest(root / "studio", "MANIFEST.json")
    if studio_manifest["source_commit"] != commit:
        raise ValueError("Studio/source commit differs")
    runtime = verify_manifest(root / "upgrade/tool", "RUNTIME-MANIFEST.json")
    if runtime["tool_version"] != "0.5.0":
        raise ValueError("frozen manager version differs")
    shutil.copytree(
        ROOT / "installer",
        root / "installer",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"),
    )
    shutil.copy2(ROOT / "duck.sh", root / "duck.sh")
    (root / "duck.sh").chmod(0o755)
    guide = (ROOT / "docs/install-upgrade-simple.zh-CN.md").read_text()
    for name in (
        "studio-0.6.0-guide.zh-CN.md",
        "script-manager.zh-CN.md",
        "deployment-register.zh-CN.md",
    ):
        guide = guide.replace(
            "](" + name + ")",
            "](studio/source/microduck-tools-0.6.0/docs/" + name + ")",
        )
    (root / "开始使用.zh-CN.md").write_text(guide)
    rows = [
        dict(path=str(p.relative_to(root)), sha256=sha(p), bytes=p.stat().st_size)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]
    record = dict(
        schema="microduck-install-kit/v1",
        version="0.6.0",
        source_commit=commit,
        upgrade_engine="frozen-0.5.0",
        upgrade_archive_sha256=FROZEN_SHA,
        daemon_target="0.18.3",
        studio_target="0.6.0",
        board="NOT_RUN",
        files=rows,
    )
    (root / "KIT-MANIFEST.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    )
    archive = out / "microduck-install-kit-0.6.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root, arcname=root.name)
    archive.with_name(archive.name + ".sha256").write_text(
        sha(archive) + "  " + archive.name + "\n"
    )
    return record


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--upgrade-archive", type=Path, required=True)
    p.add_argument("--studio-archive", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--source-commit", required=True)
    a = p.parse_args()
    record = build(a.upgrade_archive, a.studio_archive, a.out, a.source_commit)
    print(
        json.dumps(
            dict(
                files=len(record["files"]),
                source_commit=record["source_commit"],
                out=str(a.out),
            )
        )
    )
