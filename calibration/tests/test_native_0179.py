"""Native calibration contract tests: temporary files and mocked live receipts only."""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'integration-0.16.3/web'))
import calibration_web as web


class Native0179Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.config=self.directory/'robotd.toml';self.config.write_text('[board]\nversion="zero3"\n[bus]\nport="/dev/SYNTHETIC"\n')
        self.dest=self.directory/'calibration.toml';self.runtime=self.directory/'runtime.json'
        self.executable='/opt/microduck-tools/native/releases/SYNTHETIC/bin/robotd'
        self.receipt={
            **web.make_default('daemon-v0.17.9'),'source':'robotd:DynamixelIo:live','pid':123,'bus_verified':True,
            'native_calibration_contract':'microduck-calibration/v2','base_revision':web.NATIVE_0179_BASE,
            'daemon_version':'0.17.9','build_revision':web.NATIVE_0179_BUILD,
            'config_path':str(self.config),'executable':self.executable,'calibration_path':'','calibration_active':False}
        self.runtime.write_text(json.dumps(self.receipt))

    def tearDown(self):self.temp.cleanup()

    def context(self,target='daemon-v0.17.9',allow_inactive=False):
        args=['--target',target,'--no-telemetry','--config',str(self.config),'--dest',str(self.dest),'--runtime',str(self.runtime)]
        if allow_inactive:args.append('--allow-inactive')
        return web.Context(web.parser().parse_args(args))

    def test_official_version_or_allow_inactive_cannot_enable_native_writes(self):
        self.runtime.write_text(json.dumps({**web.make_default('daemon-v0.17.9'),'source':'robotd:DynamixelIo:live','pid':123,'bus_verified':True}))
        ctx=self.context(allow_inactive=True)
        try:
            with patch.object(web,'valid_active_file',return_value=(True,'synthetic PID')):
                self.assertFalse(ctx.status()['runtime_verified']);self.assertFalse(ctx.status()['can_save'])
                with self.assertRaises(web.cal.CalibrationError):ctx.save({'calibration':web.make_default('daemon-v0.17.9')})
            self.assertFalse(self.dest.exists())
        finally:ctx.monitor.close();ctx.servo.close()

    def test_auto_recognises_verified_0179_and_saves_16_4_without_restarting(self):
        ctx=self.context('auto')
        try:
            with patch.object(web,'valid_active_file',return_value=(True,'synthetic PID')),patch.object(web.os,'readlink',return_value=self.executable),patch.object(web.subprocess,'run') as run:
                status=ctx.status();self.assertEqual(status['calibration_target'],'daemon-v0.17.9');self.assertTrue(status['can_save'])
                values=web.make_default('daemon-v0.17.9');values.update(gyro_profile='custom_raw_div_16_4',gyro_raw_counts_per_dps=16.4)
                payload=dict(calibration=values,confirm_safe=True,confirm_hardware=True)
                self.assertTrue(ctx.save(payload)['ok']);run.assert_not_called()
                saved=web.cal.parse_calibration(self.dest,'daemon-v0.17.9')
                self.assertEqual(saved['gyro_raw_counts_per_dps'],16.4)
                self.assertAlmostEqual(web.cal.effective_gyro(saved)['gyro_rad_per_raw_count']*164,math.radians(10))
                self.assertTrue(ctx.status()['pending_restart']);self.assertTrue(list(self.directory.glob('robotd.toml.backup-*')))
                # Only a new receipt from the decoder proves application, not reading the saved file.
                self.receipt.update(gyro_raw_counts_per_dps=16.4,calibration_path=str(self.dest),calibration_active=True)
                self.runtime.write_text(json.dumps(self.receipt));self.assertFalse(ctx.status()['pending_restart'])
                self.config.write_text('[bus]\nport="/dev/SYNTHETIC"\n')
                self.assertTrue(ctx.status()['pending_restart'])
        finally:ctx.monitor.close();ctx.servo.close()

    def test_native_identity_config_and_value_mismatches_refuse_writes(self):
        bad=[{'base_revision':'wrong'},{'build_revision':'official-only'},{'executable':'/wrong/robotd'},
             {'build_revision':'96b89d86-calv3-'+12*'a'},
             {'config_path':'/wrong/config.toml'},{'gyro_raw_counts_per_dps':0},
             {'joint_direction':[0]*15},{'imu_mount_wxyz':[0,0,0,0]}]
        for changed in bad:
            with self.subTest(changed=changed),patch.object(web.os,'readlink',return_value=self.executable):
                self.assertFalse(web.verify_0179_receipt({**self.receipt,**changed},self.config)[0])

    def test_firmware_and_safe_confirmations_are_required(self):
        ctx=self.context();values=web.make_default('daemon-v0.17.9');values.update(gyro_profile='custom_raw_div_16_4',gyro_raw_counts_per_dps=16.4)
        try:
            with patch.object(web,'valid_active_file',return_value=(True,'synthetic PID')),patch.object(web.os,'readlink',return_value=self.executable):
                for confirmations in [dict(confirm_safe=True),dict(confirm_hardware=True),{}]:
                    with self.assertRaises(web.cal.CalibrationError):ctx.save(dict(calibration=values,**confirmations))
            self.assertFalse(self.dest.exists())
        finally:ctx.monitor.close();ctx.servo.close()

    def test_verified_native_contract_still_requires_write_permissions(self):
        ctx=self.context()
        try:
            with patch.object(web,'valid_active_file',return_value=(True,'synthetic PID')),patch.object(web.os,'readlink',return_value=self.executable),patch.object(web.os,'access',return_value=False):
                self.assertTrue(ctx.status()['runtime_verified']);self.assertFalse(ctx.status()['can_save'])
        finally:ctx.monitor.close();ctx.servo.close()

    def test_calibration_migration_preserves_offsets_directions_and_mount(self):
        original=web.make_default('daemon-v0.16.3');original['joint_zero_rad'][2]=.08;original['joint_direction'][4]=-1
        source=self.directory/'old.toml';source.write_text(web.cal.render_toml(original));before=source.read_bytes()
        args=web.cal.parser().parse_args(['migrate','--source',str(source),'--out',str(self.dest),'--target','daemon-v0.17.9',
            '--gyro-profile','custom_raw_div_16_4','--confirm-same-hardware','--confirm-firmware-scale'])
        args.fn(args);result=web.cal.parse_calibration(self.dest,'daemon-v0.17.9')
        for key in ('joint_zero_rad','joint_direction','imu_mount_wxyz'):self.assertEqual(result[key],web.cal.parse_calibration(source)[key])
        self.assertEqual(result['gyro_raw_counts_per_dps'],16.4);self.assertEqual(source.read_bytes(),before)
        with self.assertRaises(web.cal.CalibrationError):args.fn(args)

    def test_migration_requires_confirmation_and_never_overwrites_source(self):
        source=self.directory/'old.toml';source.write_text(web.cal.render_toml(web.make_default('daemon-v0.16.3')))
        args=web.cal.parser().parse_args(['migrate','--source',str(source),'--out',str(source),'--target','daemon-v0.17.9'])
        with self.assertRaises(web.cal.CalibrationError):args.fn(args)
        args.confirm_same_hardware=True
        with self.assertRaises(web.cal.CalibrationError):args.fn(args)
        args.out=self.dest;args.gyro_raw_counts_per_dps=16.4
        with self.assertRaises(web.cal.CalibrationError):args.fn(args)

    def test_published_native_revision_matches_write_allowlist(self):
        metadata=json.loads((ROOT/'daemon-v0.17.9/native-build.json').read_text())
        self.assertEqual(metadata['build_revision'],web.NATIVE_0179_BUILD)

    def test_malformed_runtime_fails_closed_without_breaking_status(self):
        self.runtime.write_text('[]');ctx=self.context()
        try:self.assertFalse(ctx.status()['runtime_verified']);self.assertFalse(ctx.status()['can_save'])
        finally:ctx.monitor.close();ctx.servo.close()

    def test_root_state_write_preserves_service_file_owner(self):
        target=self.directory/'state.json';target.write_text('{}');owner=target.stat()
        for writer in (lambda: web.cal.atomic_write(target,b'{}',0o600),lambda: web.servo_health.atomic_json(target,{})):
            with patch.object(web.os,'geteuid',return_value=0),patch.object(web.os,'fchown') as chown:
                writer();self.assertEqual(chown.call_args.args[1:],(owner.st_uid,owner.st_gid))


if __name__=='__main__':unittest.main()
