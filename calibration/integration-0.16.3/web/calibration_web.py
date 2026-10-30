#!/usr/bin/env python3
"""Local-only Microduck calibration dashboard, stdlib Python 3.11+.

No direct bus writes or torque commands. Camera apply may restart mediad only when enabled.
require CSRF, explicit confirmation, supported daemon and deliberate action.
Run over SSH port forwarding, NEVER bind directly to a public network interface.
"""
from __future__ import annotations
import argparse
import json
import os
import secrets
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'common'))
import duck_calibrate as cal
sys.path.insert(0,str(Path(__file__).resolve().parent))
import camera_config
import servo_health
import daemon_adapter
import config_store
import native_camera

PREVIEW_TEMPLATE=Path(__file__).with_name('index.html')
MAX_POST=48_000
NATIVE_0179_BASE='96b89d8613f06722c1d64ce75ab0d7124fed9d8f'
NATIVE_0179_BUILD='96b89d86-calv3-f76915b8568d'
NATIVE_0183_BASE='b2b0d213a402caeac492dd662ea891faa17f67c0'
NATIVE_0183_BUILD='b2b0d213-calv3-30452a086d1e'
NATIVE_BUILDS={'daemon-v0.17.9':(NATIVE_0179_BASE,NATIVE_0179_BUILD),'daemon-v0.18.3':(NATIVE_0183_BASE,NATIVE_0183_BUILD)}


def verify_native_receipt(data, config, target):
    """An official version string alone never enables native calibration writes."""
    try:
        if data.get('native_calibration_contract')!='microduck-calibration/v2':
            raise ValueError('native calibration contract missing; official daemon remains read-only')
        base,build=NATIVE_BUILDS[target]
        if data.get('base_revision')!=base or data.get('daemon_version')!=target.removeprefix('daemon-v'):
            raise ValueError('native calibration base/version differs')
        revision=data.get('build_revision')
        if revision!=build:
            raise ValueError('unverified calibration build revision')
        if Path(data['config_path']).resolve()!=Path(config).resolve():raise ValueError('daemon uses another configuration path')
        if data.get('executable')!=os.readlink(f"/proc/{data['pid']}/exe"):
            raise ValueError('calibration executable identity differs')
        values={key:data[key] for key in ('joint_zero_rad','joint_direction','imu_mount_wxyz','gyro_raw_counts_per_dps','imu_module')}
        values.update(schema=2,target_tag=target,gyro_profile='manual')
        cal.check_calibration(values,target)
        if target=='daemon-v0.18.3':
            expected=cal.config_layers.user_layer_for(config,target)
            if data['config_layering']!='user-over-base/v1' or data['user_config_path']!=(str(expected) if expected else None):
                raise ValueError('native user/base configuration layout differs')
            if data['user_layer_rejected'] is not None:
                raise ValueError('daemon rejected user layer: '+str(data['user_layer_rejected']))
    except (OSError,ValueError,KeyError,TypeError,cal.CalibrationError) as error:return False,str(error)
    return True,'verified '+target+' native calibration contract and live decoder values'


def verify_0179_receipt(data,config):return verify_native_receipt(data,config,'daemon-v0.17.9')


def make_default(target):
    return {'schema':2,'target_tag':target,'joint_zero_rad':[0.0]*15,
        'joint_direction':[1]*15,
        'imu_mount_wxyz':list(cal.MOUNT_DEFAULT['zero3']),
        'imu_module':'bus_imu200','gyro_profile':'st_500dps',
        'gyro_raw_counts_per_dps':cal.GYRO_PROFILES['st_500dps']}


def state_delta(a,b):
    if a is None or b is None:
        return ['missing_saved_or_active']
    changes=[]
    for key in ('gyro_raw_counts_per_dps','imu_mount_wxyz','joint_zero_rad','joint_direction'):
        x,y=a.get(key),b.get(key)
        if isinstance(x,(tuple,list)) and isinstance(y,(tuple,list)):
            mismatch=(len(x)!=len(y) or any(abs(float(q)-float(z))>1e-8 for q,z in zip(x,y)))
        elif isinstance(x,(float,int)) and isinstance(y,(float,int)):
            mismatch=abs(x-y)>1e-7
        else:mismatch=x!=y
        if mismatch:changes.append(key)
    return changes


def read_robotd_pid():
    try:
        p=subprocess.run(['systemctl','show','robotd','-p','MainPID','--value'],
                    capture_output=True,text=True,timeout=1.5)
        if p.returncode==0:
            n=int(p.stdout.strip())
            return n if n>0 else None
    except (OSError,subprocess.SubprocessError,ValueError):pass
    return None


def valid_active_file(data, *, demo=False):
    if not isinstance(data,dict) or data.get('source')!='robotd:DynamixelIo:live' or data.get('bus_verified') is not True:
        return False,'invalid snapshot provenance'
    if demo:return False,'DEMO: offline example, never verified as live'
    try:
        pid=int(data['pid'])
        if pid<=1:return False,'invalid process ID'
        cmdline=Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\x00',b' ')
        if b'robotd' not in cmdline:return False,'reported daemon is no longer running'
        main=read_robotd_pid()
        if main is not None and main!=pid:return False,'runtime PID does not match active systemd robotd'
    except (ValueError,OSError,KeyError):return False,'runtime daemon process not verified'
    return True,'live daemon runtime snapshot verified'


class Monitor:
    """Read-only stream: actual gyro values already converted to rad/s by robotd."""
    def __init__(self,robotctl='robotctl',enable=True,observer=None,disconnected=None):
        self.last={}
        self.lock=threading.Lock()
        self.robotctl=robotctl
        self.enable=enable
        self.stop_event=threading.Event()
        self.proc=None
        self.observer=observer
        self.disconnected=disconnected
        self.thread=None
        if enable:
            self.thread=threading.Thread(target=self._loop,name='cal-web-telemetry',daemon=True)
            self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            proc=None
            try:
                proc=subprocess.Popen([self.robotctl,'monitor','--hz','5','--json'],
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
                self.proc=proc
                for raw in proc.stdout:
                    try:obj=json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('non-finite telemetry')))
                    except (ValueError,json.JSONDecodeError):continue
                    if not isinstance(obj,dict):continue
                    if obj.get('method')!='robot.state':continue
                    s=obj.get('params') or {}
                    if not isinstance(s,dict):continue
                    imu=s.get('imu') or {}
                    safety=s.get('safety') if isinstance(s.get('safety'),dict) else {}
                    loop=s.get('loop') if isinstance(s.get('loop'),dict) else {}
                    gyro=imu.get('gyro') if isinstance(imu,dict) else None
                    if gyro is not None and (not isinstance(gyro,list) or len(gyro)!=3):gyro=None
                    sample={'time':time.time(),'policy':s.get('policy'),
                         'gravity':safety.get('gravity'),
                         'joints':s.get('joints'), 'gyro_rad_s':gyro,
                         'loop_hz':loop.get('hz'),
                         'bus_tick':s.get('t')}
                    with self.lock:self.last=sample
                    if self.observer is not None:
                        try:self.observer(s)
                        except Exception:pass  # Optional observer must never interrupt calibration telemetry.
            except (OSError,ValueError):pass
            finally:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:proc.kill()
                if self.disconnected:
                    try:self.disconnected()
                    except Exception:pass
            self.proc=None
            self.stop_event.wait(2)

    def close(self):
        self.stop_event.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        if self.thread:self.thread.join(timeout=3)

    def sample(self):
        with self.lock:out=dict(self.last)
        if out and time.time()-out.get('time',0)>4:
            out['stale']=True
        return out


class Context:
    def __init__(self,args):
        self.target=args.target
        self.config=Path(args.config)
        self._requested_dest=args.dest
        self.runtime=Path(args.runtime)
        self.demo=args.demo
        self.allow_inactive=args.allow_inactive
        self.csrf=secrets.token_urlsafe(32)
        plugin_error=None
        try:plugin_config=daemon_adapter.load_plugin_config(getattr(args,'plugin_config',None))
        except (OSError,ValueError) as e:
            plugin_config={};plugin_error='插件配置无效，已停用可选监测；原校准服务继续可用：'+str(e)[:200]
        self.adapter=daemon_adapter.Client(getattr(args,'robot_socket','/run/robotd.sock'),self.target,
            device_id=plugin_config.get('device_id'),calibration_hash=plugin_config.get('calibration_hash') or self.calibration_identity)
        observer_dir=getattr(args,'servo_health_dir','/var/lib/robot/diagnostics/servo-health')
        try:
            self.servo=servo_health.Plugin(observer_dir,enabled=not plugin_error and (getattr(args,'enable_servo_health',False) or plugin_config.get('enabled',False)),
                live=not args.no_telemetry and not args.demo,
                robot_socket=getattr(args,'robot_socket','/run/robotd.sock'),adapter=self.adapter)
            if plugin_error:self.servo.startup_error=plugin_error
        except Exception as e:
            self.servo=servo_health.Plugin(observer_dir)
            self.servo.startup_error='舵机插件启动失败，校准服务继续可用：'+str(e)[:200]
        self.monitor=Monitor(args.robotctl,enable=not args.no_telemetry and not args.demo,
                             observer=self.servo.submit if self.servo.enabled else None,
                             disconnected=self.servo.disconnect if self.servo.enabled else None)
        self.lock=threading.RLock()
        self.camera=camera_config.Camera(self.config,
            getattr(args,'camera_runtime','/run/mediad/camera-effective.json'),
            self.target,self.demo,getattr(args,'enable_camera_apply',False),
            getattr(args,'camera_web_port',8080))
        if self.target in ('auto',*NATIVE_BUILDS):
            self.camera=native_camera.Camera(self.config,getattr(args,'camera_settings','/var/lib/microduck-tools/camera.json'),
                self.target,self.demo,getattr(args,'enable_camera_apply',False),getattr(args,'camera_web_port',8080))
    def calibration_identity(self):
        try:
            target=self.calibration_target();base,user=cal.config_layers.texts(self.config,target)
            raw=base+b'\0USER\0'+user if cal.config_layers.user_layer_for(self.config,target) else base
            params=self.effective_params()
            pointer=params.get('bus',{}).get('calibration_path')
            calibration=Path(pointer).read_bytes() if pointer else b'official-default-calibration'
            return daemon_adapter.fingerprint(dict(config_sha=config_store.revision(raw),calibration_sha=config_store.revision(calibration)))
        except (OSError,ValueError,TypeError):return None
    @property
    def dest(self):return Path(self._requested_dest or cal.config_layers.default_calibration(self.calibration_target()))
    def effective_params(self):return cal.config_layers.load(self.config,self.calibration_target())
    def write_config(self):return cal.config_layers.write_path(self.config,self.calibration_target())
    def calibration_target(self, runtime=None, verified=False):
        if self.target in cal.TARGETS:return self.target
        if self.target=='auto':
            if runtime is None:runtime,verified,_=self.runtime_read()
            if verified:return runtime['target_tag']
            candidate='daemon-v'+str(self.adapter.status().get('daemon_version',''))
            if candidate in cal.TARGETS:return candidate
        return None
    def write_allowed(self, verified=None, target=None):
        if verified is None:
            runtime,verified,_=self.runtime_read();target=self.calibration_target(runtime,verified)
        target=target or self.target
        try:self.effective_params()
        except (OSError,ValueError,TypeError):return False
        legacy_dev=self.allow_inactive and self.target in ('daemon-v0.16.3','daemon-v0.17.8')
        path=cal.config_layers.write_path(self.config,target)
        permission=(not path.exists() or os.access(path,os.W_OK)) and os.access(path.parent,os.W_OK) and os.access(self.dest.parent,os.W_OK)
        return bool(target in cal.TARGETS and (verified or legacy_dev) and permission and (not self.demo or legacy_dev))
    def capabilities(self):
        runtime,verified,reason=self.runtime_read();target=self.calibration_target(runtime,verified)
        return dict(daemon=self.adapter.status(),plugins=[dict(id='servo_health',schema=1,enabled=self.servo.enabled)],
            calibration=dict(supported_target=target in cal.TARGETS,resolved_target=target,
                native_verified=verified,writable=self.write_allowed(verified,target),
                reason=reason if not verified else '原生校正已核验；保存仍需文件权限、工况与比例确认'))
    def configured(self):
        try:
            data=self.effective_params()
            ptr=(data.get('bus') or {}).get('calibration_path')
            if ptr is not None and not isinstance(ptr,str):raise ValueError('calibration_path must be a string')
            return ptr,None
        except (OSError,tomllib.TOMLDecodeError,ValueError) as e:return None,str(e)
    def saved(self):
        ptr,err=self.configured()
        path=Path(ptr) if ptr else self.dest
        if path.exists():
            try:return cal.parse_calibration(path,self.calibration_target()),str(path),err,bool(ptr)
            except (OSError,cal.CalibrationError) as e:return None,str(path),str(e),bool(ptr)
        return None,str(path),err,bool(ptr)
    def runtime_read(self):
        try:
            obj=json.loads(self.runtime.read_text('utf-8'))
            if not isinstance(obj,dict):return None,False,'runtime snapshot must be a JSON object'
            ok,reason=valid_active_file(obj,demo=self.demo)
            if obj.get('target_tag') not in cal.TARGETS or self.target!='auto' and obj.get('target_tag')!=self.target:
                ok=False;reason='active daemon tag differs from dashboard target'
            if ok and obj.get('target_tag') in NATIVE_BUILDS:ok,reason=verify_native_receipt(obj,self.config,obj['target_tag'])
            return obj,ok,reason
        except (OSError,json.JSONDecodeError) as e:
            return None,False,f'no verified runtime snapshot: {e}'
    def status(self):
        saved,sp,serr,installed=self.saved()
        runtime,verified,reason=self.runtime_read()
        normalized=None
        if saved:
            normalized={**saved,**cal.effective_gyro(saved)}
        if runtime and verified:
            diffs=state_delta(normalized,runtime)
            pointer,_=self.configured()
            if (pointer or '')!=(runtime.get('calibration_path') or ''):diffs.append('calibration_path')
        else:diffs=[]
        telem=self.monitor.sample()
        if self.demo and not telem:
            telem={'stale':True,'policy':'SIMULATION','gravity':[0,0,-1],
                   'gyro_rad_s':[0,0,0],'joints':[0]*15,'loop_hz':50}
        return {'target':self.target,'calibration_target':self.calibration_target(runtime,verified),'configured_file':str(self.config),'write_config_file':str(self.write_config()),
                'install_dest':str(self.dest),'configured':installed,
                'saved_path':sp,'saved_error':serr,'saved':normalized,
                'runtime':runtime,'runtime_verified':verified,
                'runtime_reason':reason,'differences':diffs,
                'pending_restart':bool(diffs) if normalized and verified else None,
                'telemetry':telem,'can_save':self.write_allowed(verified,self.calibration_target(runtime,verified)),
                'capabilities':self.capabilities(),
                'allow_inactive':self.allow_inactive,'demo':self.demo,
                'profiles':cal.GYRO_PROFILES,
                'joint_names':cal.JOINT_NAMES,'joint_ids':cal.JOINT_IDS,
                'camera':self.camera.status()}
    def validate_input(self,body):
        target=self.calibration_target()
        if target not in cal.TARGETS:
            raise cal.CalibrationError('native calibration unsupported for this target; read-only adapter cannot apply calibration')
        if not isinstance(body,dict) or not isinstance(body.get('calibration'),dict):
            raise cal.CalibrationError('missing calibration object')
        values=body['calibration']
        if values.get('target_tag')!=target:raise cal.CalibrationError('target_tag does not match selected version')
        cal.check_calibration(values,target)
        if values['schema'] !=2:
            raise cal.CalibrationError('web editor writes schema 2 only')
        return values
    def save(self,body):
        values=self.validate_input(body)
        if not self.write_allowed():
            raise cal.CalibrationError('no verified matching patched robotd or write permissions; native targets cannot bypass their native contract')
        g=cal.effective_gyro(values)
        if g['gyro_profile']!='st_500dps' and body.get('confirm_hardware') is not True:
            raise cal.CalibrationError('confirm firmware gyro raw value scale before saving custom profile')
        if body.get('confirm_safe') is not True:
            raise cal.CalibrationError('confirm robot is supported and will not be restarted automatically')
        # Use same atomic install + backup path as CLI, no subprocess to mutate config.
        with self.lock, config_store.locked(self.write_config()), tempfile.TemporaryDirectory(prefix='duck-cal-web-') as td:
            if not self.write_allowed() or values['target_tag']!=self.calibration_target():
                raise cal.CalibrationError('native identity or write permissions changed; refresh before saving')
            source=Path(td)/'generated.toml'
            source.write_text(cal.render_toml(values),'utf-8')
            a=argparse.Namespace(generated=str(source),target=values['target_tag'],
                 config=str(self.config),dest=str(self.dest),apply=True,
                 confirm_firmware_scale=body.get('confirm_hardware') is True)
            cal.cmd_install(a)
        return {'ok':True,'message':'Written and backed up; restart robotd safely outside this dashboard to apply.'}


def new_handler(ctx:Context):
    class Handler(BaseHTTPRequestHandler):
        server_version='DuckCalibration/3.0'
        def log_message(self,fmt,*args):
            sys.stderr.write('CAL-WEB '+fmt%args+'\n')
        def _host_ok(self):
            host=self.headers.get('Host','').split(':')[0].strip('[]').lower()
            return host in ('localhost','127.0.0.1','::1')
        def _reply(self,status,obj):
            data=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if not self._host_ok():return self._reply(403,{'error':'Host not allowed; use localhost / SSH tunnel'})
            path=urlsplit(self.path).path
            if path=='/servo-health.js':
                data=PREVIEW_TEMPLATE.with_name('servo-health.js').read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/javascript; charset=utf-8')
                self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data);return
            if path.startswith('/api/servo-health/'):
                route=path.rsplit('/',1)[-1]
                try:
                    query=parse_qs(urlsplit(self.path).query)
                    if route=='status':return self._reply(200,ctx.servo.status(compact=query.get('compact')==['1']))
                    if not ctx.servo.enabled:return self._reply(409,{'error':'servo health plugin disabled'})
                    query=parse_qs(urlsplit(self.path).query)
                    if route=='history':return self._reply(200,ctx.servo.engine.series(int(query.get('id',['20'])[0]),query.get('after_t_ns',[None])[0],query.get('epoch',[None])[0]))
                    if route=='maintenance':return self._reply(200,{'report':ctx.servo.engine.status()['maintenance']})
                    if route=='export':
                        fmt=query.get('format',['json'])[0];raw,mime=ctx.servo.engine.export(fmt)
                        self.send_response(200);self.send_header('Content-Type',mime)
                        self.send_header('Content-Length',str(len(raw)))
                        self.send_header('Content-Disposition',f'attachment; filename="servo-health.{fmt}"')
                        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                        self.end_headers();self.wfile.write(raw);return
                except (ValueError,TypeError) as e:return self._reply(400,{'error':str(e)})
                except Exception as e:return self._reply(503,{'error':'observer unavailable: '+str(e)})
                return self._reply(404,{'error':'unknown observer route'})
            if path=='/api/status':return self._reply(200,ctx.status())
            if path=='/api/capabilities':return self._reply(200,ctx.capabilities())
            if path=='/api/camera/frame':
                try:
                    raw=ctx.camera.frame()
                    self.send_response(200)
                    self.send_header('Content-Type','image/png')
                    self.send_header('Content-Length',str(len(raw)))
                    self.send_header('Cache-Control','no-store')
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.end_headers();self.wfile.write(raw);return
                except (OSError,cal.CalibrationError,subprocess.SubprocessError) as e:
                    return self._reply(503,{'error':str(e)})
            if path=='/api/export':
                saved,_,err,_=ctx.saved()
                if not saved:return self._reply(404,{'error':err or 'no saved calibration'})
                raw=cal.render_toml(saved).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','application/toml; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="robot-calibration.toml"')
                self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(raw);return
            if path in ('/','/index.html'):
                raw=PREVIEW_TEMPLATE.read_text(encoding='utf-8').replace('__CALIBRATION_CSRF__',ctx.csrf)
                data=raw.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Frame-Options','DENY')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Referrer-Policy','no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers();self.wfile.write(data);return
            return self._reply(404,{'error':'not found'})
        def do_POST(self):
            if not self._host_ok():return self._reply(403,{'error':'invalid Host'})
            origin=self.headers.get('Origin')
            if origin:
                parsed=urlsplit(origin)
                if parsed.scheme!='http' or parsed.hostname not in ('localhost','127.0.0.1','::1'):
                    return self._reply(403,{'error':'Origin not allowed'})
            if self.headers.get('X-Calibration-CSRF')!=ctx.csrf:
                return self._reply(403,{'error':'invalid CSRF token'})
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
                return self._reply(415,{'error':'application/json required'})
            try:
                n=int(self.headers.get('Content-Length','0'))
                path=urlsplit(self.path).path
                limit=1_048_576 if path=='/api/servo-health/maintenance' else MAX_POST
                if not 0<n<=limit:return self._reply(413,{'error':'invalid request length'})
                payload=json.loads(self.rfile.read(n),parse_constant=lambda x:(_ for _ in ()).throw(ValueError('non-finite JSON')))
                if not isinstance(payload,dict):
                    raise cal.CalibrationError('request body must be an object')
                if path.startswith('/api/servo-health/'):
                    if not ctx.servo.enabled:return self._reply(409,{'error':'servo health plugin disabled'})
                    action={'config':ctx.servo.engine.configure,'baseline':ctx.servo.engine.baseline,
                            'maintenance':ctx.servo.engine.import_maintenance}.get(path.rsplit('/',1)[-1])
                    if action is None:return self._reply(404,{'error':'unknown observer route'})
                    try:return self._reply(200,action(payload))
                    except (ValueError,TypeError) as e:return self._reply(400,{'error':str(e)})
                    except Exception as e:return self._reply(503,{'error':'observer unavailable: '+str(e)})
                if path=='/api/camera/preview':
                    result=ctx.camera.preview(payload)
                    return self._reply(200,{k:v for k,v in result.items() if k!='config'})
                if path=='/api/camera/save':
                    with ctx.lock:
                        return self._reply(200,ctx.camera.save(payload))
                if path=='/api/camera/apply':
                    with ctx.lock:
                        return self._reply(200,ctx.camera.apply(payload))
                if path=='/api/preview':
                    cfg=ctx.validate_input(payload)
                    return self._reply(200,{'ok':True,'toml':cal.render_toml(cfg),'conversion':cal.effective_gyro(cfg),
                                            'differences':state_delta({**cfg,**cal.effective_gyro(cfg)},
                                                                     ctx.runtime_read()[0])})
                if path=='/api/save':return self._reply(200,ctx.save(payload))
                return self._reply(404,{'error':'not found'})
            except (cal.CalibrationError,ValueError,TypeError,json.JSONDecodeError) as e:
                return self._reply(400,{'error':str(e)})
            except (OSError,subprocess.SubprocessError) as e:
                return self._reply(500,{'error':str(e)})
    return Handler


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target',required=True,choices=(*cal.TARGETS,'auto'))
    p.add_argument('--plugin-config',help='independent JSON plugin-host schema 1; does not alter robotd.toml')
    p.add_argument('--listen',default='127.0.0.1')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--config',default='/etc/robot/robotd.toml')
    p.add_argument('--dest',help='calibration destination; auto follows verified target defaults')
    p.add_argument('--runtime',default='/run/robotd/calibration-effective.json')
    p.add_argument('--camera-runtime',default='/run/mediad/camera-effective.json')
    p.add_argument('--camera-settings',default='/var/lib/microduck-tools/camera.json',help='official CLI camera adapter settings, independent of robotd.toml')
    p.add_argument('--camera-web-port',type=int,default=8080)
    p.add_argument('--enable-camera-apply',action='store_true',
                   help='allow explicit camera-only mediad restart; briefly interrupts media sessions')
    p.add_argument('--robotctl',default='robotctl')
    p.add_argument('--enable-servo-health',action='store_true',help='read-only observer; no serial access or control changes')
    p.add_argument('--servo-health-dir',default='/var/lib/robot/diagnostics/servo-health')
    p.add_argument('--robot-socket',default='/run/robotd.sock',help='existing read-only health RPC socket')
    p.add_argument('--no-telemetry',action='store_true')
    p.add_argument('--allow-inactive',action='store_true',help='explicit dev-only file editing without verified live daemon')
    p.add_argument('--demo',action='store_true',help='visually show simulated telemetry, never claim real runtime active')
    return p


def main():
    args=parser().parse_args()
    if not 1<=args.camera_web_port<=65535:
        raise SystemExit('camera web port must be 1..65535')
    if args.listen not in ('127.0.0.1','::1','localhost'):
        raise SystemExit('Refusing to bind public interface. Use --listen 127.0.0.1 and SSH -L.')
    def stop_signal(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_signal)
    if args.demo and not args.allow_inactive:
        print('DEMO displays offline visuals and will not write until --allow-inactive.',file=sys.stderr)
    ctx=Context(args)
    server=ThreadingHTTPServer((args.listen,args.port),new_handler(ctx))
    print(f'Microduck Calibration Web: http://127.0.0.1:{args.port}/',flush=True)
    print('No robotd restart or motor write. Camera apply restarts mediad only when explicitly enabled.',flush=True)
    try:server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:pass
    finally:
        ctx.monitor.close()
        ctx.servo.close()
        server.server_close()


if __name__=='__main__':main()
