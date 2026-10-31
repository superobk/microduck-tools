#!/usr/bin/env python3
"""Fixed 0.18.3 deployment scripts. No SSH, direct bus access or robot motion commands.
Production paths are fixed; tests inject a temporary filesystem and a fake system backend.
"""
import argparse
import ctypes
import signal
import contextlib
import fcntl
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import shlex
import re
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import uuid

PACKAGE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('upgrade_review',PACKAGE/'tools/upgrade-review.py')
review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)
sys.path.insert(0,str(PACKAGE/'calibration/common'))
import duck_calibrate as calibration

STATE='/var/lib/microduck-tools-manager'
DROPIN='zzzzzz-microduck-manager.conf'
NATIVE='/opt/microduck-tools/native/releases/b2b0d213-calv3-30452a086d1e-glibc230'
TARGET='0.18.3'
PLANS={
 'check':'核对固定程序、运行库、当前服务和校正来源；仅写诊断缓存。',
 'backup':'核验外部系统镜像；停止机器人服务；保存原配置、程序、服务状态和校正。',
 'install':'保存维护覆盖、关闭自动升级；用原可信更新器预演并安装固定官方0.18.3。',
 'configure':'安装迁移后的校正、配套native和Tools；保持no-policy，服务留待启动。',
 'start':'启动所选robotd；校正模式下启动Tools并启用其开机自启；不运行机器人init/enable/动作。',
 'stop':'互斥停止机器人服务；保留程序和配置。',
 'reload':'停止本次机器人服务，再启动已选择的robotd和Tools以加载保存值；会重新打开总线。',
 'rollback':'只撤销本次维护或退回同版官方；完整回到0.16.3需救援镜像恢复。',
 'recover':'核对中断后的实际版本与事务；只在已证明安全时结束中断或重做配置。',
 'maintenance':'临时停止非root Tools，打开仅本机监听的维护Web；结束后恢复Tools，不重启robotd。'}


class Error(ValueError):pass


def digest(path,progress=None):
    with open(path,'rb') as stream:
        h=hashlib.sha256()
        size=Path(path).stat().st_size;done=0;last=0.0
        while block:=stream.read(4*1024*1024):
            h.update(block);done+=len(block)
            if progress and time.monotonic()-last>1:progress(f'{done*100//max(size,1)}%');last=time.monotonic()
        if progress:progress('100%')
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(),parse_constant=lambda v:(_ for _ in ()).throw(Error('非法JSON数值')))


def write_json(path,value):
    review.atomic_file(path,(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode())


class Paths:
    def __init__(self,root='/'):self.root=Path(root)
    def at(self,path):return self.root/str(path).lstrip('/')
    @property
    def state(self):return self.at(STATE)
    @property
    def current(self):return self.at('/opt/robot/daemon/current')
    @property
    def base(self):return self.at('/etc/robot/robotd.toml')
    @property
    def user(self):return self.at('/var/lib/robot/robotd.toml')
    @property
    def cal(self):return self.at('/var/lib/robot/calibration/microduck-0.18.3.toml')
    def dropin(self,unit):return self.at('/etc/systemd/system/'+unit+'.d/'+DROPIN)


def updater_idle(rows):
    if not isinstance(rows,list):raise Error('升级器状态格式未知，未执行任何停止/安装')
    row=next((x for x in rows if isinstance(x,dict) and x.get('component')=='daemon'),None)
    if not row or row.get('phase') not in ('idle','Idle'):raise Error('升级事务忙或状态未知；等待原事务，不重试安装')
    return row


class System:
    def __init__(self,paths):self.p=paths;self.hash_cache={}
    def privileged(self):return os.geteuid()==0
    def is_board(self):return platform.system()=='Linux' and platform.machine() in ('aarch64','arm64')
    def machine(self):
        value=self.p.at('/etc/machine-id').read_text().strip()
        if not value:raise Error('缺少稳定machine-id，不能绑定管理会话')
        return value
    def run(self,args,timeout=30):
        result=subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=timeout)
        if result.returncode:raise Error('命令失败：'+str(args[0])+'\n'+(result.stderr or result.stdout)[-4000:])
        return result.stdout
    def run_review(self,args,**kwargs):return subprocess.run([str(x) for x in args],**kwargs)
    def live(self,args,log):
        # Leave long installer output visible and save it. It may exceed the updater's 30s gate.
        print('  正在执行，详细输出同时保存在 '+str(log),flush=True)
        with open(log,'a') as out:
            process=subprocess.Popen([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            try:
                for line in process.stdout:print(line.rstrip(),flush=True);out.write(line);out.flush()
                code=process.wait()
            except BaseException:
                # Do not kill updaterd: a disconnected client is not proof the server stopped applying.
                if process.poll() is None:process.terminate();process.wait(timeout=10)
                raise
        if code:raise Error('操作返回非零；先运行 ./recover.sh，不要重试安装。日志：'+str(log))
    def props(self,unit):
        result=subprocess.run(['systemctl','show',unit,'-p','Id','-p','LoadState','-p','ActiveState','-p','MainPID','-p','ExecStart','-p','ExecStartPre','-p','ExecStartPost','-p','ExecStop','-p','ExecStopPost','-p','UnitFileState','-p','ConditionResult','-p','RequiredBy','-p','BoundBy','-p','ConsistsOf','-p','PropagatesStopTo'],capture_output=True,text=True,timeout=10)
        props=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode and props.get('LoadState')!='not-found':raise Error('无法检查服务 '+unit)
        return props
    def units(self,bundle):
        units=set((bundle/'robot-units.txt').read_text().splitlines())
        directory=self.p.current/'systemd'
        if directory.is_dir():units.update(p.name for p in directory.iterdir() if p.suffix in ('.service','.timer'))
        if any('/' in x or not x.endswith(('.service','.timer')) for x in units):raise Error('服务清单非法')
        return sorted(units)
    def guard_units(self,units):
        allowed=set(units)|{'multi-user.target'}
        for unit in units:
            p=self.props(unit)
            if p.get('LoadState')=='not-found':continue
            if p.get('Id')!=unit:raise Error('服务别名未经审阅：'+unit)
            if any(p.get(key) for key in ('ExecStartPre','ExecStartPost','ExecStop','ExecStopPost')):raise Error('存在未审阅启停钩子：'+unit)
            if p.get('UnitFileState','').startswith('masked') or p.get('ConditionResult')=='no':raise Error('存在mask/安全条件未通过：'+unit)
            for key in ('RequiredBy','BoundBy','ConsistsOf','PropagatesStopTo'):
                extra=set(p.get(key,'').split())-allowed
                if extra:raise Error('存在未审阅服务联动 '+unit+' '+key+': '+', '.join(sorted(extra)))
        if 'updaterd.service' not in units:return
        up=self.props('updaterd.service').get('ExecStart','')
        for expected in ('/opt/robot/daemon/current/bin/updaterd',):
            if expected not in up:raise Error('更新器入口不是已审阅的官方current，停止适配')
        if '/etc/robot/updater.toml' not in up and str(self.p.state/'updater-manual.toml') not in up:raise Error('更新器配置入口未知')
    def updater_config(self):
        # Both reviewed units use this same state_dir; never guess a lock location.
        c=tomllib.loads(self.p.at('/etc/robot/updater.toml').read_text())
        directory=Path(c['state_dir'])
        if not directory.is_absolute() or not directory.is_dir():raise Error('升级器state_dir无效')
        if Path(c['component']['daemon']['install_dir'])!=Path('/opt/robot/daemon'):raise Error('非标准daemon发布目录未适配')
        return c,directory
    def accounts(self):
        robot=self.run(['getent','group','robot'])
        result=self.run_review(['getent','passwd','microduck-tools'],capture_output=True,text=True,timeout=10)
        if result.returncode==2:
            group=self.run_review(['getent','group','microduck-tools'],capture_output=True,text=True,timeout=10)
            if group.returncode==2:self.run(['groupadd','--system','microduck-tools'])
            elif group.returncode:raise Error('无法检查工具用户组')
            self.run(['useradd','--system','--gid','microduck-tools','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','microduck-tools'])
        elif result.returncode or len(result.stdout.split(':'))<7 or not result.stdout.split(':')[2].isdigit() or result.stdout.split(':')[2]=='0':raise Error('已有工具用户异常，未改变账户')
        self.run(['usermod','-a','-G','robot','microduck-tools'])
    def settle(self,units,ctl,progress):
        deadline=time.monotonic()+45
        while True:
            try:
                self.updater(ctl)
                if self.pending():raise Error('pending尚未清除')
                review.deferred(units,run=self.run_review);return
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                if time.monotonic()>=deadline:raise Error('等待官方事务/延迟重启超时；请recover查看，不重试安装') from exc
                progress('  等待官方事务和延迟重启完成…');time.sleep(1)
    def updater(self,ctl):return updater_idle(json.loads(self.run([ctl,'update','status','--json'])))
    def pending(self):
        _,directory=self.updater_config();path=directory/'pending.json'
        if not path.exists():return False
        data=read_json(path)
        if not isinstance(data,dict):raise Error('待恢复事务文件非法')
        return bool(data)
    def quiesce(self,units,ctl):
        review.deferred(units,run=self.run_review)
        c,directory=self.updater_config()
        fd=os.open(directory/'update.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Error('升级器持有互斥锁；未停止任何服务')
            if self.pending():raise Error('存在待恢复升级事务；未停止任何服务')
            if self.props('updaterd.service').get('ActiveState')=='active':self.updater(ctl)
            for unit in units:
                if unit.endswith('.timer') and self.props(unit).get('ActiveState')=='active':self.run(['systemctl','stop',unit])
            self.run(['systemctl','stop','updaterd.service'])
            for unit in units:
                if unit=='updaterd.service' or unit.endswith('.timer'):continue
                if self.props(unit).get('ActiveState')=='active':self.run(['systemctl','stop',unit])
            review.stopped(units,run=self.run_review,cgroup_root=self.p.at('/sys/fs/cgroup'))
        finally:os.close(fd)
    def extract(self,archive,out):
        out.mkdir()
        self.run(['tar','--zstd','-xf',archive,'-C',out],timeout=60)
    def runtime(self,bundle,official):return review.runtime('/',official,bundle/'native',bundle/'inputs.json','before')
    def actual(self):
        disk=self.p.current.resolve().name if self.p.current.is_symlink() and self.p.current.exists() else None
        result=dict(release=disk,robotd={},tools={},receipt=None,web=None,pending=None,official_integrity=None)
        if disk==TARGET:
            known=read_json(PACKAGE/'deploy/manual-upgrade/inputs.json')['official']
            try:
                okay=True
                for name,expected in known.items():
                    file=self.p.current/'bin'/name;info=file.stat();key=(str(file),info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
                    if key not in self.hash_cache:self.hash_cache[key]=digest(file)
                    if self.hash_cache[key]!=expected:okay=False
                result['official_integrity']=okay
            except OSError:result['official_integrity']=False
        for unit,key in [('robotd.service','robotd'),('microduck-tools.service','tools')]:
            props=self.props(unit);result[key]=dict(active=props.get('ActiveState'),pid=props.get('MainPID'),command=props.get('ExecStart'))
        try:
            pid=int(result['robotd']['pid'] or 0)
            if pid:
                result['robotd']['exe']=os.readlink('/proc/'+str(pid)+'/exe')
                result['robotd']['sha256']=digest('/proc/'+str(pid)+'/exe')
        except (OSError,ValueError):pass
        receipt=self.p.at('/run/robotd/calibration-effective.json')
        if receipt.exists():
            try:result['receipt']=read_json(receipt)
            except (OSError,ValueError):pass
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=1) as response:result['web']=json.load(response)
        except (OSError,ValueError):pass
        try:result['pending']=self.pending()
        except (OSError,ValueError):pass
        return result
    def image_external(self,image):
        data=json.loads(self.run(['lsblk','--json','--paths','--output','PATH,TYPE,MOUNTPOINTS,MAJ:MIN']))
        def walk(nodes):
            for node in nodes:yield node;yield from walk(node.get('children',[]))
        disks=[d for d in data['blockdevices'] if d.get('type')=='disk' and any('/' in (n.get('mountpoints') or []) for n in walk([d]))]
        if len(disks)!=1:raise Error('系统盘布局未知，镜像范围需人工核对')
        device=image.stat().st_dev;name=str(os.major(device))+':'+str(os.minor(device))
        if name in {n.get('maj:min') for n in walk(disks)}:raise Error('镜像在系统同一物理盘上，不能作为恢复保障')
        if name not in {n.get('maj:min') for n in walk(data['blockdevices'])}:raise Error('镜像介质来源无法核对')
        return disk_identity(disks[0]['path'])
    def archive(self,out):
        paths=[x for x in ('etc/robot','etc/systemd/system','etc/microduck-tools','opt/robot','opt/microduck-tools','var/lib/robot','var/lib/microduck-tools') if self.p.at(x).exists()]
        self.run(['tar','--acls','--xattrs','--numeric-owner','--exclude='+str(Path(out).parent).lstrip('/'),'--exclude=var/lib/robot/manual-upgrades','--exclude=var/lib/microduck-tools-manager','-C','/','-czpf',out,*paths],timeout=600)
    def check_start(self,unit):
        own=self.p.dropin(unit)
        if not own.is_file():raise Error('缺少本次服务覆盖：'+unit)
        expected=[line[len('ExecStart='):] for line in own.read_text().splitlines() if line.startswith('ExecStart=') and line!='ExecStart=']
        actual=re.findall(r'argv\[\]=(.*?)\s*;',self.props(unit).get('ExecStart',''))
        if len(expected)!=1 or len(actual)!=1 or shlex.split(actual[0])!=shlex.split(expected[0]):raise Error('有效启动命令与本次完整参数不一致：'+unit)
    def identity_start(self,unit,expected):
        self.check_start(unit)
        if expected not in self.p.dropin(unit).read_text():raise Error('启动入口不符：'+unit)
        self.run(['systemctl','start',unit])
    def maintenance(self,state,bundle,camera_apply):
        parent=os.getpid()
        def parent_death():
            # Linux-only child: prevent an orphaned privileged Web after manager SIGKILL.
            library=ctypes.CDLL(None)
            if library.prctl(1,signal.SIGTERM,0,0,0)!=0 or os.getppid()!=parent:os._exit(125)
        args=[sys.executable,'/opt/microduck-tools/current/calibration/integration-0.16.3/web/calibration_web.py','--target','daemon-v0.18.3','--listen','127.0.0.1','--port','8765','--config','/etc/robot/robotd.toml','--dest','/var/lib/robot/calibration/microduck-0.18.3.toml','--robotctl','/opt/microduck-tools/native/current/bin/robotctl','--plugin-config','/etc/microduck-tools/plugins.json','--servo-health-dir',str(state/'maintenance-health'),'--camera-settings',str(state/'maintenance-camera.json')]
        if camera_apply:args.append('--enable-camera-apply')
        print('维护Web：http://127.0.0.1:8765；请在Mac建立SSH端口转发。完成后按Ctrl-C，自动恢复普通Tools。',flush=True)
        process=subprocess.Popen(args,preexec_fn=parent_death)
        try:
            try:code=process.wait(timeout=3600)
            except KeyboardInterrupt:return
            except subprocess.TimeoutExpired:raise Error('维护达到1小时上限，已结束；保存值仍需另行重载')
            if code:raise Error('维护Web启动/运行失败；原服务将恢复')
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            if process.poll() is None:raise Error('维护Web未退出，未启动重复服务')
    def wait_maintenance_exit(self):
        deadline=time.monotonic()+20
        while True:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=.5):pass
            except OSError:return
            if time.monotonic()>=deadline:raise Error('8765仍被占用，未恢复重复Web；请查看状态')
            time.sleep(.5)
    def tool_account(self):
        data=self.run(['getent','passwd','microduck-tools']).split(':');return int(data[2]),int(data[3])
    def reload(self):self.run(['systemctl','daemon-reload'])


class Manager:
    def __init__(self,bundle,paths=None,system=None,progress=print):
        self.bundle=Path(bundle).resolve();self.p=paths or Paths();self.sys=system or System(self.p);self.say=progress
    def load(self):
        path=self.p.state/'session.json'
        if not path.exists():return None
        s=read_json(path)
        if s.get('schema')!='microduck-manager/v1' or not isinstance(s.get('install_attempted'),bool):raise Error('会话记录损坏：停止操作，不猜测旧状态')
        if s.get('manager_version')!=(PACKAGE/'VERSION').read_text().strip() or s.get('bundle_code_sha')!=digest(self.bundle/'tool/RUNTIME-MANIFEST.json'):raise Error('管理程序/运行包改变，保留旧会话，不自动接管')
        if s['machine']!=self.sys.machine():raise Error('会话来自另一块板，不能沿用')
        return s
    def save(self,s):
        write_json(self.p.state/'session.json',s)
    @contextlib.contextmanager
    def locked(self):
        self.p.state.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=self.p.state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:raise Error('管理状态目录须为当前root私有目录')
        fd=os.open(self.p.state/'manager.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Error('另一个管理脚本正在工作，请用status查看')
            yield
        finally:os.close(fd)
    def inputs(self):
        path=self.bundle/'inputs.json'
        if path.read_bytes()!=(PACKAGE/'deploy/manual-upgrade/inputs.json').read_bytes():raise Error('输入清单与本版工具不一致')
        known=read_json(path)
        for name,source in [('robot-units.txt',PACKAGE/'deploy/manager/robot-units.txt'),*[(f'deploy/{x}',PACKAGE/'deploy/manual-upgrade'/x) for x in ('robotd-native.conf','robotd-official.conf')]]:
            file=self.bundle/name
            if file.is_symlink() or file.read_bytes()!=source.read_bytes():raise Error('服务模板/清单与本版不一致：'+name)
        if digest(self.bundle/'official/daemon-0.18.3.tar.zst')!=known['official_tar_sha256']:raise Error('官方归档SHA不符')
        for name,expected in known['native'].items():
            file=self.bundle/'native/bin'/name
            if file.is_symlink() or digest(file)!=expected:raise Error('候选SHA不符：'+name)
        rt=read_json(self.bundle/'tool/RUNTIME-MANIFEST.json')
        if rt['tool_version']!=(PACKAGE/'VERSION').read_text().strip():raise Error('运行包版本不匹配')
        for item in rt['files']:
            rel=Path(item['path'])
            if rel.is_absolute() or '..' in rel.parts:raise Error('运行包路径非法')
            file=self.bundle/'tool'/rel
            if file.is_symlink() or digest(file)!=item['sha256']:raise Error('运行文件不符：'+str(rel))
        return known
    def session(self):
        s=self.load()
        if s:
            if s['bundle_sha']!=digest(self.bundle/'inputs.json'):raise Error('材料已改变，不自动接管旧会话')
            return s
        return dict(schema='microduck-manager/v1',manager_version=(PACKAGE/'VERSION').read_text().strip(),bundle_code_sha=digest(self.bundle/'tool/RUNTIME-MANIFEST.json'),machine=self.sys.machine(),bundle_sha=digest(self.bundle/'inputs.json'),stage='new',backup=None,ledger={},operation=None,install_attempted=False,last_error=None)
    def step(self,s,label,func):
        s['operation']['step']=label;self.save(s);self.say('  ▶ '+label)
        result=func();return result
    def allowed_files(self):
        return {str(p) for p in [self.p.user,self.p.cal,self.p.at('/etc/microduck-tools/plugins.json'),self.p.at('/etc/systemd/system/microduck-tools.service'),self.p.at('/var/lib/microduck-tools/camera.json'),*[self.p.dropin(x) for x in ('robotd.service','updaterd.service','microduck-tools.service')]]}
    def managed(self,s,path,raw):
        path=Path(path);key=str(path)
        if key not in self.allowed_files():raise Error('不在本工具管理文件范围：'+key)
        new=hashlib.sha256(raw).hexdigest();entry=s['ledger'].get(key)
        if path.is_symlink():raise Error('拒绝写符号链接 '+key)
        current=digest(path) if path.exists() else None
        if entry is None:
            if path.exists():
                original=self.p.state/'files'/hashlib.sha256(key.encode()).hexdigest();original.parent.mkdir(mode=0o700,exist_ok=True)
                review.atomic_file(original,path.read_bytes())
            else:original=None
            entry=dict(before=current,snapshot=str(original) if original else None,mode=path.stat().st_mode&0o777 if path.exists() else 0o644,uid=path.stat().st_uid if path.exists() else os.geteuid(),gid=path.stat().st_gid if path.exists() else os.getegid(),allowed=[]);s['ledger'][key]=entry
        if current not in [entry['before'],*entry['allowed']]:raise Error('文件在脚本外被改动，保留现场：'+key)
        if new not in entry['allowed']:entry['allowed'].append(new)
        self.save(s) # journal intent BEFORE the atomic replacement
        path.parent.mkdir(parents=True,exist_ok=True)
        if current!=new:review.atomic_file(path,raw)
        self.file_permissions(path,entry)
    def file_permissions(self,path,entry):
        info=path.stat()
        if (info.st_uid,info.st_gid)!=(entry['uid'],entry['gid']):os.chown(path,entry['uid'],entry['gid'])
        path.chmod(entry['mode'])
        with path.open('rb') as stream:os.fsync(stream.fileno())
    def original_cal(self):
        data=tomllib.loads(self.p.base.read_text());value=data.get('bus',{}).get('calibration_path')
        if not isinstance(value,str) or not value or not Path(value).is_absolute():raise Error('没有可迁移的实际旧校正文件；固定编译imu16p4须先核实参数，不使用demo')
        path=self.p.at(value)
        if path.name.startswith('demo-'):raise Error('不能使用合成demo作为实际旧校正')
        calibration.parse_calibration(path,'daemon-v0.16.3')
        return path
    def official(self):
        out=self.p.state/'official'
        if out.exists():
            # Never reuse partial extraction.
            known=read_json(self.bundle/'inputs.json')
            if all((out/'bin'/n).is_file() and digest(out/'bin'/n)==h for n,h in known['official'].items()):return out
            shutil.rmtree(out)
        self.sys.extract(self.bundle/'official/daemon-0.18.3.tar.zst',out)
        return out
    def ensure_backup(self,s):
        b=s.get('backup')
        if not b:raise Error('没有完整可核验备份，请先 ./backup.sh --apply')
        archive=Path(b['archive'])
        if not archive.is_file():raise Error('应用备份丢失')
        identity=[archive.stat().st_ino,archive.stat().st_size,archive.stat().st_mtime_ns]
        if identity!=b.get('archive_stat') and digest(archive)!=b['archive_sha']:raise Error('应用备份已改变')
        image=Path(b['image'])
        if not image.is_file() or image.stat().st_size!=b['image_bytes']:raise Error('原系统镜像不可访问，不能继续')
        if digest(b['calibration'])!=b['calibration_sha']:raise Error('迁移原件改变，停止操作')
    def verify_official(self):
        if self.sys.actual()['release']!=TARGET:raise Error('磁盘发布不是固定0.18.3')
        known=read_json(self.bundle/'inputs.json')
        if any(digest(self.p.current/'bin'/n)!=h for n,h in known['official'].items()):raise Error('当前官方套件不完整或被改动')
        if self.sys.pending():raise Error('升级仍有pending事务，不配置、不重试')
    def action(self,name,apply=False,image=None,scale_confirmed=False,servo_health=None,to='official',camera_apply=False):
        if name not in PLANS:raise Error('未知操作')
        if not apply and name!='check':return dict(plan=self.plan(name,servo_health,to),applied=False,next='确认现场后运行对应脚本 --apply')
        if not self.sys.is_board():raise Error('安装操作只适用于Linux ARM64板端；当前仅可查看预览')
        if not self.sys.privileged():raise Error('请用sudo运行此脚本；不会自动提权')
        with self.locked():
            self.inputs();s=self.session()
            if s.get('operation') and name!='recover':raise Error('上次操作未结束，请先 ./recover.sh；不要重复安装')
            s['operation']=dict(name=name,step='开始',status='running',started=int(time.time()));s['last_error']=None;self.save(s)
            try:
                if name=='check':self.check(s)
                elif name=='backup':self.backup(s,image,scale_confirmed)
                elif name=='install':self.install(s)
                elif name=='configure':self.configure(s,servo_health)
                elif name=='start':self.start(s)
                elif name=='stop':self.stop(s)
                elif name=='reload':self.stop(s);self.start(s)
                elif name=='rollback':self.rollback(s,to)
                elif name=='maintenance':self.maintenance(s,camera_apply)
                else:self.recover(s)
                s['operation']=None;s['last_error']=None;self.save(s)
            except BaseException as exc:
                s['last_error']=str(exc) or type(exc).__name__;s['operation']['status']='failed';self.save(s);raise
            return self.status()
    def check(self,s):
        units=self.sys.units(self.bundle);self.sys.guard_units(units)
        actual=self.sys.actual()
        if actual['release'] not in ('0.16.3',TARGET):raise Error('只适配0.16.3→0.18.3或本次0.18.3维护')
        self.step(s,'检查动态库和ONNX（不启动robotd）',lambda:self.sys.runtime(self.bundle,self.official()))
        if actual['release']=='0.16.3':
            self.original_cal()
            if self.sys.props('updaterd.service').get('ActiveState')=='active':self.sys.updater(self.p.current/'bin/robotctl')
        if self.sys.pending():raise Error('有未完成的升级恢复事务')
        s['checked']=True;s['stage']='checked' if not s['backup'] else s['stage']
    def backup(self,s,image,scale_confirmed):
        if s['install_attempted'] or self.sys.actual()['release']!='0.16.3':raise Error('安装开始后不能重做旧备份')
        if s.get('backup'):
            if image and str(Path(image).resolve())!=s['backup']['image']:
                image=Path(image).resolve();self.sys.image_external(image)
                if digest(image,lambda p:self.say('  重新绑定镜像校验 '+p))!=s['backup']['image_sha']:raise Error('不是原备份镜像，未重新绑定')
                s['backup']['image']=str(image)
            self.ensure_backup(s);self.say('  原备份已存在，保持不变');return
        self.check(s)
        if not image:raise Error('请选择原系统镜像：./backup.sh --image /外部介质/原系统.img --apply')
        image=Path(image).resolve();proof=read_json(str(image)+'.proof.json')
        if proof.get('schema')!='microduck-image/v1' or proof.get('image_name')!=image.name or not proof.get('compared'):raise Error('镜像不是image.sh生成且逐字节核验的记录')
        current_identity=self.sys.image_external(image) or {}
        matched=compare_disk_identity(proof.get('disk_identity',{}),current_identity)
        self.step(s,'核验原系统镜像（大文件需要时间）',lambda:None)
        if digest(image,lambda p:self.say('  镜像SHA '+p))!=proof['sha256'] or image.stat().st_size!=proof['bytes']:raise Error('镜像校验失败')
        cal=self.original_cal();old=calibration.parse_calibration(cal,'daemon-v0.16.3');profile=calibration.effective_gyro(old)['gyro_profile']
        if profile!='st_500dps' and not scale_confirmed:raise Error('旧比例为'+profile+'；核实MCU契约后使用 --firmware-scale-confirmed')
        s['old_release']=str(self.p.current.resolve());s['units']=self.sys.units(self.bundle)
        if 'active_before' not in s:s['active_before']=[u for u in s['units'] if self.sys.props(u).get('ActiveState')=='active']
        self.save(s)
        self.step(s,'互斥停止更新器与机器人服务',lambda:self.sys.quiesce(s['units'],self.p.current/'bin/robotctl'))
        private=self.p.state/'private';private.mkdir(mode=0o700,exist_ok=True)
        oldcal=private/'original-calibration.toml';review.atomic_file(oldcal,cal.read_bytes())
        migrated=private/'calibration-0183.toml'
        args=calibration.parser().parse_args(['migrate','--source',str(oldcal),'--target','daemon-v0.18.3','--out',str(migrated),'--confirm-same-hardware',*(['--confirm-firmware-scale'] if scale_confirmed else []),*(['--overwrite'] if migrated.exists() else [])]);
        with contextlib.redirect_stdout(io.StringIO()):calibration.cmd_migrate(args)
        ctl=private/'old-robotctl';shutil.copy2(self.p.current/'bin/robotctl',ctl);ctl.chmod(0o700)
        external=Path(s.get('external_backup') or image.parent/('microduck-backup-'+uuid.uuid4().hex[:12]));external.mkdir(mode=0o700,exist_ok=True)
        s['external_backup']=str(external);self.save(s)
        archive=external/'application-before.tar.gz';partial=external/'application.partial.tar.gz'
        self.step(s,'保存应用、配置、服务和插件（不替代系统镜像）',lambda:self.sys.archive(partial));os.replace(partial,archive)
        links={str(self.p.at(k)):os.readlink(self.p.at(k)) if self.p.at(k).is_symlink() else 'MISSING' for k in ('/opt/microduck-tools/current','/opt/microduck-tools/native/current')}
        shutil.copy2(oldcal,external/'original-calibration.toml');shutil.copy2(migrated,external/'calibration-0183.toml')
        s['backup']=dict(archive=str(archive),archive_sha=digest(archive),archive_stat=[archive.stat().st_ino,archive.stat().st_size,archive.stat().st_mtime_ns],image=str(image),image_bytes=image.stat().st_size,image_sha=proof['sha256'],image_identity='自动匹配稳定标识' if matched else '依据操作员核对',calibration=str(migrated),calibration_sha=digest(migrated),original_cal_path=str(cal),original_cal_sha=digest(cal),base_sha=digest(self.p.base),old_pointer=tomllib.loads(self.p.base.read_text())['bus']['calibration_path'],old_links=links);s['stage']='backed_up'
        write_json(external/'backup-record.json',s)
    def install(self,s):
        self.ensure_backup(s)
        if s['install_attempted']:raise Error('本会话已开始过安装，先 ./recover.sh，不重复apply')
        if self.sys.actual()['release']!='0.16.3':raise Error('当前不是备份对应的0.16.3')
        if digest(self.p.base)!=s['backup']['base_sha'] or digest(s['backup']['original_cal_path'])!=s['backup']['original_cal_sha']:raise Error('原配置/校正在备份后发生变化，不使用过期备份安装')
        units=s['units'];ctl=self.p.state/'private/old-robotctl'
        self.sys.guard_units(units);self.sys.quiesce(units,ctl)
        original=self.p.at('/etc/robot/updater.toml').read_text();manual=self.p.state/'updater-manual.toml';review.atomic_file(manual,review.manual_config(original).encode())
        self.step(s,'确认新更新器能读取原配置',lambda:self.sys.run([self.official()/'bin/updaterd','--config',manual,'--self-test']))
        up=f'[Service]\nExecStart=\nExecStart=/opt/robot/daemon/current/bin/updaterd --config {manual} --socket /run/updaterd.sock\n'.encode()
        self.managed(s,self.p.dropin('updaterd.service'),up)
        self.managed(s,self.p.dropin('robotd.service'),(self.bundle/'deploy/robotd-official.conf').read_bytes());self.sys.reload()
        self.sys.identity_start('updaterd.service',str(manual))
        command=[ctl,'update','apply','daemon','--version',TARGET,'--from',self.bundle/'official']
        self.step(s,'官方签名和兼容预演',lambda:self.sys.live([*command,'--dry-run'],self.p.state/'install.log'))
        s['install_attempted']=True;self.save(s) # durable intent before the actual server call
        self.step(s,'升级官方套件；原事务可能在终端断开后继续',lambda:self.sys.live(command,self.p.state/'install.log'))
        self.step(s,'等待官方事务和延迟重启',lambda:self.sys.settle(units,self.p.current/'bin/robotctl',self.say))
        self.step(s,'核对官方版本和升级事务',self.verify_official)
        self.step(s,'升级后重新检查运行库',lambda:self.sys.runtime(self.bundle,self.p.current));s['official_verified']=True;s['selected_mode']='official';s['stage']='official_installed'
    def stop(self,s):
        units=self.sys.units(self.bundle);self.sys.guard_units(units);self.sys.quiesce(units,self.p.current/'bin/robotctl');s['stage']='stopped'
    def configure(self,s,servo_health):
        self.ensure_backup(s);self.verify_official()
        if not s.get('official_verified'):raise Error('官方安装尚未完成核验，请先recover')
        if s.get('configured') and s.get('selected_mode')=='native' and servo_health is None:
            self.say('  已配置：IMU/关节/相机请在统一Web调整；此处不重置已有校正。');return
        if servo_health is not None and s.get('configured') and s.get('selected_mode')=='native':
            self.installed_programs()
            self.sys.guard_units(['microduck-tools.service']);self.sys.check_start('microduck-tools.service')
            path=self.p.at('/etc/microduck-tools/plugins.json');data=read_json(path) if path.exists() else {'schema':1};data.setdefault('servo_health',{})['enabled']=servo_health
            s['plugin_restart_pending']=self.sys.props('microduck-tools.service').get('ActiveState')=='active';self.save(s)
            self.managed(s,path,(json.dumps(data,indent=2)+'\n').encode())
            if s['plugin_restart_pending']:self.sys.run(['systemctl','restart','microduck-tools.service'])
            s['plugin_restart_pending']=False;s['stage']='configured';return
        self.sys.guard_units(s['units']);self.sys.quiesce(s['units'],self.p.current/'bin/robotctl')
        path=self.p.user;current=path.read_text() if path.exists() else ''
        user=tomllib.loads(current);pointer=user.get('bus',{}).get('calibration_path')
        if s.get('configured'):
            calibration.parse_calibration(self.p.cal,'daemon-v0.18.3')
            if pointer!='/var/lib/robot/calibration/microduck-0.18.3.toml':raise Error('校正指针已改变，不自动重置')
        else:
            calibration.parse_calibration(s['backup']['calibration'],'daemon-v0.18.3')
            if pointer not in (None,'',s['backup']['old_pointer'],'/var/lib/robot/calibration/microduck-0.18.3.toml'):raise Error('已有其他校正指针，未自动改写')
            if self.p.cal.exists() and digest(self.p.cal)!=s['backup']['calibration_sha'] and str(self.p.cal) not in s['ledger']:raise Error('已有不同的新校正，未覆盖其零位/方向/比例')
            self.managed(s,self.p.cal,Path(s['backup']['calibration']).read_bytes())
            modified=calibration.replace_bus_key(current,'calibration_path','/var/lib/robot/calibration/microduck-0.18.3.toml')
            self.managed(s,path,modified.encode())
        version=(PACKAGE/'VERSION').read_text().strip();native=self.p.at(NATIVE);tool=self.p.at('/opt/microduck-tools/releases/'+version)
        for source,dest in [(self.bundle/'native',native),(self.bundle/'tool',tool)]:
            self.step(s,'准备独立程序目录 '+dest.name,lambda source=source,dest=dest:self.copy_release(source,dest))
        self.step(s,'验证安装后的程序',lambda:self.sys.runtime(self.bundle,self.p.current))
        expected=read_json(self.bundle/'inputs.json')['native']
        if any(digest(native/'bin'/name)!=sha for name,sha in expected.items()):raise Error('复制后的native不完整')
        for key,target in [('/opt/microduck-tools/native/current',native),('/opt/microduck-tools/current',tool)]:
            link=self.p.at(key);link.parent.mkdir(parents=True,exist_ok=True);review.tool_link(link,target,s['backup']['old_links'][str(link)])
        self.managed(s,self.p.dropin('robotd.service'),(self.bundle/'deploy/robotd-native.conf').read_bytes())
        baseunit=self.p.at('/etc/systemd/system/microduck-tools.service')
        if not baseunit.exists():self.managed(s,baseunit,(tool/'deploy/microduck-tools.service').read_bytes())
        self.sys.accounts()
        settings=self.p.at('/etc/microduck-tools/plugins.json')
        if not settings.exists():self.managed(s,settings,b'{"schema":1,"servo_health":{"enabled":true}}\n')
        if servo_health is not None:
            data=read_json(settings);data.setdefault('servo_health',{})['enabled']=servo_health;self.managed(s,settings,(json.dumps(data,indent=2)+'\n').encode())
        command='/usr/bin/python3 /opt/microduck-tools/current/calibration/integration-0.16.3/web/calibration_web.py --target auto --listen 127.0.0.1 --port 8765 --plugin-config /etc/microduck-tools/plugins.json --servo-health-dir /var/lib/microduck-tools/servo-health --camera-settings /var/lib/microduck-tools/camera.json --robotctl /opt/microduck-tools/native/current/bin/robotctl'
        self.managed(s,self.p.dropin('microduck-tools.service'),('[Service]\nExecStart=\nExecStart='+command+'\n').encode())
        self.sys.run(['install','-d','-m','0700','-o','microduck-tools','-g','microduck-tools','/var/lib/microduck-tools'])
        self.sys.reload();s['configured']=True;s['selected_mode']='native';s['stage']='configured'
    def copy_release(self,source,dest):
        manifest=source/'RUNTIME-MANIFEST.json'
        names=[x['path'] for x in read_json(manifest)['files']]+['RUNTIME-MANIFEST.json'] if manifest.exists() else ['bin/robotd','bin/robotctl']
        expected={name:digest(source/name) for name in names}
        if dest.exists():
            actual={str(x.relative_to(dest)):digest(x) for x in dest.rglob('*') if x.is_file() and '__pycache__' not in x.parts and x.suffix!='.pyc'}
            if expected!=actual:raise Error('版本目录内容不同，保留现场未覆盖：'+str(dest))
            return
        dest.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.'+dest.name+'-',dir=dest.parent) as work:
            temp=Path(work)/'release';temp.mkdir(mode=0o755)
            for name in names:
                target=temp/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target)
            os.replace(temp,dest)
    def start(self,s):
        self.ensure_backup(s);self.verify_official()
        if not s.get('official_verified'):raise Error('请先核验官方安装')
        self.sys.guard_units(s['units'])
        if s.get('selected_mode')=='official':
            self.sys.reload();self.sys.identity_start('robotd.service','/opt/robot/daemon/current/bin/robotd');s['stage']='official_mode';return
        if not s.get('configured'):raise Error('请先 ./configure.sh --apply')
        self.installed_programs()
        calibration.parse_calibration(self.p.cal,'daemon-v0.18.3')
        if tomllib.loads(self.p.user.read_text()).get('bus',{}).get('calibration_path')!='/var/lib/robot/calibration/microduck-0.18.3.toml':raise Error('校正指针不符，未启动robotd')
        self.sys.guard_units(s['units']);self.sys.reload();self.sys.check_start('robotd.service');self.sys.check_start('microduck-tools.service')
        self.sys.run(['systemctl','enable','microduck-tools.service'])
        self.sys.identity_start('robotd.service','/opt/microduck-tools/native/current/bin/robotd');self.sys.identity_start('microduck-tools.service','--robotctl /opt/microduck-tools/native/current/bin/robotctl');s['stage']='running_unverified'
    def installed_programs(self):
        for source,target,link in [(self.bundle/'native',self.p.at(NATIVE),self.p.at('/opt/microduck-tools/native/current')),(self.bundle/'tool',self.p.at('/opt/microduck-tools/releases/'+(PACKAGE/'VERSION').read_text().strip()),self.p.at('/opt/microduck-tools/current'))]:
            if not target.is_dir() or not link.is_symlink() or link.resolve()!=target.resolve():raise Error('已安装程序/工具链接不符，请保留现场检查：'+str(link))
            self.copy_release(source,target) # existing destination: verification only; no copying
    def maintenance(self,s,camera_apply):
        self.ensure_backup(s);self.verify_official()
        if not s.get('configured') or s.get('selected_mode')!='native':raise Error('请先完成配置并启动校正模式')
        self.installed_programs()
        self.sys.guard_units(['microduck-tools.service',*(['mediad.service'] if camera_apply else [])])
        self.sys.check_start('microduck-tools.service');actual=self.sys.actual()
        if actual['robotd'].get('sha256')!=read_json(self.bundle/'inputs.json')['native']['robotd']:raise Error('实际robotd不是本包校正程序')
        settings=self.p.at('/var/lib/microduck-tools/camera.json');temp=self.p.state/'maintenance-camera.json'
        if settings.is_symlink():raise Error('相机设置不能是符号链接')
        if temp.exists():temp.unlink()
        if settings.exists():
            validate_camera(settings.read_bytes());review.atomic_file(temp,settings.read_bytes())
        # Capture before image in the regular journal without opening up private observer files.
        s['maintenance']=dict(restore_tools=self.sys.props('microduck-tools.service').get('ActiveState')=='active',restore_mediad=camera_apply and self.sys.props('mediad.service').get('ActiveState')!='active',camera_before=digest(settings) if settings.exists() else None)
        self.save(s)
        try:
            self.sys.run(['systemctl','stop','microduck-tools.service'])
            if camera_apply:self.sys.run(['systemctl','start','mediad.service'])
            self.sys.maintenance(self.p.state,self.bundle,camera_apply)
        finally:self.finish_maintenance(s)
    def finish_maintenance(self,s):
        info=s.get('maintenance')
        if not info:return
        # A vanished parent terminates its Linux child; wait for the local port to disappear.
        if not info.get('web_exited'):
            self.sys.wait_maintenance_exit();info['web_exited']=True;self.save(s)
        settings=self.p.at('/var/lib/microduck-tools/camera.json');temp=self.p.state/'maintenance-camera.json'
        try:
            if temp.exists():
                raw=temp.read_bytes();validate_camera(raw)
                current=digest(settings) if settings.exists() else None
                if current not in (info['camera_before'],info.get('camera_target')):raise Error('相机设置被外部修改，保留维护副本，未覆盖；普通Tools已尝试恢复')
                info['camera_target']=hashlib.sha256(raw).hexdigest();self.save(s)
                self.managed(s,settings,raw)
                if info['camera_before'] is None:
                    account=self.sys.tool_account();entry=s['ledger'][str(settings)];entry.update(uid=account[0],gid=account[1],mode=0o600);self.file_permissions(settings,entry);self.save(s)
                temp.unlink()
        finally:
            if info['restore_mediad']:
                self.sys.run(['systemctl','stop','mediad.service']);info['restore_mediad']=False;self.save(s)
            if info['restore_tools']:
                self.installed_programs()
                self.sys.guard_units(['microduck-tools.service']);self.sys.check_start('microduck-tools.service');self.sys.run(['systemctl','start','microduck-tools.service']);info['restore_tools']=False;self.save(s)
        s['maintenance']=None;self.save(s)
    def restore_files(self,s):
        for name,entry in s['ledger'].items():
            path=Path(name)
            if name not in self.allowed_files():raise Error('恢复记录含非本工具文件，未恢复')
            current=digest(path) if path.exists() else None
            if path.is_symlink() or current not in [entry['before'],*entry['allowed']]:raise Error('恢复前发现外部改动：'+name)
            if entry['snapshot']:
                if Path(entry['snapshot'])!=self.p.state/'files'/hashlib.sha256(name.encode()).hexdigest():raise Error('恢复快照位置异常')
                if digest(entry['snapshot'])!=entry['before']:raise Error('原文件快照损坏')
        for name,entry in s['ledger'].items():
            path=Path(name)
            if entry['snapshot']:
                review.atomic_file(path,Path(entry['snapshot']).read_bytes());self.file_permissions(path,entry)
            elif path.exists():
                path.unlink();fd=os.open(path.parent,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
    def rollback(self,s,to):
        if to=='image':raise Error('完整回退：用救援系统启动后运行 ./image.sh restore；当前系统不会被覆盖')
        if s.get('units'):self.sys.guard_units(s['units'])
        if to=='session':
            if s['install_attempted'] or self.sys.actual()['release']!='0.16.3':raise Error('已开始安装，不能把应用恢复冒充完整回退；请用镜像')
            if s.get('backup'):self.ensure_backup(s)
            elif not s.get('old_release') or str(self.p.current.resolve())!=s['old_release'] or 'active_before' not in s or s['ledger']:
                raise Error('没有可核对的安装前维护记录，不猜测原服务状态')
            self.sys.quiesce(s['units'],self.p.current/'bin/robotctl');self.restore_files(s);self.sys.reload()
            for unit in s.get('active_before',[]):self.sys.run(['systemctl','start',unit])
            s['selected_mode']=None;s['stage']='session_restored';return
        self.ensure_backup(s)
        self.verify_official()
        if not s.get('official_verified'):raise Error('官方安装尚未完成核验，先recover')
        self.sys.quiesce(s['units'],self.p.current/'bin/robotctl')
        self.managed(s,self.p.dropin('robotd.service'),(self.bundle/'deploy/robotd-official.conf').read_bytes());self.sys.reload();self.sys.identity_start('robotd.service','/opt/robot/daemon/current/bin/robotd');s['selected_mode']='official';s['stage']='official_mode'
    def recover(self,s):
        if s.get('maintenance'):self.finish_maintenance(s)
        if not s.get('backup'):s['stage']='new';return
        self.ensure_backup(s);actual=self.sys.actual()
        if actual['pending']:raise Error('官方恢复事务尚未完成，不启动另一引擎')
        if actual['release']==TARGET:
            if not s['install_attempted']:raise Error('版本被外部操作改变，未自动接管')
            self.verify_official();self.sys.settle(s['units'],self.p.current/'bin/robotctl',self.say)
            if s.get('plugin_restart_pending'):
                self.installed_programs()
                self.sys.guard_units(['microduck-tools.service']);self.sys.check_start('microduck-tools.service');self.sys.run(['systemctl','restart','microduck-tools.service']);s['plugin_restart_pending']=False
            s['official_verified']=True
            if not s.get('selected_mode'):s['selected_mode']='official'
            s['stage']='official_installed'
        elif actual['release']=='0.16.3':
            if s['install_attempted']:raise Error('可能已自动回退；系统依赖/服务可能有残留，请保留日志并选择镜像恢复')
            s['stage']='backed_up'
        else:raise Error('实际版本未知，改用救援镜像恢复')
    def plan(self,name,servo_health=None,to='official'):
        s=self.load() if self.sys.is_board() else None
        if name=='configure' and s and s.get('configured') and s.get('selected_mode')=='native':
            return '仅调整健康插件开关，必要时重启Tools，robotd保持运行。' if servo_health is not None else '已有配置保持不变；显示统一Web入口。'
        if name=='rollback':
            return {'official':'停止相关机器人服务，退回同版官方robotd，再启动官方robotd；Tools暂留停止。','session':'只在正式安装前撤销本次覆盖，恢复原先运行的服务；不降级发布。','image':'仅显示完整回退入口；用独立救援系统运行image.sh restore，不覆盖当前运行系统。'}[to]
        return PLANS[name]
    def status(self):
        if not self.sys.is_board():return dict(mode='本机预览',stage='未连接板端',checks=[],next='./check.sh（在板端运行）',hardware='NOT_RUN')
        try:s=self.load()
        except PermissionError:return dict(mode='权限不足',stage='需要只读sudo权限',error='管理记录为root私有；尚未读取，不代表记录损坏',next='sudo ./status.sh',hardware='未知')
        except (OSError,ValueError) as exc:return dict(mode='需要恢复',stage='会话损坏',error=str(exc),next='./recover.sh（先预览）',hardware='未知')
        try:actual=self.sys.actual()
        except (OSError,ValueError,subprocess.SubprocessError) as exc:return dict(mode='状态未知',stage='无法完成只读诊断',error=str(exc),next='./check.sh；不要据此重复安装',hardware='未知')
        checks=[]
        checks.append(dict(item='磁盘官方版本',state='文件SHA已核对' if actual.get('official_integrity') is True else '待升级/待核对',value=actual['release']))
        checks.append(dict(item='机器人进程',state='运行中' if actual['robotd'].get('active')=='active' else '已停止/未知',value=actual['robotd'].get('exe')))
        web=actual.get('web') or {};r=actual.get('receipt') or {};known=read_json(self.bundle/'inputs.json') if (self.bundle/'inputs.json').exists() else {}
        verified=bool(actual.get('official_integrity') is True and actual['pending'] is False and web.get('runtime_verified') is True and web.get('pending_restart') is False and web.get('differences')==[] and web.get('demo') is False and web.get('allow_inactive') is False and r.get('pid')==int(actual['robotd'].get('pid') or 0) and actual['robotd'].get('sha256')==known.get('native',{}).get('robotd') and r.get('build_revision')==known.get('native_revision') and r.get('user_layer_rejected','unknown') is None)
        pointer=None
        try:pointer=tomllib.loads(self.p.user.read_text()).get('bus',{}).get('calibration_path') if self.p.user.exists() else None
        except (OSError,ValueError):pass
        config_valid=self.p.cal.exists() and pointer=='/var/lib/robot/calibration/microduck-0.18.3.toml'
        checks.append(dict(item='校正文件/用户指针',state='已保存（仍需运行回执）' if config_valid else '未配置/需检查',value=str(self.p.cal)))
        backup=s.get('backup') if s else None
        image_ready=bool(backup and Path(backup['image']).is_file() and Path(backup['image']).stat().st_size==backup['image_bytes'])
        checks.append(dict(item='原系统镜像',state='可访问（上次完整SHA已核验）' if image_ready else '尚未备份/介质离线',value=backup['image'] if backup else None))
        checks.append(dict(item='运行时校正',state='已加载并一致' if verified else '待验证/未加载',value=web.get('runtime_reason')))
        plugins=(web.get('capabilities') or {}).get('plugins',[]);enabled=next((p.get('enabled') for p in plugins if p.get('id')=='servo_health'),None)
        settings=self.p.at('/etc/microduck-tools/plugins.json');wanted=None
        try:wanted=read_json(settings).get('servo_health',{}).get('enabled',False) if settings.exists() else None
        except (OSError,ValueError):pass
        checks.append(dict(item='健康插件',state='保存/运行一致' if enabled is not None and enabled==wanted else '待加载/待重启',value=dict(saved=wanted,running=enabled)))
        if s and s.get('operation'):
            running=False
            if s['operation'].get('status')=='running':
                try:
                    fd=os.open(self.p.state/'manager.lock',os.O_RDONLY|os.O_NOFOLLOW)
                    try:
                        try:fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
                        except BlockingIOError:running=True
                    finally:os.close(fd)
                except OSError:pass
            stage=('正在执行：' if running else '中断/未完成：')+s['operation']['name'];nxt='等待当前操作；./status.sh --watch' if running else './recover.sh'
        elif s and s.get('stage')=='session_restored':stage='本次维护已撤销，仍为旧版';nxt='./check.sh'
        elif s and s.get('backup') and not image_ready:stage='备份介质离线';nxt='连接原介质，或 ./backup.sh --image 新路径 --apply'
        elif s and s.get('selected_mode')=='official' and s.get('stage') in ('official_mode','stopped'):stage='官方模式（未加载校正）';nxt='./services.sh start --apply；需要校正时 ./configure.sh --apply'
        elif s and s.get('configured') and s.get('selected_mode')=='native' and not config_valid:stage='配置需检查';nxt='检查用户层和校正文件；脚本不会重置外部改动'
        elif verified:stage='校正已加载；插件和硬件分别查看';nxt='使用Web；硬件/运动验收继续等待授权'
        elif (not s or not s.get('backup')) and actual['release']==TARGET:stage='检测到外部已安装版本，尚未接管';nxt='只读查看；核对原备份后再适配，不重复安装'
        elif s and not s.get('backup') and 'active_before' in s:stage='备份未完成，原服务可能已停止';nxt='./backup.sh --apply；取消则 ./rollback.sh --to session --apply'
        elif not s or not s.get('backup'):stage='待备份';nxt='./backup.sh --image /外部介质/原系统.img --apply'
        elif actual['release']=='0.16.3':stage='已备份，待安装';nxt='./install.sh --apply'
        elif not s.get('configured') or s.get('stage')=='official_mode':stage='官方已安装，待配置校正';nxt='./configure.sh --apply'
        elif actual['robotd'].get('active')!='active':stage='已配置，待启动';nxt='./services.sh start --apply'
        else:stage='进程已启动，等待真实回执';nxt='./status.sh；检查实物连接，不能用demo代替'
        return dict(mode='板端状态',stage=stage,checks=checks,next=nxt,last_error=s.get('last_error') if s else None,operation=s.get('operation') if s else None,hardware='待现场验收',calibration_verified=verified,actual=actual)


def validate_camera(raw):
    data=json.loads(raw)
    if not isinstance(data,dict) or data.get('schema')!='microduck-camera-rotation/v1' or set(data)-{'schema','rotation'}:raise Error('维护相机配置格式错误')
    rotation=data.get('rotation')
    if rotation is not None and (type(rotation) is not int or rotation not in (0,90,180,270)):raise Error('相机仅支持默认/0/90/180/270')
    return data


def display(report):
    if 'plan' in report:
        print(('\n完成：' if report.get('applied') else '\n预览：')+report['plan']);print(report['next']);return
    print('\nMicroduck 管理状态｜目标 0.18.3')
    print('当前：'+report['stage'])
    for row in report.get('checks',[]):
        content=row.get('value')
        if isinstance(content,dict):content='已保存='+str(content.get('saved'))+'；运行中='+str(content.get('running'))
        value=' — '+str(content) if content is not None else ''
        print('  ['+row['state']+'] '+row['item']+value)
    if report.get('operation'):print('正在/上次步骤：'+report['operation'].get('step','未知'))
    if report.get('last_error') or report.get('error'):print('原因：'+str(report.get('last_error') or report.get('error')))
    print('下一步：'+report['next'])
    print('硬件状态：'+report.get('hardware','未知'))


def disk_identity(disk):
    reply=subprocess.run(['lsblk','--json','--paths','--nodeps','--output','PATH,MODEL,SERIAL,WWN',str(disk)],check=True,capture_output=True,text=True,timeout=10)
    rows=json.loads(reply.stdout)['blockdevices']
    if len(rows)!=1:raise Error('无法读取唯一原盘身份')
    result=rows[0];cid=Path('/sys/class/block')/Path(disk).name/'device/cid'
    result['cid']=cid.read_text().strip() if cid.exists() else None
    return result


def compare_disk_identity(original,current):
    common=False
    for key in ('cid','wwn','serial'):
        if original.get(key) and current.get(key):
            if original[key]!=current[key]:raise Error('原盘身份不匹配：'+key+'；未写盘/绑定')
            common=True
    return common


def image_operation(mode,disk,image,apply=False):
    if not apply:return dict(plan='镜像'+('备份：读取原盘，写另一介质' if mode=='backup' else '恢复：从独立救援系统覆盖原盘'),applied=False,next='./image.sh '+mode+' --disk /dev/原盘 --image /外部/原系统.img --apply')
    if platform.system()!='Linux' or os.geteuid()!=0:raise Error('镜像操作只在Linux救援环境以root运行')
    if not disk or not image:raise Error('镜像操作必须选择 --disk 与 --image，不能猜测设备')
    os.umask(0o077)
    image=Path(image).resolve();check=review.disk_check(Path(disk),image,mode)
    identity=disk_identity(check['disk'])
    if mode=='restore':
        proof=read_json(str(image)+'.proof.json')
        if proof.get('schema')!='microduck-image/v1' or not proof.get('compared') or proof['bytes']!=check['bytes'] or proof['image_name']!=image.name:raise Error('镜像证明不匹配')
        matched=compare_disk_identity(proof.get('disk_identity',{}),identity)
        print('原盘记录：'+json.dumps(proof.get('disk_identity'),ensure_ascii=False));print('当前盘：'+json.dumps(identity,ensure_ascii=False))
        if not matched:print('没有可自动匹配的身份字段，必须人工确认设备；容量相同不等于同一块盘。')
        print('正在校验完整镜像，请等待…',flush=True)
        if digest(image)!=proof['sha256']:raise Error('镜像SHA不符，未写盘')
    if not sys.stdin.isatty():raise Error('镜像写入/读取需要终端确认原盘身份')
    print(json.dumps(check,ensure_ascii=False,indent=2));print('请核对原盘身份；目标盘必须没有挂载。')
    if input('完整输入原盘路径 '+str(check['disk'])+'：').strip()!=check['disk']:raise Error('已取消，未执行dd')
    source,target=(check['disk'],str(image)) if mode=='backup' else (str(image),check['disk'])
    args=['dd','if='+source,'of='+target,'bs=4M','status=progress','conv=fsync']+(['oflag=excl'] if mode=='backup' else [])
    subprocess.run(args,check=True)
    subprocess.run(['sync'],check=True)
    if image.stat().st_size!=check['bytes']:raise Error('镜像长度不符，不能标记完整')
    subprocess.run(['cmp','-n',str(check['bytes']),str(image),check['disk']],check=True)
    if mode=='backup':write_json(str(image)+'.proof.json',dict(schema='microduck-image/v1',image_name=image.name,bytes=check['bytes'],sha256=digest(image),original_device=check['identity'],disk_identity=identity,compared=True))
    return dict(plan='镜像'+mode+'完成，完整字节比较通过；关机后按需要切换启动介质',applied=True,next='备份后正常启动原系统，再运行 ./backup.sh --apply；恢复后运行 ./status.sh')


def confirmation(action,image=None,to=None,plan=None):
    if not sys.stdin.isatty():raise Error('真实操作需在终端确认；默认无--apply只预览')
    print('\n将执行：'+(plan or PLANS[action]))
    if action=='backup' and image:
        proof=Path(str(image)+'.proof.json')
        if proof.exists():print('镜像来源记录：'+json.dumps(read_json(proof).get('disk_identity') or read_json(proof).get('original_device'),ensure_ascii=False))
        print('请同时确认这份镜像来自当前这块板。')
    print('确认当前机器人稳固支撑、舵机动力物理断开；no-policy不是断动力。')
    if input('输入 EXECUTE 执行，其他输入取消：').strip()!='EXECUTE':raise Error('已取消')


def menu(manager):
    choices={'1':'check','2':'backup','3':'install','4':'configure','5':'start','6':'stop','7':'rollback','8':'recover'}
    while True:
        display(manager.status())
        print('\n1 检查  2 备份  3 安装官方版本  4 配置校正/Tools\n5 启动  6 停止  7 回滚  8 中断恢复  9 镜像向导  0 退出')
        value=input('选择：').strip()
        if value=='0':return
        if value=='9':
            try:
                mode=input('镜像操作 backup（备份）/restore（恢复）：').strip()
                if mode not in ('backup','restore'):raise Error('请选择backup或restore')
                subprocess.run(['lsblk','-p','-o','NAME,SIZE,TYPE,MOUNTPOINTS,MODEL,SERIAL'],check=True)
                result=image_operation(mode,input('原盘完整路径：').strip(),input('外部镜像完整路径：').strip(),True);display(result)
            except (OSError,ValueError,subprocess.SubprocessError) as exc:print(str(exc))
            continue
        action=choices.get(value)
        if not action:continue
        if not manager.sys.is_board():print('当前是本机预览，请将完整包复制到Linux ARM64板端后运行');continue
        if not manager.sys.privileged():print('请用 sudo ./duck.sh 打开管理菜单；状态可单独查看');continue
        image=None;to='official';scale=False;servo=None
        if action=='backup':
            image=input('原系统镜像完整路径（由image.sh backup生成）：').strip()
            try:
                gyro=calibration.effective_gyro(calibration.parse_calibration(manager.original_cal(),'daemon-v0.16.3'))
                if gyro['gyro_profile']!='st_500dps':
                    print('原校正请求的比例（不是实测）：'+json.dumps(gyro,ensure_ascii=False))
                    scale=input('已核实MCU契约请输入 SCALE-CONFIRMED，否则取消：').strip()=='SCALE-CONFIRMED'
            except (OSError,ValueError) as exc:print(str(exc));continue
        if action=='rollback':
            print('official：回同版官方0.18.3；session：安装前撤维护；image：完整回原0.16.3')
            to=input('回滚目标 [official]：').strip() or 'official'
            if to not in ('official','session','image'):print('未知回滚目标');continue
        camera=False
        if action=='configure' and manager.load() and manager.load().get('configured'):
            value=input('1 打开维护Web  2 健康插件开关  回车仅查看：').strip()
            if value=='1':
                action='maintenance';camera=input('允许相机应用（会启动/重启mediad）？输入 CAMERA-APPLY，否则只保存：').strip()=='CAMERA-APPLY'
            elif value!='2':display(manager.status());continue
        if action=='configure':
            value=input('健康插件 [保留/on/off]：').strip();servo=True if value=='on' else False if value=='off' else None
        try:
            if action!='check':confirmation(action,image,to,manager.plan(action,servo,to))
            result=manager.action(action,apply=action!='check',image=image,scale_confirmed=scale,servo_health=servo,to=to,camera_apply=camera);display(result)
        except (OSError,ValueError,subprocess.SubprocessError) as exc:print('\n[未完成] '+str(exc)+'\n先查看状态或运行中断恢复，不盲目重试。')


def main():
    parser=argparse.ArgumentParser(description='按任务管理Microduck安装/配置/状态/回滚；默认预览，不连接远端')
    parser.add_argument('--bundle',type=Path,default=PACKAGE.parent)
    parser.add_argument('action',choices=['menu','check','backup','install','configure','status','services','rollback','recover','image'])
    parser.add_argument('mode',nargs='?',choices=['start','stop','reload','web','backup','restore'])
    parser.add_argument('--apply',action='store_true');parser.add_argument('--json',action='store_true');parser.add_argument('--watch',action='store_true')
    parser.add_argument('--camera-apply',action='store_true');parser.add_argument('--image',type=Path);parser.add_argument('--disk',type=Path);parser.add_argument('--firmware-scale-confirmed',action='store_true');parser.add_argument('--servo-health',choices=['on','off']);parser.add_argument('--to',choices=['official','session','image'],default='official')
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    args=parser.parse_args();manager=Manager(args.bundle,progress=lambda value:print(value,file=sys.stderr if args.json else sys.stdout,flush=True))
    try:
        if args.json and args.apply:raise Error('交互执行不使用--json；执行后用 ./status.sh --json 读取结构化状态')
        if args.action=='menu':
            if not sys.stdin.isatty():raise Error('菜单需交互终端；可改用 ./status.sh --json')
            menu(manager);return
        if args.action=='status':
            while True:
                result=manager.status()
                if args.json:print(json.dumps(result,ensure_ascii=False,indent=2))
                else:display(result)
                if not args.watch:break
                time.sleep(2)
            return
        if args.apply and args.action!='image':
            if not manager.sys.is_board():raise Error('当前不是Linux ARM64板端，未执行真实操作')
            if not manager.sys.privileged():raise Error('请用sudo在板端运行')
        if args.action=='backup' and args.apply and not args.image and sys.stdin.isatty():args.image=Path(input('原系统镜像完整路径：').strip())
        if args.action=='backup' and args.apply and manager.sys.is_board() and sys.stdin.isatty() and not args.firmware_scale_confirmed:
            old=calibration.parse_calibration(manager.original_cal(),'daemon-v0.16.3');gyro=calibration.effective_gyro(old)
            if gyro['gyro_profile']!='st_500dps':
                print('旧陀螺仪比例：'+json.dumps(gyro,ensure_ascii=False))
                args.firmware_scale_confirmed=input('核实MCU输出契约后输入 SCALE-CONFIRMED，否则取消：').strip()=='SCALE-CONFIRMED'
        if args.action=='image':
            if args.mode not in ('backup','restore'):raise Error('用法：./image.sh backup|restore [--apply]；执行时脚本会询问设备和镜像路径')
            if args.apply and (not args.disk or not args.image) and sys.stdin.isatty():
                if platform.system()!='Linux' or os.geteuid()!=0:raise Error('镜像向导只在Linux救援环境以root运行')
                subprocess.run(['lsblk','-p','-o','NAME,SIZE,TYPE,MOUNTPOINTS,MODEL,SERIAL'],check=True)
                if not args.disk:args.disk=Path(input('原盘完整路径：').strip())
                if not args.image:args.image=Path(input('外部镜像完整路径：').strip())
            result=image_operation(args.mode,args.disk,args.image,args.apply)
        else:
            action='maintenance' if args.action=='configure' and args.mode=='web' else args.mode if args.action=='services' else args.action
            if action not in PLANS:raise Error('用法：./services.sh start|stop|reload [--apply]')
            if args.apply:
                if not manager.sys.is_board():raise Error('当前不是Linux ARM64板端，未执行真实操作')
                if os.geteuid()!=0:raise Error('请在板端使用sudo运行')
                if action=='maintenance' and args.camera_apply:print('本次还允许启动mediad；Web中明确应用时会重启mediad，视频会中断。')
                confirmation(action,args.image,args.to,manager.plan(action,None if args.servo_health is None else args.servo_health=='on',args.to))
            result=manager.action(action,args.apply,args.image,args.firmware_scale_confirmed,None if args.servo_health is None else args.servo_health=='on',args.to,args.camera_apply)
        if args.json:print(json.dumps(result,ensure_ascii=False,indent=2))
        else:display(result)
    except KeyboardInterrupt:print('\n已中断；先 ./status.sh，再 ./recover.sh。');sys.exit(130)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        if args.json:print(json.dumps(dict(error=str(exc),completed=False,next='./status.sh / ./recover.sh'),ensure_ascii=False))
        else:print('\n[未完成] '+str(exc)+'\n下一步：./status.sh；存在中断时 ./recover.sh。',file=sys.stderr)
        sys.exit(2)


if __name__=='__main__':main()
