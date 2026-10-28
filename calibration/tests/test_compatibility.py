"""Cross-version contracts and defect regressions. All daemons/devices are synthetic."""
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'integration-0.16.3/web'))
import calibration_web as web
import daemon_adapter as adapter
import config_store
import native_camera
import servo_health as sh


class FakeDaemon:
    def __init__(self,path,api,version,names=None):
        self.api=api;self.version=version;self.names=names or adapter.JOINT_NAMES
        self.calls=[];self.stop=threading.Event();self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.bind(str(path));self.sock.listen();self.sock.settimeout(.1)
        self.thread=threading.Thread(target=self.run);self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:conn,_=self.sock.accept()
            except socket.timeout:continue
            with conn,conn.makefile('rwb') as file:
                while True:
                    line=file.readline()
                    if not line:break
                    call=json.loads(line);method=call['method'];self.calls.append(method)
                    result={'api_version':self.api,'daemon_version':self.version,'revision':'synthetic-commit'} if method=='hello' else (
                        {'asset':'synthetic','joint_names':self.names} if method=='robot.model' else {'healthy':True,'motors':{'max_c':40}})
                    file.write((json.dumps({'id':call['id'],'result':result})+'\n').encode());file.flush()

    def close(self):self.stop.set();self.thread.join(timeout=2);self.sock.close()


class AdapterTests(unittest.TestCase):
    def test_fake_daemons_41_44_45_identity_model_cache_and_read_allowlist(self):
        for api,version in [(41,'0.16.3'),(44,'0.17.9'),(45,'0.18.0-dev')]:
            with self.subTest(api=api), tempfile.TemporaryDirectory(prefix='md-') as directory:
                path=Path(directory)/'ipc';daemon=FakeDaemon(path,api,version)
                try:
                    client=adapter.Client(path,device_id='synthetic-device',calibration_hash='synthetic-calibration')
                    health,identity,capability=client.poll();client.poll()
                    self.assertTrue(health['healthy']);self.assertTrue(capability['read_contract_verified'])
                    self.assertTrue(capability['baseline_identity_verified']);self.assertEqual(identity['daemon']['api_version'],api)
                    self.assertEqual(daemon.calls,['hello','robot.model','robot.health','hello','robot.health'])
                    client.invalidate('synthetic reconnect');client.poll()
                    self.assertEqual(daemon.calls.count('robot.model'),2)
                finally:daemon.close()
        file=Mock()
        for method in ('robot.enable','robot.move','robot.relax','config.set','media.frame'):
            with self.assertRaises(ValueError):adapter.rpc(file,1,method,{})
        file.write.assert_not_called()

    def test_unknown_api_wrong_topology_or_target_never_enable_analysis(self):
        hello={'api_version':44,'daemon_version':'0.17.9','revision':'synthetic'}
        model={'joint_names':adapter.JOINT_NAMES}
        for h,m,t in [({**hello,'api_version':900},model,'auto'),(hello,{'joint_names':list(reversed(adapter.JOINT_NAMES))},'auto'),(hello,model,'daemon-v0.16.3')]:
            self.assertFalse(adapter.contract(h,m,t)['read_contract_verified'])

    def test_profile_extension_requires_explicit_unique_api(self):
        try:
            adapter.register_profile('synthetic-future',(900,))
            self.assertTrue(adapter.contract({'api_version':900},{'joint_names':adapter.JOINT_NAMES})['read_contract_verified'])
            with self.assertRaises(ValueError):adapter.register_profile('duplicate',(900,))
        finally:adapter.PROFILES.pop('synthetic-future',None)

    def test_invalid_ipc_and_future_plugin_schema_fail_closed(self):
        for raw in [b'{"id":1,"result":{"x":NaN}}\n',b'{"id":2,"result":{}}\n',b'x'*262145]:
            file=Mock();file.readline.return_value=raw
            with self.assertRaises(ValueError):adapter.rpc(file,1,'hello',{})
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'plugins.json'
            for value in [{'schema':2},{'schema':1,'unknown_plugin':{}},{'schema':1,'servo_health':{'enabled':'yes'}}]:
                p.write_text(json.dumps(value))
                with self.assertRaises(ValueError):adapter.load_plugin_config(p)
            p.write_text('{"schema":1,"servo_health":{"enabled":true}}')
            self.assertTrue(adapter.load_plugin_config(p)['enabled'])

    def test_new_target_never_writes_native_calibration_even_allow_inactive(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'robotd.toml';p.write_text('[board]\nversion="zero3"\n')
            for target in ('auto','daemon-v0.17.9'):
                args=web.parser().parse_args(['--target',target,'--no-telemetry','--allow-inactive','--config',str(p)])
                ctx=web.Context(args)
                try:
                    self.assertFalse(ctx.status()['can_save'])
                    with self.assertRaises(web.cal.CalibrationError):ctx.save({'calibration':web.make_default('daemon-v0.16.3')})
                    self.assertIsInstance(ctx.camera,native_camera.Camera)
                finally:ctx.monitor.close();ctx.servo.close()
            self.assertNotIn('calibration_path',p.read_text())


class RegressionTests(unittest.TestCase):
    def test_camera_external_writer_between_preview_backup_and_replace_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml';path.write_text('[media]\nquality="720p30"\n')
            camera=web.camera_config.Camera(path,Path(directory)/'runtime','daemon-v0.16.3',False,False,8080)
            old_backup=web.cal.make_backup
            def write_elsewhere(path):
                path.write_text('[media]\nquality="480p30"\n');return old_backup(path)
            with patch.object(camera,'status',return_value={'can_save':True}),patch.object(web.cal,'make_backup',side_effect=write_elsewhere):
                with self.assertRaisesRegex(web.cal.CalibrationError,'conflict'):camera.save({'rotation':270,'confirm_save':True})
            self.assertIn('480p30',path.read_text());self.assertNotIn('camera_rotate',path.read_text())

    def test_stale_preview_revision_conflicts_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml';path.write_text('[media]\nquality="720p30"\n')
            camera=web.camera_config.Camera(path,Path(directory)/'runtime','daemon-v0.16.3',False,False,8080)
            revision=camera.preview({'rotation':90})['config_revision']
            path.write_text('[media]\nquality="480p30"\n')
            with patch.object(camera,'status',return_value={'can_save':True}):
                with self.assertRaisesRegex(web.cal.CalibrationError,'conflict'):
                    camera.save({'rotation':90,'confirm_save':True,'config_revision':revision})
            self.assertIn('480p30',path.read_text())

    def test_crlf_camera_configuration_revision_matches_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml';path.write_bytes(b'[media]\r\nquality="720p30"\r\n')
            camera=web.camera_config.Camera(path,Path(directory)/'runtime','daemon-v0.16.3',False,False,8080)
            preview=camera.preview({'rotation':90})
            self.assertEqual(preview['config_revision'],config_store.revision(path.read_bytes()))
            with patch.object(camera,'status',return_value={'can_save':True}):
                self.assertTrue(camera.save({'rotation':90,'config_revision':preview['config_revision'],'confirm_save':True})['changed'])

    def test_cached_low_temperature_and_voltage_cannot_resolve_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[100.];engine=sh.Engine(directory,clock=lambda:now[0])
            engine.on_health({'motors':{'max_c':66},'battery':{'volts':7.4}})
            for _ in range(20):
                now[0]+=1;engine.on_health({'motors':{'max_c':40},'battery':{'volts':5}})
            self.assertIn('temperature',engine.active);self.assertIn('voltage',engine.active)
            self.assertFalse(any(e['phase']=='resolved' for e in engine.events))
            self.assertTrue(all(a['evidence_status']=='awaiting_fresh_sensor_evidence' for a in engine.status()['alerts']))

    def test_bound_baseline_survives_same_identity_and_rejects_changed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[100.];engine=sh.Engine(directory,clock=lambda:now[0],require_identity=True)
            identity={'device_id':'synthetic','calibration':'a'};engine.set_identity(identity,True)
            engine.baselines={'stand':dict(p95_ma=[100.]*15,version=1,identity=identity)}
            engine.policy='stand';self.assertTrue(engine.status()['baseline_valid'])
            engine.set_identity({'device_id':'synthetic','calibration':'b'},True)
            self.assertFalse(engine.status()['baseline_valid'])
            self.assertEqual(engine.baselines['stand']['version'],1)
            engine.baselines['stand'].pop('identity');self.assertIsNone(engine._base('stand'))
            with self.assertRaises(ValueError):engine.baseline({'action':'start','confirm_normal':True})

    def test_cursor_delta_and_epoch_resets_preserve_nanosecond_precision(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[10_000_000.];engine=sh.Engine(directory,clock=lambda:now[0])
            def feed():
                now[0]+=.2;engine.on_sample(dict(t_ns=round(now[0]*1e9),policy='stand',safety={},
                    joints=[0.]*15,targets=[0.]*15,velocities=[0.]*15,currents_ma=[40.]*15))
            for _ in range(10):feed()
            first=engine.series(20);feed();delta=engine.series(20,first['cursor'],first['epoch'])
            self.assertFalse(delta['reset']);self.assertEqual(len(delta['samples']),1)
            self.assertIsInstance(delta['cursor'],str)
            self.assertEqual(engine.series(20,delta['cursor'],delta['epoch'])['samples'],[])
            self.assertTrue(engine.series(20,delta['cursor'],'old-epoch')['reset'])
            engine.reset_continuity();self.assertNotEqual(engine.stream_epoch,delta['epoch'])
            with self.assertRaises(ValueError):engine.series(20,'-1')

    def test_queue_retains_latest_subset_and_marks_discontinuity(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(sh.threading,'Thread'):
            plugin=sh.Plugin(directory,enabled=True)
            for i in range(20):plugin.submit({'t_ns':i,'skeleton':['unused']*1000})
            self.assertGreater(plugin.dropped_frames,0);self.assertTrue(plugin.gap.is_set())
            pending=[]
            while not plugin.frames.empty():pending.append(plugin.frames.get_nowait()[0])
            self.assertEqual(pending[-1]['t_ns'],19);self.assertNotIn('skeleton',pending[-1])
            plugin.close()

    def test_compact_status_does_not_ship_maintenance_document(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=sh.Engine(directory);engine.maintenance={'synthetic':'large'};engine.maintenance_version='v'
            self.assertEqual(engine.status()['maintenance'],{'synthetic':'large'})
            self.assertIsNone(engine.status(compact=True)['maintenance'])
            self.assertEqual(engine.status(compact=True)['maintenance_version'],'v')


class NativeCameraTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.config=self.directory/'robotd.toml';self.config.write_text('[board]\nversion="zero3"\n')
        self.settings=self.directory/'camera.json';self.dropin=self.directory/'unit/90-microduck-tools-camera.conf'
        self.unit=self.directory/'mediad.service';self.unit.write_text('[Service]\nExecStart='+native_camera.BINARY+'\n')
        self.camera=native_camera.Camera(self.config,self.settings,'daemon-v0.17.9',False,True,8080,dropin_path=self.dropin)

    def tearDown(self):self.temp.cleanup()

    def test_four_rotations_and_reset_store_only_tool_config_and_apply_only_media(self):
        before=self.config.read_bytes();commands=[]
        def run(argv,**kw):
            commands.append(argv)
            return Mock(stdout=str(self.unit) if 'FragmentPath' in argv else '')
        runtime=dict(pid=123,rotation_degrees=90)
        with patch.object(self.camera,'_runtime',return_value=runtime),patch.object(native_camera.subprocess,'run',side_effect=run):
            for value in (0,90,180,270,None):
                preview=self.camera.preview({'rotation':value})
                self.camera.save({'rotation':value,'config_revision':preview['config_revision'],'confirm_save':True})
                self.camera.apply({'confirm_video_interrupt':True})
                self.assertEqual(self.dropin.read_text(),native_camera.dropin(value))
        self.assertEqual(self.config.read_bytes(),before)
        mutations=[argv for argv in commands if argv[1] in ('restart','daemon-reload')]
        self.assertEqual(mutations,[['systemctl','daemon-reload'],['systemctl','restart','mediad']]*5)

    def test_foreign_dropin_or_vendor_arguments_are_never_replaced(self):
        with patch.object(native_camera.subprocess,'run',side_effect=[Mock(stdout=str(self.unit)),Mock(stdout='/etc/systemd/system/mediad.service.d/foreign.conf')]):
            with self.assertRaisesRegex(web.cal.CalibrationError,'other mediad'):self.camera._unit()
        self.unit.write_text('[Service]\nExecStart='+native_camera.BINARY+' --custom-flag\n')
        with patch.object(native_camera.subprocess,'run',side_effect=[Mock(stdout=str(self.unit)),Mock(stdout='')]):
            with self.assertRaisesRegex(web.cal.CalibrationError,'vendor'):self.camera._unit()
        self.assertFalse(self.dropin.exists())

    def test_no_live_identity_no_writes_and_no_restart(self):
        self.camera.demo=True
        with patch.object(native_camera.subprocess,'run') as run:
            self.assertFalse(self.camera.status()['can_save'])
            with self.assertRaises(web.cal.CalibrationError):self.camera.save({'rotation':90,'confirm_save':True})
            with self.assertRaises(web.cal.CalibrationError):self.camera.apply({'confirm_video_interrupt':True})
            run.assert_not_called()
        self.assertFalse(self.settings.exists())

    def test_runtime_requires_revision_process_identity_and_reviewed_arguments(self):
        identity=self.directory/'identity.json';self.camera.runtime=identity
        executable='/opt/robot/daemon/releases/0.17.9/bin/mediad'
        data=dict(service='mediad',version='0.17.9',revision=next(iter(native_camera.NATIVE_REVISIONS)),pid=123,exe=executable)
        identity.write_text(json.dumps(data))
        for angle in (0,90,180,270,None):
            args=[executable] if angle is None else [executable,'--rotate',str(angle)]
            with patch.object(native_camera.subprocess,'run',return_value=Mock(stdout='123')),patch.object(native_camera.os,'readlink',return_value=executable),patch.object(Path,'read_bytes',return_value=('\x00'.join(args)+'\x00').encode()):
                self.assertEqual(self.camera._runtime(90)['rotation_degrees'],90 if angle is None else angle)
        with patch.object(native_camera.subprocess,'run',return_value=Mock(stdout='123')),patch.object(native_camera.os,'readlink',return_value=executable),patch.object(Path,'read_bytes',return_value=(executable+'\x00--flip-in-pipeline\x00').encode()):
            with self.assertRaisesRegex(web.cal.CalibrationError,'arguments'):self.camera._runtime(90)
        identity.write_text(json.dumps({**data,'revision':'unreviewed'}))
        with self.assertRaisesRegex(web.cal.CalibrationError,'revision'):self.camera._runtime(90)


if __name__=='__main__':unittest.main()
