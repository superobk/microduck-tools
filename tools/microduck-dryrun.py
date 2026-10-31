#!/usr/bin/env python3
"""Manage an isolated, fake-only daemon rehearsal. Never manages production services."""
import argparse
import contextlib
import datetime
import fcntl
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
import socket
import subprocess
import sys
import time
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = Path('/var/lib/microduck-tools-dryrun')
UNIT = Path('/etc/systemd/system/microduck-dryrun.service')
SERVICE = 'microduck-dryrun.service'
USER = 'microduck-tools'
APIS = {'0.16.3': 41, '0.17.9': 44, '0.18.3': 47}
CYCLE = ['0.16.3', '0.17.9', '0.18.3', '0.17.9', '0.16.3']
sys.path.insert(0, str(PACKAGE / 'calibration/integration-0.16.3/web'))
import daemon_adapter  # shared, bounded read-only IPC parser and topology contract


class Error(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    tmp = path.with_name('.' + path.name + '.tmp')
    with tmp.open('x') as file:
        os.chmod(tmp, 0o600)
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write('\n')
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, path)


class Board:
    def require(self):
        if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or os.geteuid() != 0:
            raise Error('Board operations require Linux ARM64 root; local tests use an injected backend.')

    def account(self):
        account = pwd.getpwnam(USER)
        if account.pw_uid == 0:
            raise Error('The rehearsal account must be non-root.')
        return account.pw_uid, account.pw_gid

    def systemctl(self, *args):
        return subprocess.run(['systemctl', *args], check=True, capture_output=True,
                              text=True, timeout=20).stdout.strip()

    def identity(self, pid):
        path = Path('/proc') / str(pid)
        return Path(os.readlink(path / 'exe')), (path / 'cmdline').read_bytes().rstrip(b'\0').decode().split('\0')

    def unit_identity(self, unit):
        fragment = self.systemctl('show', SERVICE, '-p', 'FragmentPath', '--value')
        overrides = self.systemctl('show', SERVICE, '-p', 'DropInPaths', '--value')
        if fragment != str(unit) or overrides:
            raise Error('Unexpected test unit fragment or drop-ins; no service was changed.')

    def sleep(self):
        time.sleep(.2)

    def probe(self, path):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(2)
            sock.connect(str(path))
            with sock.makefile('rwb') as file:
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
        for name in ('owner.json', 'state.json', 'versions', 'run', 'evidence', '.lock'):
            path = self.root / name
            if path.is_symlink() or not path.exists():
                raise Error('Unsafe or incomplete rehearsal layout: ' + name)
        if read_json(self.root / 'owner.json') != {'schema': 'microduck-fake-rehearsal/v1'}:
            raise Error('Unknown rehearsal owner marker.')
        if self.unit.is_symlink() or self.unit.read_bytes() != (self.assets / SERVICE).read_bytes():
            raise Error('Test unit differs from the managed template; review it before continuing.')
        wrapper = self.root / 'robotctl-dryrun'
        if wrapper.is_symlink() or wrapper.read_bytes() != (self.assets / 'robotctl-dryrun').read_bytes():
            raise Error('Test robotctl wrapper differs from the managed template.')

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
        if set(value) != {'current', 'previous', 'pending'}:
            raise Error('Invalid rehearsal state.')
        for key in ('current', 'previous'):
            if value[key] is not None and value[key] not in APIS:
                raise Error('Invalid saved version.')
        pending = value['pending']
        if pending is not None and (not isinstance(pending, dict) or set(pending) != {'from', 'to'}
                                   or pending['to'] not in APIS or pending['from'] not in (*APIS, None)):
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
        self.root.mkdir(mode=0o755)
        for name, mode in (('versions', 0o755), ('run', 0o700), ('evidence', 0o700)):
            (self.root / name).mkdir(mode=mode)
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
        if version not in APIS or not isinstance(revision, str) or not re.fullmatch(r'[A-Za-z0-9._+-]{1,200}', revision):
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
        try:
            (staging / 'bin').mkdir(mode=0o755)
            for name in ('robotd', 'robotctl'):
                shutil.copyfile(Path(source) / name, staging / 'bin' / name)
                (staging / 'bin' / name).chmod(0o755)
                if digest(staging / 'bin' / name) != hashes[name]:
                    raise Error('Source changed while copying: ' + name)
            shutil.copyfile(self.assets / 'dryrun.robotd.toml', staging / 'robotd.toml')
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
        for item in (path, path / 'bin', path / 'release.json', path / 'robotd.toml'):
            if item.is_symlink() or not item.exists():
                raise Error('Missing or symlinked staged version.')
        meta = read_json(path / 'release.json')
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
        tmp = self.root / '.current-next'
        if tmp.exists() or tmp.is_symlink():
            raise Error('Unexpected temporary current link; inspect it first.')
        tmp.symlink_to('versions/' + version)
        os.replace(tmp, self.root / 'current')

    def verify_once(self, version):
        meta = self.release(version)
        if self.current_link() != version:
            raise Error('Selected directory and expected version differ.')
        pid = int(self.board.systemctl('show', SERVICE, '-p', 'MainPID', '--value'))
        if pid <= 1:
            raise Error('Test daemon is not running.')
        executable, argv = self.board.identity(pid)
        expected = (self.root / 'versions' / version / 'bin/robotd').resolve()
        command = [str(self.root / 'current/bin/robotd'), '--fake', '--no-policy',
                   '--params', str(self.root / 'current/robotd.toml'), '--socket', str(self.root / 'run/robotd.sock')]
        if executable != expected or argv != command:
            raise Error('PID/executable/arguments are not the exact managed fake command.')
        hello, model, health, samples = self.board.probe(self.root / 'run/robotd.sock')
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
            self.board.systemctl('stop', SERVICE)
            self.select(version)
            self.board.systemctl('start', SERVICE)
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
        if old:
            self.release(old)  # refuse a corrupted fallback before stopping/selecting anything
        self.board.systemctl('stop', SERVICE)
        if old:
            self.select(old)
            self.board.systemctl('start', SERVICE)
            result = self.verify(old)
        else:
            if self.current_link() is not None:
                (self.root / 'current').unlink()
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
            results.append(self.record('cycle-switch', result))
            if not result['ok']:
                return dict(ok=False, steps=results)
        return dict(ok=True, steps=results)

    def web_command(self):
        self.release(self.state()['current'])
        return ['sudo', '-u', USER, 'python3', str(PACKAGE / 'calibration/integration-0.16.3/web/calibration_web.py'),
                '--target', 'auto', '--listen', '127.0.0.1', '--port', '18765',
                '--config', str(self.root / 'current/robotd.toml'), '--dest', str(self.root / 'run/calibration.toml'),
                '--runtime', str(self.root / 'run/calibration-effective.json'),
                '--camera-runtime', str(self.root / 'run/camera-effective.json'),
                '--robot-socket', str(self.root / 'run/robotd.sock'), '--robotctl', str(self.root / 'robotctl-dryrun'),
                '--enable-servo-health', '--servo-health-dir', str(self.root / 'run/servo-health'),
                '--camera-settings', str(self.root / 'run/camera.json')]


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
            result = app.record('init', app.init())
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
                    app.board.systemctl('stop', SERVICE)
                    result = dict(ok=True, stopped=True, state=app.state())
                elif args.command == 'web-command':
                    result = dict(ok=True, command=shlex.join(app.web_command()), printed_only=True)
                else:
                    result = dict(ok=True, state=app.state(), selected=app.current_link(),
                                  pid=app.board.systemctl('show', SERVICE, '-p', 'MainPID', '--value'))
                result = app.record(args.command, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(ok=False, error=str(error), hardware_acceptance=False), ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
