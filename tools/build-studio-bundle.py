#!/usr/bin/env python3
"""Build a NEW offline add-on review bundle. No target installation or daemon operations."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(runtime, assets, wheels, out, commit):
    import sys

    sys.path.insert(0, str(ROOT))
    from studio.manage import verify_assets

    if out.exists():
        raise ValueError("output exists; choose a new immutable output directory")
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("require exact source commit")
    manifest = verify_assets(assets)
    out.mkdir(parents=True)
    bundle = out / "microduck-studio-0.6.0"
    bundle.mkdir()
    with tarfile.open(runtime) as archive:
        for row in archive.getmembers():
            path = Path(row.name)
            if (
                path.parts[0] != "microduck-tools-0.6.0"
                or ".." in path.parts
                or not row.isfile()
            ):
                raise ValueError("runtime contains unexpected path/type")
            dest = bundle / "source" / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(row) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            dest.chmod(row.mode & 0o755)
    shutil.copytree(assets, bundle / "runtime-assets")
    shutil.copytree(wheels, bundle / "wheels")
    wrapper = bundle / "studio.sh"
    wrapper.write_text(
        '#!/bin/sh\nset -eu\nROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$ROOT/source/microduck-tools-0.6.0/tools/studio.sh" "$@"\n'
    )
    wrapper.chmod(0o755)
    shutil.copy2(
        ROOT / "docs/studio-0.6.0-guide.zh-CN.md", bundle / "使用指南.zh-CN.md"
    )
    (bundle / "README.txt").write_text(
        'Studio 0.6.0 offline add-on / BOARD NOT_RUN\nRead 使用指南.zh-CN.md before any target action.\nPreview: sudo ./studio.sh setup --server-ip ACTUAL_IPV4 --assets "$PWD/runtime-assets" --wheels "$PWD/wheels"\nThis package does not contain or upgrade robotd; retain the frozen 0.5.0 daemon upgrade bundle.\n'
    )
    files = [
        dict(path=str(p.relative_to(bundle)), sha256=sha(p), bytes=p.stat().st_size)
        for p in sorted(bundle.rglob("*"))
        if p.is_file()
    ]
    record = dict(
        schema="microduck-studio-portable/v1",
        version="0.6.0",
        source_commit=commit,
        daemon_bundled=False,
        hardware_acceptance="NOT_RUN",
        model_sha=manifest["model_sha"],
        wheel_target="CPython3.11 Linux ARM64",
        files=files,
    )
    (bundle / "MANIFEST.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    )
    archive = out / "microduck-studio-0.6.0-portable.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle, arcname=bundle.name)
    archive.with_name(archive.name + ".sha256").write_text(
        sha(archive) + "  " + archive.name + "\n"
    )
    return record


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--assets", type=Path, required=True)
    p.add_argument("--wheels", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--source-commit", required=True)
    a = p.parse_args()
    r = build(a.runtime, a.assets, a.wheels, a.out, a.source_commit)
    print(
        json.dumps(
            dict(
                files=len(r["files"]), source_commit=r["source_commit"], out=str(a.out)
            )
        )
    )
