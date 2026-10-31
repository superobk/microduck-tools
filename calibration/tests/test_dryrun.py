"""Local filesystem/state-machine tests; no systemd, serial or board access."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('dryrun', ROOT / 'tools/microduck-dryrun.py')
dry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dry)


def sample(t):
    return dict(t_ns=t, joints=[0.] * 15, targets=[0.] * 15, policy='held')


class FakeBoard:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.running = False
        self.pid = 100
        self.bad = set()
        self.overrides = False
        self.samples = None
        self.api = None
        self.revision = None
        self.argv = None

    def require(self):
        pass

    def account(self):
        return os.getuid(), os.getgid()

    def sleep(self):
        pass

    def unit_identity(self, unit):
        if self.overrides:
            raise dry.Error('unreviewed drop-in')

    def systemctl(self, *args):
        self.calls.append(args)
        if args[0] == 'start':
            self.running = True
            self.pid += 1
        elif args[0] == 'stop':
            self.running = False
        return str(self.pid if self.running else 0)

    def identity(self, pid):
        path = self.root / 'current'
        return (path / 'bin/robotd').resolve(), self.argv or [str(path / 'bin/robotd'), '--fake', '--no-policy',
            '--params', str(path / 'robotd.toml'), '--socket', str(self.root / 'run/robotd.sock')]

    def probe(self, path):
        assert path == self.root / 'run/robotd.sock'
        version = (self.root / 'current').resolve().name
        return (dict(api_version=self.api or dry.APIS[version], daemon_version=version,
                     revision=self.revision or 'revision-' + version),
                dict(joint_names=dry.daemon_adapter.JOINT_NAMES),
                dict(healthy=self.running and version not in self.bad),
                self.samples if self.samples is not None else [sample(100), sample(200), sample(300)])


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        root = self.base / 'rehearsal'
        self.board = FakeBoard(root)
        self.app = dry.Rehearsal(root, self.base / 'unit.service', self.board)
        self.source = self.base / 'source'
        self.source.mkdir()
        header = b'\x7fELF\x02\x01' + b'\0' * 12 + b'\xb7\0'
        for name in ('robotd', 'robotctl'):
            (self.source / name).write_bytes(header + name.encode())
        self.hashes = {name: dry.digest(self.source / name) for name in ('robotd', 'robotctl')}
        self.app.init()

    def stage(self, version='0.16.3'):
        return self.app.stage(version, self.source, self.hashes, 'revision-' + version)

    def switch(self, version):
        with self.app.locked():
            return self.app.switch(version)

    def test_full_cycle_verifies_all_versions_and_only_test_service(self):
        for version in dry.APIS:
            self.stage(version)
        with self.app.locked():
            result = self.app.cycle()
        self.assertTrue(result['ok'])
        self.assertEqual([x['version'] for x in result['steps']], dry.CYCLE)
        self.assertEqual(self.app.state(), dict(current='0.16.3', previous='0.17.9', pending=None))
        self.assertFalse(any('robotd.service' in call or 'mediad' in call for call in self.board.calls))
        self.assertTrue(all(call[0] == 'daemon-reload' or call[1] == dry.SERVICE for call in self.board.calls))
        reports = list((self.app.root / 'evidence').glob('*.json'))
        self.assertEqual(len(reports), 5)
        self.assertTrue(all(not dry.read_json(p)['hardware_acceptance'] for p in reports))

    def test_failed_switch_automatically_restores_verified_previous(self):
        self.stage(); self.stage('0.17.9')
        self.assertTrue(self.switch('0.16.3')['ok'])
        before = self.app.state()
        self.board.bad.add('0.17.9')
        result = self.switch('0.17.9')
        self.assertFalse(result['ok'])
        self.assertEqual(result['restored']['version'], '0.16.3')
        self.assertEqual(self.app.state(), before)
        self.assertTrue(self.board.running)

    def test_cli_failed_upgrade_remains_nonzero_after_successful_recovery(self):
        self.stage(); self.stage('0.18.3'); self.switch('0.16.3')
        self.board.bad.add('0.18.3')
        output = io.StringIO()
        with patch.object(dry, 'Rehearsal', return_value=self.app), contextlib.redirect_stdout(output):
            code = dry.main(['switch', '--version', '0.18.3', '--apply'])
        self.assertEqual(code, 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report['ok'])
        self.assertEqual(report['restored']['version'], '0.16.3')
        self.assertTrue(Path(report['evidence']).exists())

    def test_cli_stage_preview_reads_files_without_mutating_layout(self):
        before = set((self.app.root / 'versions').iterdir())
        with patch.object(dry, 'Rehearsal', return_value=self.app), contextlib.redirect_stdout(io.StringIO()):
            code = dry.main(['stage', '--version', '0.18.3', '--bin-dir', str(self.source),
                '--robotd-sha256', self.hashes['robotd'], '--robotctl-sha256', self.hashes['robotctl'], '--revision', 'rev'])
        self.assertEqual(code, 0)
        self.assertEqual(before, set((self.app.root / 'versions').iterdir()))

    def test_first_boot_failure_leaves_no_selected_running_version(self):
        self.stage(); self.board.bad.add('0.16.3')
        result = self.switch('0.16.3')
        self.assertFalse(result['ok'])
        self.assertTrue(result['restored']['stopped'])
        self.assertIsNone(self.app.current_link())
        self.assertIsNone(self.app.state()['pending'])
        self.assertFalse(self.board.running)

    def test_rollback_and_repeated_switch_do_not_erase_previous(self):
        self.stage(); self.stage('0.18.3')
        self.switch('0.16.3'); self.switch('0.18.3'); self.switch('0.18.3')
        self.assertEqual(self.app.state()['previous'], '0.16.3')
        self.assertTrue(self.app.rollback()['ok'])
        self.assertEqual(self.app.state()['current'], '0.16.3')

    def test_interrupted_transition_requires_explicit_recovery(self):
        self.stage(); self.stage('0.18.3'); self.switch('0.16.3')
        state = self.app.state()
        self.app.save(dict(state, pending={'from': '0.16.3', 'to': '0.18.3'}))
        self.app.select('0.18.3')
        with self.assertRaisesRegex(dry.Error, 'Interrupted'):
            self.switch('0.18.3')
        result = self.app.rollback()
        self.assertTrue(result['ok'])
        self.assertEqual(self.app.current_link(), '0.16.3')
        self.assertIsNone(self.app.state()['pending'])

    def test_failed_recovery_keeps_pending_transaction(self):
        self.stage(); self.stage('0.18.3'); self.switch('0.16.3')
        self.board.bad.update(dry.APIS)
        result = self.switch('0.18.3')
        self.assertFalse(result['ok']); self.assertTrue(result['pending'])
        self.assertIsNotNone(self.app.state()['pending'])
        self.board.bad.clear()
        self.assertTrue(self.app.rollback()['ok'])
        self.assertEqual(self.app.state()['current'], '0.16.3')

    def test_unprepared_cycle_does_not_stop_service(self):
        self.stage(); self.switch('0.16.3')
        before = list(self.board.calls)
        with self.assertRaises((dry.Error, FileNotFoundError)):
            self.app.cycle()
        self.assertEqual(self.board.calls, before)
        self.assertTrue(self.board.running)

    def test_hash_and_architecture_checks_before_staging(self):
        with self.assertRaisesRegex(dry.Error, 'SHA256'):
            self.app.stage('0.16.3', self.source, dict(self.hashes, robotd='0' * 64), 'rev')
        (self.source / 'robotd').write_bytes(b'wrong architecture')
        hashes = dict(self.hashes, robotd=dry.digest(self.source / 'robotd'))
        with self.assertRaisesRegex(dry.Error, 'AArch64'):
            self.app.stage('0.16.3', self.source, hashes, 'rev')
        self.assertEqual(list((self.app.root / 'versions').iterdir()), [])

    def test_staged_corruption_and_overwrite_refused_before_stop(self):
        self.stage(); self.switch('0.16.3')
        with self.assertRaisesRegex(dry.Error, 'immutable'):
            self.stage()
        (self.app.root / 'versions/0.16.3/bin/robotd').write_bytes(b'corrupted')
        before = list(self.board.calls)
        with self.assertRaisesRegex(dry.Error, 'SHA256'):
            self.switch('0.16.3')
        self.assertEqual(self.board.calls, before)

    def test_unmanaged_paths_units_and_dropins_refused(self):
        with self.assertRaises(dry.Error): self.app.init()
        self.board.overrides = True
        with self.assertRaisesRegex(dry.Error, 'drop-in'):
            with self.app.locked(): pass
        self.board.overrides = False
        self.app.unit.write_text('ExecStart=real hardware')
        with self.assertRaisesRegex(dry.Error, 'unit differs'):
            with self.app.locked(): pass

    def test_symlink_escape_and_concurrent_operations_refused(self):
        (self.app.root / 'current').symlink_to('/opt/robot/daemon/current')
        with self.assertRaisesRegex(dry.Error, 'outside'):
            self.app.current_link()
        with self.app.locked():
            with self.assertRaisesRegex(dry.Error, 'in progress'):
                with self.app.locked(): pass

    def test_no_history_rollback_is_refused(self):
        with self.assertRaisesRegex(dry.Error, 'previously verified'):
            self.app.rollback()

    def test_verify_rejects_wrong_api_revision_policy_and_timestamps(self):
        self.stage(); self.switch('0.16.3')
        variants = [('api', 47), ('revision', 'wrong'), ('samples', [sample(100)] * 3),
                    ('samples', [dict(sample(1), joints=[0.] * 14), sample(2), sample(3)]),
                    ('samples', [dict(sample(1), policy='walk'), sample(2), sample(3)]),
                    ('samples', [dict(sample(1), targets=[float('nan')] * 15), sample(2), sample(3)]),
                    ('argv', ['robotd', '--no-policy'])]
        for attr, value in variants:
            with self.subTest(attr=attr, value=value):
                old = getattr(self.board, attr)
                setattr(self.board, attr, value)
                with self.assertRaises(dry.Error): self.app.verify('0.16.3')
                setattr(self.board, attr, old)

    def test_web_command_is_socket_pinned_and_only_printed(self):
        self.stage(); self.switch('0.16.3')
        before = list(self.board.calls)
        argv = self.app.web_command()
        self.assertEqual(argv[argv.index('--robot-socket') + 1], str(self.app.root / 'run/robotd.sock'))
        self.assertEqual(argv[argv.index('--robotctl') + 1], str(self.app.root / 'robotctl-dryrun'))
        for forbidden in ('--allow-inactive', '--enable-camera-apply', '--demo'):
            self.assertNotIn(forbidden, argv)
        self.assertEqual(before, self.board.calls)


class CommandAndWireTests(unittest.TestCase):
    def test_all_mutations_preview_without_touching_board(self):
        commands = [['init'], ['switch', '--version', '0.18.3'], ['rollback'], ['cycle'], ['stop']]
        for command in commands:
            with patch.object(dry.Board, 'require', side_effect=AssertionError('board access')):
                output = io.StringIO()
                with contextlib.redirect_stdout(output): self.assertEqual(dry.main(command), 0)
                self.assertTrue(json.loads(output.getvalue())['preview'])

    def test_apply_is_rejected_off_board(self):
        with patch.object(dry.platform, 'system', return_value='Darwin'):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(dry.main(['init', '--apply']), 1)

    def test_probe_sends_only_read_requests_and_bounds_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ipc.sock'
            listener = socket.socket(socket.AF_UNIX)
            self.addCleanup(listener.close)
            listener.bind(str(path)); listener.listen(1)
            seen = []; errors = []
            def server():
                try:
                    conn, _ = listener.accept()
                    with conn, conn.makefile('rwb') as file:
                        for value in ({'api_version': 47}, {'joint_names': []}, {'healthy': True}):
                            request = json.loads(file.readline()); seen.append(request['method'])
                            file.write((json.dumps(dict(jsonrpc='2.0', id=request['id'], result=value)) + '\n').encode()); file.flush()
                        request = json.loads(file.readline()); seen.append(request['method'])
                        for t in (1, 2, 3):
                            file.write((json.dumps(dict(method='robot.state', params=sample(t))) + '\n').encode()); file.flush()
                except Exception as error: errors.append(error)
            thread = threading.Thread(target=server, daemon=True); thread.start()
            hello, model, health, samples = dry.Board().probe(path)
            thread.join(3)
            self.assertFalse(errors); self.assertFalse(thread.is_alive())
            self.assertEqual(seen, ['hello', 'robot.model', 'robot.health', 'robot.subscribe'])
            self.assertEqual([s['t_ns'] for s in samples], [1, 2, 3])


if __name__ == '__main__': unittest.main()
