"""Cooperative file lock and optimistic conflict checks for tool-owned writes."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import duck_calibrate as cal


def revision(data):return hashlib.sha256(data).hexdigest()


@contextmanager
def locked(path):
    path=Path(path)
    if path.is_symlink():raise cal.CalibrationError('symlink config destinations are not supported')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path.with_name(path.name+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def check(path, original):
    if Path(path).is_symlink() or Path(path).read_bytes()!=original:
        raise cal.CalibrationError('configuration conflict: changed since preview; refresh and review again')


def replace_checked(path, original, updated, expected=None):
    if expected is not None and expected!=revision(original):
        raise cal.CalibrationError('configuration conflict: preview revision differs; refresh and review again')
    check(path,original)
    backup=cal.make_backup(path)
    check(path,original)
    cal.atomic_write(path,updated,Path(path).stat().st_mode&0o777)
    if Path(path).read_bytes()!=updated:raise cal.CalibrationError('configuration readback differs; review external writers')
    return backup
