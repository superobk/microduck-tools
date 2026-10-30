"""The reviewed 0.18.3 user-over-base layout; other targets keep one file."""
from copy import deepcopy
from pathlib import Path
import tomllib

DEFAULT_PATH=Path('/etc/robot/robotd.toml')
USER_PATH=Path('/var/lib/robot/robotd.toml')


def default_calibration(target):
    return '/var/lib/robot/calibration/microduck-0.18.3.toml' if target=='daemon-v0.18.3' else '/etc/robot/robot-calibration.toml'


def user_layer_for(path,target):
    return USER_PATH if target=='daemon-v0.18.3' and Path(path)==DEFAULT_PATH else None


def write_path(path,target):return user_layer_for(path,target) or Path(path)


def merge(base,user):
    result=deepcopy(base)
    for key,value in user.items():
        result[key]=merge(result[key],value) if isinstance(result.get(key),dict) and isinstance(value,dict) else deepcopy(value)
    return result


def texts(path,target):
    base=Path(path).read_bytes();user=user_layer_for(path,target)
    return base,user.read_bytes() if user and user.exists() else b''


def load(path,target):
    base,user=texts(path,target)
    result=merge(tomllib.loads(base.decode()),tomllib.loads(user.decode()))
    for key in ('bus','board'):
        if key in result and not isinstance(result[key],dict):raise ValueError(key+' must be a table')
    return result
