"""Fault matrix for PR7 review; exclusively simulated, never a native port."""
import tempfile
import unittest
from servo_workbench.engine import Engine
from servo_workbench.store import Store
from servo_workbench.simulation import SimBus,Offline
from servo_workbench.profiles import Refused,NoAck
from servo_workbench.protocol import packet


class SafetyReview(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.addCleanup(self.store.close)
        self.bus=SimBus();self.bus.attach('A');self.t=0.
        self.e=Engine(self.store,self.bus,clock=lambda:self.t);self.e.create('hd1910')
    def stable(self):
        for _ in range(3):self.t+=.3;self.e.tick(True)
    def request(self,p,jid='review'):
        return dict(job_id=jid,revision=p['revision'],connection_epoch=self.e.status()['connection_epoch'],plan_hash=p['plan']['hash'])
    def plan(self):
        self.stable();return self.e.plan(label='physical-A',model_confirmed=True)
    def optional(self,kind):
        self.stable();return self.e.action_plan(kind,True,True,True)

    def test_reopened_adapter_cannot_use_old_confirmation(self):
        p=self.plan();self.bus.close();self.bus.reconnect()
        with self.assertRaises(Refused):self.e.apply(self.request(p))
        self.assertEqual(self.bus.sent,[])

    def test_unsupported_firmware_is_not_reported_as_no_power_response(self):
        self.bus.devices['A']['raw'][0]=4;self.e.tick(True)
        self.assertEqual(self.e.status()['error_code'],'IDENTITY')
        self.assertEqual(self.e.status()['adapter_state'],'present')
        self.assertEqual(self.bus.sent,[])

    def test_device_error_does_not_claim_adapter_is_busy(self):
        def failed(*args):raise Refused('DEVICE_STATUS','status 0x02')
        self.bus.read=failed;self.e.tick(True)
        self.assertEqual(self.e.status()['connection_state'],'blocked')
        self.assertEqual(self.e.status()['adapter_state'],'present')

    def test_read_error_before_write_cannot_reopen_inside_transaction(self):
        p=self.plan();original=self.bus.read
        def read(i,b,a,n):
            if a==19:
                self.bus.close();self.bus.reconnect()
            return original(i,b,a,n)
        self.bus.read=read
        with self.assertRaises(ConnectionError):self.e.apply(self.request(p))
        self.assertEqual(self.bus.sent,[])
        self.assertIsNone(self.bus.pinned_epoch)

    def test_relock_requires_model_ack_before_any_write(self):
        self.stable();self.bus.devices['A']['raw'][55]=0
        with self.assertRaises(Refused):self.e.reconcile(True,False)
        self.assertEqual(self.bus.sent,[])

    def test_relock_refuses_unsafe_voltage_before_any_write(self):
        self.stable();self.bus.devices['A']['raw'].update({55:0,62:100})
        with self.assertRaises(Refused):self.e.reconcile(True,True)
        self.assertEqual(self.bus.sent,[])

    def unusual_device(self):
        d=self.bus.devices['A'];d['raw'].update({5:42,6:3,7:42});d['persistent']=d['raw'].copy()
        result=self.e.scan([128000]);self.assertTrue(result['auto_selected']);self.stable()
        self.assertEqual(self.e.status()['observed']['id'],42)

    def test_scan_can_select_unknown_id_and_nondefault_baud_read_only(self):
        self.unusual_device();self.assertEqual(self.bus.sent,[])
        p=self.e.plan(label='physical-A',model_confirmed=True)
        self.assertEqual((p['plan']['source_id'],p['plan']['source_baud']),(42,128000))

    def test_id_and_baud_write_ack_loss_reconciles_without_replay(self):
        self.unusual_device();p=self.e.plan(label='physical-A',model_confirmed=True)
        self.bus.fail_at=3
        with self.assertRaises(Offline):self.e.apply(self.request(p))
        n=len(self.bus.sent);self.bus.fail_at=None;self.stable()
        self.assertEqual(len(self.bus.sent),n)
        self.e.reconcile(True,True)
        p=self.e.plan(label='physical-A',model_confirmed=True,same_servo=True)
        self.assertEqual(p['plan']['changes'],[])
        self.e.apply(self.request(p,'remaining'))
        self.assertEqual(sum(x.get('address')==5 for x in self.bus.sent),1)
        self.assertEqual(sum(x.get('address')==6 for x in self.bus.sent),1)

    def test_interrupted_rollback_finds_old_id_and_writes_only_remaining_baud(self):
        self.unusual_device();p=self.e.plan(label='physical-A',model_confirmed=True)
        self.e.apply(self.request(p));p=self.optional('rollback')
        self.bus.fail_at=self.bus.sends+2
        with self.assertRaises(Offline):self.e.apply(self.request(p,'rollback'))
        self.bus.fail_at=None;self.stable();self.e.reconcile(True,True)
        p=self.e.action_plan('rollback',True,True,True)
        self.assertEqual([c['address'] for c in p['plan']['changes']],[6])
        n=len(self.bus.sent);self.e.apply(self.request(p,'finish-rollback'))
        self.assertFalse(any(x.get('address')==5 for x in self.bus.sent[n:]))
        self.assertEqual(self.bus.devices['A']['raw'][6],3)

    def test_unknown_backup_baud_cannot_be_written_during_rollback(self):
        p=self.plan();self.e.apply(self.request(p));parent=self.store.job('review')
        parent['plan']['changes'].append(dict(address=6,size=1,old=99,value=0));self.store.save_job(parent)
        n=len(self.bus.sent)
        with self.assertRaises(Refused):self.optional('rollback')
        self.assertEqual(len(self.bus.sent),n)

    def test_heartbeat_expiry_after_intent_never_enables_torque(self):
        p=self.optional('motion');self.e.heartbeat()
        self.e.fault_hook=lambda stage:setattr(self,'t',self.t+1) if stage=='after_intent' else None
        with self.assertRaises(Refused):self.e.apply(self.request(p))
        self.assertFalse(any(x.get('address')==40 and x.get('value')==1 for x in self.bus.sent))

    def test_stop_during_configuration_preflight_is_not_cleared(self):
        p=self.plan();read=self.bus.read
        def stopped(i,b,a,n):
            if a==19:self.e.cancel_action()
            return read(i,b,a,n)
        self.bus.read=stopped
        with self.assertRaises(Refused):self.e.apply(self.request(p))
        self.assertEqual(self.bus.sent,[])

    def test_stop_during_motion_preflight_is_not_cleared(self):
        p=self.optional('motion');self.e.heartbeat();read=self.bus.read
        def stopped(i,b,a,n):
            if a==19:self.e.cancel_action()
            return read(i,b,a,n)
        self.bus.read=stopped
        with self.assertRaises(Refused):self.e.apply(self.request(p))
        self.assertFalse(any(x.get('address') in (41,42,44,46,48) for x in self.bus.sent))
        self.assertFalse(any(x.get('address')==40 and x.get('value')==1 for x in self.bus.sent))

    def test_existing_high_current_is_rejected_before_torque_enable(self):
        p=self.optional('motion');self.bus.devices['A']['raw'][69]=100;self.e.heartbeat()
        with self.assertRaises(Refused):self.e.apply(self.request(p))
        self.assertFalse(any(x.get('address')==40 and x.get('value')==1 for x in self.bus.sent))

    def test_missing_position_after_arming_stops_before_sweep(self):
        p=self.optional('motion');self.e.heartbeat();write=self.bus.write
        def wrapped(i,b,a,n,v,purpose='configuration'):
            write(i,b,a,n,v,purpose)
            if a==40 and v==1:self.bus.devices['A']['raw'][56]=None
        self.bus.write=wrapped
        with self.assertRaises(Refused):self.e.apply(self.request(p))
        self.assertEqual(self.bus.devices['A']['raw'][40],0)
        self.assertTrue(all(x['value']==2048 for x in self.bus.sent if x.get('address')==42))

    def test_position_drift_during_ram_setup_never_arms_old_goal(self):
        p=self.optional('motion');self.e.heartbeat();write=self.bus.write
        def drift(i,b,a,n,v,purpose='configuration'):
            write(i,b,a,n,v,purpose)
            if a==41:self.bus.devices['A']['raw'][56]+=20
        self.bus.write=drift
        with self.assertRaises(Refused):self.e.apply(self.request(p))
        self.assertFalse(any(x.get('address')==40 and x.get('value')==1 for x in self.bus.sent))
        self.assertFalse(any(x.get('address')==42 for x in self.bus.sent))

    def test_link_loss_after_arming_does_not_reconnect_for_cleanup(self):
        p=self.optional('motion');self.e.heartbeat();write=self.bus.write
        def wrapped(i,b,a,n,v,purpose='configuration'):
            write(i,b,a,n,v,purpose)
            if a==40 and v==1:self.bus.close();raise Offline('lost immediately after torque-on')
        self.bus.write=wrapped
        with self.assertRaises(ConnectionError):self.e.apply(self.request(p))
        self.assertEqual(self.store.job('review')['cleanup'],'not_attempted_after_link_failure')
        n=len(self.bus.sent);self.stable();self.assertEqual(len(self.bus.sent),n)

    def test_midpoint_ack_loss_is_read_back_without_repeating_instruction(self):
        self.bus.devices['A']['raw'][56]=2300;p=self.optional('midpoint');reset=self.bus.reset_mid
        def noack(i,b):reset(i,b);raise NoAck('lost ack')
        self.bus.reset_mid=noack
        self.assertEqual(self.e.apply(self.request(p))['state'],'verified')
        self.assertEqual(sum(x.get('instruction')==11 for x in self.bus.sent),1)

    def test_midpoint_does_not_cross_closed_connection(self):
        p=self.optional('midpoint')
        self.e.fault_hook=lambda stage:self.bus.close() if stage=='after_intent' else None
        with self.assertRaises(ConnectionError):self.e.apply(self.request(p))
        self.assertEqual(self.bus.sent,[])

    def test_dxl_reserved_id_rejected(self):
        with self.assertRaises(Refused):packet('dxl2',253,3,b'')

    def test_group_readback_cannot_join_evidence_across_adapter_reopen(self):
        for i in range(15):
            self.bus.attach(None);self.e.tick(True);self.bus.attach(str(i))
            p=self.plan();self.e.apply(self.request(p,'cfg'+str(i)))
        self.bus.attached=[str(i) for i in range(15)];self.bus.power_cycle()
        original=self.bus.read
        def lost(i,b,a,n):
            if i==20:self.bus.close()
            return original(i,b,a,n)
        self.bus.read=lost;n=len(self.bus.sent)
        result=self.e.verify_all(True,True)
        self.assertTrue(all(r['ok'] for r in result['rows'][:5]))
        self.assertTrue(all(not r['ok'] for r in result['rows'][5:]))
        self.assertEqual(len(self.bus.sent),n)
