import http.cookiejar
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from servo_workbench.service import Controller
from servo_workbench.server import Server, PREFIX
from servo_workbench.store import Store
from servo_workbench.engine import Engine
from servo_workbench.simulation import SimBus
from servo_workbench.profiles import Refused
from servo_workbench.reports import validate
from servo_workbench.remote import Remote
from servo_workbench.agent import serve_stdio


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(self.tmp.name)
        self.server = Server(('127.0.0.1', 0), self.controller)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.opener, self.auth = self.browser()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.controller.close()
        self.tmp.cleanup()

    def browser(self):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        opener.open(self.origin + '/', timeout=2).close()
        with opener.open(self.origin + PREFIX + 'session', timeout=2) as response:
            return opener, json.load(response)

    def call(self, name, body=None, opener=None, auth=None, origin=None):
        headers = {'Origin': self.origin if origin is None else origin,
                   'Content-Type': 'application/json',
                   'X-Workbench-CSRF': (auth or self.auth)['csrf']}
        request = urllib.request.Request(self.origin + PREFIX + name,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers=headers)
        with (opener or self.opener).open(request, timeout=3) as response:
            return json.load(response)

    def create(self):
        return self.call('session-create', dict(kind='hd1910', layout='xgo'))

    def test_default_has_no_native_bus_or_serial_import(self):
        self.assertIsNone(self.controller.engine)
        self.assertTrue(self.call('status')['demo'])

    def test_cross_origin_write_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call('session-create', {}, origin='https://elsewhere.invalid')
        self.assertEqual(error.exception.code, 403)

    def test_missing_csrf_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call('session-create', {}, auth={'csrf': 'invalid-test-value'})
        self.assertEqual(error.exception.code, 403)

    def test_host_rebinding_rejected(self):
        request = urllib.request.Request(self.origin + '/', headers={'Host': 'elsewhere.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.opener.open(request)
        self.assertEqual(error.exception.code, 403)

    def test_page_static_and_headers(self):
        with self.opener.open(self.origin + '/') as response:
            self.assertIn('frame-ancestors', response.headers['Content-Security-Policy'])
            self.assertIn('进度一直在', response.read().decode())
        for asset in ('app.js', 'style.css'):
            with self.opener.open(self.origin + '/' + asset) as response:
                self.assertGreater(len(response.read()), 100)

    def test_two_clients_read_but_only_one_writes(self):
        self.create()
        other, auth = self.browser()
        self.assertTrue(self.call('status', opener=other, auth=auth)['id'])
        with self.assertRaises(urllib.error.HTTPError):
            self.call('claim', {}, other, auth)

    def test_refresh_cookie_retains_owner_and_batch(self):
        original = self.create()
        self.opener.open(self.origin + '/').close()
        after = self.call('status')
        self.assertEqual(after['id'], original['id'])
        self.assertTrue(after['writer_owned'])

    def test_simulated_device_comes_back_without_write(self):
        self.create()
        self.call('simulation', dict(op='attach', label='first'))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.call('status')['connection_state'] == 'ready':
                break
            time.sleep(.1)
        self.assertEqual(self.call('status')['connection_state'], 'ready')
        self.assertEqual(self.controller.engine.bus.sent, [])
        self.call('simulation', dict(op='usb-off'))
        self.call('detect', {})
        self.assertEqual(self.call('status')['connection_state'], 'waiting')
        self.call('simulation', dict(op='usb-on'))
        for _ in range(3):
            self.call('detect', {}); time.sleep(.26)
        self.assertEqual(self.call('status')['connection_state'], 'ready')
        self.assertEqual(self.controller.engine.bus.sent, [])

    def test_reports_historical_and_no_path_traversal(self):
        self.create()
        report = self.call('report')
        report['hardware_acceptance'] = True
        report['live'] = True
        result = self.call('report-import', dict(report=report))
        loaded = self.call('report-get?id=' + result['id'])
        self.assertFalse(loaded['hardware_acceptance'])
        self.assertFalse(loaded['live'])
        with self.assertRaises(urllib.error.HTTPError):
            self.call('report-get?id=../../anything')

    def test_arbitrary_native_method_not_exposed(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call('serial-write', dict(address=40, value=1))
        self.assertEqual(error.exception.code, 404)

    def test_events_are_incremental(self):
        self.create()
        events = self.call('events?after=0')
        self.assertTrue(events)
        self.assertEqual(self.call('events?after=' + str(events[-1]['seq'])), [])

    def test_preview_is_backup_only_and_never_writes(self):
        self.create()
        self.call('simulation',dict(op='attach',label='preview-only'))
        for _ in range(3):self.call('detect',{});time.sleep(.26)
        result=self.call('plan',dict(label='physical-label',model_confirmed=True))
        self.assertTrue(result['plan']['changes'])
        self.assertIsNotNone(self.call('status')['slots'][0]['backup'])
        self.assertEqual(self.controller.engine.bus.sent,[])


class PersistenceTests(unittest.TestCase):
    def test_real_abrupt_process_exit_preserves_intent(self):
        source = '''import os,sys
from servo_workbench.engine import Engine
from servo_workbench.store import Store
from servo_workbench.simulation import SimBus
b=SimBus();b.attach('A');clock=[0.]
e=Engine(Store(sys.argv[1]),b,clock=lambda:clock[0]);e.create('hd1910')
for t in (0,.3,.6):clock[0]=t;e.tick(True)
p=e.plan(label='actual-physical-label',model_confirmed=True)
e.fault_hook=lambda stage:os._exit(19) if stage=='after_intent' else None
e.apply(dict(job_id='abrupt',revision=p['revision'],connection_epoch=e.status()['connection_epoch'],plan_hash=p['plan']['hash']))
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', '-c', source, directory])
            self.assertEqual(result.returncode, 19)
            store = Store(directory)
            try:
                self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                bus = SimBus(); bus.attach('A')
                engine = Engine(store, bus)
                self.assertEqual(engine.status()['phase'], 'interrupted')
                engine.tick(True)
                self.assertEqual(bus.sent, [])
                self.assertEqual(store.job('abrupt')['steps'][0]['state'], 'intent')
            finally:
                store.close()

    def test_demo_devices_and_batch_survive_backend_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Controller(directory)
            first.rpc('session-create', dict(kind='hd1910'))
            first.rpc('simulation', dict(op='attach', label='saved'))
            ident = first.engine.active
            first.close()
            second = Controller(directory)
            try:
                self.assertEqual(second.engine.active, ident)
                self.assertEqual(second.engine.bus.attached, ['saved'])
                self.assertIsNone(second.engine.status()['plan'])
                self.assertEqual(second.engine.bus.sent, [])
            finally:
                second.close()

    def test_switch_profile_never_reuses_foreign_active_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Controller(directory)
            try:
                one = c.rpc('session-create', dict(kind='xl330'))
                two = c.rpc('session-create', dict(kind='hd1910'))
                c.rpc('session-open', {'id': one['id']})
                self.assertEqual(c.engine.bus.kind, 'xl330')
                self.assertEqual(c.engine.status()['kind'], 'xl330')
            finally:
                c.close()

    def test_ssh_host_cannot_inject_arguments(self):
        for host in ('-oProxyCommand=bad', 'host;command', 'user@host extra'):
            with self.assertRaises(Refused):
                Remote(host)

    def test_agent_allows_only_reviewed_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            source = io.StringIO('\n'.join(json.dumps(dict(id=str(i), method=m,
                                    owner='test', params={})) for i, m in
                                    enumerate(('hello', 'arbitrary-shell', 'simulation'))) + '\n')
            output = io.StringIO()
            serve_stdio(controller, 'hd1910', source, output)
            rows = {x['id']: x for x in map(json.loads, output.getvalue().splitlines())}
            self.assertTrue(rows['0']['ok'])
            self.assertFalse(rows['1']['ok'])
            self.assertFalse(rows['2']['ok'])


if __name__ == '__main__':
    unittest.main()
