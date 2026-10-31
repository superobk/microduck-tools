"""Manual upgrade helpers: temporary files and injected probes only; no board access."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import signal
import time
import re
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('upgrade_review',ROOT/'tools/upgrade-review.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fake_elf():
    b=bytearray(1024);b[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',b,18,183);struct.pack_into('<Q',b,32,64);struct.pack_into('<HH',b,54,56,2)
    struct.pack_into('<IIQQQQQQ',b,64,1,4,0,0x400000,0,1024,1024,4096)
    struct.pack_into('<IIQQQQQQ',b,120,2,4,256,0x400100,0,80,80,8)
    for i,(k,v) in enumerate([(5,0x400200),(1,1),(0x6ffffffe,0x400280),(0x6fffffff,1),(0,0)]):struct.pack_into('<qQ',b,256+16*i,k,v)
    strings=b'\0libc.so.6\0GLIBC_2.17\0GLIBC_2.30\0';b[512:512+len(strings)]=strings
    struct.pack_into('<HHIII',b,640,1,2,1,16,0)
    struct.pack_into('<IHHII',b,656,0,0,2,strings.index(b'GLIBC_2.17'),16)
    struct.pack_into('<IHHII',b,672,0,0,3,strings.index(b'GLIBC_2.30'),0)
    b[900:910]=b'GLIBC_9.99';return bytes(b)


class UpgradeReviewTests(unittest.TestCase):
    def test_elf_reads_version_table_not_stray_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'elf';p.write_bytes(fake_elf());r=m.elf(p)
            self.assertEqual(r['glibc_floor'],'2.30');self.assertEqual(r['needed'],['libc.so.6'])

    def test_elf_rejects_wrong_architecture_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'elf'
            for raw in (b'',b'\x7fELF',fake_elf()[:670]):
                p.write_bytes(raw)
                with self.assertRaises(m.Error):m.elf(p)
            raw=bytearray(fake_elf());struct.pack_into('<H',raw,18,62);p.write_bytes(raw)
            with self.assertRaises(m.Error):m.elf(p)

    def test_manual_config_changes_only_root_auto_apply(self):
        for prefix in ('','auto_apply="mandatory"\n','auto_apply="all" # old\n'):
            raw=prefix+'trusted_keys_dir="/keys"\n[component.daemon]\ninstall_dir="/opt/robot/daemon"\n[component.daemon.source]\ntype="github_releases"\nrepo="real/repo"\n[component.daemon.health]\nprobe="socket"\ntimeout="30s"\n'
            expected=tomllib.loads(raw);expected['auto_apply']='off'
            result=m.manual_config(raw);self.assertEqual(tomllib.loads(result),expected);self.assertEqual(m.manual_config(result),result)

    def test_manual_config_rejects_broken_toml(self):
        with self.assertRaises(ValueError):m.manual_config('auto_apply="all"\nauto_apply="off"\n')

    def test_atomic_file_failure_before_replace_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'file';p.write_bytes(b'old')
            with patch.object(m.os,'replace',side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):m.atomic_file(p,b'new')
            self.assertEqual(p.read_bytes(),b'old');m.atomic_file(p,b'new');self.assertEqual(p.read_bytes(),b'new')

    def test_atomic_link_idempotent_and_guards_changed_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);old=d/'old';new=d/'new';old.mkdir();new.mkdir();link=d/'current';link.symlink_to(old)
            with self.assertRaises(m.Error):m.tool_link(link,new,'MISSING')
            self.assertEqual(m.tool_link(link,new,str(old)),'selected')
            with patch.object(m.os,'fsync',wraps=m.os.fsync) as sync:
                self.assertEqual(m.tool_link(link,new,str(old)),'already-selected');self.assertGreater(sync.call_count,0)

    def test_atomic_link_interruption_before_and_after_replace_can_resume(self):
        for after in (False,True):
            with self.subTest(after=after),tempfile.TemporaryDirectory() as tmp:
                d=Path(tmp);old=d/'old';new=d/'new';old.mkdir();new.mkdir();link=d/'current';link.symlink_to(old);replace=m.os.replace
                def fail(*args):
                    if after:replace(*args)
                    raise OSError('interrupted')
                with patch.object(m.os,'replace',side_effect=fail):
                    with self.assertRaises(OSError):m.tool_link(link,new,str(old))
                self.assertEqual(os.readlink(link),str(new if after else old))
                m.tool_link(link,new,str(old));self.assertEqual(os.readlink(link),str(new))

    @unittest.skipUnless(hasattr(signal,'SIGKILL'),'requires POSIX process kill')
    def test_real_sigkill_before_and_after_link_replace(self):
        for after in (False,True):
            with self.subTest(after=after),tempfile.TemporaryDirectory() as tmp:
                d=Path(tmp);old=d/'old';new=d/'new';old.mkdir();new.mkdir();link=d/'current';link.symlink_to(old);ready=d/'ready'
                source="""import importlib.util,os,signal,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('review',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
replace=m.os.replace
def hold(a,b):
 if sys.argv[6]=='True':replace(a,b)
 Path(sys.argv[5]).write_text('ready')
 signal.pause()
m.os.replace=hold
m.tool_link(sys.argv[2],sys.argv[3],sys.argv[4])
"""
                process=subprocess.Popen([os.sys.executable,'-c',source,str(ROOT/'tools/upgrade-review.py'),str(link),str(new),str(old),str(ready),str(after)])
                try:
                    deadline=time.monotonic()+5
                    while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue(ready.exists());process.kill();process.wait(timeout=5)
                    self.assertEqual(process.returncode,-signal.SIGKILL);self.assertEqual(os.readlink(link),str(new if after else old))
                    m.tool_link(link,new,str(old));self.assertEqual(os.readlink(link),str(new))
                finally:
                    if process.poll() is None:process.kill();process.wait(timeout=5)

    def test_manual_documents_parse_as_bash_and_retire_global_restore_loop(self):
        count=0
        for path in sorted((ROOT/'docs').glob('manual-upgrade*.md')):
            text=path.read_text();self.assertNotIn('UP_Q=',text)
            for source in re.findall(r'```bash\n(.*?)\n```',text,re.S):
                result=subprocess.run(['bash','-n'],input=source,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,path.name+': '+result.stderr);count+=1
        self.assertGreater(count,15)

    def test_atomic_link_rejects_real_current_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'current').mkdir()
            with self.assertRaises(m.Error):m.tool_link(d/'current',d,'MISSING')

    def test_cli_refuses_official_daemon_link(self):
        result=subprocess.run([os.sys.executable,str(ROOT/'tools/upgrade-review.py'),'tool-link','--link','/opt/robot/daemon/current','--target','/tmp','--expect','MISSING'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2);self.assertIn('Only tool current',result.stderr)

    def test_loader_nonzero_and_missing_library_refused(self):
        for code,text in [(1,'error'),(0,'libx.so => not found'),(0,'GLIBC_2.35 not found'),(0,'undefined symbol: gst_missing_api'),(0,'symbol lookup error: missing export')]:
            with self.subTest(text=text),self.assertRaises(m.Error):m.checked_loader('fixture',lambda *a,**k:subprocess.CompletedProcess(a,code,text,''))
        self.assertIn('libc',m.checked_loader('fixture',lambda *a,**k:subprocess.CompletedProcess(a,0,'libc => /lib/libc.so','')))

    def test_runtime_refuses_non_linux_without_executing_anything(self):
        with patch.object(m.platform,'system',return_value='Darwin'):
            with self.assertRaises(m.Error):m.runtime('/','x','y','z','before',run=lambda *a,**k:self.fail('must not run'))

    def test_runtime_rejects_input_hash_before_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'bin').mkdir();(d/'bin/robotd').write_bytes(fake_elf());p=d/'inputs.json';names='btd configd mediad netpadd nfcd padd pet-detect pet-features robotctl robotd sounds tofd updaterd'.split();p.write_text(json.dumps({'official':dict.fromkeys(names,'wrong'),'native':dict.fromkeys(['robotd','robotctl'],'wrong')}))
            for name in names:(d/'bin'/name).write_bytes(fake_elf())
            with patch.object(m.platform,'system',return_value='Linux'),patch.object(m.platform,'machine',return_value='aarch64'):
                with self.assertRaises(m.Error):m.runtime(d,d,d,p,'before',run=lambda *a,**k:self.fail('no loader for unverified input'))

    def test_runtime_requires_onnx_probe_even_when_ldd_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'bin').mkdir();raw=fake_elf();digest=m.hashlib.sha256(raw).hexdigest()
            names='btd configd mediad netpadd nfcd padd pet-detect pet-features robotctl robotd sounds tofd updaterd'.split()
            for name in names:(d/'bin'/name).write_bytes(raw)
            p=d/'inputs.json';p.write_text(json.dumps({'official':dict.fromkeys(names,digest),'native':dict.fromkeys(['robotd','robotctl'],digest)}))
            with patch.object(m.platform,'system',return_value='Linux'),patch.object(m.platform,'machine',return_value='aarch64'):
                with self.assertRaises(m.Error):m.runtime(tmp,tmp,tmp,p,'before',run=lambda argv,**k:subprocess.CompletedProcess(argv,1,'','ORT API missing') if 'onnx' in argv else subprocess.CompletedProcess(argv,0,'libc => /lib/libc.so',''))

    def disk_tree(self,mounts=None):
        return {'blockdevices':[{'path':'/dev/mmcblk0','type':'disk','maj:min':'179:0','mountpoints':[None],'children':[{'path':'/dev/mmcblk0p1','type':'part','maj:min':'179:1','mountpoints':mounts or [None]}]},{'path':'/dev/sda','type':'disk','maj:min':'8:0','mountpoints':[None],'children':[{'path':'/dev/sda1','type':'part','maj:min':'8:1','mountpoints':['/mnt/backup']}]}]}

    def test_image_guard_rejects_mounted_source(self):
        for mounts in (['/'],['/mnt/auto'],['[SWAP]']):
            with self.assertRaises(m.Error):m.validate_disk_tree(self.disk_tree(mounts),'/dev/mmcblk0','8:1')

    def test_image_guard_rejects_same_physical_disk(self):
        for device in ('179:0','179:1','0:999'):
            with self.assertRaises(m.Error):m.validate_disk_tree(self.disk_tree(),'/dev/mmcblk0',device)

    def test_image_guard_accepts_unmounted_disk_and_external_image(self):
        self.assertEqual(m.validate_disk_tree(self.disk_tree(),'/dev/mmcblk0','8:1')['type'],'disk')
        with self.assertRaises(m.Error):m.validate_disk_tree(self.disk_tree(),'/dev/mmcblk0p1','8:1')

    def props(self,**values):
        p=dict(Id='robotd.service',LoadState='loaded',ActiveState='inactive',MainPID='0',ControlPID='0',Job='',ControlGroup='');p.update(values)
        return subprocess.CompletedProcess([],0,'\n'.join(k+'='+v for k,v in p.items()),'')

    def test_quiescence_rejects_active_pid_job_and_alias(self):
        for values in (dict(ActiveState='deactivating'),dict(MainPID='42'),dict(ControlPID='9'),dict(Job='42'),dict(Id='other.service')):
            with self.subTest(values=values),self.assertRaises(m.Error):m.stopped(['robotd.service'],check_deferred=False,run=lambda *a,**k:self.props(**values))
        self.assertIn('PASS',m.stopped(['robotd.service'],check_deferred=False,run=lambda *a,**k:self.props()))

    def test_quiescence_skips_uninstalled_unit_but_not_query_failure(self):
        result=subprocess.CompletedProcess([],1,'LoadState=not-found\n','unit not found')
        self.assertIn('PASS',m.stopped(['netpadd.service'],check_deferred=False,run=lambda *a,**k:result))
        with self.assertRaises(m.Error):m.stopped(['robotd.service'],check_deferred=False,run=lambda *a,**k:subprocess.CompletedProcess([],1,'','bus unavailable'))

    def test_runtime_rejects_empty_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'inputs.json';p.write_text('{"official":{},"native":{}}')
            with patch.object(m.platform,'system',return_value='Linux'),patch.object(m.platform,'machine',return_value='aarch64'):
                with self.assertRaises(m.Error):m.runtime(tmp,tmp,tmp,p,'before',run=lambda *a,**k:self.fail('no execution'))

    def test_deferred_restart_timer_blocks_before_stopping_services(self):
        def query(argv,**kwargs):
            if argv[1]=='list-units':return subprocess.CompletedProcess(argv,0,json.dumps([{'unit':'run-123.timer','active':'active'}]),'')
            if argv[2].endswith('.timer'):return subprocess.CompletedProcess(argv,0,'Transient=yes\nTriggers=run-123.service\n','')
            return subprocess.CompletedProcess(argv,0,'Transient=yes\nExecStart={ path=/usr/bin/systemctl ; argv[]=/usr/bin/systemctl restart updaterd ; }\n','')
        with self.assertRaises(m.Error):m.deferred(['updaterd.service'],run=query)
        with self.assertRaises(m.Error):m.stopped(['updaterd.service'],run=query)
        self.assertIn('PASS',m.deferred(['mediad.service'],run=query))
        self.assertIn('PASS',m.deferred(['robotd.service'],run=lambda *a,**k:subprocess.CompletedProcess([],0,'[]','')))

    def test_inactive_timer_without_service_pid_properties_is_valid(self):
        def timer(argv,**kwargs):
            return subprocess.CompletedProcess(argv,0,'Id=robot-boot-check.timer\nLoadState=loaded\nActiveState=inactive\nJob=\n','')
        self.assertIn('PASS',m.stopped(['robot-boot-check.timer'],run=timer,check_deferred=False))
        def broken_service(argv,**kwargs):
            return subprocess.CompletedProcess(argv,0,'Id=robotd.service\nLoadState=loaded\nActiveState=inactive\nJob=\n','')
        with self.assertRaises(m.Error):m.stopped(['robotd.service'],run=broken_service,check_deferred=False)

    def test_quiescence_rejects_descendant_cgroup_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'robot/child').mkdir(parents=True);(root/'robot/cgroup.procs').write_text('');(root/'robot/child/cgroup.procs').write_text('123\n')
            with self.assertRaises(m.Error):m.stopped(['robotd.service'],check_deferred=False,run=lambda *a,**k:self.props(ControlGroup='/robot'),cgroup_root=root)

    def test_recovery_lost_markers_never_reapply(self):
        self.assertTrue(m.recovery_action('0.16.3','0.18.3',False,False).startswith('INSPECT_NEW'))
        self.assertTrue(m.recovery_action('0.16.3','0.16.3',True,False).startswith('INSPECT_OLD'))
        self.assertTrue(m.recovery_action('0.16.3','unknown',False,False).startswith('STOP'))
        self.assertTrue(m.recovery_action('0.16.3','0.16.3',False,True).startswith('STOP'))
        self.assertTrue(m.recovery_action('0.16.3','0.16.3',False,False).startswith('INSPECT_OLD'))

    def test_preparation_cli_preserves_existing_changed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);source=d/'source';out=d/'manual';source.write_text('auto_apply="all"\n')
            command=[os.sys.executable,str(ROOT/'tools/upgrade-review.py'),'manual-config','--source',str(source),'--out',str(out)]
            subprocess.run(command,check=True,capture_output=True);subprocess.run(command,check=True,capture_output=True)
            out.write_text('edited')
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,2);self.assertEqual(out.read_text(),'edited');self.assertEqual(source.read_text(),'auto_apply="all"\n')


if __name__=='__main__':unittest.main()
