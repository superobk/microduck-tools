"""Pinned API68 contracts and synthetic control/Unix IPC checks, never real hardware."""
import asyncio
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from calibration.common import daemon_profiles as profiles
from studio.core import JOINTS, Refused, compact_state
from studio.ipc import Identity, Broker
from studio.helper import fetch_policy
from studio.diagnostics import Diagnostics
import test_studio as legacy
from test_studio import ready, state, Clock, PREFIX

def hello(kind='native',version='0.23.0'):
    p=profiles.profiles()[version]
    return dict(api_version=p['ipc_api'],daemon_version=version,
                revision=p['build_revision' if kind=='native' else 'base_revision'])

class ProfileTests(unittest.TestCase):
    def test_exact_profiles_and_no_future_api_unlock(self):
        for version in ('0.18.3','0.23.0'):
            for kind in ('native','official'):
                self.assertEqual(profiles.match(hello(kind,version))[1],kind)
        for change in [dict(api_version=69),dict(api_version=True),dict(daemon_version='0.23.1'),dict(revision='unreviewed')]:
            self.assertIsNone(profiles.match({**hello(),**change}))
    def test_native_identity_uses_correct_target_and_requires_matching_receipt(self):
        identity=Identity(local=True)
        identity.info=dict(pid=123)
        module=SimpleNamespace(
            verify_native_receipt=MagicMock(return_value=(True,'synthetic')),
            valid_active_file=MagicMock(return_value=(True,'synthetic')),
            cal=SimpleNamespace(config_layers=SimpleNamespace(load=MagicMock(return_value={'bus':{'calibration_path':'/tmp/synthetic-cal.toml'}})),
                parse_calibration=MagicMock(return_value={'imu_mount_wxyz':[0,0,0,1],'gyro_raw_counts_per_dps':16.4}),
                effective_gyro=lambda s: {'gyro_rad_per_raw_count':0.0010642251536550791}),
            state_delta=MagicMock(return_value=[]))
        receipt=dict(pid=123,calibration_path='/tmp/synthetic-cal.toml')
        with patch('studio.ipc.platform.system',return_value='Linux'),patch('studio.ipc.platform.machine',return_value='aarch64'),patch.object(identity,'executable',return_value=True) as binary,patch.dict(sys.modules,{'studio_legacy_verify':module}),patch('studio.ipc.Path.read_text',return_value=json.dumps(receipt)):
            self.assertEqual(identity.robot(hello(),dict(joint_names=JOINTS)),(True,True))
            binary.assert_called_with('robotd.service',profiles.profiles()['0.23.0']['native']['robotd'])
            self.assertEqual(module.verify_native_receipt.call_args.args[2],'daemon-v0.23.0')
            module.state_delta.return_value=['imu_mount_wxyz']
            self.assertEqual(identity.robot(hello(),dict(joint_names=JOINTS)),(True,False))
            module.state_delta.return_value=['gyro_raw_counts_per_dps']
            self.assertEqual(identity.robot(hello(),dict(joint_names=JOINTS)),(True,False))
            binary.return_value=False
            self.assertEqual(identity.robot(hello(),dict(joint_names=JOINTS)),(False,False))
    def test_official_daemon_with_calibration_pointer_never_verifies_custom_calibration(self):
        identity=Identity(local=True)
        with patch('studio.ipc.platform.system',return_value='Linux'),patch('studio.ipc.platform.machine',return_value='aarch64'),patch.object(identity,'executable',return_value=True),patch('studio.ipc.Path.exists',return_value=True),patch('studio.ipc.Path.read_text',return_value='[bus]\ncalibration_path="/custom.toml"'):
            self.assertEqual(identity.robot(hello('official'),dict(joint_names=JOINTS)),(True,False))
        with patch('studio.ipc.platform.system',return_value='Linux'),patch('studio.ipc.platform.machine',return_value='aarch64'),patch.object(identity,'executable') as exe:
            self.assertEqual(identity.robot({**hello(), 'api_version':69},dict(joint_names=JOINTS)),(False,False))
            exe.assert_not_called()
    def test_camera_pins_hash_revision_api_and_actual_unix_peer(self):
        original=Path.read_text
        def read(p,*a,**k):
            if str(p)=='/proc/123/status':return 'Uid:\t1001\t1001\t1001\t1001\n'
            return original(p,*a,**k)
        for kind in ('native','official'):
            identity=Identity(local=True);identity.info=dict(pid=123)
            client=MagicMock();client.__enter__.return_value=client
            client.getsockopt.return_value=struct.pack('3i',123,1001,1001)
            client.recv.return_value=(json.dumps(dict(id=1,result=hello(kind)))+'\n').encode()
            expected=profiles.profiles()['0.23.0'][kind]['mediad']
            with patch.object(identity,'executable',side_effect=lambda unit,sha:sha==expected),patch('studio.ipc.socket.socket',return_value=client),patch('studio.ipc.socket.SO_PEERCRED',17,create=True),patch.object(Path,'read_text',read):
                self.assertTrue(identity.camera())
                client.getsockopt.return_value=struct.pack('3i',124,1001,1001)
                self.assertFalse(identity.camera())
                client.getsockopt.return_value=struct.pack('3i',123,1001,1001)
                client.recv.return_value=(json.dumps(dict(id=1,result={**hello(kind),'api_version':47}))+'\n').encode()
                self.assertFalse(identity.camera())
    def test_native_modes_are_visible_and_block_all_control_without_auto_disable(self):
        for extra in [dict(autonomous=True),dict(laser={'camera':'looking','head':False}),dict(soccer={'autonomous':True,'phase':'searching'}),
                      dict(autonomous='unknown'),dict(laser=[]),dict(soccer={})]:
            s=ready();s.ingest({**state(2000000000),**extra})
            with self.assertRaisesRegex(Refused,'原生'):s.ready()
        s=ready();s.ingest({**state(2000000000),'soccer':{'autonomous':False,'phase':'manual'}});s.ready()
        self.assertIsNone(compact_state(state())['native_activity']['soccer_phase'])
    def test_freshness_restart_and_missing_current_are_not_masked_by_api68(self):
        clock=Clock();s=ready(clock)
        s.hello=hello();s.ingest({**state(2000000000),'currents_ma':None})
        self.assertIsNone(s.state['currents_ma'])
        clock.advance(.7);s.ingest({**state(2000000000),'currents_ma':[1]*15})
        with self.assertRaises(Refused):s.ready()
        s.ingest(state(100))
        self.assertFalse(s.calibration_verified);self.assertFalse(s.identity_verified)

class HelperFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ipc=SimpleNamespace(rpc=AsyncMock(side_effect=lambda service,method,*a,**k: hello() if method=='hello' else dict(joint_names=JOINTS) if method=='robot.model' else {'accepted':True}))
        self.identity=SimpleNamespace(robot=MagicMock(return_value=(True,True)))
        self.handoff=SimpleNamespace(status=AsyncMock(return_value=dict(pending=True,units={'padd.service':dict(ActiveState='inactive',MainPID='0',Job='')})))
        self.request=dict(op='policy_fetch',confirm=True,params=dict(repo='pollen-robotics/microduck-policies',revision='v10',file='walk.onnx'))
    async def test_fetch_is_single_allowlisted_download_without_load_or_service_commands(self):
        await fetch_policy(self.ipc,self.identity,self.handoff,self.request)
        self.assertEqual([c.args[1] for c in self.ipc.rpc.call_args_list],['hello','robot.model','policy.fetch'])
        self.assertEqual(self.ipc.rpc.call_args.kwargs,dict(write=True,timeout=65))
    async def test_invalid_fields_no_confirmation_or_unverified_identity_refuse(self):
        for change in [dict(confirm=False),dict(params={'repo':'../x'}),dict(params={'repo':'a/b','file':'../walk.onnx'}),
                       dict(params={'repo':'a/b','method':'os.apply'}),dict(params={'repo':'a/b','file':'walk.sh'})]:
            with self.assertRaises(Refused):await fetch_policy(self.ipc,self.identity,self.handoff,{**self.request,**change})
        self.identity.robot.return_value=(True,False)
        with self.assertRaises(Refused):await fetch_policy(self.ipc,self.identity,self.handoff,self.request)
    async def test_active_padd_or_no_handoff_never_downloads(self):
        for status in [dict(pending=False),dict(pending=True,units={'padd.service':dict(ActiveState='active',MainPID=12)})]:
            self.handoff.status.return_value=status
            with self.assertRaises(Refused):await fetch_policy(self.ipc,self.identity,self.handoff,self.request)
        self.ipc.rpc.assert_not_awaited()

class Api68HttpTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=legacy.GatewayTests.asyncSetUp
    asyncTearDown=legacy.GatewayTests.asyncTearDown
    async def test_status_api68_and_auth_checks_still_apply(self):
        studio=self.app['studio'];studio.config['demo']=False
        studio.store=ready();studio.store.hello=hello()
        response=await self.client.get(PREFIX+'/status')
        self.assertEqual(response.status,200)
        payload=await response.json()
        self.assertIn('68',json.dumps(payload));self.assertIn('0.23.0',json.dumps(payload))
        response=await self.client.post(PREFIX+'/action',json={'operation':'fetch'},headers={'Origin':self.origin})
        self.assertEqual(response.status,403)
        self.helper.assert_not_awaited()
    async def test_native_activity_prevents_claim_and_no_helper_take(self):
        studio=self.app['studio'];studio.config.update(demo=False,controls_enabled=True)
        studio.store=ready();studio.store.ingest({**state(2000000000),'autonomous':True})
        with self.assertRaises(Refused):await studio.ws_command(dict(id='x',mode='live',visible=True),dict(op='claim',source='keyboard',confirm=True))
        self.helper.assert_not_awaited()

if __name__=='__main__':unittest.main()
