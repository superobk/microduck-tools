#!/usr/bin/env python3
"""Freeze browser assets/model into a NEW delivery directory. Never installs on a robot."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import urllib.error

REV = "9463a9c508a344c837ffefe9d724f3827bf2dc79"
REPO = "onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX"
BASE = "https://huggingface.co/" + REPO + "/resolve/" + REV + "/"


def build(node, out, model=False, model_cache=None):
    if out.exists():
        raise ValueError(
            "choose a new output directory; frozen assets are never overwritten"
        )
    assets = out / "assets"
    models = out / "models/tinyclip"
    assets.mkdir(parents=True)
    models.mkdir(parents=True)
    node = Path(node)

    def copy(source, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)

    module = node / "three/build/three.module.js"
    copy(module, assets / "three/three.module.js")
    core = node / "three/build/three.core.js"
    if core.is_file():
        copy(core, assets / "three/three.core.js")
    elif "three.core.js" in module.read_text():
        raise ValueError("split Three module requires three.core.js")
    copy(node / "three/LICENSE", assets / "licenses/three-LICENSE")
    dist = node / "@huggingface/transformers/dist"
    for name in (
        "transformers.min.js",
        "ort-wasm-simd-threaded.jsep.mjs",
        "ort-wasm-simd-threaded.jsep.wasm",
    ):
        copy(dist / name, assets / "transformers" / name)
    copy(
        node / "@huggingface/transformers/LICENSE",
        assets / "licenses/transformers-LICENSE",
    )
    copy(
        Path(__file__).resolve().parents[1] / "studio/licenses/onnxruntime-MIT.txt",
        assets / "licenses/onnxruntime-LICENSE",
    )
    if model_cache:
        from sys import path as module_path

        module_path.insert(0, str(Path(__file__).resolve().parents[1]))
        from studio.manage import verify_assets

        previous = verify_assets(model_cache)
        if (
            previous.get("model_revision") != REV
            or previous.get("model_sha")
            != "10921310ddef06557ec1598d1260470a0a4db53f70ffe0deb60b946dcad6d27a"
        ):
            raise ValueError("model cache does not match fixed TinyCLIP revision/hash")
        shutil.copytree(
            Path(model_cache) / "models/tinyclip", models, dirs_exist_ok=True
        )
    if model:
        required = (
            "config.json",
            "preprocessor_config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "onnx/model_quantized.onnx",
        )
        for name in (
            *required,
            "special_tokens_map.json",
            "vocab.json",
            "merges.txt",
            "README.md",
        ):
            dest = models / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with urllib.request.urlopen(
                    BASE + name, timeout=60
                ) as response, dest.open("wb") as target:
                    shutil.copyfileobj(response, target, 1024 * 1024)
            except urllib.error.HTTPError as e:
                if e.code == 404 and name not in required:
                    dest.unlink(missing_ok=True)
                else:
                    raise
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file():
            with p.open("rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            files.append(
                dict(
                    path=str(p.relative_to(out)), sha256=digest, bytes=p.stat().st_size
                )
            )
    model_file = models / "onnx/model_quantized.onnx"
    manifest = dict(
        schema="microduck-studio-assets/v1",
        three="0.170.0",
        transformers="3.7.2",
        ready=True,
        model_ready=model_file.exists(),
        model_repository=REPO,
        model_revision=REV,
        model_sha=(
            hashlib.file_digest(model_file.open("rb"), "sha256").hexdigest()
            if model_file.exists()
            else None
        ),
        model_local_id="tinyclip",
        licenses={
            "three": "MIT",
            "transformers": "Apache-2.0",
            "onnxruntime": "MIT",
            "tinyclip": "MIT",
        },
        files=files,
    )
    (assets / "asset-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--node-modules", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", action="store_true")
    p.add_argument("--model-cache", type=Path)
    a = p.parse_args()
    r = build(a.node_modules, a.out, a.model, a.model_cache)
    print(
        json.dumps(
            dict(
                files=len(r["files"]),
                model_ready=r["model_ready"],
                model_sha=r["model_sha"],
            )
        )
    )
