#!/usr/bin/env python3
"""Build a source-only runtime archive; never installs or accesses a robot."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile


def build(root,out):
    root=Path(root);out=Path(out)
    if out.exists():raise ValueError('output already exists; choose a new release path')
    version=(root/'VERSION').read_text().strip()
    files=[root/'VERSION',root/'README.md',root/'docs/compatibility.zh-CN.md']
    files += list((root/'deploy').glob('*'))
    files += list((root/'calibration/common').glob('*.py'))
    files += [p for p in (root/'calibration/integration-0.16.3/web').iterdir() if p.suffix in ('.py','.html','.js')]
    files += [root/'calibration/integration-0.16.3/servo-maintenance.py',root/'calibration/integration-0.16.3/requirements-maintenance.txt']
    files += list((root/'calibration/integration-0.16.3/evidence/demo').glob('*.toml'))
    entries=[]
    for p in sorted(files):
        if not p.is_file() or p.is_symlink():raise ValueError('release inputs must be regular files')
        entries.append((str(p.relative_to(root)),p.read_bytes()))
    manifest=dict(schema='microduck-tools-runtime/v1',tool_version=version,python_min='3.11',hardware_acceptance=False,
        files=[dict(path=name,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)) for name,raw in entries])
    entries.append(('RUNTIME-MANIFEST.json',(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()))
    out.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(out,'w:gz') as archive:
        for name,raw in entries:
            info=tarfile.TarInfo('microduck-tools-'+version+'/'+name);info.size=len(raw);info.mode=0o644
            archive.addfile(info,io.BytesIO(raw))
    digest=hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_name(out.name+'.sha256').write_text(digest+'  '+out.name+'\n')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();result=build(Path(__file__).resolve().parents[1],args.out)
    print(json.dumps(dict(version=result['tool_version'],files=len(result['files']),out=str(args.out))))
