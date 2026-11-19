"""API68 synthetic receipts and filesystem migration; no device access."""
import argparse
from contextlib import ExitStack
import json
import math
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import test_native_0179 as previous
import calibration_web as web

class Native0230Tests(unittest.TestCase):
    def setUp(self):
        previous.Native0179Tests.setUp(self)
        self.receipt.update(web.make_default('daemon-v0.23.0'))
        self.receipt.update(daemon_version='0.23.0', base_revision=web.NATIVE_0230_BASE,
            build_revision=web.NATIVE_0230_BUILD, config_layering='user-over-base/v1',
            user_config_path=None, user_layer_rejected=None, imu_mount_wxyz=[0,0,0,1],
            gyro_profile='custom_raw_div_16_4', gyro_raw_counts_per_dps=16.4)
        self.flush()
        self.mocks=ExitStack()
        self.mocks.enter_context(patch.object(web, 'valid_active_file', return_value=(True,'synthetic')))
        self.mocks.enter_context(patch.object(web.os,'readlink',return_value=self.executable))
        self.hash_check=self.mocks.enter_context(patch.object(web.daemon_profiles,'verify_process',return_value=True))
    def tearDown(self):
        self.mocks.close()
        previous.Native0179Tests.tearDown(self)
    def flush(self): self.runtime.write_text(json.dumps(self.receipt))
    def context(self,target='auto'):
        return previous.Native0179Tests.context(self,target)
    def test_auto_target_and_horizontal_custom_scale_remain_distinct_from_runtime(self):
        ctx=self.context()
        try:
            self.assertEqual(ctx.status()['calibration_target'],'daemon-v0.23.0')
            self.assertTrue(ctx.status()['runtime_verified'])
            values={**web.make_default('daemon-v0.23.0'), 'imu_mount_wxyz':[0,0,0,1],
                    'gyro_profile':'custom_raw_div_16_4','gyro_raw_counts_per_dps':16.4}
            ctx.save(dict(calibration=values,confirm_safe=True,confirm_hardware=True))
            saved=web.cal.parse_calibration(self.dest)
            self.assertEqual(saved['imu_mount_wxyz'],[0,0,0,1])
            self.assertAlmostEqual(web.cal.effective_gyro(saved)['gyro_rad_per_raw_count'],math.pi/(180*16.4))
            self.assertTrue(ctx.status()['pending_restart'])
            self.receipt.update(calibration_path=str(self.dest),calibration_active=True)
            self.flush()
            self.assertFalse(ctx.status()['pending_restart'])
            # A decoder still using factory scale can never be shown as the saved /16.4.
            self.receipt['gyro_raw_counts_per_dps']=1/0.0175;self.flush()
            self.assertTrue(ctx.status()['pending_restart'])
        finally: ctx.monitor.close();ctx.servo.close()
    def test_wrong_hash_revision_target_or_user_layer_cannot_enable_writes(self):
        for key,value in [('build_revision','wrong'),('daemon_version','0.18.3'),
                          ('user_layer_rejected','bad'),('config_layering','other')]:
            data={**self.receipt,key:value}
            self.assertFalse(web.verify_native_receipt(data,self.config,'daemon-v0.23.0')[0])
        self.hash_check.return_value=False
        ctx=self.context()
        try:
            self.assertFalse(ctx.status()['can_save'])
            self.assertFalse(ctx.status()['runtime_verified'])
        finally: ctx.monitor.close();ctx.servo.close()
    def test_migration_keeps_every_calibration_value_and_source_bytes(self):
        old=self.directory/'old.toml';values=web.make_default('daemon-v0.18.3')
        values.update(imu_mount_wxyz=[0,0,0,1],gyro_profile='custom_raw_div_16_4',gyro_raw_counts_per_dps=16.4)
        values['joint_zero_rad'][4]=.125;values['joint_direction'][8]=-1
        old.write_text(web.cal.render_toml(values));before=old.read_bytes()
        args=web.cal.parser().parse_args(['migrate','--source',str(old),'--out',str(self.dest),
                                        '--target','daemon-v0.23.0','--confirm-same-hardware','--confirm-firmware-scale'])
        args.fn(args);got=web.cal.parse_calibration(self.dest)
        self.assertEqual(old.read_bytes(),before)
        for key in ('imu_mount_wxyz','gyro_raw_counts_per_dps','gyro_profile','joint_zero_rad','joint_direction'):
            self.assertEqual(got[key],values[key])
    def test_persistent_layer_and_default_path_only_for_reviewed_targets(self):
        layer=web.cal.config_layers;user=self.directory/'user.toml'
        user.write_text('[control]\nhz=50\n')
        before=self.config.read_bytes()
        with patch.object(layer,'DEFAULT_PATH',self.config),patch.object(layer,'USER_PATH',user):
            self.receipt['user_config_path']=str(user);self.flush()
            ctx=self.context()
            try:
                values={**web.make_default('daemon-v0.23.0'),'imu_mount_wxyz':[0,0,0,1],
                    'gyro_profile':'custom_raw_div_16_4','gyro_raw_counts_per_dps':16.4}
                ctx.save(dict(calibration=values,confirm_safe=True,confirm_hardware=True))
                self.assertEqual(self.config.read_bytes(),before)
                self.assertIn('hz=50',user.read_text())
            finally:ctx.monitor.close();ctx.servo.close()
        self.assertEqual(layer.default_calibration('daemon-v0.23.0'),'/var/lib/robot/calibration/microduck-0.23.0.toml')
        self.assertEqual(layer.default_calibration('daemon-v9.0.0'),'/etc/robot/robot-calibration.toml')
    def test_stale_page_target_and_missing_firmware_confirmation_are_rejected(self):
        ctx=self.context()
        try:
            for target,confirm in [('daemon-v0.18.3',True),('daemon-v0.23.0',False)]:
                values=web.make_default(target)
                values.update(gyro_profile='custom_raw_div_16_4',gyro_raw_counts_per_dps=16.4)
                with self.assertRaises(web.cal.CalibrationError):
                    ctx.save(dict(calibration=values,confirm_safe=True,confirm_hardware=confirm))
            self.assertFalse(self.dest.exists())
        finally:ctx.monitor.close();ctx.servo.close()
    def test_api68_read_profile_does_not_enable_unknown_versions_or_writes(self):
        a=web.daemon_adapter;m=dict(joint_names=a.JOINT_NAMES)
        self.assertTrue(a.contract(dict(api_version=68,daemon_version='0.23.0'),m)['read_contract_verified'])
        for api,version in [(69,'0.23.1'),(68,'0.18.3'),(47,'0.23.0')]:
            # API47 has its legacy read contract, but an explicit 0.18.3 target must match.
            self.assertFalse(a.contract(dict(api_version=api,daemon_version=version),m,'daemon-v0.23.0' if api!=47 else 'daemon-v0.18.3')['read_contract_verified'])
        self.assertFalse(a.contract(dict(api_version=47,daemon_version='0.23.0'),m)['read_contract_verified'])
        with self.assertRaises(ValueError):a.rpc(None,1,'robot.move',{})
    def test_camera_rotation_uses_reviewed_native_hash_and_never_double_rotates(self):
        c=web.native_camera.Camera(self.config,self.directory/'camera.json','daemon-v0.23.0',False,False,8080)
        c.runtime=self.directory/'media.json'
        exe='/opt/robot/daemon/releases/0.23.0-calv3/bin/mediad'
        c.runtime.write_text(json.dumps(dict(service='mediad',version='0.23.0',
            revision=web.NATIVE_0230_BUILD,pid=456,exe=exe)))
        with patch.object(web.native_camera.subprocess,'run',return_value=Mock(stdout='456')),patch.object(web.native_camera.os,'readlink',return_value=exe):
            for rotation in (0,90,180,270):
                with patch.object(Path,'read_bytes',return_value=(exe+'\0--rotate\0'+str(rotation)+'\0').encode()):
                    got=c._runtime(90)
                    self.assertEqual(got['rotation_degrees'],rotation)
                    self.assertEqual(got['snapshot_orientation'],'upright_by_mediad')
            self.hash_check.return_value=False
            with self.assertRaisesRegex(web.cal.CalibrationError,'hash'):c._runtime(90)

if __name__=='__main__':unittest.main()
