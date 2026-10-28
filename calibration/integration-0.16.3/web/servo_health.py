"""Optional read-only observer. No serial access, control RPC or robot configuration writes."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import csv
import io
import json
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import uuid
import daemon_adapter

IDS = [20,21,22,23,24,30,31,32,33,34,10,11,12,13,14]
NAMES = ['left_hip_yaw','left_hip_roll','left_hip_pitch','left_knee','left_ankle',
         'neck_pitch','head_pitch','head_yaw','head_roll','mouth',
         'right_hip_yaw','right_hip_roll','right_hip_pitch','right_knee','right_ankle']
DEFAULTS = dict(current_multiplier=1.5, current_margin_ma=100., duration_s=2.,
                error_deg=10., speed_deg_s=3., target_span_deg=2.,
                temp_warn_c=55., temp_alarm_c=65., voltage_min_v=3.7, voltage_max_v=6.)
LIMITS = dict(current_multiplier=(1,10), current_margin_ma=(1,5000), duration_s=(.5,60),
              error_deg=(1,180), speed_deg_s=(.1,180), target_span_deg=(.1,30),
              temp_warn_c=(20,69), temp_alarm_c=(21,70), voltage_min_v=(1,20), voltage_max_v=(1,30))
REGISTER_SIZES = {0:2,6:1,11:1,31:1,32:2,34:2,36:2,38:2,63:1,64:1,70:1,
                  124:2,126:2,128:4,132:4,144:2,146:1}


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def vector(value, nonnegative=False):
    if not isinstance(value,list) or len(value)!=15:return None
    if not all(finite(v) and (not nonnegative or v>=0) for v in value):return None
    return list(value)


def utc():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    name=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as f:
            if os.geteuid()==0:
                owner=path.stat() if path.exists() else path.parent.stat()
                os.fchown(f.fileno(),owner.st_uid,owner.st_gid)
            name=f.name;json.dump(value,f,ensure_ascii=False,allow_nan=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        Path(name).replace(path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if name:Path(name).unlink(missing_ok=True)


def rules(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('unknown monitoring rule')
    result={**DEFAULTS,**values}
    for k,v in result.items():
        lo,hi=LIMITS[k]
        if not finite(v) or not lo<=v<=hi:raise ValueError(f'invalid {k}: {lo}..{hi}')
    if result['temp_warn_c']>=result['temp_alarm_c']:raise ValueError('temperature warning must be below alarm')
    if result['voltage_min_v']>=result['voltage_max_v']:raise ValueError('voltage range is reversed')
    return result


def decode_registers(raw):
    """Only XL330 profiles are supported; raw register words remain available for others."""
    model=raw.get('0')
    if model not in (1190,1200):return {'supported':False,'model':model}
    def val(addr,scale=1):return raw.get(str(addr))*scale if str(addr) in raw else None
    def signed(addr,bits):
        n=raw.get(str(addr))
        return None if n is None else n-(1<<bits) if n&(1<<(bits-1)) else n
    fault=raw.get('70')
    errors=None if fault is None else [name for bit,name in [(1,'input_voltage'),(4,'overheating'),
                (16,'electrical_shock'),(32,'overload')] if fault&bit]
    return dict(supported=True,model=model,firmware=val(6),operating_mode=val(11),
                temp_limit_c=val(31),max_voltage_v=val(32,.1),min_voltage_v=val(34,.1),
                pwm_limit=val(36),current_limit_ma=val(38),shutdown=val(63),torque_enable=val(64),
                hardware_error=fault,errors=errors,unknown_error_bits=None if fault is None else fault&~0x35,
                input_voltage_shutdown_enabled=None if '63' not in raw else bool(raw['63']&1),
                current_ma=signed(126,16),pwm_raw=signed(124,16),velocity_raw=signed(128,32),
                position_raw=signed(132,32),voltage_v=val(144,.1),temp_c=val(146))


def validate_report(report):
    if not isinstance(report,dict) or report.get('schema')!='microduck-servo-maintenance/v1':
        raise ValueError('unsupported maintenance schema')
    label=report.get('robot_label');stamp=report.get('collected_at');servos=report.get('servos')
    if not isinstance(label,str) or not 1<=len(label)<=120:raise ValueError('robot_label required')
    try:
        if not isinstance(stamp,str) or len(stamp)>80:raise ValueError()
        dt=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError()
    except (TypeError,ValueError):raise ValueError('timezone-aware collected_at required')
    if not isinstance(servos,list) or len(servos)!=15:raise ValueError('report must contain the 15 expected servo IDs')
    output=[];seen=set()
    for row in servos:
        if not isinstance(row,dict):raise ValueError('invalid servo record')
        ident=row.get('id')
        if type(ident)!=int or ident not in IDS or ident in seen:raise ValueError('unknown or duplicate servo ID')
        seen.add(ident);raw=row.get('raw');errors=row.get('read_errors',[])
        if not isinstance(raw,dict) or not isinstance(errors,list) or len(errors)>30:raise ValueError('invalid raw/error records')
        for addr,value in raw.items():
            if addr not in {str(k) for k in REGISTER_SIZES}:raise ValueError('unknown register address')
            if type(value)!=int or not 0<=value<1<<(REGISTER_SIZES[int(addr)]*8):raise ValueError('register word out of range')
        if not all(isinstance(e,str) and len(e)<=500 for e in errors):raise ValueError('invalid read error')
        sampled=row.get('sampled_at',stamp)
        if not isinstance(sampled,str) or len(sampled)>80:raise ValueError('invalid sample time')
        output.append(dict(id=ident,name=NAMES[IDS.index(ident)],sampled_at=sampled,
                           raw=raw,read_errors=errors,decoded=decode_registers(raw)))
    return dict(schema=report['schema'],robot_label=label,collected_at=stamp,servos=output,
                imported_at=utc(),identity='report_claim_only_not_verified',source='historical_maintenance',
                live=False)


class Engine:
    """Deterministic reducer; feed recorded frames with the same monotonic clock as t_ns."""
    def __init__(self, directory, clock=time.monotonic, emit=None, require_identity=False):
        self.directory=Path(directory);self.clock=clock;self.emit=emit or (lambda e:None)
        self.lock=threading.RLock();self.config=rules({});self.config_version=1
        self.baselines={};self.capture=None;self.maintenance=None;self.errors=deque(maxlen=12)
        self.history=deque(maxlen=1600);self.events=deque(maxlen=100);self.pending=deque(maxlen=100)
        self.conditions={};self.active={};self.latest=None;self.last_ns=None;self.policy=None
        self.health=None;self.health_received=None;self.frames=0;self.repeats=0;self.capture_result=None
        self.identity=None;self.identity_verified=False;self.require_identity=require_identity
        self.stream_epoch=uuid.uuid4().hex;self.maintenance_version=None
        for file,attr in [('rules.json','config'),('baselines.json','baselines'),('maintenance.json','maintenance')]:
            try:
                value=json.loads((self.directory/file).read_text())
                if attr=='config':self.config=rules(value['rules']);self.config_version=int(value['version'])
                elif attr=='baselines':
                    if not isinstance(value,dict):raise ValueError('invalid baseline storage')
                    for p,b in value.items():
                        if not isinstance(p,str) or vector(b.get('p95_ma'),True) is None:raise ValueError('invalid stored baseline')
                    self.baselines=value
                else:self.maintenance=validate_report(value);self.maintenance_version=daemon_adapter.fingerprint(self.maintenance)
            except FileNotFoundError:pass
            except (OSError,ValueError,TypeError,KeyError,AttributeError) as e:self.errors.append(f'{file}: {e}')

    def _error(self,e):self.errors.append(str(e)[:300])

    def reset_continuity(self,reason='transport interrupted'):
        with self.lock:
            self.conditions.clear()
            if self.capture:self.capture=None;self.capture_result='cancelled: '+reason
            self._flush_evidence(self.clock(),force=True)
            self.history.clear();self.latest=None;self.last_ns=None
            self.stream_epoch=uuid.uuid4().hex

    def set_identity(self,identity,verified):
        with self.lock:
            if identity!=self.identity or verified!=self.identity_verified:
                self.reset_continuity('device/software/calibration identity changed')
            self.identity=deepcopy(identity);self.identity_verified=bool(verified)

    def _base(self,policy):
        base=self.baselines.get(policy)
        if not self.require_identity:return base
        return base if base and self.identity_verified and base.get('identity')==self.identity else None


    def configure(self,body):
        with self.lock:
            values=body.get('rules')
            if not isinstance(values,dict):raise ValueError('rules object required')
            cfg=rules({**self.config,**values});version=self.config_version+1
            atomic_json(self.directory/'rules.json',dict(version=version,rules=cfg,updated_at=utc()))
            self.config=cfg;self.config_version=version;self.conditions.clear()
            return dict(ok=True,rules=cfg,version=version)

    def baseline(self,body):
        with self.lock:
            if body.get('action')=='cancel':self.capture=None;return {'ok':True,'cancelled':True}
            if body.get('action')!='start' or body.get('confirm_normal') is not True:
                raise ValueError('confirm_normal=true required after manually checking a normal operating condition')
            status=self.status()
            if self.require_identity and not self.identity_verified:raise ValueError('verified device/software/calibration identity required')
            if status['stale'] or not self.latest or self.latest['currents_ma'] is None:
                raise ValueError('fresh measured currents required')
            if self._paused(self.latest):raise ValueError('baseline unavailable while held/fallen/limp/picked_up')
            self.capture=dict(start=self.latest['sensor_s'],policy=self.policy,values=[],source='operator_confirmed_reference')
            self.capture_result=None
            return {'ok':True,'duration_s':60}

    def import_maintenance(self,body):
        report=validate_report(body.get('report'))
        with self.lock:
            atomic_json(self.directory/'maintenance.json',report);self.maintenance=report
            self.maintenance_version=daemon_adapter.fingerprint(report)
        return {'ok':True,'report':report}

    @staticmethod
    def _paused(sample):
        s=sample['safety']
        return sample['policy']=='held' or any(s.get(k) for k in ('fallen','limp','picked_up'))

    def _event(self,key,phase,details,now):
        event=dict(key=key,phase=phase,at=utc(),monotonic_s=now,rule_version=self.config_version,
                   source='robot.state' if details.get('id') is not None else 'robot.health_or_stream',identity=deepcopy(self.identity),**deepcopy(details))
        self.events.append(event)
        before=[deepcopy(s) for s in self.history if now-5<=s['sensor_s']<=now]
        times=[now-5]+[s['sensor_s'] for s in before]+[now]
        complete=bool(before) and all(b-a<=.500001 for a,b in zip(times,times[1:]))
        self.pending.append(dict(event=event,before=before,before_complete=complete,after=[],until=now+5))

    def _condition(self,key,condition,details,now,duration=0):
        # Unknown evidence never clears an existing alert or counts toward recovery.
        if condition is None:
            self.conditions.pop(key,None);return
        state=self.conditions.setdefault(key,{'since':None,'recovery':None})
        if condition:
            state['recovery']=None
            if state['since'] is None:state['since']=now
            if now-state['since']+1e-8>=duration:
                details={**details,'duration_s':now-state['since']}
                if key not in self.active:self._event(key,'triggered',details,now)
                self.active[key]=details
        else:
            state['since']=None
            if key in self.active:
                if state['recovery'] is None:state['recovery']=now
                if now-state['recovery']+1e-8>=5:
                    self._event(key,'resolved',self.active.pop(key),now);state['recovery']=None

    def _flush_evidence(self,now,force=False):
        while self.pending and (force or self.pending[0]['until']<=now):
            entry=self.pending.popleft();after=entry['after'];start=entry['event']['monotonic_s']
            times=[start]+[s['sensor_s'] for s in after]+[entry['until']]
            entry['after_complete']=not force and bool(after) and all(b-a<=.500001 for a,b in zip(times,times[1:]))
            entry['baseline_versions']={k:v.get('version') for k,v in self.baselines.items()}
            self.emit(entry)

    def tick(self):
        with self.lock:
            now=self.clock()
            age=None if self.last_ns is None else now-self.last_ns/1e9
            stale=age is None or age>1
            self._condition('stream_stale',stale,dict(rule='stream_stale',severity='warning',
                 message='传感器样本停更或没有可验证时间戳',value=age,threshold=1.,unit='s'),now)
            if stale:
                for key in list(self.conditions):
                    if key.startswith(('current:','stall:')):self.conditions.pop(key,None)
                if self.capture:self.capture=None;self.capture_result='cancelled: stream stale'
            self._flush_evidence(now)

    def on_sample(self,s,received=None):
        with self.lock:
            now=self.clock() if received is None else received;ns=s.get('t_ns')
            if type(ns)!=int or ns<=0 or not -.1<=now-ns/1e9<=1:
                self.conditions={k:v for k,v in self.conditions.items() if not k.startswith(('current:','stall:'))}
                return
            if self.last_ns is not None and ns<=self.last_ns:
                self.repeats+=1;return
            t=ns/1e9;policy=s.get('policy') if isinstance(s.get('policy'),str) else 'unknown'
            gap=None if self.last_ns is None else (ns-self.last_ns)/1e9
            discontinuity=gap is not None and gap>.5 or self.policy!=policy
            if discontinuity:
                self.conditions={k:v for k,v in self.conditions.items() if not k.startswith(('current:','stall:'))}
                if self.capture:self.capture=None;self.capture_result='cancelled: policy changed or sample gap'
            safety=s.get('safety') if isinstance(s.get('safety'),dict) else {}
            sample=dict(t_ns=str(ns),sensor_s=t,received_s=now,policy=policy,safety=deepcopy(safety),
                        currents_ma=vector(s.get('currents_ma'),True),velocities=vector(s.get('velocities')),
                        joints=vector(s.get('joints')),targets=vector(s.get('targets')))
            self.last_ns=ns;self.policy=policy;self.latest=sample;self.frames+=1
            self.history.append(sample)
            while self.history and t-self.history[0]['sensor_s']>300:self.history.popleft()
            for entry in self.pending:
                if entry['event']['monotonic_s']<t<=entry['until']:entry['after'].append(deepcopy(sample))
            self._flush_evidence(t)
            if self.capture:
                if sample['currents_ma'] is None or self._paused(sample):
                    self.capture=None;self.capture_result='cancelled: invalid current or suspended context'
                else:
                    self.capture['values'].append(sample['currents_ma'])
                    if t-self.capture['start']>=60:
                        rows=self.capture['values'];p95=[sorted(row[i] for row in rows)[math.ceil(.95*len(rows))-1] for i in range(15)]
                        if len(rows)>=120:
                            previous=self.baselines.get(policy,{})
                            b=dict(p95_ma=p95,version=previous.get('version',0)+1,created_at=utc(),
                                   samples=len(rows),duration_s=t-self.capture['start'],source=self.capture['source'],identity=deepcopy(self.identity))
                            updated={**self.baselines,policy:b}
                            try:atomic_json(self.directory/'baselines.json',updated);self.baselines=updated;self.capture_result='saved'
                            except OSError as e:self._error(e);self.capture_result='failed to persist baseline'
                        else:self.capture_result='insufficient samples'
                        self.capture=None
            base=self._base(policy);cfg=self.config;recent=[x for x in self.history if t-x['sensor_s']<=1.000001]
            for i,ident in enumerate(IDS):
                current=sample['currents_ma'][i] if sample['currents_ma'] is not None else None
                threshold=max(cfg['current_multiplier']*base['p95_ma'][i],base['p95_ma'][i]+cfg['current_margin_ma']) if base else None
                high=None if current is None or threshold is None else current>threshold
                details=dict(id=ident,name=NAMES[i],rule='current',severity='warning',message='相对参考负载持续偏高',
                             value=current,threshold=threshold,unit='mA',policy=policy,baseline_version=base.get('version') if base else None)
                self._condition(f'current:{ident}',high,details,t,cfg['duration_s'])
                ready=all(sample[k] is not None for k in ('joints','targets','velocities'))
                stable=(len(recent)>=2 and t-recent[0]['sensor_s']>=.8 and all(x['targets'] is not None and x['policy']==policy for x in recent)
                        and max(x['targets'][i] for x in recent)-min(x['targets'][i] for x in recent)<=math.radians(cfg['target_span_deg']))
                error=math.degrees(abs(sample['targets'][i]-sample['joints'][i])) if ready else None
                speed=math.degrees(abs(sample['velocities'][i])) if ready else None
                paused=self._paused(sample)
                stall=None if paused or not ready or high is None else high and stable and error>cfg['error_deg'] and speed<cfg['speed_deg_s']
                self._condition(f'stall:{ident}',stall,{**details,'rule':'stall','severity':'alarm','message':'疑似卡滞，需人工核查',
                    'error_deg':error,'speed_deg_s':speed,'error_threshold_deg':cfg['error_deg'],
                    'speed_threshold_deg_s':cfg['speed_deg_s'],'target_span_threshold_deg':cfg['target_span_deg']},t,cfg['duration_s'])

    def on_health(self,health):
        if not isinstance(health,dict):raise ValueError('invalid robot.health response')
        # Reject non-finite JSON before it can reach the Web serializer.
        json.dumps(health,allow_nan=False)
        with self.lock:
            now=self.clock();self.health=deepcopy(health);self.health_received=now
            motors=health.get('motors') or {};battery=health.get('battery') or {};bus=health.get('bus') or {};cfg=self.config
            temp=motors.get('max_c');volts=battery.get('volts')
            self._condition('temperature',True if finite(temp) and temp>=cfg['temp_warn_c'] else None,dict(rule='temperature',
                 severity='alarm' if finite(temp) and temp>=cfg['temp_alarm_c'] else 'warning',message='最近报告温度偏高，原始采样新鲜度未知',
                 value=temp if finite(temp) else None,threshold=cfg['temp_warn_c'],alarm_threshold=cfg['temp_alarm_c'],unit='°C',
                 hottest=motors.get('hottest'),sample_freshness='unverifiable'),now)
            self._condition('voltage',True if finite(volts) and not cfg['voltage_min_v']<=volts<=cfg['voltage_max_v'] else None,
                 dict(rule='voltage',severity='warning',message='汇总平滑电压超出监测参考范围，原始采样新鲜度未知',
                      value=volts if finite(volts) else None,threshold=[cfg['voltage_min_v'],cfg['voltage_max_v']],unit='V',
                      manufacturer_range_v=[3.7,6.0],sample_freshness='unverifiable'),now)
            failures=bus.get('consecutive_errors');missing=bus.get('missing',[])
            self._condition('bus',None if type(failures)!=int else failures>0 or bool(missing),dict(rule='bus',severity='warning',
                 message='总线读取异常；不据此推断某个关节损坏',value=failures,threshold=0,missing=missing),now)

    def status(self, compact=False):
        with self.lock:
            now=self.clock();age=None if self.last_ns is None else max(0.,now-self.last_ns/1e9)
            stale=age is None or age>1;sample=self.latest or {};rows=[];base=self._base(self.policy)
            for i,ident in enumerate(IDS):
                def at(key):return sample[key][i] if sample.get(key) is not None else None
                pos,target,vel=at('joints'),at('targets'),at('velocities')
                rows.append(dict(id=ident,name=NAMES[i],current_ma=at('currents_ma'),position_deg=None if pos is None else math.degrees(pos),
                    target_deg=None if target is None else math.degrees(target),velocity_deg_s=None if vel is None else math.degrees(vel),
                    error_deg=None if pos is None or target is None else math.degrees(abs(target-pos)),
                    threshold_ma=max(self.config['current_multiplier']*base['p95_ma'][i],base['p95_ma'][i]+self.config['current_margin_ma']) if base else None,
                    state='stale' if stale else 'alarm' if f'stall:{ident}' in self.active else 'warning' if f'current:{ident}' in self.active else
                          'insufficient' if at('currents_ma') is None or vel is None or pos is None or target is None or not base else 'observing',
                    stall_paused=bool(sample and self._paused(sample))))
            capture=None if self.capture is None else dict(policy=self.capture['policy'],elapsed_s=max(0,(sample.get('sensor_s',now)-self.capture['start'])),
                                                          samples=len(self.capture['values']),duration_s=60)
            coverage={k:sample.get(k) is not None for k in ('currents_ma','velocities','joints','targets')}
            alerts=[]
            for key,value in self.active.items():
                comparable=not stale
                if key.startswith(('current:','stall:')):
                    comparable=comparable and self.policy==value.get('policy') and sample.get('currents_ma') is not None and base is not None
                    if key.startswith('stall:'):comparable=comparable and all(coverage.values()) and not self._paused(sample)
                else:comparable=self.health_received is not None and now-self.health_received<=2
                alerts.append(dict(key=key,evidence_status=('awaiting_fresh_sensor_evidence' if key in ('temperature','voltage') else 'comparable' if comparable else 'awaiting_comparable_evidence'),**value))
            return deepcopy(dict(enabled=True,mode='read_only',stale=stale,sensor_age_ms=None if age is None else age*1000,
               policy=self.policy,frames=self.frames,repeated_or_reordered_frames=self.repeats,rows=rows,coverage=coverage,
               overall='stale' if stale else 'alarm' if any(a['severity']=='alarm' for a in self.active.values()) else
                       'warning' if self.active else 'insufficient' if not all(coverage.values()) or not base else 'observing',
               alerts=alerts,events=list(self.events)[-15:] if compact else list(self.events),rules=self.config,rule_version=self.config_version,
               baseline_capture=capture,baseline_result=self.capture_result,baselines=self.baselines,
               baseline_valid=bool(base),identity_verified=self.identity_verified,identity=self.identity,stream_epoch=self.stream_epoch,
               health=self.health,health_rpc_age_ms=None if self.health_received is None else (now-self.health_received)*1000,
               slow_sensor_freshness='unverifiable',maintenance=None if compact else self.maintenance,maintenance_version=self.maintenance_version,errors=list(self.errors),
               unavailable_live=['per_servo_temperature','per_servo_voltage','torque_enable','hardware_error','pwm'],
               inference='No rule violation is not proof of healthy hardware; current is supply-side magnitude, not torque.'))

    def series(self,ident,after_t_ns=None,epoch=None):
        if type(ident)!=int or ident not in IDS:raise ValueError('unknown joint ID')
        if after_t_ns is not None and (not isinstance(after_t_ns,str) or not after_t_ns.isdigit() or len(after_t_ns)>24):raise ValueError('invalid history cursor')
        i=IDS.index(ident)
        with self.lock:
            reset=after_t_ns is None or epoch!=self.stream_epoch or bool(self.history and not int(self.history[0]['t_ns'])<=int(after_t_ns)<=int(self.history[-1]['t_ns']))
            rows=list(self.history) if reset else [s for s in self.history if int(s['t_ns'])>int(after_t_ns)]
            return dict(id=ident,name=NAMES[i],epoch=self.stream_epoch,reset=reset,cursor=self.history[-1]['t_ns'] if self.history else None,samples=[dict(t_ns=s['t_ns'],t=s['sensor_s'],policy=s['policy'],
                **{key:None if s[key] is None else s[key][i] for key in ('currents_ma','velocities','joints','targets')}) for s in rows])

    def export(self,fmt='json'):
        with self.lock:
            if fmt=='json':return json.dumps(dict(schema='microduck-servo-health/v1',exported_at=utc(),
                status=self.status(),samples=list(self.history)),ensure_ascii=False,allow_nan=False).encode(),'application/json'
            if fmt!='csv':raise ValueError('format must be json or csv')
            out=io.StringIO();w=csv.writer(out);w.writerow(['t_ns','policy','id','current_ma','position_rad','target_rad','velocity_rad_s'])
            for s in self.history:
                for i,ident in enumerate(IDS):w.writerow([s['t_ns'],s['policy'],ident,*[None if s[k] is None else s[k][i] for k in ('currents_ma','joints','targets','velocities')]])
            return ('\ufeff'+out.getvalue()).encode(),'text/csv; charset=utf-8'


def read_health(path,timeout=.5):
    """Only existing read-only RPCs; never fall back to raw serial access."""
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout);sock.connect(str(path));file=sock.makefile('rwb')
        try:
            for ident,method,params in [(1,'hello',{'api_version':41}),(2,'robot.health',{})]:
                file.write((json.dumps(dict(jsonrpc='2.0',id=ident,method=method,params=params))+'\n').encode());file.flush()
                line=file.readline(262145)
                if len(line)>262144:raise ValueError('health reply too large')
                reply=json.loads(line)
                if reply.get('id')!=ident or reply.get('error') or not isinstance(reply.get('result'),dict):raise ValueError('invalid health RPC reply')
            return reply['result']
        finally:file.close()


class Plugin:
    def __init__(self,directory,*,robot_socket='/run/robotd.sock',enabled=False,live=False,clock=time.monotonic,adapter=None):
        self.enabled=enabled;self.directory=Path(directory);self.socket=robot_socket;self.live=live;self.clock=clock
        self.adapter=adapter;self.analysis_ready=not live;self.submit_lock=threading.Lock();self.gap=threading.Event()
        self.stop=threading.Event();self.frames=queue.Queue(maxsize=5);self.logs=queue.Queue(maxsize=100)
        self.log_stop=threading.Event()
        self.dropped_frames=0;self.dropped_logs=0;self.threads=[];self.engine=None
        if not enabled:return
        try:
            self.engine=Engine(directory,clock,self._emit,require_identity=live)
            for name,target in [('servo-analysis',self._work),('servo-log',self._write_logs)]+([('servo-health-rpc',self._health)] if live else []):
                thread=threading.Thread(name=name,target=target,daemon=True);thread.start();self.threads.append(thread)
        except Exception:
            self.stop.set();self.log_stop.set()
            for thread in self.threads:thread.join(timeout=2)
            raise

    def _emit(self,event):
        try:self.logs.put_nowait(event)
        except queue.Full:self.dropped_logs+=1

    def disconnect(self):
        if not self.enabled:return
        self.analysis_ready=not self.live
        if self.adapter:self.adapter.invalidate('monitor disconnected; awaiting renewed identity')
        self.engine.reset_continuity('monitor disconnected')

    def submit(self,sample):
        if not self.enabled or not self.analysis_ready:return
        fields={key:deepcopy(sample.get(key)) for key in ('t_ns','policy','safety','currents_ma','velocities','joints','targets')}
        with self.submit_lock:
            if self.frames.full():
                while True:
                    try:self.frames.get_nowait();self.dropped_frames+=1
                    except queue.Empty:break
                self.gap.set()
            self.frames.put_nowait((fields,self.clock()))

    def _work(self):
        while not self.stop.is_set():
            try:
                try:sample,received=self.frames.get(timeout=.1)
                except queue.Empty:sample=None
                if self.gap.is_set():
                    self.gap.clear();self.engine.reset_continuity('telemetry queue overflow')
                if sample is not None:
                    if self.clock()-received>1:self.dropped_frames+=1;self.engine.reset_continuity('analysis backlog stale')
                    elif self.analysis_ready:self.engine.on_sample(sample)
                self.engine.tick()
            except Exception as e:
                with self.engine.lock:self.engine._error(e)

    def _health(self):
        while not self.stop.is_set():
            start=time.monotonic()
            try:
                if self.adapter is not None:
                    health,identity,capabilities=self.adapter.poll()
                    self.analysis_ready=capabilities['read_contract_verified']
                    self.engine.set_identity(identity,capabilities['baseline_identity_verified'])
                    if self.analysis_ready:self.engine.on_health(health)
                else:self.engine.on_health(read_health(self.socket))
            except Exception as e:
                with self.engine.lock:
                    self.engine._error(f'robot.health: {e}')
                    if self.adapter is not None:
                        self.analysis_ready=False;self.engine.reset_continuity('health/identity unavailable')
                        self.adapter.invalidate('health/identity unavailable: '+str(e)[:120])
                    for key in ('temperature','voltage','bus'):self.engine.conditions.pop(key,None)
            self.stop.wait(max(0,1-(time.monotonic()-start)))

    def _write_logs(self):
        handler=None
        try:
            while not self.log_stop.is_set() or not self.logs.empty():
                try:event=self.logs.get(timeout=.1)
                except queue.Empty:continue
                try:
                    if handler is None:
                        self.directory.mkdir(parents=True,exist_ok=True)
                        # Active file plus four backups = five files total, each <=10 MiB.
                        handler=RotatingFileHandler(self.directory/'events.jsonl',maxBytes=10*1024*1024,backupCount=4,encoding='utf-8')
                    text=json.dumps(event,ensure_ascii=False,allow_nan=False)
                    size=len((text+'\n').encode('utf-8'))
                    if size>handler.maxBytes:raise ValueError('event exceeds 10 MiB log record cap')
                    if handler.stream is None:handler.stream=handler._open()
                    # Enforce bytes, not character count, for Chinese evidence as well.
                    if os.fstat(handler.stream.fileno()).st_size+size>handler.maxBytes:handler.doRollover()
                    handler.stream.write(text+'\n');handler.flush()
                except (OSError,ValueError) as e:
                    self.dropped_logs+=1
                    with self.engine.lock:self.engine._error(f'event log: {e}')
        finally:
            if handler:handler.close()

    def status(self,compact=False):
        if not self.enabled:return dict(enabled=False,message=getattr(self,'startup_error',
            '插件关闭；以 --enable-servo-health 启动Web后启用，不修改robotd。'))
        result=self.engine.status(compact=compact);result.update(compatibility=self.adapter.status() if self.adapter else None,live_transport=self.live,dropped_frames=self.dropped_frames,dropped_logs=self.dropped_logs)
        return result

    def close(self):
        if not self.enabled:return
        self.stop.set()
        for t in self.threads:
            if t.name!='servo-log':t.join(timeout=2)
        with self.engine.lock:self.engine._flush_evidence(self.clock(),force=True)
        self.log_stop.set()
        for t in self.threads:t.join(timeout=2)
