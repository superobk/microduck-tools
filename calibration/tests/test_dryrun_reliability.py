"""Fault injection for transactional recovery and production isolation; no board access."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_dryrun as fixtures

dry = fixtures.dry


class Abrupt(BaseException):
    """Simulate interruption outside switch's automatic exception recovery."""


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.RehearsalTests.setUp
    stage = fixtures.RehearsalTests.stage
    switch = fixtures.RehearsalTests.switch

    def prepare(self):
        for version in dry.APIS:
            self.stage(version)
        self.assertTrue(self.switch('0.16.3')['ok'])
        self.assertTrue(self.switch('0.17.9')['ok'])

    def test_stale_state_and_link_temporaries_do_not_block_rollback(self):
        self.prepare()
        (self.app.root / '.state.json.tmp').write_text('partial old writer')
        (self.app.root / '.state.json-crashed').write_text('partial new writer')
        (self.app.root / '.current-next').symlink_to('versions/0.18.3')
        (self.app.root / '.current-crashed').symlink_to('versions/0.18.3')
        self.assertTrue(self.app.rollback()['ok'])
        self.assertEqual(self.app.current_link(), '0.16.3')

    def test_atomic_write_failure_preserves_old_state_and_allows_retry(self):
        before = self.app.state()
        with patch.object(dry.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.app.save(dict(before, current='0.16.3'))
        self.assertEqual(self.app.state(), before)
        self.assertEqual(list(self.app.root.glob('.state.json-*')), [])
        self.app.save(before)

    def test_real_sigkill_during_json_replace_does_not_poison_next_write(self):
        # A real child process is killed after creating/flushing a temporary, before rename.
        script = f'''
import importlib.util,os,signal
from pathlib import Path
s=importlib.util.spec_from_file_location('dry', {str(dry.PACKAGE / 'tools/microduck-dryrun.py')!r})
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.os.replace=lambda *a:os.kill(os.getpid(),signal.SIGKILL)
m.write_json(Path({str(self.app.root / 'state.json')!r}), {{'incomplete':True}})
'''
        before = self.app.state()
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertEqual(self.app.state(), before)
        self.assertTrue(list(self.app.root.glob('.state.json-*')))
        self.app.save(before)
        self.assertEqual(self.app.state(), before)

    def test_real_sigkill_between_link_creation_and_replace_is_recoverable(self):
        self.prepare()
        state = self.app.state()
        self.app.save(dict(state, pending={'from': '0.17.9', 'to': '0.18.3'}))
        script = f'''
import importlib.util,os,signal
s=importlib.util.spec_from_file_location('dry', {str(dry.PACKAGE / 'tools/microduck-dryrun.py')!r})
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.os.replace=lambda *a:os.kill(os.getpid(),signal.SIGKILL)
m.Rehearsal(root={str(self.app.root)!r}).select('0.18.3')
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertTrue(list(self.app.root.glob('.current-*')))
        self.assertTrue(self.app.rollback()['ok'])
        self.assertEqual(self.app.state(), state)

    @contextlib.contextmanager
    def cut(self, step, when, exception=Abrupt):
        name = 'save' if step == 'journal' else step
        method = getattr(self.app, name) if step != 'stop-job' else self.board.systemctl
        triggered = False
        def wrapped(*args, **kwargs):
            nonlocal triggered
            matched = not triggered
            if step == 'stop-job': matched = matched and args[0] == 'stop'
            if step == 'save': matched = matched and args[0]['pending'] is None
            if step == 'journal': matched = matched and args[0]['pending'] is not None
            if matched and when == 'before':
                triggered = True
                raise exception()
            result = method(*args, **kwargs)
            if matched and when == 'after':
                triggered = True
                raise exception()
            return result
        target, name = (self.board, 'systemctl') if step == 'stop-job' else (self.app, name)
        with patch.object(target, name, side_effect=wrapped):
            yield
        self.assertTrue(triggered)

    def test_switch_interruptions_at_each_mutation_boundary(self):
        self.prepare()
        for step in ('journal', 'stop-job', 'select', 'start', 'verify', 'save'):
            for when in ('before', 'after'):
                with self.subTest(step=step, when=when):
                    # Each attempt begins at the same two successful versions.
                    if self.app.state()['current'] != '0.17.9':
                        self.assertTrue(self.switch('0.17.9')['ok'])
                    with self.cut(step, when):
                        with self.assertRaises(Abrupt): self.switch('0.18.3')
                    state = self.app.state()
                    if state['pending']:
                        self.assertTrue(self.app.rollback()['ok'])
                        self.assertEqual(self.app.current_link(), '0.17.9')
                    else:
                        expected = '0.17.9' if step == 'journal' and when == 'before' else '0.18.3'
                        self.assertEqual(state['current'], expected)
                        self.assertTrue(self.app.verify(expected)['ok'])

    def test_recovery_interruptions_are_idempotent_at_each_boundary(self):
        self.prepare()
        for step in ('stop-job', 'select', 'start', 'verify', 'save'):
            for when in ('before', 'after'):
                with self.subTest(step=step, when=when):
                    state = self.app.state()
                    self.app.save(dict(state, pending={'from': '0.17.9', 'to': '0.18.3'}))
                    self.app.select('0.18.3')
                    with self.cut(step, when):
                        with self.assertRaises(Abrupt): self.app.rollback()
                    if self.app.state()['pending']:
                        self.assertTrue(self.app.rollback()['ok'])
                    self.assertEqual(self.app.state(), state)
                    self.assertEqual(self.app.current_link(), '0.17.9')
                    self.assertTrue(self.app.verify('0.17.9')['ok'])

    def test_unconfirmed_stop_never_changes_current_or_starts_target(self):
        self.prepare(); self.board.stop_lingers = True
        before = list(self.board.calls)
        result = self.switch('0.18.3')
        self.assertFalse(result['ok']); self.assertTrue(result['pending'])
        self.assertEqual(self.app.current_link(), '0.17.9')
        self.assertFalse(any(c[0] == 'start' for c in self.board.calls[len(before):]))
        self.board.stop_lingers = False
        self.assertTrue(self.app.rollback()['ok'])

    def test_stop_timeout_with_pending_job_requires_explicit_later_recovery(self):
        self.prepare()
        original = self.board.systemctl
        stops = []
        def timeout(*args):
            if args[0] == 'stop':
                stops.append(args); self.board.job = '42 /org/test/job/42'
                raise subprocess.TimeoutExpired('systemctl', 20)
            return original(*args)
        with patch.object(self.board, 'systemctl', side_effect=timeout):
            result = self.switch('0.18.3')
        self.assertFalse(result['ok']); self.assertTrue(result['pending'])
        self.assertEqual(len(stops), 1)
        self.assertEqual(self.app.current_link(), '0.17.9')
        self.board.job = '0'
        self.assertTrue(self.app.rollback()['ok'])

    def test_start_timeout_does_not_replace_pending_manager_job(self):
        self.prepare(); original = self.board.systemctl
        def timeout(*args):
            value = original(*args)
            if args[0] == 'start':
                self.board.job = '43 /org/test/job/43'
                raise subprocess.TimeoutExpired('systemctl', 20)
            return value
        with patch.object(self.board, 'systemctl', side_effect=timeout):
            result = self.switch('0.18.3')
        self.assertFalse(result['ok']); self.assertTrue(result['pending'])
        self.assertEqual(self.app.current_link(), '0.18.3')
        self.board.job = '0'
        self.assertTrue(self.app.rollback()['ok'])
        self.assertEqual(self.app.current_link(), '0.17.9')

    def test_direct_stop_refuses_foreign_process_and_control_process(self):
        self.prepare(); self.board.argv = ['robotd', '--params', '/etc/robot/robotd.toml']
        before = list(self.board.calls)
        with self.assertRaisesRegex(dry.Error, 'Refusing to stop'): self.app.stop()
        self.assertFalse(any(c[0] == 'stop' for c in self.board.calls[len(before):]))
        self.board.argv = None; self.board.control_pid = '55'
        with self.assertRaisesRegex(dry.Error, 'fully stop'): self.app.stop()

    def test_disk_failure_after_success_is_reported_without_hidden_rollback(self):
        self.prepare(); output = io.StringIO()
        with patch.object(dry, 'Rehearsal', return_value=self.app), patch.object(self.app, 'record', side_effect=OSError('disk full')):
            with contextlib.redirect_stdout(output): code = dry.main(['switch', '--version', '0.18.3', '--apply'])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1); self.assertTrue(report['execution_ok'])
        self.assertIn('disk full', report['evidence_error'])
        self.assertEqual(self.app.state()['current'], '0.18.3')
        self.assertIsNone(self.app.state()['pending'])

    def test_cycle_stops_if_evidence_cannot_be_saved(self):
        self.prepare()
        with patch.object(self.app, 'record', side_effect=OSError('disk full')):
            result = self.app.cycle()
        self.assertFalse(result['ok']); self.assertEqual(len(result['steps']), 1)
        self.assertEqual(self.app.state()['current'], '0.16.3')

    def test_keyboard_interrupt_returns_130_and_preserves_pending(self):
        self.prepare(); output = io.StringIO()
        with patch.object(dry, 'Rehearsal', return_value=self.app), self.cut('select', 'after', KeyboardInterrupt):
            with contextlib.redirect_stderr(output): code = dry.main(['switch', '--version', '0.18.3', '--apply'])
        self.assertEqual(code, 130); self.assertTrue(json.loads(output.getvalue())['interrupted'])
        self.assertIsNotNone(self.app.state()['pending'])
        self.assertTrue(self.app.rollback()['ok'])

    def test_malformed_state_and_inconsistent_pending_are_rejected(self):
        before = self.app.state()
        for value in ([], None, dict(before, current=[]),
                      dict(before, pending={'from': '0.16.3', 'to': '0.18.3'})):
            with self.subTest(value=value):
                dry.write_json(self.app.root / 'state.json', value)
                with self.assertRaises(dry.Error): self.app.state()
        self.app.save(before)

    def test_pending_prevents_web_command_and_service_actions_use_fail_job_mode(self):
        self.prepare()
        self.assertTrue(all('--job-mode=fail' in c for c in self.board.calls if c[0] in ('start', 'stop')))
        state = self.app.state(); self.app.save(dict(state, pending={'from': '0.17.9', 'to': '0.18.3'}))
        with self.assertRaisesRegex(dry.Error, 'Interrupted'): self.app.web_command()

    def test_unrelated_selected_version_cannot_be_silently_recovered(self):
        self.prepare(); state = self.app.state()
        self.app.save(dict(state, pending={'from': '0.17.9', 'to': '0.18.3'}))
        self.app.select('0.16.3'); before = list(self.board.calls)
        with self.assertRaisesRegex(dry.Error, 'outside'): self.app.rollback()
        self.assertFalse(any(c[0] in ('start', 'stop') for c in self.board.calls[len(before):]))

    def test_group_writable_release_or_layout_is_rejected_before_stop(self):
        self.prepare(); path = self.app.root / 'versions/0.18.3/robotd.toml'
        path.chmod(0o666); before = list(self.board.calls)
        with self.assertRaisesRegex(dry.Error, 'writable'): self.switch('0.18.3')
        self.assertFalse(any(c[0] in ('start', 'stop') for c in self.board.calls[len(before):]))
        path.chmod(0o644); self.app.root.chmod(0o777)
        with self.assertRaisesRegex(dry.Error, 'writable'):
            with self.app.locked(): pass
        self.app.root.chmod(0o755)

    def test_explicit_directory_modes_survive_restrictive_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'new'; board = fixtures.FakeBoard(root)
            app = dry.Rehearsal(root, Path(tmp) / 'unit', board)
            original = os.umask(0o077)
            try:
                app.init()
                app.stage('0.16.3', self.source, self.hashes, 'revision-0.16.3')
            finally: os.umask(original)
            for path in (root, root/'versions', root/'versions/0.16.3', root/'versions/0.16.3/bin'):
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual((root/'versions/0.16.3/robotd.toml').stat().st_mode & 0o777, 0o644)

    def test_init_refuses_a_loaded_alias_before_creating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'new'; board = fixtures.FakeBoard(root)
            app = dry.Rehearsal(root, Path(tmp) / 'unit', board)
            with patch.object(board, 'absent', side_effect=dry.Error('name is an alias')):
                with self.assertRaises(dry.Error): app.init()
            self.assertFalse(root.exists()); self.assertFalse(app.unit.exists())

    def test_recovery_stops_failed_target_even_if_fallback_became_corrupt(self):
        self.prepare(); original = self.board.probe
        fallback = self.app.root/'versions/0.17.9/bin/robotd'
        saved = fallback.read_bytes()
        def probe(path, pid):
            result = original(path, pid)
            if self.app.current_link() == '0.18.3':
                fallback.write_bytes(b'corrupted during target verification')
                return result[0], result[1], {'healthy': False}, result[3]
            return result
        with patch.object(self.board, 'probe', side_effect=probe): result = self.switch('0.18.3')
        self.assertFalse(result['ok']); self.assertTrue(result['pending']); self.assertFalse(self.board.running)
        fallback.write_bytes(saved)
        self.assertTrue(self.app.rollback()['ok'])

    def test_failed_daemon_reload_during_init_never_starts_any_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'new'; board = fixtures.FakeBoard(root)
            app = dry.Rehearsal(root, Path(tmp)/'unit', board)
            with patch.object(board, 'systemctl', side_effect=subprocess.CalledProcessError(1, 'daemon-reload')) as calls:
                with self.assertRaises(subprocess.CalledProcessError): app.init()
                self.assertEqual(calls.call_args_list[0].args, ('daemon-reload',))
                self.assertEqual(calls.call_count, 1)
            self.assertTrue(root.exists())
            with self.assertRaises(dry.Error): app.init()


def unit_properties(unit):
    values = dict(Id=dry.SERVICE, Names=dry.SERVICE, FragmentPath=str(unit), DropInPaths='',
                  NeedDaemonReload='no', LoadState='loaded', Transient='no', User=dry.USER, Group=dry.USER,
                  Slice='system.slice', KillMode='control-group', Restart='no', PrivateDevices='yes',
                  ProtectSystem='strict', ProtectHome='yes', FailureAction='none', SuccessAction='none',
                  JobTimeoutAction='none', StartLimitAction='none', Requires='sysinit.target system.slice',
                  Conflicts='shutdown.target', ConflictedBy='', Upholds='', UpheldBy='')
    values.update({key: '' for key in dry.EMPTY_RELATIONS})
    return values


class IsolationAndWireTests(unittest.TestCase):
    def test_manager_absence_preflight_refuses_existing_service_or_alias(self):
        board = dry.Board()
        with patch.object(board, 'properties', return_value=dict(LoadState='not-found', FragmentPath='', Names=dry.SERVICE)):
            board.absent()
        for values in (dict(LoadState='loaded', FragmentPath='/lib/systemd/system/robotd.service', Names=dry.SERVICE),
                       dict(LoadState='not-found', FragmentPath='', Names='robotd.service ' + dry.SERVICE)):
            with patch.object(board, 'properties', return_value=values):
                with self.assertRaises(dry.Error): board.absent()

    def test_wrapper_allows_only_exact_web_monitor_and_preserves_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); client = root / 'client'
            client.write_text('#!/usr/bin/env python3\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\n'); client.chmod(0o755)
            wrapper = root / 'wrapper'
            wrapper.write_text((dry.PACKAGE/'deploy/robotctl-dryrun').read_text().replace(
                '/var/lib/microduck-tools-dryrun/current/bin/robotctl', str(client)))
            wrapper.chmod(0o755)
            allowed = subprocess.run([str(wrapper), 'monitor', '--hz', '5', '--json'], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(allowed.stdout), ['--robot-socket', '/var/lib/microduck-tools-dryrun/run/robotd.sock',
                                                       'monitor', '--hz', '5', '--json'])
            for args in ([], ['apply'], ['robot', 'init'], ['health', '--json'],
                         ['monitor', '--hz', '5', '--json', '--robot-socket', '/run/robotd.sock']):
                with self.subTest(args=args):
                    result = subprocess.run([str(wrapper), *args], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2); self.assertEqual(result.stdout, '')

    def test_loaded_alias_stale_manager_and_cross_service_edges_refused(self):
        board = dry.Board(); unit = Path('/test/unit')
        good = unit_properties(unit)
        variants = [(key, 'robotd.service') for key in dry.EMPTY_RELATIONS]
        variants += [('Names', dry.SERVICE + ' robotd.service'), ('NeedDaemonReload', 'yes'),
                     ('Conflicts', 'robotd.service'), ('ConflictedBy', 'robotd.service'), ('Requires', 'robotd.service'),
                     ('User', 'root'), ('PrivateDevices', 'no'), ('KillMode', 'process'),
                     ('Upholds', 'mediad.service'), ('UpheldBy', 'robotd.service')]
        with patch.object(board, 'properties', return_value=good): board.unit_identity(unit)
        for key, value in variants:
            with self.subTest(key=key):
                with patch.object(board, 'properties', return_value=dict(good, **{key: value})):
                    with self.assertRaises(dry.Error): board.unit_identity(unit)

    def test_systemd_property_reader_rejects_missing_required_and_duplicate_properties(self):
        board = dry.Board()
        with patch.object(board, 'systemctl', return_value='Job=0\n'):
            self.assertEqual(board.properties('Job', optional=('Future',)), {'Job': '0'})
            with self.assertRaises(dry.Error): board.properties('Job', 'MainPID')
        with patch.object(board, 'systemctl', return_value='Job=0\nJob=12\n'):
            with self.assertRaises(dry.Error): board.properties('Job')

    def test_linux_peer_credentials_decode_pid(self):
        class Sock:
            def getsockopt(self, *args): return struct.pack('3i', 1234, 1000, 1000)
        with patch.object(dry.socket, 'SO_PEERCRED', 17, create=True):
            self.assertEqual(dry.Board().peer_pid(Sock()), 1234)

    def test_socket_symlink_is_refused_before_any_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'socket'; path.symlink_to('/run/robotd.sock')
            # lstat of a dangling symlink succeeds and is never followed.
            with patch.object(dry.socket, 'socket', side_effect=AssertionError('must not connect')):
                with self.assertRaisesRegex(dry.Error, 'symlinks'): dry.Board().probe(path, 1234)

    def test_wrong_peer_receives_no_rpc(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='mdpeer-') as tmp:
            path = Path(tmp) / 'socket'
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path)); listener.listen(1); received = []
                def server():
                    conn, _ = listener.accept()
                    with conn: received.append(conn.recv(1024))
                thread = threading.Thread(target=server, daemon=True); thread.start()
                with patch.object(dry.Board, 'peer_pid', return_value=999):
                    with self.assertRaisesRegex(dry.Error, 'peer'): dry.Board().probe(path, 1234)
                thread.join(2)
                self.assertFalse(thread.is_alive()); self.assertEqual(received, [b''])

    def test_trickled_partial_lines_have_an_absolute_deadline(self):
        class Sock:
            def settimeout(self, value): pass
            def recv(self, size): return b'x'
        with patch.object(dry.time, 'monotonic', side_effect=[0, .1, .5, 1, 1.5, 2.1]):
            stream = dry.DeadlineStream(Sock(), timeout=2)
            with self.assertRaisesRegex(dry.Error, 'deadline'): stream.readline(262145)


if __name__ == '__main__': unittest.main()
