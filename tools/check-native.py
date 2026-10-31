#!/usr/bin/env python3
"""Build/test an isolated, patched native checkout. Never deploys or accesses a robot."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'calibration/common'))
import apply_source_patch as patcher


def run(args):
    source=args.source.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    expected=patcher.EXPECTED_TAG_COMMITS[args.target]
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    if head!=expected:raise ValueError('source HEAD is not the pinned official target')
    allowed={patcher.PARAMS,patcher.REGISTRY,patcher.BUS,patcher.IMU,patcher.MAIN}
    if args.target in ('daemon-v0.17.9','daemon-v0.18.3'):allowed.add(patcher.EDITOR)
    changed=set(subprocess.check_output(['git','diff','HEAD','--name-only'],cwd=source,text=True).splitlines())
    if not changed or changed-allowed:raise ValueError('expected only the reviewed native calibration patch')
    diff=subprocess.check_output(['git','diff','HEAD','--binary'],cwd=source)
    if args.target in ('daemon-v0.17.9','daemon-v0.18.3') and args.stage in ('host-test','cross-build'):
        reviewed=Path(__file__).resolve().parents[1]/'calibration'/args.target/'native-calibration-v3.patch'
        if diff!=reviewed.read_bytes():raise ValueError('native diff differs from the published reviewed patch; use the pinned formatter or review/register a new build')
    digest=hashlib.sha256(diff).hexdigest();revision=expected[:8]+'-calv3-'+digest[:12]
    env=os.environ.copy();env['DUCK_REVISION']=revision;env['CARGO_TARGET_DIR']=str(out/'target')
    if args.runtime_root:
        runtime=args.runtime_root.resolve()
        env.update(CARGO_HOME=str(runtime/'cargo'),RUSTUP_HOME=str(runtime/'rustup'),
            RUSTUP_TOOLCHAIN='1.89.0-aarch64-apple-darwin',
            CARGO_ZIGBUILD_CACHE_DIR=str(runtime/'cache/cargo-zigbuild'),ZIG_GLOBAL_CACHE_DIR=str(runtime/'cache/zig-global'),
            ZIG_LOCAL_CACHE_DIR=str(out/'zig-local'),CARGO_ZIGBUILD_ZIG_PATH=str(runtime/'venv/lib/python3.12/site-packages/ziglang/zig'))
        env['PATH']=':'.join([str(runtime/'venv/bin'),str(runtime/'cargo/bin'),env['PATH']])
        sysroot=runtime/'calv2-sysroot'
        if sysroot.is_dir():
            env.update(PKG_CONFIG_ALLOW_CROSS='1',PKG_CONFIG_SYSROOT_DIR=str(sysroot),
                PKG_CONFIG_LIBDIR=':'.join(str(sysroot/p) for p in ('usr/lib/aarch64-linux-gnu/pkgconfig','usr/share/pkgconfig')))
    commands={
        'fmt':['cargo','fmt','--all'],
        'host-test':['cargo','test','--locked','--offline','-p','robotd-params','-p','duck-control','-p','robotd','-p','robotctl'],
        'cross-build':['cargo','zigbuild','--locked','--offline','--release','--target',args.board_target,'-p','robotd','-p','robotctl'],
        'fetch':['cargo','fetch','--locked'],
    }
    command=commands[args.stage];record=dict(stage=args.stage,argv=command,source=str(source),target=args.target,
        base_revision=head,patch_sha256=digest,build_revision=revision,started_at=datetime.now(timezone.utc).isoformat(),hardware_access=False)
    log=out/(args.stage+'.log')
    with log.open('w') as stream:result=subprocess.run(command,cwd=source,env=env,stdout=stream,stderr=subprocess.STDOUT)
    record.update(returncode=result.returncode,finished_at=datetime.now(timezone.utc).isoformat())
    (out/(args.stage+'.json')).write_text(json.dumps(record,indent=2)+'\n')
    print(log.read_text(errors='replace')[-5500:]);return result.returncode


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--target',choices=sorted(patcher.SUPPORTED),default='daemon-v0.17.9')
    parser.add_argument('--stage',choices=['fmt','host-test','cross-build','fetch'],required=True)
    parser.add_argument('--board-target',default='aarch64-unknown-linux-gnu.2.35')
    parser.add_argument('--runtime-root',type=Path,help='optional existing workspace macOS toolchain; never installs packages')
    args=parser.parse_args()
    try:sys.exit(run(args))
    except (OSError,ValueError,subprocess.CalledProcessError) as error:parser.exit(2,str(error)+'\n')
