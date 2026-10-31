#!/usr/bin/env python3
"""Manage an isolated, fake-only daemon rehearsal. Never manages production services."""
import argparse
import contextlib
import datetime
import fcntl
import grp
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import pwd
import re
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import tempfile
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = Path('/var/lib/microduck-tools-dryrun')
UNIT = Path('/etc/systemd/system/microduck-dryrun.service')
SERVICE = 'microduck-dryrun.service'
USER = 'microduck-tools'
APIS = {'0.16.3': 41, '0.17.9': 44, '0.18.3': 47}
CYCLE = ['0.16.3', '0.17.9', '0.18.3', '0.17.9', '0.16.3']
EMPTY_RELATIONS = ('Requisite', 'Wants', 'BindsTo', 'PartOf', 'RequiredBy', 'RequisiteOf',
                   'WantedBy', 'BoundBy', 'ConsistsOf', 'OnFailure', 'OnSuccess',
                   'PropagatesStopTo', 'StopPropagatedFrom', 'Triggers', 'TriggeredBy')
sys.path.insert(0, str(PACKAGE / 'calibration/integration-0.16.3/web'))
import daemon_adapter  # shared, bounded read-only IPC parser and topology contract


class Error(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(), parse_constant=lambda _: (_ for _ in ()).throw(Error('Non-finite JSON.')))


def trusted(path, directory=False):
    info = Path(path).lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise Error('Managed path must be owned by the operator and not group/world writable: ' + str(path))


def write_json(path, value):
    path = Path(path)
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, 'w') as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DeadlineStream:
    """Bound the entire RPC exchange, including peers that trickle partial lines."""
    def __init__(self, sock, timeout=8):
        self.sock, self.deadline, self.buffer = sock, time.monotonic() + timeout, b''

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Error('IPC exchange deadline exceeded.')
        self.sock.settimeout(min(2, remaining))

    def write(self, raw):
        self.remaining()
        self.sock.sendall(raw)

    def flush(self):
        pass

    def readline(self, limit):
        while b'\n' not in self.buffer and len(self.buffer) < limit:
            self.remaining()
            raw = self.sock.recv(min(65536, limit - len(self.buffer)))
            if not raw:
                break
            self.buffer += raw
        end = self.buffer.find(b'\n') + 1
        size = min(end if end else len(self.buffer), limit)
        line, self.buffer = self.buffer[:size], self.buffer[size:]
        return line


class Board:
    def require(self):
        if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or os.geteuid() != 0:
            raise Error('Board operations require Linux ARM64 root; local tests use an injected backend.')

    def account(self):
        account = pwd.getpwnam(USER)
        group = grp.getgrnam(USER)
        if account.pw_uid == 0 or group.gr_gid == 0:
            raise Error('The rehearsal account and group must be non-root.')
        return account.pw_uid, group.gr_gid

    def absent(self):
        values = self.properties('LoadState', 'FragmentPath', 'Names')
        if values['LoadState'] != 'not-found' or values['FragmentPath'] or set(values['Names'].split()) - {SERVICE}:
            raise Error('The test service name is already loaded or aliases another unit; init refused.')

    def systemctl(self, *args):
        return subprocess.run(['systemctl', *args], check=True, capture_output=True,
                              text=True, timeout=20).stdout.strip()

    def identity(self, pid):
        path = Path('/proc') / str(pid)
        return Path(os.readlink(path / 'exe')), (path / 'cmdline').read_bytes().rstrip(b'\0').decode().split('\0')

    def properties(self, *names, optional=()):
        raw = self.systemctl('show', SERVICE, '--all', '-p', ','.join((*names, *optional)))
        result = {}
        for line in raw.splitlines():
            key, sep, value = line.partition('=')
            if not sep or key in result:
                raise Error('Invalid systemd property output.')
            result[key] = value
        if not set(names) <= result.keys():
            raise Error('Required systemd properties unavailable; refuse unsafe compatibility fallback.')
        return result

    def unit_identity(self, unit):
        expected = dict(Id=SERVICE, Names=SERVICE, FragmentPath=str(unit), DropInPaths='',
                        NeedDaemonReload='no', LoadState='loaded', Transient='no', User=USER, Group=USER,
                        Slice='system.slice', KillMode='control-group', Restart='no', PrivateDevices='yes',
                        ProtectSystem='strict', ProtectHome='yes', FailureAction='none', SuccessAction='none',
                        JobTimeoutAction='none', StartLimitAction='none')
        values = self.properties(*expected, *EMPTY_RELATIONS, 'Requires', 'Conflicts', 'ConflictedBy', optional=('Upholds', 'UpheldBy'))
        if any(values[key] != value for key, value in expected.items()):
            raise Error('Test unit identity, loaded configuration or sandbox differs; no service was changed.')
        for key in EMPTY_RELATIONS:
            if values[key]:
                raise Error('Test service has external systemd relationship: ' + key)
        if values.get('Upholds') or values.get('UpheldBy'):
            raise Error('Test service has an external Upholds relationship.')
        allowed = {'Requires': {'sysinit.target', 'basic.target', 'system.slice'},
                   'Conflicts': {'shutdown.target'}, 'ConflictedBy': {'shutdown.target'}}
        if any(set(values[key].split()) - names for key, names in allowed.items()):
            raise Error('Unexpected systemd dependency/conflict; production isolation cannot be established.')

    def sleep(self):
        time.sleep(.2)

    def peer_pid(self, sock):
        if not hasattr(socket, 'SO_PEERCRED'):
            raise Error('Linux SO_PEERCRED is required for board verification.')
        return struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]

    def probe(self, path, expected_pid):
        before = Path(path).lstat()
        if not stat.S_ISSOCK(before.st_mode):
            raise Error('Test IPC path is not a socket (symlinks are refused).')
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(2)
            sock.connect(str(path))
            after = Path(path).lstat()
            if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                    or self.peer_pid(sock) != expected_pid):
                raise Error('IPC peer is not the verified test daemon PID.')
            file = DeadlineStream(sock)
            hello = daemon_adapter.rpc(file, 1, 'hello', {'api_version': 41})
            model = daemon_adapter.rpc(file, 2, 'robot.model', {})
            health = daemon_adapter.rpc(file, 3, 'robot.health', {})
            # The only additional RPC is the existing read-only telemetry subscription.
            file.write(b'{"jsonrpc":"2.0","id":4,"method":"robot.subscribe","params":{"hz":5}}\n')
            file.flush()
            samples = []
            deadline = time.monotonic() + 3
            while len(samples) < 3 and time.monotonic() < deadline:
                line = file.readline(262145)
                if len(line) > 262144 or not line.endswith(b'\n'):
                    raise Error('Invalid bounded telemetry reply.')
                value = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(Error('Non-finite telemetry.')))
                if isinstance(value, dict) and value.get('method') == 'robot.state':
                    samples.append(value.get('params'))
            return hello, model, health, samples


class Rehearsal:
    # Root/unit overrides are dependency injection for local tests, not public CLI options.
    def __init__(self, root=ROOT, unit=UNIT, board=None):
        self.root, self.unit, self.board = Path(root), Path(unit), board or Board()
        self.assets = PACKAGE / 'deploy'

    def layout(self):
        if self.root.is_symlink() or not self.root.is_dir():
            raise Error('Run init first; existing unmarked/symlink directories are not adopted.')
        trusted(self.root, directory=True)
        for name in ('owner.json', 'state.json', 'versions', 'run', 'evidence', '.lock'):
            path = self.root / name
            if path.is_symlink() or not path.exists():
                raise Error('Unsafe or incomplete rehearsal layout: ' + name)
            if name != 'run':
                trusted(path, directory=name in ('versions', 'evidence'))
        if read_json(self.root / 'owner.json') != {'schema': 'microduck-fake-rehearsal/v1'}:
            raise Error('Unknown rehearsal owner marker.')
        if self.unit.is_symlink() or self.unit.read_bytes() != (self.assets / SERVICE).read_bytes():
            raise Error('Test unit differs from the managed template; review it before continuing.')
        trusted(self.unit)
        wrapper = self.root / 'robotctl-dryrun'
        if wrapper.is_symlink() or wrapper.read_bytes() != (self.assets / 'robotctl-dryrun').read_bytes():
            raise Error('Test robotctl wrapper differs from the managed template.')
        trusted(wrapper)

    @contextlib.contextmanager
    def locked(self):
        self.layout()
        with (self.root / '.lock').open('r') as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Error('Another rehearsal operation is in progress.') from None
            self.board.unit_identity(self.unit)
            yield

    def state(self):
        value = read_json(self.root / 'state.json')
        if not isinstance(value, dict) or set(value) != {'current', 'previous', 'pending'}:
            raise Error('Invalid rehearsal state.')
        for key in ('current', 'previous'):
            if value[key] is not None and (not isinstance(value[key], str) or value[key] not in APIS):
                raise Error('Invalid saved version.')
        pending = value['pending']
        if pending is not None and (not isinstance(pending, dict) or set(pending) != {'from', 'to'}
                                   or not isinstance(pending['to'], str) or pending['to'] not in APIS
                                   or pending['from'] != value['current']):
            raise Error('Invalid pending transaction.')
        return value

    def save(self, value):
        write_json(self.root / 'state.json', value)

    def record(self, command, result):
        name = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8] + '.json'
        report = dict(result, schema='microduck-fake-result/v1', action=command,
                      hardware_acceptance=False, automatic_updater_rollback='NOT_RUN')
        write_json(self.root / 'evidence' / name, report)
        return dict(report, evidence=str(self.root / 'evidence' / name))

    def init(self):
        self.board.require()
        uid, gid = self.board.account()
        if self.root.exists() or self.root.is_symlink() or self.unit.exists() or self.unit.is_symlink():
            raise Error('init requires a new root and an absent test unit; never overwrites/adopts existing installations.')
        self.board.absent()
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        for name, mode in (('versions', 0o755), ('run', 0o700), ('evidence', 0o700)):
            (self.root / name).mkdir(mode=mode)
            (self.root / name).chmod(mode)
        os.chown(self.root / 'run', uid, gid)
        (self.root / '.lock').touch(mode=0o600)
        write_json(self.root / 'owner.json', {'schema': 'microduck-fake-rehearsal/v1'})
        self.save(dict(current=None, previous=None, pending=None))
        shutil.copyfile(self.assets / 'robotctl-dryrun', self.root / 'robotctl-dryrun')
        (self.root / 'robotctl-dryrun').chmod(0o755)
        shutil.copyfile(self.assets / SERVICE, self.unit)
        self.unit.chmod(0o644)
        self.board.systemctl('daemon-reload')
        self.board.unit_identity(self.unit)
        return dict(ok=True, initialized=str(self.root), autostart=False)

    def source(self, version, source, hashes, revision):
        if (not isinstance(hashes, dict) or set(hashes) != {'robotd', 'robotctl'}
                or any(not isinstance(v, str) for v in hashes.values())):
            raise Error('Invalid binary hash metadata.')
        if not isinstance(version, str) or version not in APIS or not isinstance(revision, str) or not re.fullmatch(r'[A-Za-z0-9._+-]{1,200}', revision):
            raise Error('A supported version and exact expected daemon revision are required.')
        for name in ('robotd', 'robotctl'):
            path = Path(source) / name
            if path.is_symlink() or not path.is_file():
                raise Error('Source must be a regular binary: ' + name)
            expected = hashes.get(name, '')
            if not re.fullmatch(r'[0-9a-f]{64}', expected) or digest(path) != expected:
                raise Error('SHA256 mismatch: ' + name)
            with path.open('rb') as file:
                header = file.read(20)
            if len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\xb7\x00':
                raise Error('Only little-endian ELF64 AArch64 binaries may be staged.')
        return dict(version=version, revision=revision, api=APIS[version], hashes=hashes)

    def stage(self, version, source, hashes, revision):
        meta = self.source(version, source, hashes, revision)
        target = self.root / 'versions' / version
        if target.exists() or target.is_symlink():
            raise Error('Version directory already exists; staged versions are immutable.')
        staging = self.root / 'versions' / ('.stage-' + uuid.uuid4().hex)
        staging.mkdir(mode=0o755)
        staging.chmod(0o755)
        try:
            (staging / 'bin').mkdir(mode=0o755)
            (staging / 'bin').chmod(0o755)
            for name in ('robotd', 'robotctl'):
                shutil.copyfile(Path(source) / name, staging / 'bin' / name)
                (staging / 'bin' / name).chmod(0o755)
                if digest(staging / 'bin' / name) != hashes[name]:
                    raise Error('Source changed while copying: ' + name)
            shutil.copyfile(self.assets / 'dryrun.robotd.toml', staging / 'robotd.toml')
            (staging / 'robotd.toml').chmod(0o644)
            write_json(staging / 'release.json', meta)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return dict(ok=True, staged=meta)

    def release(self, version):
        if version not in APIS:
            raise Error('Unsupported or absent version.')
        path = self.root / 'versions' / version
        for item in (path, path / 'bin', path / 'release.json', path / 'robotd.toml', path / 'bin/robotd', path / 'bin/robotctl'):
            if item.is_symlink() or not item.exists():
                raise Error('Missing or symlinked staged version.')
            trusted(item, directory=item in (path, path / 'bin'))
        meta = read_json(path / 'release.json')
        if not isinstance(meta, dict) or set(meta) != {'version', 'revision', 'api', 'hashes'}:
            raise Error('Invalid staged release metadata.')
        checked = self.source(version, path / 'bin', meta['hashes'], meta['revision'])
        if checked != meta or (path / 'robotd.toml').read_bytes() != (self.assets / 'dryrun.robotd.toml').read_bytes():
            raise Error('Staged metadata/config was changed.')
        return meta

    def current_link(self):
        path = self.root / 'current'
        if not path.is_symlink():
            if path.exists():
                raise Error('current must be a managed symlink.')
            return None
        value = os.readlink(path)
        version = value.removeprefix('versions/')
        if version not in APIS or value != 'versions/' + version:
            raise Error('current points outside the managed version directories.')
        return version

    def select(self, version):
        self.current_link()
        tmp = self.root / ('.current-' + uuid.uuid4().hex)
        try:
            tmp.symlink_to('versions/' + version)
            os.replace(tmp, self.root / 'current')
            sync_directory(self.root)
        finally:
            tmp.unlink(missing_ok=True)

    def command(self):
        return [str(self.root / 'current/bin/robotd'), '--fake', '--no-policy',
                '--params', str(self.root / 'current/robotd.toml'), '--socket', str(self.root / 'run/robotd.sock')]

    def guard(self):
        self.layout()
        self.board.unit_identity(self.unit)
        job = self.board.properties('Job')['Job'].split()
        if not job or job[0] != '0':
            raise Error('Test service has a pending systemd job; wait and review before retrying.')

    def running_identity(self):
        pid = int(self.board.systemctl('show', SERVICE, '-p', 'MainPID', '--value'))
        if pid > 1:
            executable, argv = self.board.identity(pid)
            allowed = [self.root.resolve() / 'versions' / v / 'bin/robotd' for v in APIS]
            if executable not in allowed or argv != self.command():
                raise Error('Refusing to stop a process that is not the managed fake command.')
        elif pid != 0:
            raise Error('Invalid test daemon PID.')
        return pid

    def stopped(self):
        values = self.board.properties('MainPID', 'ControlPID', 'ActiveState', 'Job')
        if (values['MainPID'] != '0' or values['ControlPID'] != '0'
                or values['ActiveState'] not in ('inactive', 'failed') or values['Job'].split()[:1] != ['0']):
            raise Error('Test service did not fully stop; current was not changed.')

    def stop(self):
        self.guard()
        self.running_identity()
        self.board.systemctl('stop', SERVICE, '--job-mode=fail')
        self.stopped()

    def start(self):
        self.guard()
        self.stopped()
        self.board.systemctl('start', SERVICE, '--job-mode=fail')

    def verify_once(self, version):
        self.guard()
        meta = self.release(version)
        if self.current_link() != version:
            raise Error('Selected directory and expected version differ.')
        pid = int(self.board.systemctl('show', SERVICE, '-p', 'MainPID', '--value'))
        if pid <= 1:
            raise Error('Test daemon is not running.')
        executable, argv = self.board.identity(pid)
        expected = (self.root / 'versions' / version / 'bin/robotd').resolve()
        if executable != expected or argv != self.command():
            raise Error('PID/executable/arguments are not the exact managed fake command.')
        hello, model, health, samples = self.board.probe(self.root / 'run/robotd.sock', pid)
        contract = daemon_adapter.contract(hello, model, 'daemon-v' + version)
        if (not contract['read_contract_verified'] or hello.get('api_version') != APIS[version]
                or hello.get('revision') != meta['revision'] or health.get('healthy') is not True):
            raise Error('Daemon identity/API/topology/health verification failed.')
        timestamps = []
        for sample in samples:
            if not isinstance(sample, dict) or type(sample.get('t_ns')) is not int or sample['t_ns'] <= 0:
                raise Error('Missing or invalid sensor timestamp.')
            for key in ('joints', 'targets'):
                vector = sample.get(key)
                if not isinstance(vector, list) or len(vector) != 15 or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
                    raise Error('Invalid joint/target vector.')
            if sample.get('policy') != 'held':
                raise Error('Unexpected active policy in fake/no-policy rehearsal.')
            timestamps.append(sample['t_ns'])
        if len(timestamps) < 3 or any(b <= a for a, b in zip(timestamps, timestamps[1:])):
            raise Error('Telemetry did not provide three increasing sensor timestamps.')
        if int(self.board.systemctl('show', SERVICE, '-p', 'MainPID', '--value')) != pid:
            raise Error('Daemon PID changed during verification.')
        return dict(ok=True, mode='fake', version=version, revision=meta['revision'], api=APIS[version],
                    pid=pid, executable=str(executable), hashes=meta['hashes'], health=health,
                    sensor_timestamps_ns=timestamps, hardware_acceptance=False)

    def verify(self, version):
        last = None
        for _ in range(5):
            try:
                return self.verify_once(version)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                last = error
                self.board.sleep()
        raise Error(str(last))

    def switch(self, version):
        state = self.state()
        if state['pending']:
            raise Error('Interrupted transaction: run rollback before another switch.')
        self.release(version)
        old = state['current']
        if self.current_link() != old:
            raise Error('Saved state and current link differ; no service was changed.')
        if old:
            self.release(old)
        self.save(dict(state, pending={'from': old, 'to': version}))
        try:
            self.stop()
            self.select(version)
            self.start()
            result = self.verify(version)
            self.save(dict(current=version, previous=old if old != version else state['previous'], pending=None))
            return result
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            try:
                restored = self.recover(state, old)
                return dict(ok=False, requested=version, error=str(error), restored=restored)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as recovery:
                return dict(ok=False, requested=version, error=str(error), recovery_error=str(recovery),
                            pending=True, message='Recovery incomplete; inspect evidence and run rollback.')

    def recover(self, state, old):
        pending = self.state()['pending']
        if pending and self.current_link() not in (pending['from'], pending['to']):
            raise Error('Selected version is outside the interrupted transaction; review before recovery.')
        self.stop()
        if old:
            self.release(old)
        if old:
            self.select(old)
            self.start()
            result = self.verify(old)
        else:
            if self.current_link() is not None:
                (self.root / 'current').unlink()
                sync_directory(self.root)
            result = dict(ok=True, version=None, stopped=True)
        self.save(dict(state, pending=None))
        return result

    def rollback(self):
        state = self.state()
        if state['pending']:
            return self.recover(dict(state, current=state['pending']['from']), state['pending']['from'])
        if state['previous'] is None:
            raise Error('No previously verified version exists.')
        return self.switch(state['previous'])

    def cycle(self):
        for version in set(CYCLE):
            self.release(version)
        results = []
        for version in CYCLE:
            result = self.switch(version)
            logged = report_result(self, 'cycle-switch', result)
            results.append(logged)
            if not logged['ok']:
                return dict(ok=False, steps=results)
            if not result['ok']:
                return dict(ok=False, steps=results)
        return dict(ok=True, steps=results)

    def web_command(self):
        if self.state()['pending']:
            raise Error('Interrupted transaction; rollback before starting a Web observer.')
        if self.current_link() != self.state()['current']:
            raise Error('Saved version and selected link differ; verify before observing.')
        self.release(self.state()['current'])
        return ['sudo', '-u', USER, 'python3', str(PACKAGE / 'calibration/integration-0.16.3/web/calibration_web.py'),
                '--target', 'auto', '--listen', '127.0.0.1', '--port', '18765',
                '--config', str(self.root / 'current/robotd.toml'), '--dest', str(self.root / 'run/calibration.toml'),
                '--runtime', str(self.root / 'run/calibration-effective.json'),
                '--camera-runtime', str(self.root / 'run/camera-effective.json'),
                '--robot-socket', str(self.root / 'run/robotd.sock'), '--robotctl', str(self.root / 'robotctl-dryrun'),
                '--enable-servo-health', '--servo-health-dir', str(self.root / 'run/servo-health'),
                '--camera-settings', str(self.root / 'run/camera.json')]


def report_result(app, action, result):
    try:
        return app.record(action, result)
    except (OSError, ValueError) as error:
        return dict(result, ok=False, execution_ok=result['ok'], evidence_error=str(error),
                    message='Evidence write failed; no additional service action was taken. Inspect status/verify.')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('init', 'stop', 'rollback', 'cycle'):
        sub.add_parser(name).add_argument('--apply', action='store_true', help='execute on the board; default is preview')
    stage = sub.add_parser('stage')
    stage.add_argument('--version', choices=APIS, required=True)
    stage.add_argument('--bin-dir', type=Path, required=True)
    stage.add_argument('--robotd-sha256', required=True)
    stage.add_argument('--robotctl-sha256', required=True)
    stage.add_argument('--revision', required=True, help='exact expected daemon hello revision, not a version label')
    stage.add_argument('--apply', action='store_true')
    switch = sub.add_parser('switch')
    switch.add_argument('--version', choices=APIS, required=True)
    switch.add_argument('--apply', action='store_true')
    for name in ('verify', 'status', 'web-command'):
        sub.add_parser(name)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    app = Rehearsal()
    try:
        if hasattr(args, 'apply') and not args.apply:
            detail = dict(ok=True, preview=True, command=args.command, root=str(ROOT), service=SERVICE,
                          board_operations='NOT_RUN', version=getattr(args, 'version', None))
            detail['steps'] = {
                'init': ['require new directory/unit and existing non-root account', 'create fake-only sandbox unit', 'daemon-reload; do not start'],
                'stage': ['verify operator-supplied hashes and ELF architecture', 'copy immutable version and synthetic config', 'recheck copied hashes'],
                'switch': ['verify target and fallback files', 'record pending', 'stop test service', 'select and start fake', 'verify; recover on failure'],
                'rollback': ['recover pending transaction, otherwise select previous verified version', 'start and verify fake'],
                'cycle': CYCLE,
                'stop': ['stop microduck-dryrun.service only; retain files and history'],
            }[args.command]
            if args.command == 'stage':
                detail['source'] = app.source(args.version, args.bin_dir,
                    dict(robotd=args.robotd_sha256, robotctl=args.robotctl_sha256), args.revision)
            print(json.dumps(detail, ensure_ascii=False, indent=2))
            return 0
        app.board.require()
        if args.command == 'init':
            result = report_result(app, 'init', app.init())
        else:
            with app.locked():
                if args.command == 'stage':
                    result = app.stage(args.version, args.bin_dir,
                        dict(robotd=args.robotd_sha256, robotctl=args.robotctl_sha256), args.revision)
                elif args.command == 'switch':
                    result = app.switch(args.version)
                elif args.command == 'verify':
                    state = app.state()
                    if state['pending']:
                        raise Error('Interrupted transaction; rollback is required.')
                    result = app.verify(state['current'])
                elif args.command == 'rollback':
                    result = app.rollback()
                elif args.command == 'cycle':
                    result = app.cycle()
                elif args.command == 'stop':
                    app.stop()
                    result = dict(ok=True, stopped=True, state=app.state())
                elif args.command == 'web-command':
                    result = dict(ok=True, command=shlex.join(app.web_command()), printed_only=True)
                else:
                    result = dict(ok=True, state=app.state(), selected=app.current_link(),
                                  pid=app.board.systemctl('show', SERVICE, '-p', 'MainPID', '--value'))
                result = report_result(app, args.command, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(ok=False, error=str(error), hardware_acceptance=False), ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps(dict(ok=False, interrupted=True, message='Review status; recover pending with rollback. No automatic recovery on interrupt.',
                              hardware_acceptance=False)), file=sys.stderr)
        return 130


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
