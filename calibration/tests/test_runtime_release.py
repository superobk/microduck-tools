"""Offline release reconstruction and local-only graceful shutdown."""
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('release_builder',ROOT/'tools/build-release.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class ReleaseTests(unittest.TestCase):
    def test_runtime_archive_reconstructs_hashes_and_imports_without_source_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory);out=directory/'runtime.tar.gz';manifest=builder.build(ROOT,out)
            with tarfile.open(out) as archive:
                names=archive.getnames()
                self.assertFalse(any('/.git/' in name or '__pycache__' in name or name.endswith('.log') for name in names))
                archive.extractall(directory/'unpack',filter='data')
            package=directory/'unpack'/('microduck-tools-'+manifest['tool_version'])
            for entry in manifest['files']:
                self.assertEqual(hashlib.sha256((package/entry['path']).read_bytes()).hexdigest(),entry['sha256'])
            run=subprocess.run([sys.executable,str(package/'calibration/integration-0.16.3/web/calibration_web.py'),'--help'],capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr);self.assertIn('--plugin-config',run.stdout)
            self.assertIn('--target {daemon-v0.16.3,daemon-v0.17.8,daemon-v0.17.9,daemon-v0.18.3,auto}',run.stdout)
            run=subprocess.run([sys.executable,str(package/'tools/microduck-dryrun.py'),'--help'],capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr);self.assertIn('rollback',run.stdout)
            self.assertTrue((package/'deploy/robotctl-dryrun').stat().st_mode & 0o111)
            self.assertTrue((package/'deploy/manual-upgrade/inputs.json').is_file())
            run=subprocess.run([sys.executable,str(package/'tools/upgrade-review.py'),'--help'],capture_output=True,text=True,timeout=5)
            self.assertEqual(run.returncode,0,run.stderr);self.assertIn('deferred',run.stdout)
            with self.assertRaises(ValueError):builder.build(ROOT,out)

    def test_unit_does_not_start_or_restart_native_services(self):
        text=(ROOT/'deploy/microduck-tools.service').read_text()
        self.assertIn('User=microduck-tools',text);self.assertIn('--target auto',text)
        self.assertIn('PrivateDevices=yes',text);self.assertNotIn('--enable-camera-apply',text)
        for forbidden in ('Requires=','Wants=','PartOf=','BindsTo=','ExecStartPre=','ExecStopPost='):
            self.assertNotIn(forbidden,text)

    def test_sigterm_exits_cleanly_and_marks_incomplete_event_window(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory);config=directory/'robotd.toml';config.write_text('[board]\nversion="zero3"\n')
            state=directory/'observer'
            command=[sys.executable,str(ROOT/'calibration/integration-0.16.3/web/calibration_web.py'),
                '--target','auto','--demo','--no-telemetry','--enable-servo-health','--port','0',
                '--config',str(config),'--servo-health-dir',str(state)]
            process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                # No real socket/serial/device is opened; wait for the local observer's stale event.
                time.sleep(.8);process.send_signal(signal.SIGTERM)
                output,error=process.communicate(timeout=8)
                self.assertEqual(process.returncode,0,error);self.assertIn('Microduck Calibration Web:',output)
                events=[json.loads(line) for line in (state/'events.jsonl').read_text().splitlines()]
                self.assertTrue(events);self.assertFalse(events[-1]['after_complete'])
            finally:
                if process.poll() is None:process.kill();process.communicate()


if __name__=='__main__':unittest.main()
