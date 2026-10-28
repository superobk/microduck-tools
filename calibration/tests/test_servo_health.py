"""Synthetic/replay tests only. No physical motor, socket or service is contacted."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

I=Path(__file__).resolve().parents[1]/'integration-0.16.3'
sys.path.insert(0,str(I/'web'))
import servo_health as sh
spec=importlib.util.spec_from_file_location('maintenance',I/'servo-maintenance.py')
maintenance=importlib.util.module_from_spec(spec);spec.loader.exec_module(maintenance)


def report():
    return dict(schema='microduck-servo-maintenance/v1',robot_label='SYNTHETIC',collected_at='2026-10-26T00:00:00+00:00',
                servos=[dict(id=i,raw={'0':1200,'6':52,'63':52,'64':0,'70':0,'126':20,'144':50,'146':32},read_errors=[]) for i in sh.IDS])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=100.;self.logs=[]
        self.engine=sh.Engine(self.tmp.name,lambda:self.now,self.logs.append)
        self.engine.baselines={'stand':dict(p95_ma=[100.]*15,version=1)}

    def tearDown(self):self.tmp.cleanup()

    def frame(self,current=100.,error=0.,velocity=0.,policy='stand',safety=None,**updates):
        row=dict(t_ns=round(self.now*1e9),policy=policy,safety=safety or {},currents_ma=[current]*15,
                 joints=[0.]*15,targets=[math.radians(error)]*15,velocities=[math.radians(velocity)]*15)
        row.update(updates);return row

    def feed(self,n=1,**kwargs):
        for _ in range(n):
            self.now=round(self.now+.2,6);self.engine.on_sample(self.frame(**kwargs));self.engine.tick()

    def test_normal_and_high_current_while_following(self):
        self.feed(20);self.assertFalse(self.engine.active)
        self.feed(15,current=400)
        self.assertIn('current:20',self.engine.active);self.assertNotIn('stall:20',self.engine.active)
        self.assertEqual(self.engine.active['current:20']['threshold'],200)

    def test_stall_needs_stable_target_error_speed_and_duration(self):
        self.feed(20,current=400,error=20)
        self.assertIn('stall:20',self.engine.active)
        self.feed(30)
        self.assertNotIn('stall:20',self.engine.active)
        self.assertTrue(any(e['phase']=='resolved' for e in self.engine.events))

    def test_moving_target_and_high_speed_do_not_trigger_stall(self):
        for i in range(30):self.feed(current=400,error=20+i*3)
        self.assertNotIn('stall:20',self.engine.active)
        self.feed(30,current=400,error=20,velocity=10)
        self.assertNotIn('stall:20',self.engine.active)

    def test_paused_contexts_and_unknown_torque_never_infer_damage(self):
        for safety in ({'fallen':True},{'limp':True},{'picked_up':True}):
            self.feed(20,current=400,error=20,safety=safety)
            self.assertNotIn('stall:20',self.engine.active)
        self.feed(20,current=400,error=20,policy='held')
        self.assertNotIn('stall:20',self.engine.active)
        self.assertTrue(self.engine.status()['rows'][0]['stall_paused'])

    def test_missing_negative_wrong_size_nan_vectors_are_unknown(self):
        for invalid in (None,[],[0]*14,[float('nan')]*15,[-1]*15,[True]*15):
            self.feed(currents_ma=invalid)
            self.assertIsNone(self.engine.status()['rows'][0]['current_ma'])
        self.assertFalse(self.engine.active)
        json.dumps(self.engine.status(),allow_nan=False)

    def test_duplicate_ns_never_counts_as_new_sensor_sample(self):
        self.feed(current=400,error=20);row=self.frame(current=400,error=20);frames=self.engine.frames
        for _ in range(20):
            self.now+=.2;self.engine.on_sample(row);self.engine.tick()
        self.assertEqual(self.engine.frames,frames)
        self.assertNotIn('stall:20',self.engine.active)
        self.assertTrue(self.engine.status()['stale'])

    def test_sample_gap_and_policy_change_restart_dwell(self):
        self.feed(8,current=400)
        self.now+=1;self.feed(current=400)
        self.assertNotIn('current:20',self.engine.active)
        self.feed(7,current=400)
        self.engine.baselines['walk']=self.engine.baselines['stand']
        self.feed(current=400,policy='walk')
        self.assertNotIn('current:20',self.engine.active)

    def test_unknown_evidence_cannot_clear_existing_alert(self):
        self.feed(15,current=400)
        self.feed(40,currents_ma=None)
        self.assertIn('current:20',self.engine.active)
        self.assertEqual(self.engine.status()['rows'][0]['state'],'warning')

    def test_missing_timestamp_never_looks_fresh(self):
        self.engine.on_sample(self.frame(t_ns=0));self.assertTrue(self.engine.status()['stale'])
        self.engine.on_sample(self.frame(t_ns=True));self.assertEqual(self.engine.frames,0)

    def test_health_is_cached_and_maintenance_is_not_live(self):
        self.feed()
        self.engine.on_health(dict(motors={'max_c':66,'mean_c':40,'hottest':'left_knee'},battery={'volts':7.4},bus={'consecutive_errors':2}))
        self.assertIn('temperature',self.engine.active);self.assertIn('voltage',self.engine.active)
        self.assertEqual(self.engine.status()['slow_sensor_freshness'],'unverifiable')
        item=report();item['servos'][0]['raw']['70']=36
        self.engine.import_maintenance({'report':item})
        self.assertFalse(self.engine.status()['maintenance']['live'])
        self.assertEqual(self.engine.status()['rows'][0]['current_ma'],100)
        self.assertNotIn('hardware_error',self.engine.active)

    def test_baseline_manual_confirmation_p95_and_persistence(self):
        self.engine.baselines={};self.feed(current=20)
        with self.assertRaises(ValueError):self.engine.baseline({'action':'start'})
        self.engine.baseline({'action':'start','confirm_normal':True});self.feed(301,current=20)
        self.assertEqual(self.engine.baselines['stand']['p95_ma'],[20]*15)
        self.assertEqual(self.engine.capture_result,'saved')
        loaded=sh.Engine(self.tmp.name);self.assertEqual(loaded.baselines['stand']['p95_ma'],[20]*15)

    def test_baseline_cancels_on_policy_gap_and_invalid_data(self):
        for update in ({'policy':'walk'},{'currents_ma':None}):
            self.feed();self.engine.baseline({'action':'start','confirm_normal':True});self.feed(**update)
            self.assertIsNone(self.engine.capture)
        self.feed();self.engine.baseline({'action':'start','confirm_normal':True});self.now+=1;self.feed()
        self.assertIsNone(self.engine.capture)

    def test_rule_validation_versions_and_config_file_only(self):
        self.engine.configure({'rules':{'current_margin_ma':150}})
        self.assertEqual(self.engine.config_version,2)
        self.engine.configure({'rules':{'duration_s':3}})
        self.assertEqual(self.engine.config['current_margin_ma'],150)
        for cfg in ({'temp_warn_c':68,'temp_alarm_c':60},{'voltage_min_v':8},{'fake':1},{'duration_s':float('nan')}):
            with self.assertRaises(ValueError):self.engine.configure({'rules':cfg})
        self.assertEqual(list(Path(self.tmp.name).iterdir()),[Path(self.tmp.name)/'rules.json'])

    def test_evidence_has_pre_and_post_context_and_rule_version(self):
        self.feed(30);self.feed(15,current=400,error=20);self.feed(30,current=400,error=20)
        current=next(x for x in self.logs if x['event']['key']=='current:20')
        self.assertTrue(current['before']);self.assertTrue(current['after']);self.assertTrue(current['after_complete'])
        self.assertEqual(current['event']['rule_version'],1)

    def test_disconnected_post_window_is_incomplete(self):
        self.feed(15,current=400);self.now+=6;self.engine.tick()
        event=next(e for e in self.logs if e['event']['key']=='current:20')
        self.assertFalse(event['after_complete'])

    def test_health_without_measurements_does_not_clear_reported_overheat(self):
        self.engine.on_health({'motors':{'max_c':66},'bus':{}})
        for i in range(10):self.now+=1;self.engine.on_health({})
        self.assertIn('temperature',self.engine.active)

    def test_history_bounded_five_minutes_and_exports(self):
        self.feed(1600)
        self.assertLessEqual(len(self.engine.history),1501)
        self.assertIn(b't_ns,policy,id',self.engine.export('csv')[0])
        self.assertEqual(json.loads(self.engine.export()[0])['schema'],'microduck-servo-health/v1')
        with self.assertRaises(ValueError):self.engine.series(200)

    def test_report_validation_and_unknown_model_no_decoding(self):
        r=report();r['servos'][0]['raw']['0']=9999
        decoded=self.engine.import_maintenance({'report':r})['report']['servos'][0]['decoded']
        self.assertFalse(decoded['supported']);self.assertNotIn('temp_c',decoded)
        for bad in (dict(r,schema='wrong'),dict(r,servos=r['servos'][:-1]),dict(r,servos=[r['servos'][0]]*15)):
            with self.assertRaises(ValueError):self.engine.import_maintenance({'report':bad})
        r['servos'][1]['raw']['70']=256
        with self.assertRaises(ValueError):self.engine.import_maintenance({'report':r})


class IsolationTests(unittest.TestCase):
    def test_disabled_plugin_no_files_threads_sockets(self):
        with tempfile.TemporaryDirectory() as d,patch.object(sh.threading,'Thread') as thread,patch.object(sh,'read_health') as rpc:
            p=sh.Plugin(Path(d)/'absent');p.submit({});p.close()
            self.assertFalse(p.status()['enabled']);thread.assert_not_called();rpc.assert_not_called()
            self.assertFalse((Path(d)/'absent').exists())

    def test_disk_error_and_observer_error_remain_local(self):
        with tempfile.TemporaryDirectory() as d:
            bad=Path(d)/'not-directory';bad.write_text('x')
            p=sh.Plugin(bad,enabled=True)
            p._emit({'test':'disk error'})
            for _ in range(30):
                if p.dropped_logs:break
                time.sleep(.01)
            self.assertGreater(p.dropped_logs,0);p.close()

    def test_utf8_log_rotation_obeys_byte_limit(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'events.jsonl';path.write_bytes(b'x'*(10*1024*1024-3))
            p=sh.Plugin(d,enabled=True);p._emit({'message':'温度偏高'*100})
            for _ in range(100):
                if (Path(d)/'events.jsonl.1').exists() and path.stat().st_size>0:break
                time.sleep(.01)
            p.close()
            self.assertTrue((Path(d)/'events.jsonl.1').exists())
            self.assertLessEqual(max(x.stat().st_size for x in Path(d).glob('events.jsonl*')),10*1024*1024)

    def test_sdk_transport_blocks_write_reboot_and_torque_commands(self):
        # The last transport boundary rejects prohibited opcodes before any fd write.
        port=object.__new__(maintenance.OwnedPort);port.fd=123;port.guard=Mock()
        with patch.object(maintenance.os,'write',return_value=10) as write:
            for op in (3,4,5,6,8,0x83):
                with self.assertRaises(RuntimeError):port.writePort([255,255,253,0,20,3,0,op,0,0])
            write.assert_not_called();port.guard.assert_not_called()
            for op in (1,2):port.writePort([255,255,253,0,20,3,0,op,0,0])
            self.assertEqual(write.call_count,2)

    def test_active_service_or_occupied_port_refuses_before_open(self):
        with patch.object(maintenance.subprocess,'run',return_value=Mock(stdout='active\n')):
            with self.assertRaises(RuntimeError):maintenance.service_inactive()
        with patch.object(maintenance.sys,'platform','linux'),patch.object(maintenance.os,'geteuid',return_value=0),\
             patch.object(maintenance,'service_inactive'),patch.object(maintenance,'no_robotd_process'),\
             patch.object(maintenance,'other_users',side_effect=RuntimeError('occupied')),patch.object(maintenance.os,'open') as op:
            with self.assertRaises(RuntimeError):
                with maintenance.exclusive_port('/dev/fake'):pass
            op.assert_not_called()

    def test_collector_is_read_only_partial_errors_and_model_gate(self):
        class Reader:
            def __init__(self):self.calls=[]
            def read(self,ident,addr,size):
                self.calls.append((ident,addr,size))
                if ident==20:return 999 if addr==0 else 52
                if addr==70:raise RuntimeError('timeout')
                return {0:1200,6:52,63:52}.get(addr,0)
        reader=Reader();r=maintenance.collect(reader,'SYNTHETIC')
        self.assertEqual({a for i,a,s in reader.calls if i==20},{0,6})
        self.assertTrue(r['servos'][1]['read_errors'])
        self.assertEqual(len(sh.validate_report(r)['servos']),15)

    def test_health_rpc_only_hello_and_health(self):
        import io
        stream=io.BytesIO()  # Separate read/write mock to inspect exact method names.
        connection=Mock();file=Mock()
        file.readline.side_effect=[b'{"id":1,"result":{"api_version":41}}\n',b'{"id":2,"result":{"healthy":true}}\n']
        connection.__enter__=Mock(return_value=connection);connection.__exit__=Mock();connection.makefile.return_value=file
        with patch.object(sh.socket,'socket',return_value=connection):self.assertTrue(sh.read_health('/fake')['healthy'])
        self.assertEqual([json.loads(c.args[0])['method'] for c in file.write.call_args_list],['hello','robot.health'])


if __name__=='__main__':unittest.main()
