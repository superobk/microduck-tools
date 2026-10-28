"""Official mediad CLI rotation adapter. Keeps native robotd configuration untouched.

Versioned extension point: only the reviewed 0.17.9 unit/CLI contract is writable.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

import camera_config
import config_store
import duck_calibrate as cal

NATIVE_VERSIONS=frozenset(('0.17.9',))
NATIVE_REVISIONS=frozenset(('96b89d8613f06722c1d64ce75ab0d7124fed9d8f',))
BINARY='/opt/robot/daemon/current/bin/mediad'
DROPIN=Path('/etc/systemd/system/mediad.service.d/90-microduck-tools-camera.conf')


def dropin(rotation):
    camera_config.angle(rotation)
    argument='' if rotation is None else ' --rotate '+str(rotation)
    return '# Managed by microduck-tools; remove only this drop-in to restore vendor rotation.\n[Service]\nExecStart=\nExecStart='+BINARY+argument+'\n'


class Camera(camera_config.Camera):
    def __init__(self,config,settings,target,demo,enable_apply,web_port,identity='/run/mediad/identity.json',dropin_path=DROPIN):
        super().__init__(config,identity,target,demo,enable_apply,web_port)
        self.settings=Path(settings);self.dropin_path=Path(dropin_path)

    def _saved(self):
        try:raw=self.settings.read_bytes()
        except FileNotFoundError:return None,None
        obj=json.loads(raw)
        if not isinstance(obj,dict) or obj.get('schema')!='microduck-camera-rotation/v1' or set(obj)-{'schema','rotation'}:
            raise cal.CalibrationError('unsupported camera plugin configuration schema')
        return camera_config.angle(obj.get('rotation')),config_store.revision(raw)

    def _runtime(self,default):
        identity=json.loads(self.runtime.read_text())
        if identity.get('service')!='mediad' or identity.get('version') not in NATIVE_VERSIONS:
            raise cal.CalibrationError('official camera adapter requires reviewed mediad version 0.17.9')
        if identity.get('revision') not in NATIVE_REVISIONS:
            raise cal.CalibrationError('unreviewed mediad revision; camera writes disabled')
        if self.target!='auto' and self.target!='daemon-v'+identity['version']:
            raise cal.CalibrationError('mediad version differs from selected target')
        pid=identity.get('pid')
        if type(pid)!=int or pid<=1:raise cal.CalibrationError('invalid mediad identity PID')
        actual=subprocess.run(['systemctl','show','mediad','-p','MainPID','--value'],capture_output=True,text=True,timeout=2,check=True)
        executable=os.readlink(f'/proc/{pid}/exe')
        if str(pid)!=actual.stdout.strip() or Path(executable).name!='mediad' or identity.get('exe')!=executable:
            raise cal.CalibrationError('mediad identity does not match live service')
        argv=Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\x00').decode().split('\x00')
        # Apply only to the reviewed vendor command and our optional rotation override.
        # Unknown arguments are kept untouched and require explicit future adapter review.
        if len(argv)==1:rotation=default
        elif len(argv)==3 and argv[1]=='--rotate' and re.fullmatch(r'0|90|180|270',argv[2]):rotation=int(argv[2])
        else:raise cal.CalibrationError('unreviewed mediad arguments; preserve them and review an adapter')
        return dict(pid=pid,rotation_degrees=rotation,version=identity['version'],source='official_mediad_identity_and_cli',
            picture_verification='requires fresh PNG and manual orientation check')

    def _unit(self):
        fragment=subprocess.run(['systemctl','show','mediad','-p','FragmentPath','--value'],capture_output=True,text=True,timeout=2,check=True).stdout.strip()
        overrides=subprocess.run(['systemctl','show','mediad','-p','DropInPaths','--value'],capture_output=True,text=True,timeout=2,check=True).stdout.split()
        if any(Path(p)!=self.dropin_path for p in overrides):raise cal.CalibrationError('other mediad drop-ins require review; not replaced')
        commands=re.findall(r'^\s*ExecStart\s*=\s*(.*?)\s*$',Path(fragment).read_text(),re.M)
        if commands!=[BINARY]:raise cal.CalibrationError('vendor mediad command changed; adapter review required')
        if self.dropin_path.exists():
            raw=self.dropin_path.read_text()
            if raw not in [dropin(value) for value in (None,0,90,180,270)]:
                raise cal.CalibrationError('tool drop-in changed externally; review before replacing')

    def status(self):
        saved=None;expected=None;error=None;revision=None;runtime=None;verified=False
        try:
            saved,revision=self._saved()
            board=tomllib.loads(self.config.read_text()).get('board',{}).get('version','zero3')
            if board not in ('zero3','beta'):raise cal.CalibrationError('unknown board default')
            default=90 if board=='zero3' else 0
            expected=default if saved is None else saved
            if self.demo:raise cal.CalibrationError('DEMO: no live official camera')
            runtime=self._runtime(default);verified=True;reason='official mediad CLI verified; inspect fresh picture for physical orientation'
        except (OSError,ValueError,TypeError,cal.CalibrationError,subprocess.SubprocessError) as exc:
            reason=str(exc)
        writable=verified and error is None
        can_apply=False
        if writable and self.enable_apply:
            try:self._unit();can_apply=True
            except (OSError,ValueError,cal.CalibrationError,subprocess.SubprocessError) as exc:reason=str(exc)
        return dict(adapter='official-mediad-cli-v1',saved_rotation=saved,expected_rotation=expected,saved_error=error,
            config_revision=revision,runtime=runtime,runtime_verified=verified,runtime_reason=reason,override=False,
            can_save=writable,can_apply=can_apply,apply_enabled=self.enable_apply,
            pending_restart=expected!=runtime['rotation_degrees'] if verified else None)

    def preview(self,body):
        if not isinstance(body,dict) or 'rotation' not in body:raise cal.CalibrationError('missing camera rotation')
        rotation=camera_config.angle(body['rotation'])
        return dict(ok=True,rotation=rotation,snippet=dropin(rotation),config_revision=self._saved()[1])

    def save(self,body):
        if body.get('confirm_save') is not True or not self.status()['can_save']:
            raise cal.CalibrationError('camera save requires confirmation and verified official mediad')
        rotation=self.preview(body)['rotation']
        with config_store.locked(self.settings):
            current=self._saved()[1]
            if body.get('config_revision')!=current:raise cal.CalibrationError('camera configuration conflict; refresh and review')
            updated=(json.dumps(dict(schema='microduck-camera-rotation/v1',rotation=rotation),indent=2)+'\n').encode()
            if self.settings.exists():
                backup=config_store.replace_checked(self.settings,self.settings.read_bytes(),updated,current)
            else:
                backup=None;cal.atomic_write(self.settings,updated,0o600)
        return dict(ok=True,backup=str(backup) if backup else None,message='plugin camera settings saved; native services unchanged')

    def apply(self,body):
        if body.get('confirm_video_interrupt') is not True or not self.status()['can_apply']:
            raise cal.CalibrationError('camera apply disabled or official command not verified')
        rotation,_=self._saved()
        with config_store.locked(self.dropin_path):
            self._unit()
            raw=dropin(rotation).encode()
            if self.dropin_path.exists():config_store.replace_checked(self.dropin_path,self.dropin_path.read_bytes(),raw)
            else:cal.atomic_write(self.dropin_path,raw,0o644)
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=10,capture_output=True,text=True)
        subprocess.run(['systemctl','restart','mediad'],check=True,timeout=20,capture_output=True,text=True)
        return dict(ok=True,restart_requested=True,runtime_verified=False,
            message='mediad restarted; verify new identity/rotation and fresh PNG; robotd untouched')
