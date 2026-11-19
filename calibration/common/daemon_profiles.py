"""Pinned, reviewed daemon contracts. Unknown releases never gain write capability."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_API = 68


@lru_cache(maxsize=1)
def profiles():
    old = json.loads((ROOT / 'deploy/manual-upgrade/inputs.json').read_text())
    new = json.loads((ROOT / 'calibration/daemon-v0.23.0/native-build.json').read_text())
    return {
        '0.18.3': {**old, 'ipc_api': 47, 'build_revision': old['native_revision']},
        '0.23.0': new,
    }


def match(hello):
    if not isinstance(hello, dict):
        return None
    p = profiles().get(hello.get('daemon_version'))
    if not p or type(hello.get('api_version')) is not int or hello['api_version'] != p['ipc_api']:
        return None
    revision = hello.get('revision')
    kind = 'native' if revision == p['build_revision'] else 'official' if revision == p['base_revision'] else None
    return (p, kind) if kind else None


@lru_cache(maxsize=8)
def _digest(path, pid, dev, ino, size, mtime_ns, ctime_ns):
    with Path(path).open('rb') as f:
        expected = (dev, ino, size, mtime_ns, ctime_ns)
        def current():
            st = os.fstat(f.fileno())
            return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns
        if current() != expected:
            raise ValueError('process executable changed before hashing')
        result = hashlib.file_digest(f, 'sha256').hexdigest()
        if current() != expected:
            raise ValueError('process executable changed while hashing')
        return result


def process_hash(pid):
    if type(pid) is not int or pid <= 1:
        raise ValueError('invalid process PID')
    path = Path(f'/proc/{pid}/exe')
    st = path.stat()
    result = _digest(str(path), pid, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    after = path.stat()
    if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('process executable changed during verification')
    return result


def verify_process(pid, version, kind, program):
    return process_hash(pid) == profiles()[version][kind][program]
