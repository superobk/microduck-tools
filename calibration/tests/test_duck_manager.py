"""Workflow tests use an injected filesystem and fake system. No SSH, systemd, bus or Linux ELF execution."""
import copy
import hashlib
import fcntl
import io
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('duck_manager',ROOT/'tools/duck-manager.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class FakeSystem(m.System):
    def __init__(self,paths,bundle):
        super().__init__(paths);self.bundle=bundle;self.calls=[];self.active={};self.busy=False;self.has_pending=False;self.fail_apply=None;self.sensor=False;self.pid=100;self.live_plugin=None
    def privileged(self):return True
    def is_board(self):return True
    def machine(self):return 'test-board-A'
    def run_review(self,argv,**kwargs):return subprocess.CompletedProcess(argv,0,'[]','')
    def run(self,args,timeout=30):
        args=[str(x) for x in args];self.calls.append(tuple(args))
        if args[:1]==['getent']:return 'microduck-tools:x:1001:1001::/nonexistent:/usr/sbin/nologin\n'
        if args[:2]==['systemctl','stop']:
            self.active[args[2]]=False
        if args[:2] in (['systemctl','start'],['systemctl','restart']):
            self.active[args[2]]=True
            if args[2]=='robotd.service':self.pid+=1
            if args[2]=='microduck-tools.service':
                p=self.p.at('/etc/microduck-tools/plugins.json');self.live_plugin=m.read_json(p)['servo_health']['enabled'] if p.exists() else None
        return ''
    def units(self,bundle):return ['robotd.service','updaterd.service','microduck-tools.service','robot-boot-check.timer']
    def guard_units(self,units):self.calls.append(('guard',*units))
    def updater(self,ctl):
        if self.busy:raise m.Error('busy')
        return dict(component='daemon',installed=self.actual()['release'],phase='idle')
    def pending(self):return self.has_pending
    def quiesce(self,units,ctl):
        if self.busy or self.has_pending:raise m.Error('busy/pending：未停服务')
        for unit in units:self.run(['systemctl','stop',unit])
    def props(self,unit):
        command=self.p.dropin(unit)
        raw=command.read_text() if command.exists() else ''
        return dict(Id=unit,LoadState='loaded',ActiveState='active' if self.active.get(unit) else 'inactive',MainPID=str(self.pid if self.active.get(unit) else 0),ExecStart=raw)
    def extract(self,archive,out):out.mkdir();import shutil;shutil.copytree(self.bundle/'official-tree',out,dirs_exist_ok=True)
    def runtime(self,bundle,official):
        self.calls.append(('runtime',str(official)))
        known=m.read_json(bundle/'inputs.json')
        for name,expected in known['official'].items():
            if m.digest(official/'bin'/name)!=expected:raise m.Error('runtime bytes mismatch')
        return {'passed':True}
    def image_external(self,image):self.calls.append(('image_external',str(image)))
    def archive(self,out):out.write_bytes(b'private-app-snapshot')
    def check_start(self,unit):self.calls.append(('check_start',unit))
    def identity_start(self,unit,expected):
        if expected not in self.props(unit).get('ExecStart',''):raise m.Error('fixture command differs')
        self.run(['systemctl','start',unit])
    def reload(self):self.calls.append(('daemon-reload',))
    def accounts(self):self.calls.append(('accounts',))
    def wait_maintenance_exit(self):self.calls.append(('wait_web_exit',))
    def tool_account(self):return os.geteuid(),os.getegid()
    def maintenance(self,state,bundle,camera_apply):
        self.calls.append(('maintenance',camera_apply))
        (state/'maintenance-camera.json').write_text('{"schema":"microduck-camera-rotation/v1","rotation":90}')
    def settle(self,units,ctl,progress):
        if self.busy or self.has_pending:raise m.Error('still pending')
        self.calls.append(('settled',))
    def live(self,args,log):
        self.calls.append(tuple(str(x) for x in args));log.write_text('fixture log')
        if '--dry-run' in args:return
        if self.fail_apply=='before':raise m.Error('apply client disconnected')
        target=self.p.at('/opt/robot/daemon/releases/0.18.3')
        import shutil
        shutil.copytree(self.bundle/'official-tree',target,dirs_exist_ok=True)
        self.p.current.unlink();self.p.current.symlink_to(target)
        if self.fail_apply=='after':raise m.Error('client disconnected after swap')
    def actual(self):
        version=self.p.current.resolve().name if self.p.current.exists() else None
        robot_active=bool(self.active.get('robotd.service'));native='/opt/microduck-tools/native/current/bin/robotd' in self.props('robotd.service')['ExecStart']
        path=self.p.at('/opt/microduck-tools/native/current/bin/robotd') if native else self.p.current/'bin/robotd'
        sha=m.digest(path) if path.exists() and robot_active else None
        known=m.read_json(self.bundle/'inputs.json');receipt=None;web=None
        if native and robot_active and self.sensor:
            receipt=dict(pid=self.pid,build_revision=known['native_revision'],user_layer_rejected=None)
        if self.active.get('microduck-tools.service'):
            web=dict(runtime_verified=receipt is not None,pending_restart=False if receipt else None,differences=[],demo=False,allow_inactive=False,runtime_reason='fixture with sensors' if receipt else 'no hardware receipt',capabilities=dict(plugins=[dict(id='servo_health',enabled=self.live_plugin)]))
        return dict(release=version,official_integrity=version=='0.18.3',robotd=dict(active='active' if robot_active else 'inactive',pid=str(self.pid if robot_active else 0),exe=str(path) if robot_active else None,sha256=sha),tools={},receipt=receipt,web=web,pending=self.has_pending)


class FixtureManager(m.Manager):
    def inputs(self):return m.read_json(self.bundle/'inputs.json')


class Fixture:
    def __init__(self,root,create=True):
        self.root=Path(root);self.p=m.Paths(self.root/'system');self.bundle=self.root/'bundle'
        if create:self.create()
        self.sys=FakeSystem(self.p,self.bundle);self.manager=FixtureManager(self.bundle,self.p,self.sys,progress=lambda x:None)
        self.image=self.root/'external/original.img'
    def create(self):
        p=self.p
        for path in ['/etc/robot','/opt/robot/daemon/releases/0.16.3/bin','/var/lib','/etc/systemd/system']:
            p.at(path).mkdir(parents=True,exist_ok=True)
        old=p.at('/opt/robot/daemon/releases/0.16.3');(old/'bin/robotctl').write_bytes(b'oldctl');(old/'bin/robotctl').chmod(0o755);p.current.symlink_to(old)
        p.base.write_text('[board]\nversion="zero3"\n[bus]\ncalibration_path="/etc/robot/actual.toml"\n')
        cal=m.calibration.parse_calibration(ROOT/'calibration/examples/demo-0.16.3.toml');cal['joint_zero_rad']=[.01]*15;cal['joint_direction']=[1,-1,1]*5
        p.at('/etc/robot/actual.toml').write_text(m.calibration.render_toml(cal))
        p.at('/etc/robot/updater.toml').write_text('auto_apply="mandatory"\n')
        b=self.bundle;(b/'official').mkdir(parents=True);(b/'native/bin').mkdir(parents=True);(b/'tool/deploy').mkdir(parents=True)
        names='btd configd mediad netpadd nfcd padd pet-detect pet-features robotctl robotd sounds tofd updaterd'.split()
        official={};native={}
        for name in names:
            file=b/'official-tree/bin'/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(('official-'+name).encode());file.chmod(0o755);official[name]=m.digest(file)
        for name in ('robotd','robotctl'):
            file=b/'native/bin'/name;file.write_bytes(('native-'+name).encode());file.chmod(0o755);native[name]=m.digest(file)
        (b/'inputs.json').write_text(json.dumps(dict(official=official,native=native,native_revision='b2b0d213-calv3-30452a086d1e')))
        (b/'tool/deploy/microduck-tools.service').write_bytes((ROOT/'deploy/microduck-tools.service').read_bytes())
        (b/'tool/RUNTIME-MANIFEST.json').write_text(json.dumps(dict(files=[dict(path='deploy/microduck-tools.service')],tool_version=(ROOT/'VERSION').read_text().strip())))
        (b/'deploy').mkdir()
        for name in ('robotd-native.conf','robotd-official.conf'):(b/'deploy'/name).write_bytes((ROOT/'deploy/manual-upgrade'/name).read_bytes())
        external=self.root/'external';external.mkdir();image=external/'original.img';image.write_bytes(b'complete-fixture-image')
        Path(str(image)+'.proof.json').write_text(json.dumps(dict(schema='microduck-image/v1',image_name=image.name,bytes=image.stat().st_size,sha256=m.digest(image),original_device={'path':'fixture-original-disk'},compared=True)))
    def backup(self):return self.manager.action('backup',True,image=self.image)
    def installed(self):self.backup();self.manager.action('install',True)
    def configured(self):self.installed();self.manager.action('configure',True)


class ManagerTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.fixture=Fixture(self.temp.name);self.f=self.fixture;self.manager=self.f.manager
    def tearDown(self):self.temp.cleanup()
    def test_maintenance_restores_regular_tools_without_robotd_restart(self):
        self.f.configured();self.manager.action('start',True);self.f.sys.calls.clear()
        self.manager.action('maintenance',True)
        self.assertTrue(self.f.sys.active['microduck-tools.service'])
        self.assertFalse(any(c[:2] in (('systemctl','stop'),('systemctl','restart')) and c[2]=='robotd.service' for c in self.f.sys.calls))
        self.assertNotIn(('systemctl','start','mediad.service'),self.f.sys.calls)
        self.assertEqual(m.read_json(self.f.p.at('/var/lib/microduck-tools/camera.json'))['rotation'],90)
        self.assertIsNone(self.manager.load()['maintenance'])
    def test_modified_installed_program_is_refused_before_start(self):
        self.f.configured();self.f.sys.calls.clear()
        self.f.p.at(m.NATIVE+'/bin/robotd').write_bytes(b'modified')
        with self.assertRaises(m.Error):self.manager.action('start',True)
        self.assertFalse(any(c[:2]==('systemctl','start') for c in self.f.sys.calls))
    def test_failed_backup_can_be_cancelled_without_completed_archive(self):
        self.f.sys.active['robotd.service']=True
        with patch.object(self.f.sys,'archive',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.f.backup()
        self.assertFalse(self.f.sys.active['robotd.service']);self.manager.action('recover',True)
        self.assertEqual(self.manager.status()['stage'],'备份未完成，原服务可能已停止')
        self.manager.action('rollback',True,to='session')
        self.assertTrue(self.f.sys.active['robotd.service']);self.assertEqual(self.f.sys.actual()['release'],'0.16.3')
    def test_camera_apply_starts_mediad_only_explicitly_and_restores_original_state(self):
        self.f.configured();self.manager.action('start',True)
        self.manager.action('maintenance',True,camera_apply=True)
        self.assertIn(('systemctl','start','mediad.service'),self.f.sys.calls)
        self.assertFalse(self.f.sys.active['mediad.service'])
    def test_failed_web_restores_tools_and_recovery_does_not_repeat_web(self):
        self.f.configured();self.manager.action('start',True)
        with patch.object(self.f.sys,'maintenance',side_effect=m.Error('Web bind failed')):
            with self.assertRaises(m.Error):self.manager.action('maintenance',True)
        self.assertTrue(self.f.sys.active['microduck-tools.service']);self.manager.action('recover',True)
        self.assertFalse(any(c[0]=='maintenance' for c in self.f.sys.calls))
    def test_camera_conflict_preserves_both_copies_and_restores_regular_service(self):
        self.f.configured();self.manager.action('start',True)
        settings=self.f.p.at('/var/lib/microduck-tools/camera.json')
        def conflict(state,bundle,camera_apply):
            (state/'maintenance-camera.json').write_text('{"schema":"microduck-camera-rotation/v1","rotation":90}')
            settings.parent.mkdir(parents=True,exist_ok=True)
            settings.write_text('{"schema":"microduck-camera-rotation/v1","rotation":270}')
        with patch.object(self.f.sys,'maintenance',side_effect=conflict):
            with self.assertRaises(m.Error):self.manager.action('maintenance',True)
        self.assertTrue(self.f.sys.active['microduck-tools.service']);self.assertEqual(m.read_json(settings)['rotation'],270)
        self.assertTrue((self.f.p.state/'maintenance-camera.json').exists())
        with self.assertRaises(m.Error):self.manager.action('recover',True)
        self.assertEqual(m.read_json(settings)['rotation'],270)
    def test_recover_interrupted_camera_commit_completes_ownership_and_cleanup(self):
        self.f.configured();self.manager.action('start',True)
        original=self.manager.managed
        def fail(s,path,raw):
            original(s,path,raw)
            if str(path).endswith('/camera.json'):raise KeyboardInterrupt()
        with patch.object(self.manager,'managed',side_effect=fail):
            with self.assertRaises(KeyboardInterrupt):self.manager.action('maintenance',True)
        self.manager.action('recover',True)
        self.assertIsNone(self.manager.load()['maintenance'])
        self.assertEqual(self.f.p.at('/var/lib/microduck-tools/camera.json').stat().st_mode&0o777,0o600)
    def test_maintenance_refuses_wrong_daemon_before_stopping_tools(self):
        self.f.configured();self.manager.action('start',True);self.f.sys.calls.clear()
        old=self.f.sys.actual
        def wrong():
            value=old();value['robotd']['sha256']='bad';return value
        with patch.object(self.f.sys,'actual',side_effect=wrong):
            with self.assertRaises(m.Error):self.manager.action('maintenance',True)
        self.assertNotIn(('systemctl','stop','microduck-tools.service'),self.f.sys.calls)
    def test_preview_never_creates_state_or_runs_mutation(self):
        for name in ('backup','install','configure','rollback','recover','start','stop'):
            self.assertFalse(self.manager.action(name)['applied'])
        self.assertFalse(self.f.p.state.exists());self.assertFalse(self.f.sys.calls)
    def test_menu_wrappers_are_executable_and_parse(self):
        for path in (ROOT/'deploy/manager').glob('*.sh'):
            self.assertTrue(path.stat().st_mode&0o111);subprocess.run(['sh','-n',str(path)],check=True)
        result=subprocess.run([str(ROOT/'deploy/manager/status.sh'),'--json'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('stage',json.loads(result.stdout))
    def test_full_flow_preserves_calibration_and_distinguishes_no_hardware(self):
        old=m.calibration.parse_calibration(self.f.p.at('/etc/robot/actual.toml'));self.f.configured()
        actual=m.calibration.parse_calibration(self.f.p.cal)
        for key in ('joint_zero_rad','joint_direction','imu_mount_wxyz'):self.assertEqual(old[key],actual[key])
        self.assertEqual(self.manager.status()['stage'],'已配置，待启动')
        self.manager.action('start',True)
        self.assertIn('等待真实回执',self.manager.status()['stage'])
        self.f.sys.sensor=True;self.assertIn('校正已加载',self.manager.status()['stage'])
        self.assertEqual(self.manager.status()['hardware'],'待现场验收')
    def test_install_requires_backup(self):
        with self.assertRaises(m.Error):self.manager.action('install',True)
        self.assertFalse(any('apply' in call for call in self.f.sys.calls))
    def test_image_bad_sha_blocks_before_any_service_stop(self):
        self.f.image.write_bytes(b'tampered')
        with self.assertRaises(m.Error):self.f.backup()
        self.assertFalse(any(call[:2]==('systemctl','stop') for call in self.f.sys.calls))
    def test_nondefault_scale_requires_confirmation(self):
        path=self.f.p.at('/etc/robot/actual.toml');data=m.calibration.parse_calibration(path);data.update(schema=2,imu_module='bus_imu200',gyro_profile='custom_raw_div_16_4',gyro_raw_counts_per_dps=16.4);path.write_text(m.calibration.render_toml(data))
        with self.assertRaises(m.Error):self.f.backup()
        self.assertFalse(any(call[:2]==('systemctl','stop') for call in self.f.sys.calls))
        self.manager.action('recover',True);self.manager.action('backup',True,image=self.f.image,scale_confirmed=True)
        self.assertEqual(m.calibration.parse_calibration(self.manager.load()['backup']['calibration'])['gyro_raw_counts_per_dps'],16.4)
    def test_updater_busy_never_stops_services(self):
        self.f.sys.busy=True
        with self.assertRaises(m.Error):self.f.backup()
        self.assertFalse(any(call[:2]==('systemctl','stop') for call in self.f.sys.calls))
    def test_client_disconnect_after_swap_does_not_reapply(self):
        self.f.backup();self.f.sys.fail_apply='after'
        with self.assertRaises(m.Error):self.manager.action('install',True)
        self.assertIn('中断',self.manager.status()['stage']);before=list(self.f.sys.calls)
        with self.assertRaises(m.Error):self.manager.action('install',True)
        self.manager.action('recover',True);self.assertEqual(self.manager.load()['stage'],'official_installed')
        self.assertEqual(sum('apply' in x and '--dry-run' not in x for x in self.f.sys.calls),1)
    def test_disconnect_before_swap_requires_inspection_not_retry(self):
        self.f.backup();self.f.sys.fail_apply='before'
        with self.assertRaises(m.Error):self.manager.action('install',True)
        with self.assertRaises(m.Error):self.manager.action('recover',True)
        self.assertTrue(self.manager.load()['install_attempted'])
        self.assertEqual(sum('apply' in x and '--dry-run' not in x for x in self.f.sys.calls),1)
    def test_partial_configure_recover_and_reapply_is_idempotent(self):
        self.f.installed();original=self.manager.managed
        def fail(s,path,raw):
            original(s,path,raw)
            if path==self.f.p.cal:raise KeyboardInterrupt('interrupted after first file')
        with patch.object(self.manager,'managed',side_effect=fail):
            with self.assertRaises(KeyboardInterrupt):self.manager.action('configure',True)
        self.assertIsNotNone(self.manager.load()['operation'])
        self.manager.action('recover',True);self.manager.action('configure',True)
        self.manager.action('configure',True);self.assertTrue(self.manager.load()['configured'])
    def test_external_file_change_is_preserved(self):
        self.f.configured();self.f.p.user.write_text('external=123\n')
        self.manager.action('configure',True)
        self.assertEqual(self.f.p.user.read_text(),'external=123\n')
        self.assertEqual(self.manager.status()['stage'],'配置需检查')
        with self.assertRaises(m.Error):self.manager.action('start',True)
    def test_owned_restore_never_changes_foreign_unit(self):
        self.f.backup();s=self.manager.load();foreign=self.f.p.at('/etc/systemd/system/sshd.service');foreign.write_text('untouched')
        self.manager.managed(s,self.f.p.dropin('updaterd.service'),b'owned override');s['operation']=None;self.manager.save(s)
        self.manager.action('rollback',True,to='session')
        self.assertFalse(self.f.p.dropin('updaterd.service').exists());self.assertEqual(foreign.read_text(),'untouched')
    def test_ledger_cannot_restore_foreign_path(self):
        self.f.backup();s=self.manager.load();foreign=self.f.p.at('/etc/systemd/system/sshd.service');foreign.write_text('foreign')
        s['ledger'][str(foreign)]=dict(before=m.digest(foreign),snapshot=None,allowed=[])
        with self.assertRaises(m.Error):self.manager.restore_files(s)
        self.assertEqual(foreign.read_text(),'foreign')
    def test_snapshot_mutation_is_refused(self):
        self.f.backup();s=self.manager.load();self.manager.managed(s,self.f.p.user,b'[bus]\ncalibration_path="new"\n')
        s['ledger'][str(self.f.p.user)]['snapshot']='/etc/passwd'
        with self.assertRaises(m.Error):self.manager.restore_files(s)
    def test_rollback_to_official_keeps_new_suite_and_no_policy(self):
        self.f.configured();self.manager.action('start',True);self.manager.action('rollback',True,to='official')
        self.assertEqual(self.f.sys.actual()['release'],'0.18.3');text=self.f.p.dropin('robotd.service').read_text();self.assertIn('/opt/robot/daemon/current/bin/robotd',text);self.assertIn('--no-policy',text)
    def test_session_restore_after_apply_is_refused(self):
        self.f.installed()
        with self.assertRaises(m.Error):self.manager.action('rollback',True,to='session')
    def test_plugin_only_change_does_not_stop_or_restart_robotd(self):
        self.f.configured();self.manager.action('start',True);pid=self.f.sys.pid;self.f.sys.calls.clear()
        self.manager.action('configure',True,servo_health=False)
        self.assertEqual(self.f.sys.pid,pid)
        self.assertFalse(any(call[:2]==('systemctl','stop') for call in self.f.sys.calls))
        self.assertIn(('systemctl','restart','microduck-tools.service'),self.f.sys.calls)
        self.assertFalse(m.read_json(self.f.p.at('/etc/microduck-tools/plugins.json'))['servo_health']['enabled'])
    def test_immutable_slot_ignores_python_cache_but_rejects_extra_code(self):
        self.f.configured();tool=self.f.p.at('/opt/microduck-tools/releases/'+(ROOT/'VERSION').read_text().strip());cache=tool/'__pycache__';cache.mkdir();(cache/'cached.pyc').write_bytes(b'cache')
        self.manager.copy_release(self.f.bundle/'tool',tool)
        (tool/'unexpected.py').write_text('extra')
        with self.assertRaises(m.Error):self.manager.copy_release(self.f.bundle/'tool',tool)
    def test_source_material_changed_cannot_take_over_session(self):
        self.f.backup();p=self.f.bundle/'inputs.json';data=m.read_json(p);data['changed']=True;p.write_text(json.dumps(data))
        with self.assertRaises(m.Error):self.manager.action('install',True)
    def test_wrong_board_and_corrupt_session_are_not_reset(self):
        self.f.backup();s=self.manager.load();s['machine']='other';self.manager.save(s)
        with self.assertRaises(m.Error):self.manager.load()
        (self.f.p.state/'session.json').write_text('{bad')
        self.assertEqual(self.manager.status()['stage'],'会话损坏')
    def test_state_must_be_private(self):
        self.f.p.state.mkdir(parents=True,mode=0o755);self.f.p.state.chmod(0o755)
        with self.assertRaises(m.Error):self.manager.action('check')
    def test_image_offline_is_visible_and_rebind_checks_hash(self):
        self.f.backup();new=self.f.image.with_name('moved.img');self.f.image.rename(new)
        self.assertEqual(self.manager.status()['stage'],'备份介质离线')
        self.manager.action('backup',True,image=new)
        self.assertEqual(self.manager.load()['backup']['image'],str(new.resolve()))
    def test_pending_recovery_never_starts_second_installer(self):
        self.f.installed();s=self.manager.load();s['operation']={'name':'install','step':'unknown'};self.manager.save(s);self.f.sys.has_pending=True
        with self.assertRaises(m.Error):self.manager.action('recover',True)
        self.assertEqual(sum('apply' in x and '--dry-run' not in x for x in self.f.sys.calls),1)
    def test_native_bytes_on_old_official_suite_cannot_be_green(self):
        self.f.configured();self.manager.action('start',True);self.f.sys.sensor=True
        old=self.f.p.at('/opt/robot/daemon/releases/0.16.3');self.f.p.current.unlink();self.f.p.current.symlink_to(old)
        self.assertNotIn('校正已加载',self.manager.status()['stage'])
    def test_process_manager_lock_prevents_second_operation(self):
        with self.manager.locked():
            with self.assertRaises(m.Error):self.manager.action('check')
        self.assertFalse(self.f.sys.calls)
    def test_original_parameters_changed_after_backup_are_not_overwritten(self):
        self.f.backup();self.f.p.base.write_text('[board]\nversion="beta"\n')
        with self.assertRaises(m.Error):self.manager.action('install',True)
        self.assertEqual(sum('apply' in x for x in self.f.sys.calls),0)
    def test_web_calibration_is_preserved_on_repeat_and_official_round_trip(self):
        self.f.configured();data=m.calibration.parse_calibration(self.f.p.cal);data['joint_zero_rad'][0]=.12;self.f.p.cal.write_text(m.calibration.render_toml(data))
        self.manager.action('configure',True);self.assertEqual(m.calibration.parse_calibration(self.f.p.cal)['joint_zero_rad'][0],.12)
        self.manager.action('rollback',True,to='official');self.manager.action('configure',True)
        self.assertEqual(m.calibration.parse_calibration(self.f.p.cal)['joint_zero_rad'][0],.12)
    def test_existing_new_calibration_is_not_reset_on_first_configuration(self):
        self.f.installed();self.f.p.cal.parent.mkdir(parents=True);self.f.p.cal.write_text('external calibration')
        with self.assertRaises(m.Error):self.manager.action('configure',True)
        self.assertEqual(self.f.p.cal.read_text(),'external calibration')
    def test_failed_backup_keeps_original_running_set_for_retry(self):
        self.f.sys.active={'robotd.service':True,'updaterd.service':True}
        with patch.object(self.f.sys,'archive',side_effect=m.Error('disk interrupted')):
            with self.assertRaises(m.Error):self.f.backup()
        self.manager.action('recover',True);self.f.backup()
        self.assertIn('robotd.service',self.manager.load()['active_before'])
        self.assertTrue(Path(self.manager.load()['backup']['archive']).is_relative_to(self.f.image.parent.resolve()))
    def test_full_command_check_refuses_embedded_expected_substring(self):
        system=m.System(self.f.p);path=self.f.p.dropin('robotd.service');path.parent.mkdir(parents=True)
        raw=(ROOT/'deploy/manual-upgrade/robotd-native.conf').read_text();path.write_text(raw);expected=[x[10:] for x in raw.splitlines() if x.startswith('ExecStart=') and x!='ExecStart='][0]
        with patch.object(system,'props',return_value={'ExecStart':'{ path=/opt/robotd ; argv[]='+expected+' ; ignore_errors=no ; }'}):system.check_start('robotd.service')
        for command in ('/usr/bin/echo '+expected,expected+' --extra-unreviewed',expected.replace('--no-policy','')):
            with patch.object(system,'props',return_value={'ExecStart':'{ path=/opt/robotd ; argv[]='+command+' ; ignore_errors=no ; }'}):
                with self.assertRaises(m.Error):system.check_start('robotd.service')
    def test_real_updater_mutex_is_respected_without_stopping_any_service(self):
        directory=Path(self.temp.name)/'updater-state';directory.mkdir();config=self.f.p.at('/etc/robot/updater.toml');config.write_text('state_dir='+json.dumps(str(directory))+'\n[component.daemon]\ninstall_dir="/opt/robot/daemon"\n')
        system=m.System(self.f.p);system.run_review=lambda *a,**k:subprocess.CompletedProcess([],0,'[]','')
        with patch.object(system,'run') as run:
            with (directory/'update.lock').open('a+b') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(m.Error):system.quiesce(['updaterd.service'],'unused-client')
            run.assert_not_called()
        (directory/'pending.json').write_text('{"daemon":{"version":"0.18.3"}}')
        with patch.object(system,'run') as run:
            with self.assertRaises(m.Error):system.quiesce(['updaterd.service'],'unused-client')
            run.assert_not_called()
    def test_existing_root_service_account_is_refused(self):
        system=m.System(self.f.p)
        with patch.object(system,'run',return_value='robot:x:1000:\n') as run,patch.object(system,'run_review',return_value=subprocess.CompletedProcess([],0,'microduck-tools:x:0:0::/:/bin/sh\n','')):
            with self.assertRaises(m.Error):system.accounts()
            self.assertEqual(run.call_args_list[0].args[0],['getent','group','robot']);self.assertEqual(run.call_count,1)
    def test_unknown_and_busy_updater_status_fail_closed(self):
        for value in ({},[],[{'component':'model','phase':'idle'}],[{'component':'daemon','phase':'health_gate'}],[{'component':'daemon','phase':'future-phase'}]):
            with self.assertRaises(m.Error):m.updater_idle(value)
        self.assertEqual(m.updater_idle([{'component':'daemon','phase':'idle'}])['phase'],'idle')
    def test_reload_is_explicit_and_start_enables_only_the_tool(self):
        self.f.configured();self.manager.action('start',True)
        self.assertIn(('systemctl','enable','microduck-tools.service'),self.f.sys.calls)
        self.f.sys.calls.clear();self.manager.action('reload',True)
        self.assertTrue(any(c[:2]==('systemctl','stop') for c in self.f.sys.calls))
        self.assertFalse(any(set(c)&{'init','home','walk'} for c in self.f.sys.calls))
    def test_disk_identity_mismatch_is_never_force_bypassed(self):
        for key in ('cid','wwn','serial'):
            with self.assertRaises(m.Error):m.compare_disk_identity({key:'original'},{key:'other'})
        self.assertTrue(m.compare_disk_identity({'cid':'same'},{'cid':'same'}));self.assertFalse(m.compare_disk_identity({},{}))
    def test_failed_image_write_never_gets_complete_proof(self):
        image=Path(self.temp.name)/'partial.img';check=dict(disk='/dev/fixture',bytes=16,identity={})
        previous=os.umask(0o077)
        def execute(argv,**kwargs):
            self.assertEqual(argv[0],'dd');image.write_bytes(b'partial');raise subprocess.CalledProcessError(1,argv)
        try:
            with patch.object(m.platform,'system',return_value='Linux'),patch.object(m.os,'geteuid',return_value=0),patch.object(m.sys.stdin,'isatty',return_value=True),patch.object(m.review,'disk_check',return_value=check),patch.object(m,'disk_identity',return_value={'cid':'fixture'}),patch('builtins.input',return_value='/dev/fixture'),patch.object(m.subprocess,'run',side_effect=execute),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):m.image_operation('backup','/dev/fixture',image,True)
            self.assertFalse(Path(str(image)+'.proof.json').exists())
        finally:os.umask(previous)
    def test_local_json_status_does_not_claim_board_validation(self):
        result=subprocess.run([str(ROOT/'deploy/manager/status.sh'),'--json'],capture_output=True,text=True,env={**os.environ,'PYTHONIOENCODING':'ascii'})
        self.assertEqual(result.returncode,0,result.stderr)
        if sys.platform!='linux':self.assertEqual(json.loads(result.stdout)['hardware'],'NOT_RUN')

    @unittest.skipUnless(hasattr(signal,'SIGKILL'),'requires POSIX process kill')
    def test_real_sigkill_after_config_file_is_recoverable(self):
        self.f.installed();ready=Path(self.temp.name)/'ready'
        code='''import importlib.util,sys,signal
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from test_duck_manager import Fixture
f=Fixture(sys.argv[2],create=False);original=f.manager.managed
def hold(s,path,raw):
 original(s,path,raw)
 if path==f.p.cal:
  Path(sys.argv[3]).write_text('ready')
  signal.pause()
f.manager.managed=hold
f.manager.action('configure',True)
'''
        process=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'calibration/tests'),self.temp.name,str(ready)])
        try:
            deadline=time.monotonic()+5
            while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(ready.exists());process.kill();process.wait(timeout=5)
            self.assertEqual(process.returncode,-signal.SIGKILL);self.assertIsNotNone(self.manager.load()['operation'])
            self.manager.action('recover',True);self.manager.action('configure',True);self.assertTrue(self.manager.load()['configured'])
        finally:
            if process.poll() is None:process.kill();process.wait()


if __name__=='__main__':unittest.main()
