"""0.18.3 layered persistence and precise native/media identities. No hardware."""
import argparse
import json
from pathlib import Path
import unittest
from unittest.mock import patch,Mock
import test_native_0179 as previous
import test_compatibility as compatibility
import calibration_web as web

class Native0183Tests(unittest.TestCase):
    def setUp(self):
        previous.Native0179Tests.setUp(self)
        self.user=self.directory/'user.toml'
        self.receipt.update(target_tag='daemon-v0.18.3',daemon_version='0.18.3',base_revision=web.NATIVE_0183_BASE,
            build_revision=web.NATIVE_0183_BUILD,config_layering='user-over-base/v1',user_config_path=None,user_layer_rejected=None)
        self.flush()
    tearDown=previous.Native0179Tests.tearDown
    def flush(self):self.runtime.write_text(json.dumps(self.receipt))
    def context(self,target='daemon-v0.18.3',allow_inactive=False):return previous.Native0179Tests.context(self,target,allow_inactive)
    def payload(self):
        v=web.make_default('daemon-v0.18.3');v.update(gyro_profile='custom_raw_div_16_4',gyro_raw_counts_per_dps=16.4)
        return dict(calibration=v,confirm_safe=True,confirm_hardware=True)
    def test_layered_auto_save_preserves_base_and_requires_new_receipt(self):
        self.receipt['user_config_path']=str(self.user);self.flush();before=self.config.read_bytes()
        with patch.object(web.cal.config_layers,'DEFAULT_PATH',self.config),patch.object(web.cal.config_layers,'USER_PATH',self.user),patch.object(web,'valid_active_file',return_value=(True,'synthetic')),patch.object(web.os,'readlink',return_value=self.executable):
            ctx=self.context('auto')
            try:
                self.assertEqual(ctx.status()['write_config_file'],str(self.user));self.assertTrue(ctx.status()['can_save'])
                ctx.save(self.payload());self.assertEqual(self.config.read_bytes(),before)
                self.assertIn('calibration_path',self.user.read_text());self.assertTrue(ctx.status()['pending_restart'])
                self.receipt.update(gyro_raw_counts_per_dps=16.4,calibration_path=str(self.dest));self.flush()
                self.assertFalse(ctx.status()['pending_restart'])
            finally:ctx.monitor.close();ctx.servo.close()
    def test_official_0183_never_writes_even_allow_inactive(self):
        self.receipt.pop('native_calibration_contract');self.flush();ctx=self.context(allow_inactive=True)
        try:
            with patch.object(web,'valid_active_file',return_value=(True,'synthetic')):
                self.assertFalse(ctx.status()['can_save'])
                with self.assertRaises(web.cal.CalibrationError):ctx.save(self.payload())
        finally:ctx.monitor.close();ctx.servo.close()
    def test_rejected_layer_and_wrong_layout_or_build_refuse(self):
        for value in [dict(user_layer_rejected='bad range'),dict(user_config_path='/unexpected'),dict(config_layering='other'),dict(build_revision=web.NATIVE_0179_BUILD)]:
            with self.subTest(value=value),patch.object(web.os,'readlink',return_value=self.executable):
                self.assertFalse(web.verify_native_receipt({**self.receipt,**value},self.config,'daemon-v0.18.3')[0])
        for field in ('config_layering','user_config_path','user_layer_rejected'):
            value=dict(self.receipt);value.pop(field)
            with self.subTest(missing=field),patch.object(web.os,'readlink',return_value=self.executable):
                self.assertFalse(web.verify_native_receipt(value,self.config,'daemon-v0.18.3')[0])
    def test_bad_user_toml_disables_save_and_keeps_status_available(self):
        self.user.write_text('[bus');self.receipt['user_config_path']=str(self.user);self.flush()
        with patch.object(web.cal.config_layers,'DEFAULT_PATH',self.config),patch.object(web.cal.config_layers,'USER_PATH',self.user),patch.object(web,'valid_active_file',return_value=(True,'synthetic')),patch.object(web.os,'readlink',return_value=self.executable):
            ctx=self.context()
            try:self.assertFalse(ctx.status()['can_save']);self.assertIsNotNone(ctx.status()['saved_error'])
            finally:ctx.monitor.close();ctx.servo.close()
    def test_remove_masks_base_pointer_and_preserves_other_user_values(self):
        self.config.write_text('[bus]\ncalibration_path="/base-cal.toml"\n');self.user.write_text('[control]\nhz=50\n[bus]\ncalibration_path="/user-cal.toml"\n');before=self.config.read_bytes()
        with patch.object(web.cal.config_layers,'DEFAULT_PATH',self.config),patch.object(web.cal.config_layers,'USER_PATH',self.user):
            args=argparse.Namespace(config=str(self.config),target='daemon-v0.18.3',apply=True);web.cal.cmd_remove(args)
            self.assertEqual(web.cal.config_layers.load(self.config,args.target)['bus']['calibration_path'],'')
            self.assertEqual(self.config.read_bytes(),before);self.assertIn('hz=50',self.user.read_text())
    def test_nonstandard_config_is_standalone_and_arrays_replace(self):
        self.user.write_text('[board]\nversion="beta"\n')
        with patch.object(web.cal.config_layers,'USER_PATH',self.user):
            self.assertEqual(web.cal.config_layers.load(self.config,'daemon-v0.18.3')['board']['version'],'zero3')
        self.assertEqual(web.cal.config_layers.merge({'x':[1,2],'table':{'a':1}},{'x':[3],'table':{'b':2}}),{'x':[3],'table':{'a':1,'b':2}})
    def test_baseline_identity_tracks_user_layer_changes(self):
        self.receipt['user_config_path']=str(self.user);self.flush()
        with patch.object(web.cal.config_layers,'DEFAULT_PATH',self.config),patch.object(web.cal.config_layers,'USER_PATH',self.user):
            ctx=self.context()
            try:
                first=ctx.calibration_identity();self.user.write_text('[control]\nhz=45\n');self.assertNotEqual(first,ctx.calibration_identity())
            finally:ctx.monitor.close();ctx.servo.close()
    def test_migrate_0179_preserves_actual_mapping(self):
        old=self.directory/'old.toml';values=web.make_default('daemon-v0.17.9');values['joint_zero_rad'][0]=.1;old.write_text(web.cal.render_toml(values));before=old.read_bytes()
        args=web.cal.parser().parse_args(['migrate','--source',str(old),'--out',str(self.dest),'--target','daemon-v0.18.3','--confirm-same-hardware'])
        args.fn(args);self.assertEqual(web.cal.parse_calibration(self.dest)['joint_zero_rad'][0],.1);self.assertEqual(old.read_bytes(),before)
    def test_published_native_metadata_matches(self):
        data=json.loads((Path(web.__file__).resolve().parents[2]/'daemon-v0.18.3/native-build.json').read_text())
        self.assertEqual(data['build_revision'],web.NATIVE_0183_BUILD)
    def test_default_destination_follows_late_auto_resolution(self):
        args=web.parser().parse_args(['--target','auto','--demo','--no-telemetry','--config',str(self.config),'--runtime',str(self.directory/'missing.json')])
        ctx=web.Context(args)
        try:
            self.assertEqual(str(ctx.dest),'/etc/robot/robot-calibration.toml')
            ctx.adapter.capabilities.update(daemon_version='0.18.3')
            self.assertEqual(str(ctx.dest),'/var/lib/robot/calibration/microduck-0.18.3.toml')
        finally:ctx.monitor.close();ctx.servo.close()
    def test_explicit_destination_remains_operator_selected(self):
        ctx=self.context()
        try:self.assertEqual(ctx.dest,self.dest)
        finally:ctx.monitor.close();ctx.servo.close()
    def test_api47_is_readonly_and_unreviewed48_remains_disabled(self):
        a=web.daemon_adapter;model=dict(joint_names=a.JOINT_NAMES)
        self.assertTrue(a.contract(dict(api_version=47,daemon_version='0.18.3'),model)['read_contract_verified'])
        self.assertFalse(a.contract(dict(api_version=48,daemon_version='0.18.4'),model)['read_contract_verified'])
        for method in ('os.apply','os.switch','telemetry.set','robot.move'):
            with self.assertRaises(ValueError):a.rpc(None,1,method,{})

class Camera0183Tests(compatibility.NativeCameraTests):
    def setUp(self):super().setUp();self.camera.target='daemon-v0.18.3'
    def test_runtime_requires_revision_process_identity_and_reviewed_arguments(self):
        identity=self.directory/'identity.json';self.camera.runtime=identity
        exe='/opt/robot/daemon/releases/0.18.3/bin/mediad';data=dict(service='mediad',version='0.18.3',revision=web.native_camera.REVIEWED_MEDIAD['0.18.3'],pid=123,exe=exe)
        identity.write_text(json.dumps(data))
        for angle in (0,90,180,270):
            with patch.object(web.native_camera.subprocess,'run',return_value=Mock(stdout='123')),patch.object(web.native_camera.os,'readlink',return_value=exe),patch.object(Path,'read_bytes',return_value=(exe+'\0--rotate\0'+str(angle)+'\0').encode()):
                self.assertEqual(self.camera._runtime(90)['rotation_degrees'],angle)
        identity.write_text(json.dumps({**data,'revision':web.native_camera.REVIEWED_MEDIAD['0.17.9']}))
        with self.assertRaisesRegex(web.cal.CalibrationError,'revision'):self.camera._runtime(90)

if __name__=='__main__':unittest.main()
