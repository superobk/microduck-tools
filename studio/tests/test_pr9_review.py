"""PR9 review regressions: epochs, process proofs, privileged RPC and upright PNGs."""
import asyncio
import json
from pathlib import Path
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import test_studio as legacy
from test_api68 import hello
from studio.ipc import Broker, Identity, IPC
from studio.helper import fetch_policy, privileged_action
from studio.server import Snapshot
from studio.core import Refused

class ProofEpochTests(unittest.IsolatedAsyncioTestCase):
    async def poll_once(self, store, identity, previous_pid=123):
        async def rpc(service, method, *a, **kw):
            return hello() if method=='hello' else dict(healthy=True) if method=='robot.health' else {}
        broker=Broker(store,SimpleNamespace(rpc=rpc),identity)
        broker.last_hello=hello();broker.last_pid=previous_pid
        with patch('studio.ipc.asyncio.sleep',AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await broker.poll()
        return broker
    async def test_sensor_epoch_reset_during_proof_cannot_restore_old_authority(self):
        store=legacy.ready()
        def verify(*a):store.reset();return True,True
        broker=await self.poll_once(store,SimpleNamespace(robot=verify,info=dict(pid=123)))
        self.assertIsNone(broker.last_hello)
        self.assertFalse(store.identity_verified)
        self.assertFalse(store.calibration_verified)
    async def test_process_pid_change_requires_another_fresh_proof(self):
        store=legacy.ready();epoch=store.epoch
        await self.poll_once(store,SimpleNamespace(robot=lambda *a:(True,True),info=dict(pid=456)))
        self.assertNotEqual(store.epoch,epoch)
        self.assertFalse(store.identity_verified)
        self.assertFalse(store.calibration_verified)

    async def test_transient_model_read_failure_is_retried_without_daemon_restart(self):
        store=legacy.ready();model_reads=[]
        async def rpc(service, method, *a, **kw):
            if method=='hello':return hello()
            if method=='robot.model':
                model_reads.append(1)
                if len(model_reads)==1:raise ConnectionError('first model read interrupted')
                return dict(joint_names=legacy.JOINTS)
            return {}
        identity=SimpleNamespace(robot=lambda *a:(True,True),info=dict(pid=123))
        broker=Broker(store,SimpleNamespace(rpc=rpc),identity)
        with patch('studio.ipc.asyncio.sleep',AsyncMock(side_effect=[None,asyncio.CancelledError])):
            with self.assertRaises(asyncio.CancelledError):await broker.poll()
        self.assertEqual(len(model_reads),2)
        self.assertEqual(store.model.get('joint_names'),legacy.JOINTS)

class PrivilegedPeerTests(unittest.IsolatedAsyncioTestCase):
    def fixtures(self, service_hello=None, service_verified=True):
        async def rpc(service, method, *a, **kw):
            if method=='hello':return hello() if service=='robot' else (service_hello or hello())
            if method=='robot.model':return {'joint_names':legacy.JOINTS}
            return dict(accepted=True)
        ipc=SimpleNamespace(rpc=AsyncMock(side_effect=rpc))
        identity=SimpleNamespace(robot=MagicMock(return_value=(True,True)),
            service_proof=MagicMock(return_value=dict(verified=service_verified,process=dict(pid=333,uid=0))))
        handoff=SimpleNamespace(status=AsyncMock(return_value=dict(pending=True,units={
            u:dict(ActiveState='inactive',MainPID='0',Job='') for u in ('padd.service','netpadd.service')})))
        request=dict(op='policy_fetch',confirm=True,params=dict(repo='a/b',revision='v10',file='walk.onnx'))
        return ipc,identity,handoff,request
    async def test_wrong_updater_version_cannot_receive_root_download(self):
        ipc,identity,handoff,request=self.fixtures(hello(version='0.18.3'))
        with self.assertRaises(Refused):await fetch_policy(ipc,identity,handoff,request)
        self.assertFalse(any(c.args[1]=='policy.fetch' for c in ipc.rpc.call_args_list))
    async def test_unverified_updater_executable_cannot_receive_root_download(self):
        ipc,identity,handoff,request=self.fixtures(service_verified=False)
        with self.assertRaises(Refused):await fetch_policy(ipc,identity,handoff,request)
        self.assertFalse(any(c.args[1]=='policy.fetch' for c in ipc.rpc.call_args_list))
    async def test_rpc_checks_unix_peer_before_transmitting_mutation(self):
        for actual,accepted in [((999,0,0),False),((333,1000,1000),False),((333,0,0),True)]:
            reader=SimpleNamespace(readline=AsyncMock(return_value=b'{"jsonrpc":"2.0","id":1,"result":{"accepted":true}}\n'))
            peer=SimpleNamespace(getsockopt=lambda *a:struct.pack('3i',*actual))
            writer=MagicMock();writer.drain=AsyncMock();writer.wait_closed=AsyncMock()
            writer.get_extra_info.return_value=peer
            with patch('studio.ipc.asyncio.open_unix_connection',AsyncMock(return_value=(reader,writer))),patch('studio.ipc.socket.SO_PEERCRED',17,create=True):
                if accepted:await IPC().rpc('updater','policy.fetch',{},write=True,expected_peer=dict(pid=333,uid=0))
                else:
                    with self.assertRaises(Refused):await IPC().rpc('updater','policy.fetch',{},write=True,expected_peer=dict(pid=333,uid=0))
                    writer.write.assert_not_called()

    async def test_isolation_revoked_while_proofs_run_prevents_download(self):
        ipc,identity,handoff,request=self.fixtures()
        isolated=handoff.status.return_value
        handoff.status.side_effect=[isolated,dict(pending=False,units=isolated['units'])]
        with self.assertRaises(Refused):await fetch_policy(ipc,identity,handoff,request)
        self.assertFalse(any(c.args[1]=='policy.fetch' for c in ipc.rpc.call_args_list))
    async def test_pair_and_forget_use_only_fixed_config_rpc_and_verified_peer(self):
        for op,params in [('pad_pair',dict(mac='AA:BB:CC:DD:EE:FF',timeout_seconds=30)),
                          ('pad_forget',dict(mac='AA:BB:CC:DD:EE:FF'))]:
            ipc,identity,handoff,request=self.fixtures()
            await privileged_action(ipc,identity,handoff,dict(op=op,params=params,confirm=True))
            self.assertEqual(ipc.rpc.call_args.args[:2],('config','pad.pair' if op=='pad_pair' else 'pad.forget'))
            self.assertEqual(ipc.rpc.call_args.kwargs['expected_peer'],dict(pid=333,uid=0))
    async def test_root_whitelist_rejects_system_ops_bad_mac_and_unbounded_pairing(self):
        for op,params in [('os.apply',{}),('pad_pair',dict(timeout_seconds=61)),('pad_pair',dict(timeout_seconds=True)),
                          ('pad_forget',{}),('pad_forget',dict(mac='../a')),('policy_fetch',dict(repo='a/b',revision='x/../y'))]:
            ipc,identity,handoff,request=self.fixtures()
            with self.assertRaises(Refused):await privileged_action(ipc,identity,handoff,dict(op=op,params=params,confirm=True))
            ipc.rpc.assert_not_awaited()

class PermissionRouteTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=legacy.GatewayTests.asyncSetUp
    asyncTearDown=legacy.GatewayTests.asyncTearDown
    async def test_api68_uses_narrow_helper_and_api47_keeps_existing_routes(self):
        studio=self.app['studio'];studio.config.update(demo=False,controls_enabled=True)
        ctx=dict(id='review',session='session',mode='live',visible=True)
        studio.sockets['review']=ctx;studio.lease.claim('review','touch')
        try:
            for version in ('0.23.0','0.18.3'):
                studio.store=legacy.ready();studio.store.hello=hello(version=version)
                self.helper.reset_mock();self.broker.ipc.calls.clear()
                for op,params in [('fetch',dict(repo='a/b')),('pair',dict(timeout_seconds=30)),('forget',dict(mac='AA:BB:CC:DD:EE:FF'))]:
                    response=await self.client.post(legacy.PREFIX+'/action',json=dict(connection_id='review',
                        operation=op,params=params,confirm=True),headers=self.headers)
                    self.assertEqual(response.status,202,await response.text())
                    await asyncio.gather(*(j['task'] for j in studio.jobs.values() if j.get('task')))
                    self.assertTrue(all(j['state']=='completed' for j in studio.jobs.values()),studio.jobs)
                methods=[args[1] for args,kw in self.broker.ipc.calls if args[1]!='robot.stop']
                if version=='0.23.0':
                    self.assertEqual(methods,[])
                    self.assertEqual([c.args[0] for c in self.helper.call_args_list],['policy_fetch','pad_pair','pad_forget'])
                else:
                    self.assertEqual(methods,['policy.fetch','pad.pair','pad.forget'])
                    self.helper.assert_not_awaited()
        finally:studio.sockets.clear()

class ProofSnapshotTests(unittest.TestCase):
    def test_camera_and_robot_proofs_cannot_cross_process_records(self):
        attempted=threading.Event()
        class Verifier(Identity):
            def _robot(self,*a):
                self.info=dict(pid=101)
                attempted.wait(1)
                return True,True
            def _camera(self):
                self.info=dict(pid=202)
                return True
        identity=Verifier(local=True)
        def camera():attempted.set();identity.camera()
        # A process record must be copied in the same locked operation as its verdict.
        worker=threading.Thread(target=camera)
        with identity._proof_lock:
            worker.start()
            proof=identity.robot_proof(hello(),{})
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(proof['process']['pid'],101)
        self.assertTrue(proof['verified'])

class ServiceIdentityTests(unittest.TestCase):
    def test_target_service_requires_pinned_hash_revision_and_root_uid(self):
        from calibration.common import daemon_profiles
        for service,program in [('updater','updaterd'),('config','configd')]:
            identity=Identity(local=True);identity.info=dict(pid=333)
            expected=daemon_profiles.profiles()['0.23.0']['native'][program]
            with patch.object(identity,'executable',return_value=True) as exe,patch('studio.ipc.Path.read_text',return_value='Uid:\t0\t0\t0\t0\n'):
                proof=identity.service_proof(service,hello())
                self.assertTrue(proof['verified'])
                exe.assert_called_with(program+'.service',expected)
            with patch.object(identity,'executable',return_value=True),patch('studio.ipc.Path.read_text',return_value='Uid:\t1000\t1000\t1000\t1000\n'):
                self.assertFalse(identity.service_proof(service,hello())['verified'])
            with patch.object(identity,'executable',return_value=False):
                self.assertFalse(identity.service_proof(service,hello())['verified'])
            with patch.object(identity,'executable') as exe:
                self.assertFalse(identity.service_proof(service,hello(version='0.18.3'))['verified'])
                exe.assert_not_called()

    def test_missing_process_uid_fails_without_leaking_stop_iteration_into_async_worker(self):
        identity=Identity(local=True);identity.info=dict(pid=333)
        client=MagicMock();client.__enter__.return_value=client
        client.getsockopt.return_value=struct.pack('3i',333,0,0)
        with patch.object(identity,'executable',return_value=True),patch('studio.ipc.Path.read_text',return_value='Name: vanished\n'),patch('studio.ipc.socket.socket',return_value=client),patch('studio.ipc.socket.SO_PEERCRED',17,create=True):
            with self.assertRaisesRegex(Refused,'UID'):identity.service_proof('updater',hello())
            with self.assertRaisesRegex(Refused,'UID'):identity.camera()

class UprightSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_already_rotated_png_is_forwarded_once_and_shared_cache_is_kept(self):
        # Known portrait PNG header: 90/270 rotation has already swapped the dimensions.
        import zlib
        def chunk(kind, value):
            return struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value))
        pixels=b'\x00'+bytes([255,0,0,0,255,0])+b'\x00'+bytes([0,0,255,255,255,0])+b'\x00'+bytes([255,0,255,0,255,255])
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>II',2,3)+b'\x08\x02\x00\x00\x00')+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
        requests=[]
        async def frame(request):requests.append(1);return web.Response(body=png,content_type='image/png')
        app=web.Application();app.router.add_get('/frame',frame);server=TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as http:
                owner=SimpleNamespace(config=dict(demo=False,camera_enabled=True,camera_port=server.port),
                    store=SimpleNamespace(state=None),broker=SimpleNamespace(identity=SimpleNamespace(camera=lambda:True)),http=http)
                snapshot=Snapshot(owner);first=await snapshot.fetch();second=await snapshot.fetch()
                self.assertEqual(first['png'],png)
                self.assertEqual((first['width'],first['height']),(2,3))
                self.assertEqual(first['id'],second['id'])
                self.assertEqual(len(requests),1)
        finally:await server.close()

class ExecutableCacheTests(unittest.TestCase):
    def test_reexec_between_stat_and_open_cannot_cache_another_executable(self):
        import tempfile,hashlib
        with tempfile.TemporaryDirectory() as directory:
            a=Path(directory)/'unreviewed';b=Path(directory)/'pinned'
            a.write_bytes(b'unreviewed executable');b.write_bytes(b'pinned executable')
            before=a.stat();identity=Identity(local=True);expected=hashlib.sha256(b.read_bytes()).hexdigest()
            with patch('subprocess.run',return_value=SimpleNamespace(stdout='333')),patch.object(Path,'stat',return_value=before),patch.object(Path,'open',side_effect=lambda *a,**k:open(b,'rb')),patch('studio.ipc.os.readlink',return_value=str(a)):
                self.assertFalse(identity.executable('robotd.service',expected))
                self.assertEqual(identity.hashes,{})

if __name__=='__main__':unittest.main()
