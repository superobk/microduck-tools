"""PR9 review: live decoder flags and camera transaction failures must fail closed."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
import test_native_0230 as previous
import calibration_web as web
REAL_ACTIVE=web.valid_active_file

class ReceiptReviewTests(unittest.TestCase):
    setUp=previous.Native0230Tests.setUp
    tearDown=previous.Native0230Tests.tearDown
    flush=previous.Native0230Tests.flush
    context=previous.Native0230Tests.context
    def test_inactive_calibration_is_not_reported_as_synchronized(self):
        ctx=self.context()
        try:
            values={**web.make_default('daemon-v0.23.0'),'imu_mount_wxyz':[0,0,0,1],
                'gyro_profile':'custom_raw_div_16_4','gyro_raw_counts_per_dps':16.4}
            ctx.save(dict(calibration=values,confirm_safe=True,confirm_hardware=True))
            self.receipt.update(calibration_path=str(self.dest),calibration_active=False)
            self.flush()
            self.assertTrue(ctx.status()['pending_restart'])
        finally:ctx.monitor.close();ctx.servo.close()
    def test_api68_live_receipt_requires_known_systemd_pid(self):
        with patch.object(web,'read_robotd_pid',return_value=None),patch.object(Path,'read_bytes',return_value=b'robotd\0'):
            self.assertFalse(REAL_ACTIVE(self.receipt)[0])
    def test_target_tag_is_part_of_native_receipt_identity(self):
        data={**self.receipt,'target_tag':'daemon-v0.18.3'}
        self.assertFalse(web.verify_native_receipt(data,self.config,'daemon-v0.23.0')[0])

class CameraReviewTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.config=self.root/'robotd.toml';self.config.write_text('[board]\nversion="zero3"\n')
        self.settings=self.root/'camera.json';self.identity=self.root/'identity.json';self.dropin=self.root/'unit/camera.conf'
        self.camera=web.native_camera.Camera(self.config,self.settings,'auto',False,True,8080,identity=self.identity,dropin_path=self.dropin)
    def tearDown(self):self.temp.cleanup()
    def test_non_object_media_identity_does_not_break_other_status(self):
        for value in [[],None,5,'broken']:
            self.identity.write_text(json.dumps(value))
            status=self.camera.status()
            self.assertFalse(status['runtime_verified'])
            self.assertFalse(status['can_save'])
    def test_saved_direction_changed_after_review_cannot_be_applied(self):
        self.camera.target='daemon-v0.23.0'
        old=json.dumps(dict(schema='microduck-camera-rotation/v1',rotation=90)).encode()
        self.settings.write_bytes(old);revision=self.camera._saved()[1]
        self.settings.write_text(json.dumps(dict(schema='microduck-camera-rotation/v1',rotation=270)))
        with patch.object(self.camera,'_runtime',return_value=dict(pid=123,rotation_degrees=90,version='0.23.0')),patch.object(self.camera,'_unit'),patch.object(web.native_camera.subprocess,'run') as run:
            with self.assertRaisesRegex(web.cal.CalibrationError,'conflict'):
                self.camera.apply(dict(confirm_video_interrupt=True,config_revision=revision))
            run.assert_not_called()
            self.assertFalse(self.dropin.exists())

class MigrationReviewTests(unittest.TestCase):
    def test_all_existing_targets_preserve_horizontal_16_4_and_fifteen_joint_values(self):
        import tempfile
        from contextlib import redirect_stdout
        import io,math
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for tag in ('daemon-v0.16.3','daemon-v0.17.8','daemon-v0.17.9','daemon-v0.18.3'):
                values=web.make_default(tag)
                values.update(imu_mount_wxyz=[0,0,0,1],gyro_profile='custom_raw_div_16_4',gyro_raw_counts_per_dps=16.4,
                    joint_zero_rad=[i/100 for i in range(15)],joint_direction=[(-1)**i for i in range(15)])
                source=root/(tag+'.toml');source.write_text(web.cal.render_toml(values));before=source.read_bytes()
                output=root/(tag+'-migrated.toml')
                args=web.cal.parser().parse_args(['migrate','--source',str(source),'--out',str(output),
                    '--target','daemon-v0.23.0','--confirm-same-hardware','--confirm-firmware-scale'])
                with redirect_stdout(io.StringIO()):args.fn(args)
                migrated=web.cal.parse_calibration(output,'daemon-v0.23.0')
                for key in ('imu_mount_wxyz','gyro_profile','gyro_raw_counts_per_dps','joint_zero_rad','joint_direction'):
                    self.assertEqual(migrated[key],values[key])
                self.assertEqual(source.read_bytes(),before)
                self.assertAlmostEqual(web.cal.effective_gyro(migrated)['gyro_rad_per_raw_count']*164,math.radians(10))
    def test_executable_replaced_during_hash_verification_is_rejected(self):
        from types import SimpleNamespace
        profiles=web.daemon_profiles
        old=SimpleNamespace(st_dev=1,st_ino=1,st_size=10,st_mtime_ns=1,st_ctime_ns=1)
        new=SimpleNamespace(st_dev=1,st_ino=2,st_size=10,st_mtime_ns=1,st_ctime_ns=1)
        with patch.object(Path,'stat',side_effect=[old,new]),patch.object(profiles,'_digest',return_value='pinned'):
            with self.assertRaisesRegex(ValueError,'changed'):profiles.process_hash(333)

class HashCacheTests(unittest.TestCase):
    def test_cache_rejects_file_opened_from_a_different_executable_inode(self):
        import tempfile
        profiles=web.daemon_profiles
        profiles._digest.cache_clear()
        try:
            with tempfile.TemporaryDirectory() as directory:
                a=Path(directory)/'before';b=Path(directory)/'reexec'
                a.write_bytes(b'unknown executable');b.write_bytes(b'reviewed executable');before=a.stat()
                with patch.object(Path,'stat',return_value=before),patch.object(Path,'open',side_effect=lambda *a,**k:open(b,'rb')):
                    with self.assertRaisesRegex(ValueError,'changed'):profiles.process_hash(333)
                self.assertEqual(profiles._digest.cache_info().currsize,0)
        finally:profiles._digest.cache_clear()

if __name__=='__main__':unittest.main()
