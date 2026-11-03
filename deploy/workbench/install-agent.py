#!/usr/bin/env python3
"""Offline manual agent install. Default prints a preview. Never touches robotd."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

VERSION='0.1.1'

def verify(source):
    source=Path(source)
    manifest=json.loads((source/'WORKBENCH-MANIFEST.json').read_text())
    if manifest.get('component_version')!=VERSION:raise ValueError('bundle version mismatch')
    for row in manifest['files']:
        path=Path(row['path'])
        if path.is_absolute() or '..' in path.parts:raise ValueError('unsafe manifest path')
        target=source/path
        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('bundle hash mismatch: '+str(path))
    return manifest

def install(source,port,kind,root=Path('/'),runner=subprocess.run):
    """root and runner are injectable only for tests; CLI always uses the real root."""
    source=Path(source);root=Path(root);manifest=verify(source)
    if kind not in ('xl330','hd1910') or not port.startswith('/dev/') or '..' in Path(port).parts:
        raise ValueError('explicit supported kind and /dev/ port required')
    base=root/'opt/microduck-servo-workbench'
    release=base/'releases'/VERSION
    config=root/'etc/microduck-servo-workbench/agent.json'
    value=dict(schema=1,port=port,kind=kind,state_dir='/var/lib/microduck-servo-workbench')
    if config.exists() and json.loads(config.read_text())!=value:
        raise ValueError('existing agent configuration differs; review it manually')
    for p in (base,base/'releases',config.parent,root/'var/lib/microduck-servo-workbench'):
        if p.is_symlink():raise ValueError('installation path must not be a symlink')
        p.mkdir(parents=True,exist_ok=True)
        p.chmod(0o700 if '/var/lib/' in str(p) else 0o755)
    if release.exists():raise ValueError('release already exists; do not overwrite rollback evidence')
    staging=base/('releases/.install-'+str(os.getpid()))
    staging.mkdir(mode=0o755)
    try:
        for row in manifest['files']:
            name=Path(row['path'])
            target=staging/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/name,target);target.chmod(0o755 if name.suffix=='.sh' else 0o644)
        (staging/'WORKBENCH-MANIFEST.json').write_text(json.dumps(manifest,indent=2))
        shutil.copyfile(staging/'deploy/workbench/agent.sh',staging/'agent.sh');(staging/'agent.sh').chmod(0o755)
        # Create the venv at its final location: venv paths must not be relocated.
        staging.rename(release)
        runner([sys.executable,'-I',str(release/'tools/setup-workbench.py')],check=True)
        if not config.exists():
            with open(config,'x') as f:json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
            config.chmod(0o644)
        link=base/'current.new'
        if link.exists() or link.is_symlink():raise ValueError('unfinished current.new; inspect before retry')
        link.symlink_to(release)
        link.replace(base/'current')
    except BaseException:
        # Keep the partial release for inspection; current/config authority is never guessed.
        raise
    return dict(release=str(release),config=str(config),autostart=False,robotd_changed=False)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--port',required=True);p.add_argument('--kind',choices=['xl330','hd1910'],required=True)
    p.add_argument('--apply',action='store_true');a=p.parse_args()
    verify(a.source)
    if not a.apply:
        print(json.dumps(dict(action='preview_only',port=a.port,kind=a.kind,version=VERSION,
                             destination='/opt/microduck-servo-workbench',robotd_changed=False),ensure_ascii=False,indent=2));return
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('apply requires Linux root')
    print(json.dumps(install(a.source,a.port,a.kind),ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        print(str(error),file=sys.stderr);raise SystemExit(1)
