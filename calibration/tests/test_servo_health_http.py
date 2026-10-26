import argparse
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'integration-0.16.3/web'))
import calibration_web as web
from test_servo_health import report


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();d=Path(self.temp.name);self.config=d/'robotd.toml';self.config.write_text('[media]\n')
        a=web.parser().parse_args(['--target','daemon-v0.16.3','--enable-servo-health','--no-telemetry',
            '--config',str(self.config),'--dest',str(d/'cal.toml'),'--runtime',str(d/'runtime.json'),'--servo-health-dir',str(d/'observer')])
        self.ctx=web.Context(a);self.server=web.ThreadingHTTPServer(('127.0.0.1',0),web.new_handler(self.ctx))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.ctx.monitor.close();self.ctx.servo.close();self.temp.cleanup()

    def call(self,route,body=None,token=True,method=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=4)
        headers={'Content-Type':'application/json'}
        if token:headers['X-Calibration-CSRF']=self.ctx.csrf
        c.request(method or ('GET' if body is None else 'POST'),route,None if body is None else json.dumps(body),headers)
        r=c.getresponse();data=r.read();status=r.status;c.close();return status,data

    def test_routes_export_csrf_and_no_config_changes(self):
        self.assertEqual(web.servo_health.IDS,list(web.cal.JOINT_IDS))
        self.assertEqual(web.servo_health.NAMES,list(web.cal.JOINT_NAMES))
        before=self.config.read_bytes()
        for route in ('status','history?id=20','export?format=json','export?format=csv'):
            self.assertEqual(self.call('/api/servo-health/'+route)[0],200)
        self.assertEqual(self.call('/api/servo-health/history?id=200')[0],400)
        self.assertEqual(self.call('/api/servo-health/config',{'rules':{}},token=False)[0],403)
        self.assertEqual(self.call('/api/servo-health/config',{'rules':{'duration_s':3}})[0],200)
        self.assertEqual(self.call('/api/servo-health/maintenance',{'report':report()})[0],200)
        self.assertEqual(self.call('/api/servo-health/maintenance',{'report':{}})[0],400)
        self.assertEqual(self.call('/api/servo-health/baseline',{'action':'start','confirm_normal':True})[0],400)
        self.assertEqual(self.call('/api/servo-health/run-maintenance',{})[0],404)
        self.assertEqual(self.config.read_bytes(),before)

    def test_plugin_failure_does_not_break_old_preview_or_status(self):
        with patch.object(self.ctx.servo,'status',side_effect=RuntimeError('observer failed')):
            self.assertEqual(self.call('/api/servo-health/status')[0],503)
            self.assertEqual(self.call('/api/status')[0],200)
            self.assertEqual(self.call('/api/camera/preview',{'rotation':270})[0],200)
            self.assertEqual(self.call('/api/preview',{'calibration':web.make_default('daemon-v0.16.3')})[0],200)

    def test_plugin_startup_failure_keeps_calibration_context_available(self):
        args=web.parser().parse_args(['--target','daemon-v0.16.3','--no-telemetry','--enable-servo-health',
            '--config',str(self.config),'--servo-health-dir',str(Path(self.temp.name)/'startup-failure')])
        with patch.object(web.servo_health,'Engine',side_effect=RuntimeError('corrupt observer state')):
            ctx=web.Context(args)
        try:
            self.assertFalse(ctx.servo.status()['enabled'])
            self.assertIn('继续可用',ctx.servo.status()['message'])
            self.assertEqual(ctx.status()['target'],'daemon-v0.16.3')
        finally:ctx.monitor.close();ctx.servo.close()

    def test_disabled_mode_returns_no_observer_and_no_background_tasks(self):
        self.ctx.servo.close();self.ctx.servo=web.servo_health.Plugin(Path(self.temp.name)/'disabled')
        self.assertFalse(json.loads(self.call('/api/servo-health/status')[1])['enabled'])
        self.assertEqual(self.call('/api/servo-health/history')[0],409)

    def test_single_unchanged_monitor_command_and_observer_isolation(self):
        monitor=web.Monitor('mock-robotctl',enable=False,observer=Mock(side_effect=RuntimeError('broken')))
        def stream():
            yield json.dumps({'method':'robot.state','params':{'policy':'stand','joints':[0]*15,'safety':{},'loop':{},'t_ns':time.monotonic_ns()}})
            monitor.stop_event.set()
        process=Mock(stdout=stream());process.poll.return_value=0
        with patch.object(web.subprocess,'Popen',return_value=process) as popen:monitor._loop()
        self.assertEqual(popen.call_count,1)
        self.assertEqual(popen.call_args.args[0],['mock-robotctl','monitor','--hz','5','--json'])
        self.assertEqual(monitor.sample()['policy'],'stand')


if __name__=='__main__':unittest.main()
