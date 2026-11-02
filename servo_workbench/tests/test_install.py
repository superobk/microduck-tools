import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('install_agent',ROOT/'deploy/workbench/install-agent.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class InstallerTests(unittest.TestCase):
    def bundle(self,d):
        source=Path(d)/'source';source.mkdir()
        files={'deploy/workbench/agent.sh':'#!/bin/sh\n','tools/setup-workbench.py':'# fixture\n'}
        for n,text in files.items():
            p=source/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(text.encode('utf-8'))
        (source/'WORKBENCH-MANIFEST.json').write_text(json.dumps(dict(component_version='0.1.1',files=[dict(path=n,sha256=hashlib.sha256(v.encode()).hexdigest()) for n,v in files.items()])))
        return source
    def test_install_isolated_root_no_services_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            source=self.bundle(d);root=Path(d)/'target';calls=[]
            out=installer.install(source,'/dev/ttyS5','hd1910',root,lambda argv,**kw:calls.append(argv))
            self.assertFalse(out['robotd_changed']);self.assertFalse(out['autostart'])
            self.assertTrue((root/'opt/microduck-servo-workbench/current').is_symlink())
            self.assertEqual(len(calls),1);self.assertNotIn('systemctl',str(calls))
            with self.assertRaises(ValueError):installer.install(source,'/dev/ttyS5','hd1910',root)
    def test_bad_hash_fails_before_any_target_write(self):
        with tempfile.TemporaryDirectory() as d:
            source=self.bundle(d);(source/'tools/setup-workbench.py').write_text('changed')
            target=Path(d)/'target'
            with self.assertRaises(ValueError):installer.install(source,'/dev/ttyS5','hd1910',target)
            self.assertFalse(target.exists())
    def test_failed_dependency_install_keeps_current_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            source=self.bundle(d);root=Path(d)/'target'
            def fail(*a,**kw):raise RuntimeError('offline install interrupted')
            with self.assertRaises(RuntimeError):installer.install(source,'/dev/ttyS5','hd1910',root,fail)
            self.assertFalse((root/'opt/microduck-servo-workbench/current').exists())
            self.assertFalse((root/'etc/microduck-servo-workbench/agent.json').exists())
