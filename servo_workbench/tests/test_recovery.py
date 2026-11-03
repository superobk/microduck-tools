import io
import json
import queue
import tempfile
import threading
import unittest
from types import SimpleNamespace
from servo_workbench.service import Controller
from servo_workbench.store import Store
from servo_workbench.engine import Engine
from servo_workbench.simulation import SimBus
from servo_workbench.remote import Remote
from servo_workbench.profiles import Refused
from servo_workbench.agent import serve_stdio


class RecoveryTests(unittest.TestCase):
    def test_group_verifies_new_offset_after_explicit_midpoint(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d);bus=SimBus();clock=[0.]
            e=Engine(store,bus,clock=lambda:clock[0]);e.create('hd1910')
            try:
                for i in range(15):
                    bus.attach('mid-part-'+str(i))
                    for _ in range(3):clock[0]+=.3;e.tick(True)
                    p=e.plan(label='mid-part-'+str(i),model_confirmed=True)
                    e.apply(dict(job_id='cfg'+str(i),revision=p['revision'],connection_epoch=e.status()['connection_epoch'],plan_hash=p['plan']['hash']))
                    bus.attach(None);clock[0]+=.3;e.tick(True)
                bus.attach('mid-part-0');bus.devices['mid-part-0']['raw'][56]=2400
                for _ in range(3):clock[0]+=.3;e.tick(True)
                p=e.action_plan('midpoint',True,True,True)
                e.apply(dict(job_id='explicit-mid',revision=p['revision'],connection_epoch=e.status()['connection_epoch'],plan_hash=p['plan']['hash']))
                self.assertEqual(e.status()['slots'][0]['after']['raw']['31'],352)
                bus.attached=list(bus.devices);bus.power_cycle()
                result=e.verify_all(True,True)
                self.assertTrue(all(row['ok'] for row in result['rows']))
                self.assertEqual(sum(x.get('instruction')==11 for x in bus.sent),1)
            finally:store.close()

    def test_group_detects_loss_of_preserved_protection_not_only_changed_fields(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d);bus=SimBus();clock=[0.]
            e=Engine(store,bus,clock=lambda:clock[0]);e.create('hd1910')
            try:
                for i in range(15):
                    bus.attach('part-'+str(i))
                    for _ in range(3):clock[0]+=.3;e.tick(True)
                    p=e.plan(label='part-'+str(i),model_confirmed=True)
                    e.apply(dict(job_id='job'+str(i),revision=p['revision'],connection_epoch=e.status()['connection_epoch'],plan_hash=p['plan']['hash']))
                    bus.attach(None);clock[0]+=.3;e.tick(True)
                bus.attached=list(bus.devices);bus.power_cycle()
                bus.devices['part-0']['raw'][19]^=1
                result=e.verify_all(True,True)
                self.assertFalse(result['rows'][0]['ok'])
                self.assertTrue(all(r['ok'] for r in result['rows'][1:]))
                self.assertEqual(e.status()['slots'][0]['state'],'repair')
            finally:store.close()
    def test_one_backend_per_authority_directory_and_release_on_close(self):
        with tempfile.TemporaryDirectory() as d:
            c=Controller(d)
            try:
                with self.assertRaises(Refused):Controller(d)
            finally:c.close()
            again=Controller(d);again.close()

    def test_simulated_batch_cannot_be_opened_in_live_mode(self):
        with tempfile.TemporaryDirectory() as d:
            c=Controller(d);ident=c.rpc('session-create',{})['id'];c.close()
            live=Controller(d,demo=False)
            try:
                with self.assertRaises(Refused):live.rpc('session-open',{'id':ident})
                self.assertIsNone(live.engine)
            finally:live.close()
    def test_other_viewer_cannot_replace_active_protocol(self):
        with tempfile.TemporaryDirectory() as d:
            c=Controller(d)
            try:
                one=c.rpc('session-create',{'kind':'xl330'},'A')
                c.rpc('session-create',{'kind':'hd1910'},'A')
                with self.assertRaises(Refused):
                    c.rpc('session-open',{'id':one['id']},'B')
                self.assertEqual(c.engine.bus.kind,'hd1910')
            finally:c.close()

    def test_old_ssh_reader_cannot_drain_new_connection_waiters(self):
        r=Remote('registered-host')
        old=SimpleNamespace(stdout=io.StringIO(''))
        current=object();r.proc=current
        waiter=queue.Queue();r.pending['new']=waiter
        r._reader(old)
        self.assertIs(r.proc,current)
        self.assertIs(r.pending['new'],waiter)
        self.assertTrue(waiter.empty())

    def test_eof_freezes_write_between_durable_intent_and_packet(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d);bus=SimBus();bus.attach('A');clock=[0.]
            e=Engine(store,bus,clock=lambda:clock[0]);e.create('hd1910')
            try:
                for t in (0,.3,.6):clock[0]=t;e.tick(True)
                p=e.plan(label='physical-A',model_confirmed=True)
                e.fault_hook=lambda stage:e.disconnected.set() if stage=='after_intent' else None
                with self.assertRaises(Refused):
                    e.apply(dict(job_id='eof',revision=p['revision'],connection_epoch=e.status()['connection_epoch'],plan_hash=p['plan']['hash']))
                self.assertEqual(bus.sent,[])
                self.assertEqual(store.job('eof')['steps'][0]['state'],'intent')
            finally:store.close()

    def test_candidate_change_requires_three_new_responses(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d);bus=SimBus();bus.attach('A');clock=[0.]
            e=Engine(store,bus,clock=lambda:clock[0]);e.create('hd1910')
            try:
                for t in (0,.3,.6):clock[0]=t;e.tick(True)
                bus.devices['A']['raw'][5]=10
                clock[0]=1;e.tick(True)
                self.assertEqual(e.status()['connection_state'],'stabilizing')
                self.assertIsNone(e.status()['observed'])
                self.assertEqual(bus.sent,[])
            finally:store.close()

    def test_agent_eof_closes_even_on_malformed_frame(self):
        class C:
            engine=None
            closed=False
            def close(self):self.closed=True
        c=C()
        with self.assertRaises(ValueError):
            serve_stdio(c,'hd1910',io.StringIO('not-json\n'),io.StringIO())
        self.assertTrue(c.closed)

    def test_report_retains_uncertain_intent_as_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d);b=SimBus();e=Engine(s,b);e.create('hd1910')
            try:
                s.save_job(dict(id='uncertain',session=e.active,state='uncertain',steps=[dict(state='intent')]))
                self.assertEqual(e.report()['jobs'][0]['steps'][0]['state'],'intent')
            finally:s.close()

    def test_large_diagnostic_history_is_bounded_and_declares_omissions(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d);e=Engine(s,SimBus());e.create('hd1910')
            try:
                s.save_job(dict(id='large',session=e.active,state='uncertain',steps=[dict(state='intent',raw='x'*100)]*20000))
                report=e.report()
                self.assertLess(len(json.dumps(report).encode()),2*1024*1024)
                self.assertEqual(len(report['jobs'][0]['steps']),500)
                self.assertEqual(report['jobs'][0]['export_window']['original_counts']['steps'],20000)
            finally:s.close()
