"""Version-independent read-only IPC boundary. Extensions register explicit contracts.

No control methods, serial fallback, shell commands or dynamic Python plugin loading.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import threading

JOINT_NAMES = ['left_hip_yaw','left_hip_roll','left_hip_pitch','left_knee','left_ankle',
    'neck_pitch','head_pitch','head_yaw','head_roll','mouth',
    'right_hip_yaw','right_hip_roll','right_hip_pitch','right_knee','right_ankle']
JOINT_IDS = [20,21,22,23,24,30,31,32,33,34,10,11,12,13,14]
READ_METHODS = frozenset(('hello','robot.health','robot.model'))
PROFILES = {}


def register_profile(name, api_versions):
    """Add reviewed read contracts here; calibration writes are deliberately separate."""
    if not isinstance(name,str) or not name or name in PROFILES:
        raise ValueError('duplicate or empty adapter name')
    if not api_versions or any(type(v)!=int or v<=0 for v in api_versions):
        raise ValueError('invalid API versions')
    if any(set(api_versions)&v for v in PROFILES.values()):
        raise ValueError('API profile overlaps an existing adapter')
    PROFILES[name] = frozenset(api_versions)


register_profile('microduck-state-v1', (41,44,45,47))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def local_device_id():
    # Never expose the machine-id itself in HTTP or exports.
    try:
        value=Path('/etc/machine-id').read_text().strip()
        if value:return fingerprint({'machine_id':value})
    except OSError:pass
    return None


def contract(hello, model, target='auto'):
    api=hello.get('api_version')
    if type(api)!=int or api<=0:raise ValueError('invalid hello API version')
    version=hello.get('daemon_version');revision=hello.get('revision')
    if version is not None and (not isinstance(version,str) or len(version)>100):raise ValueError('invalid daemon version')
    if revision is not None and (not isinstance(revision,str) or len(revision)>200):raise ValueError('invalid daemon revision')
    profile=next((name for name,versions in PROFILES.items() if api in versions),None)
    topology=model.get('joint_names')==JOINT_NAMES
    target_match=target=='auto' or version is not None and target=='daemon-v'+version
    ready=bool(profile and topology and target_match)
    reason='reviewed read contract; runtime hardware acceptance pending' if ready else (
        'joint topology missing or different' if not topology else
        'selected target differs from hello' if not target_match else
        'unreviewed API; raw telemetry only, rule analysis disabled')
    return dict(adapter=profile,api_version=api,daemon_version=version,revision=revision,
        topology_verified=topology,read_contract_verified=ready,reason=reason,
        joint_ids=JOINT_IDS if topology else None,unit_contract='mA magnitude / rad / rad_s' if profile else 'unverified',
        native_calibration=False,hardware_acceptance=False)


def rpc(file, ident, method, params):
    if method not in READ_METHODS:raise ValueError('RPC method outside read-only allowlist')
    file.write((json.dumps(dict(jsonrpc='2.0',id=ident,method=method,params=params))+'\n').encode());file.flush()
    line=file.readline(262145)
    if len(line)>262144 or not line.endswith(b'\n'):raise ValueError('invalid bounded IPC reply')
    response=json.loads(line,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('non-finite IPC value')))
    if not isinstance(response,dict) or response.get('id')!=ident or response.get('error') or not isinstance(response.get('result'),dict):
        raise ValueError('invalid '+method+' reply')
    return response['result']


class Client:
    def __init__(self,path,target='auto',device_id=None,calibration_hash=None):
        self.path=path;self.target=target;self.device_id=device_id or local_device_id()
        self.calibration_hash=calibration_hash;self.model=None;self.hello_key=None
        self.lock=threading.Lock();self.capabilities=dict(read_contract_verified=False,reason='awaiting hello/model',hardware_acceptance=False)

    def poll(self,timeout=.5):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout);sock.connect(str(self.path));file=sock.makefile('rwb')
            try:
                hello=rpc(file,1,'hello',{'api_version':41})
                key=fingerprint(hello)
                # Model is cached across health polls; refresh after identity changes.
                if self.model is None or key!=self.hello_key:
                    self.model=rpc(file,2,'robot.model',{})
                capabilities=contract(hello,self.model,self.target)
                health=rpc(file,3,'robot.health',{})
            finally:file.close()
        self.hello_key=key
        calibration_hash=self.calibration_hash() if callable(self.calibration_hash) else self.calibration_hash
        identity=dict(device_id=self.device_id,daemon=hello,joint_names=self.model.get('joint_names'),
            model_asset=self.model.get('asset'),calibration_hash=calibration_hash,
            unit_contract=capabilities['unit_contract'])
        capabilities['baseline_identity_verified']=bool(capabilities['read_contract_verified'] and self.device_id
            and hello.get('revision') and calibration_hash)
        with self.lock:self.capabilities=capabilities
        return health,identity,capabilities

    def status(self):
        with self.lock:return deepcopy(self.capabilities)

    def invalidate(self,reason):
        with self.lock:
            self.model=None
            self.capabilities={**self.capabilities,'read_contract_verified':False,'reason':reason}


def load_plugin_config(path):
    """Independent, fail-closed host schema; never written into robotd.toml."""
    if path is None:return {}
    value=json.loads(Path(path).read_text(),parse_constant=lambda _:(_ for _ in ()).throw(ValueError('non-finite config')))
    if not isinstance(value,dict) or value.get('schema')!=1 or set(value)-{'schema','servo_health'}:
        raise ValueError('unsupported plugin host schema or plugin')
    servo=value.get('servo_health',{})
    if not isinstance(servo,dict) or set(servo)-{'enabled','device_id','calibration_hash'}:
        raise ValueError('unknown servo_health option')
    if 'enabled' in servo and type(servo['enabled'])!=bool:raise ValueError('enabled must be boolean')
    for key in ('device_id','calibration_hash'):
        if key in servo and (not isinstance(servo[key],str) or not 1<=len(servo[key])<=200):raise ValueError('invalid '+key)
    return servo
