#!/usr/bin/env python3
"""Manual-upgrade aids: inspect ABI, check loaders, prepare no-auto config, atomic tool links.
Never applies a daemon release, starts/stops a service, opens a bus, or performs recovery.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import stat
import shutil
import subprocess
import tempfile
import tomllib


class Error(ValueError):
    pass


def version(value):
    if not re.fullmatch(r'\d+(?:\.\d+)+', value):
        raise Error('Invalid version: '+value)
    return tuple(map(int, value.split('.')))


def elf(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise Error('Expected a regular ELF file: '+str(path))
    data=path.read_bytes()
    def unpack(fmt, offset):
        if offset<0 or offset+struct.calcsize(fmt)>len(data):raise Error('Truncated ELF')
        return struct.unpack_from(fmt,data,offset)
    if data[:6]!=b'\x7fELF\x02\x01' or unpack('<H',18)[0]!=183:
        raise Error('Expected ELF64 little-endian AArch64: '+str(path))
    def string(table, pos):
        if not 0<=pos<len(table):raise Error('Invalid string offset')
        end=table.find(b'\0',pos)
        if end<0:raise Error('Unterminated ELF string')
        return table[pos:end].decode('ascii')
    phoff=unpack('<Q',32)[0];size,count=unpack('<HH',54)
    segments=[unpack('<IIQQQQQQ',phoff+i*size) for i in range(count)]
    def mapped(addr):
        for typ,_,off,va,_,length,_,_ in segments:
            if typ==1 and va<=addr<va+length:return off+addr-va
        raise Error('Unmapped ELF address')
    tags=[];interpreter=None
    for typ,_,off,_,_,length,_,_ in segments:
        if typ==3:interpreter=string(data,off)
        if typ==2:
            for pos in range(off,off+length,16):
                key,val=unpack('<qQ',pos)
                if key==0:break
                tags.append((key,val))
    tables=[mapped(v) for k,v in tags if k==5]
    if len(tables)!=1:raise Error('Missing/ambiguous dynamic string table')
    table=tables[0]
    needed=[string(data,table+v) for k,v in tags if k==1]
    # DT_VERNEED / DT_VERNEEDNUM, not a regex over arbitrary executable strings.
    starts=[mapped(v) for k,v in tags if k==0x6ffffffe]
    counts=[v for k,v in tags if k==0x6fffffff]
    if len(starts)!=1 or len(counts)!=1:raise Error('Missing/ambiguous version requirements')
    required=[];pos=starts[0];seen=set()
    for _ in range(counts[0]):
        if pos in seen:raise Error('Cyclic version requirements')
        seen.add(pos);_,n,_,aux,nxt=unpack('<HHIII',pos);ap=pos+aux;aux_seen=set()
        for _ in range(n):
            if ap in aux_seen:raise Error('Cyclic version auxiliaries')
            aux_seen.add(ap);_,_,_,name,step=unpack('<IHHII',ap)
            label=string(data,table+name)
            if label.startswith('GLIBC_'):required.append(label.removeprefix('GLIBC_'))
            if not step:break
            ap+=step
        if not nxt:break
        pos+=nxt
    if not required:raise Error('No GLIBC version requirements')
    floor=max(required,key=version)
    return dict(file=str(path),sha256=hashlib.sha256(data).hexdigest(),machine='AArch64',interpreter=interpreter,needed=needed,glibc_required=sorted(set(required),key=version),glibc_floor=floor)


def manual_config(raw):
    config=tomllib.loads(raw)
    # Changing the root setting alone must preserve all source, keys and gate settings.
    first=re.search(r'^\s*\[',raw,re.M);end=first.start() if first else len(raw)
    head,tail=raw[:end],raw[end:]
    pattern=r'(?m)^auto_apply\s*=.*$'
    if re.search(pattern,head):head=re.sub(pattern,'auto_apply = "off"',head)
    else:head='auto_apply = "off"\n'+head
    candidate=head+tail;expected=dict(config);expected['auto_apply']='off'
    if tomllib.loads(candidate)!=expected:raise Error('Manual config changed unrelated values')
    return candidate


def atomic_file(path, raw):
    path=Path(path)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.lexists(name):os.unlink(name)


def tool_link(path, target, expected):
    path=Path(path);target=Path(target)
    if not target.is_absolute() or not target.is_dir():raise Error('Target must be an existing absolute directory')
    if os.path.lexists(path) and not path.is_symlink():raise Error('Refuse non-symlink current')
    current=os.readlink(path) if path.is_symlink() else None
    if current==str(target):
        dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
        return 'already-selected'
    if current!=(None if expected=='MISSING' else expected):raise Error('Current changed; inspect, do not overwrite')
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'-upgrade-',dir=path.parent);os.close(fd);os.unlink(tmp)
    try:
        os.symlink(str(target),tmp);os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.lexists(tmp):os.unlink(tmp)
    return 'selected'


def checked_loader(path, run):
    result=run(['ldd','-r',str(path)],capture_output=True,text=True,timeout=20)
    text=result.stdout+'\n'+result.stderr
    if result.returncode or re.search(r'not found|cannot open|No such file|undefined symbol|symbol lookup error',text,re.I):
        raise Error('Loader failed for '+str(path)+': '+text.strip())
    return text.strip()


def onnx_version(path):
    library=ctypes.CDLL(str(path))
    library.OrtGetApiBase.restype=ctypes.POINTER(ctypes.c_void_p*2)
    base=library.OrtGetApiBase()
    if not base:raise Error('OrtGetApiBase returned null')
    get_api=ctypes.CFUNCTYPE(ctypes.c_void_p,ctypes.c_uint32)(base.contents[0])
    get_version=ctypes.CFUNCTYPE(ctypes.c_char_p)(base.contents[1])
    value=get_version()
    if not value or not get_api(23):raise Error('ONNX Runtime does not supply API 23')
    value=value.decode('ascii')
    if version(value)<version('1.23'):raise Error('ONNX Runtime below 1.23')
    return value


def runtime(root, official, native, inputs, phase, run=subprocess.run):
    if platform.system()!='Linux' or platform.machine() not in ('aarch64','arm64'):
        raise Error('Runtime probes require Linux ARM64; do not execute Linux payloads on this host')
    known=json.loads(Path(inputs).read_text());reports=[]
    expected_official={'btd','configd','mediad','netpadd','nfcd','padd','pet-detect','pet-features','robotctl','robotd','sounds','tofd','updaterd'}
    if set(known.get('official',{}))!=expected_official or set(known.get('native',{}))!={'robotd','robotctl'}:raise Error('Incomplete/unknown 13+2 program manifest')
    for group,directory in [('official',official),('native',native)]:
        for name,expected in known[group].items():
            file=Path(directory)/'bin'/name
            if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=expected:raise Error('Input SHA mismatch: '+str(file))
            report=elf(file);report['loader']=checked_loader(file,run);reports.append(report)
    # A filename alone is not evidence: load the actual runtime in a bounded subprocess.
    ort=Path(root)/'usr/local/lib/libonnxruntime.so'
    script=Path(__file__).resolve()
    result=run([os.sys.executable,str(script),'onnx',str(ort)],capture_output=True,text=True,timeout=15)
    if result.returncode:raise Error('ONNX probe failed (ldd does not cover dlopen), exit '+str(result.returncode)+': '+result.stderr.strip())
    return dict(phase=phase,elf=reports,onnx=result.stdout.strip(),board_probe=True,hardware_verified=False)


def validate_disk_tree(tree, disk, image_device):
    def nodes(items):
        for item in items:
            yield item
            yield from nodes(item.get('children',[]))
    allnodes=list(nodes(tree['blockdevices']))
    selected=next((x for x in allnodes if x.get('path')==str(disk)),None)
    if not selected or selected.get('type')!='disk':raise Error('Select the whole original disk, not a partition')
    family=list(nodes([selected]))
    if any(any(m for m in (x.get('mountpoints') or []) if m) for x in family):raise Error('Disk or a descendant is mounted/in use')
    if image_device in {x.get('maj:min') for x in family}:raise Error('Image must be on another physical storage device')
    if image_device not in {x.get('maj:min') for x in allnodes}:raise Error('Image filesystem has unknown physical backing; use a verified external block device')
    return selected


def disk_check(disk, image, mode):
    if platform.system()!='Linux':raise Error('Disk checks require the independent Linux rescue environment')
    disk=Path(disk).resolve();image=Path(image).resolve()
    if not stat.S_ISBLK(disk.stat().st_mode):raise Error('Not a block device')
    result=subprocess.run(['lsblk','--json','--paths','--output','PATH,TYPE,MOUNTPOINTS,MAJ:MIN'],check=True,capture_output=True,text=True,timeout=10)
    device=image.parent.stat().st_dev
    selected=validate_disk_tree(json.loads(result.stdout),disk,f'{os.major(device)}:{os.minor(device)}')
    size=int(subprocess.check_output(['blockdev','--getsize64',str(disk)],text=True,timeout=10))
    if mode=='backup':
        if image.exists():raise Error('Backup image already exists')
        if shutil.disk_usage(image.parent).free<size:raise Error('Insufficient external free space for full disk image')
    else:
        if not image.is_file() or image.stat().st_size!=size:raise Error('Restore image must match original whole-disk size')
    return dict(mode=mode,disk=str(disk),bytes=size,identity=selected,image=str(image),read_only_check=True)


def deferred(units, run=subprocess.run):
    result=run(['systemctl','list-units','--all','--type=timer','--type=service','--output=json'],capture_output=True,text=True,timeout=10)
    if result.returncode:raise Error('Cannot inspect deferred restart timers')
    items=json.loads(result.stdout)
    if not isinstance(items,list):raise Error('Invalid deferred unit inventory')
    def properties(unit):
        reply=run(['systemctl','show',unit,'-p','Transient','-p','Triggers','-p','ExecStart'],capture_output=True,text=True,timeout=10)
        if reply.returncode:raise Error('Cannot inspect transient '+unit)
        return dict(line.split('=',1) for line in reply.stdout.splitlines() if '=' in line)
    for item in items:
        name=item.get('unit','')
        if not name.startswith('run-') or item.get('active') not in ('active','activating','reloading'):continue
        props=properties(name)
        if props.get('Transient')!='yes':raise Error('Unknown active run unit; inspect '+name)
        services=props.get('Triggers','').split() if name.endswith('.timer') else [name]
        if not services:raise Error('Cannot determine transient timer trigger '+name)
        for service in services:
            command=properties(service).get('ExecStart','')
            if not command:raise Error('Cannot determine transient command '+service)
            if 'systemctl' in command and 'restart' in command:
                for unit in units:
                    bare=unit.removesuffix('.service')
                    if re.search(r'(?<![\w.-])'+re.escape(bare)+r'(?:\.service)?(?![\w.-])',command):raise Error('Deferred robot restart still pending: '+name+' -> '+service)
    return 'PASS: no active deferred robot restart found'


def stopped(units, run=subprocess.run, cgroup_root=Path('/sys/fs/cgroup'), check_deferred=True):
    if check_deferred:deferred(units,run)
    for unit in units:
        result=run(['systemctl','show',unit,'-p','Id','-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlPID','-p','Job','-p','ControlGroup'],capture_output=True,text=True,timeout=10)
        props=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        if props.get('LoadState')=='not-found':continue
        if result.returncode:raise Error('Cannot inspect '+unit)
        if props.get('Id')!=unit:raise Error('Unexpected unit alias: '+unit)
        if props.get('ActiveState') not in ('inactive','failed'):raise Error('Service is not stopped: '+unit)
        if props.get('Job') not in ('','0'):raise Error('Job remains: '+unit)
        allowed_pids=(None,'0') if unit.endswith('.timer') else ('0',)
        if props.get('MainPID') not in allowed_pids or props.get('ControlPID') not in allowed_pids:raise Error('PID remains or service PID unavailable: '+unit)
        group=props.get('ControlGroup','')
        if group:
            if not group.startswith('/') or '..' in Path(group).parts:raise Error('Invalid cgroup path')
            path=cgroup_root/group.lstrip('/')
            if path.exists():
                for procs in [path/'cgroup.procs',*path.rglob('*/cgroup.procs')]:
                    if procs.exists() and procs.read_text().strip():raise Error('cgroup still has tasks: '+unit)
    return 'PASS: selected services stopped, no PIDs, Jobs or cgroup tasks at inspection time'


def recovery_action(old, current, apply_started, completed):
    # Markers are hints; a changed release always requires inspection even if a marker was lost.
    if completed and current!='0.18.3':return 'STOP: release differs from completed session; inspect updater recovery'
    if current=='0.18.3':return 'INSPECT_NEW: do not re-apply; inspect update outcome and health before continuing'
    if current==old:return 'INSPECT_OLD: marker absence proves nothing; inspect update outcome/hooks before cleanup or retry'
    return 'STOP: unknown release or interrupted swap; use console and verified image if consistency cannot be proved'


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    q=sub.add_parser('elf');q.add_argument('files',nargs='+',type=Path)
    q=sub.add_parser('runtime');q.add_argument('--official',type=Path,required=True);q.add_argument('--native',type=Path,required=True);q.add_argument('--inputs',type=Path,required=True);q.add_argument('--phase',choices=['before','after'],required=True)
    q=sub.add_parser('manual-config');q.add_argument('--source',type=Path,required=True);q.add_argument('--out',type=Path,required=True)
    q=sub.add_parser('remember-links');q.add_argument('--out',type=Path,required=True)
    q=sub.add_parser('tool-link');q.add_argument('--link',type=Path,required=True);q.add_argument('--target',type=Path,required=True);g=q.add_mutually_exclusive_group(required=True);g.add_argument('--expect');g.add_argument('--expect-file',type=Path)
    q=sub.add_parser('onnx',help='bounded child ONNX API probe; no hardware');q.add_argument('file',type=Path)
    q=sub.add_parser('disk-check');q.add_argument('--disk',type=Path,required=True);q.add_argument('--image',type=Path,required=True);q.add_argument('--mode',choices=['backup','restore'],required=True)
    q=sub.add_parser('deferred');q.add_argument('--units',type=Path,required=True)
    q=sub.add_parser('stopped');q.add_argument('--units',type=Path,required=True)
    q=sub.add_parser('recovery');q.add_argument('--old',required=True);q.add_argument('--current',required=True);q.add_argument('--apply-started',action='store_true');q.add_argument('--completed',action='store_true')
    a=p.parse_args()
    try:
        if a.cmd=='elf':print(json.dumps([elf(f) for f in a.files],indent=2))
        elif a.cmd=='onnx':print(onnx_version(a.file))
        elif a.cmd=='disk-check':print(json.dumps(disk_check(a.disk,a.image,a.mode),indent=2))
        elif a.cmd=='deferred':print(deferred(a.units.read_text().splitlines()))
        elif a.cmd=='stopped':print(stopped(a.units.read_text().splitlines()))
        elif a.cmd=='runtime':print(json.dumps(runtime('/',a.official,a.native,a.inputs,a.phase),indent=2))
        elif a.cmd=='manual-config':
            if a.source.resolve()==a.out.resolve():raise Error('Never overwrite production updater config')
            raw=manual_config(a.source.read_text()).encode()
            if a.out.is_symlink():raise Error('Refuse symlink output')
            if a.out.exists():
                if a.out.read_bytes()!=raw:raise Error('Existing manual config differs; inspect session')
            else:atomic_file(a.out,raw)
            print(a.out)
        elif a.cmd=='remember-links':
            values={}
            for name in ('/opt/microduck-tools/current','/opt/microduck-tools/native/current'):
                path=Path(name)
                if os.path.lexists(path) and not path.is_symlink():raise Error('Current must be absent or a symlink: '+name)
                values[name]=os.readlink(path) if path.is_symlink() else 'MISSING'
            raw=(json.dumps(values,indent=2)+'\n').encode()
            if a.out.exists():
                if a.out.read_bytes()!=raw:raise Error('Saved links differ; do not replace the original snapshot')
            else:atomic_file(a.out,raw)
            print(a.out)
        elif a.cmd=='tool-link':
            if str(a.link) not in ('/opt/microduck-tools/current','/opt/microduck-tools/native/current'):raise Error('Only tool current links; official daemon is managed by updater')
            expected=json.loads(a.expect_file.read_text())[str(a.link)] if a.expect_file else a.expect
            print(tool_link(a.link,a.target,expected))
        else:print(recovery_action(a.old,a.current,a.apply_started,a.completed))
    except (OSError,ValueError,KeyError,AttributeError,struct.error,subprocess.SubprocessError) as exc:p.exit(2,str(exc)+'\n')


if __name__=='__main__':main()
