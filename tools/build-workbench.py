#!/usr/bin/env python3
"""Build a self-contained source + universal serial wheel portable kit."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

VERSION='0.1.0'
WHEEL='pyserial-3.5-py2.py3-none-any.whl'
SHA='c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0'

def build(root,out,wheelhouse):
    root=Path(root);out=Path(out)
    if out.exists():raise ValueError('output already exists')
    wheel=Path(wheelhouse)/WHEEL
    if hashlib.sha256(wheel.read_bytes()).hexdigest()!=SHA:raise ValueError('pyserial hash mismatch')
    paths=[root/'workbench.sh',root/'workbench.cmd',root/'tools/setup-workbench.py']
    paths += [p for p in (root/'servo_workbench').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']
    paths += list((root/'deploy/workbench').glob('*'))
    paths += list((root/'docs').glob('servo-workbench*.md'))
    entries={str(p.relative_to(root)):p.read_bytes() for p in paths}
    entries['README.zh-CN.md']=(root/'docs/servo-workbench-guide.zh-CN.md').read_bytes()
    entries['wheels/'+WHEEL]=wheel.read_bytes()
    manifest=dict(schema='microduck-servo-workbench-bundle/v1',component_version=VERSION,
                  tools_version=(root/'VERSION').read_text().strip(),python_min='3.11',
                  hardware_acceptance=False,files=[dict(path=n,bytes=len(b),sha256=hashlib.sha256(b).hexdigest()) for n,b in sorted(entries.items())])
    entries['WORKBENCH-MANIFEST.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    out.parent.mkdir(parents=True,exist_ok=True);prefix='microduck-servo-workbench-'+VERSION+'/'
    if out.suffix=='.zip':
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for name,raw in sorted(entries.items()):
                info=zipfile.ZipInfo(prefix+name,date_time=(2026,11,2,0,0,0))
                info.external_attr=(0o100755 if name.endswith('.sh') else 0o100644)<<16
                z.writestr(info,raw,compress_type=zipfile.ZIP_DEFLATED)
    else:
        with tarfile.open(out,'w:gz') as z:
            for name,raw in sorted(entries.items()):
                info=tarfile.TarInfo(prefix+name);info.size=len(raw);info.mode=0o755 if name.endswith('.sh') else 0o644
                z.addfile(info,io.BytesIO(raw))
    out.with_name(out.name+'.sha256').write_text(hashlib.sha256(out.read_bytes()).hexdigest()+'  '+out.name+'\n')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--wheelhouse',type=Path,required=True)
    a=p.parse_args();m=build(Path(__file__).resolve().parents[1],a.out,a.wheelhouse)
    print(json.dumps(dict(component_version=VERSION,files=len(m['files']),out=str(a.out))))
