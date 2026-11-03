import json
from pathlib import Path
import tempfile
import unittest
from servo_workbench.engine import Engine
from servo_workbench.store import Store
from servo_workbench.simulation import SimBus,Offline
from servo_workbench.profiles import Refused,profile,layout,identity,metrics,options
from servo_workbench.transport import select_port,SerialBus
from servo_workbench.protocol import packet,Decoder,crc16,stuff


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock=0.
        self.bus=SimBus("hd1910");self.bus.attach("A")
        self.store=Store(self.tmp.name);self.addCleanup(self.store.close)
        self.engine=Engine(self.store,self.bus,clock=lambda:self.clock)
        self.engine.create("hd1910")

    def stable(self):
        for _ in range(3):
            self.engine.tick(True);self.clock+=.3

    def plan(self,**kwargs):
        self.stable()
        return self.engine.plan(label="physical-label",model_confirmed=True,**kwargs)

    def request(self,plan,job="job"):
        return dict(job_id=job,revision=plan["revision"],
                    connection_epoch=self.engine.status()["connection_epoch"],plan_hash=plan["plan"]["hash"])

    def apply(self,job="job"):
        return self.engine.apply(self.request(self.plan(),job))


class Workflow(Fixture):
    def test_initial_read_only_discovery(self):
        self.stable()
        self.assertEqual(self.engine.status()["connection_state"],"ready")
        self.assertEqual(self.bus.sent,[])

    def test_three_replies_need_half_second(self):
        for _ in range(3):self.engine.tick(True)
        self.assertEqual(self.engine.status()["connection_state"],"stabilizing")
        self.clock=.6;self.engine.tick(True)
        self.assertEqual(self.engine.status()["connection_state"],"ready")

    def test_steady_read_does_not_expire_preview(self):
        p=self.plan();revision=p["revision"]
        self.engine.tick(True)
        self.assertEqual(self.engine.status()["revision"],revision)
        self.assertEqual(self.engine.apply(self.request(p))["state"],"readback")

    def test_only_one_confirmation_and_no_mid_or_goal(self):
        self.apply()
        self.assertEqual(self.engine.status()["configured"],1)
        self.assertEqual(self.bus.devices["A"]["raw"][5],10)
        self.assertEqual([x for x in self.bus.sent if x.get("address")==55][-1]["value"],1)
        self.assertFalse(any(x.get("instruction")==11 or x.get("address") in (40,42) for x in self.bus.sent))

    def test_duplicate_submit_exactly_once(self):
        p=self.plan();r=self.request(p)
        one=self.engine.apply(r);n=len(self.bus.sent)
        self.assertEqual(self.engine.apply(r)["state"],one["state"])
        self.assertEqual(len(self.bus.sent),n)
        with self.assertRaises(Refused):self.engine.apply({**r,"plan_hash":"changed"})

    def test_all_fifteen_power_swap_and_persistence(self):
        for i in range(15):
            if i:
                self.bus.attach(None);self.engine.tick(True)
                self.bus.create(str(i));self.bus.attach(str(i))
            self.apply(str(i))
        self.assertEqual(self.engine.status()["configured"],15)
        self.assertEqual(self.engine.status()["completed"],0)
        self.bus.attached=list(self.bus.devices)
        self.bus.power_cycle()
        result=self.engine.verify_all(True,True)
        self.assertTrue(all(x["ok"] for x in result["rows"]))
        self.assertEqual(self.engine.status()["completed"],15)
        self.assertFalse(self.engine.report()["hardware_acceptance"])

    def test_dropped_persistent_parameter_is_not_completed(self):
        self.test_all_fifteen_power_swap_and_persistence()
        self.bus.devices["A"]["raw"][33]=0
        rows=self.engine.verify_all(True,True)["rows"]
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(self.engine.status()["completed"],14)

    def test_readback_does_not_claim_power_cycle(self):
        self.apply()
        self.assertEqual(self.engine.status()["slots"][0]["persistence"],"pending_operator_power_cycle")

    def test_power_claim_is_required(self):
        with self.assertRaises(Refused):self.engine.verify_all(False,True)

    def test_lost_link_invalidates_confirmation(self):
        p=self.plan();r=self.request(p)
        self.bus.attach(None);self.engine.tick(True)
        self.bus.attach("A");self.stable()
        with self.assertRaises(Refused):self.engine.apply(r)
        self.assertEqual(self.bus.sent,[])

    def test_unexpected_identical_new_servo_not_assumed_same(self):
        self.plan();self.engine._lost("link")
        self.bus.create("B");self.bus.attach("B");self.stable()
        s=self.engine.status()
        self.assertEqual(s["slots"][0]["state"],"pending")
        self.assertEqual(self.bus.sent,[])

    def test_returned_completed_servo_cannot_fill_next_slot(self):
        self.apply();self.stable()
        with self.assertRaisesRegex(Refused,"已登记"):self.engine.plan(label="wrong-slot",model_confirmed=True)

    def test_interrupted_after_id_write_only_remaining_difference(self):
        p=self.plan();r=self.request(p)
        self.bus.fail_at=2  # unlock then ID commit, ACK lost
        with self.assertRaises(Offline):self.engine.apply(r)
        self.assertEqual(self.engine.status()["phase"],"interrupted")
        self.assertEqual(self.bus.devices["A"]["raw"][5],10)
        self.bus.fail_at=None;before=len(self.bus.sent);self.stable()
        self.assertEqual(len(self.bus.sent),before)  # no relock on reconnect
        self.engine.reconcile(True,True)
        p=self.engine.plan(label="physical-label",model_confirmed=True,same_servo=True)
        self.assertEqual(p["plan"]["changes"],[])
        self.engine.apply(self.request(p,"remaining"))
        self.assertEqual(sum(x.get("address")==5 for x in self.bus.sent),1)

    def test_interruption_before_tx_has_no_replayed_tx(self):
        self.bus.fail_at=1;self.bus.failure="before"
        with self.assertRaises(Offline):self.apply()
        self.assertEqual(self.bus.sent,[])
        self.bus.fail_at=None;self.stable()
        self.assertEqual(self.bus.sent,[])
        with self.assertRaises(Refused):self.engine.plan(label="x",model_confirmed=True)

    def test_process_restart_uncertain_job(self):
        p=self.plan()
        self.engine.fault_hook=lambda stage:(_ for _ in ()).throw(SystemExit(9)) if stage=="before_commit" else None
        with self.assertRaises(SystemExit):self.engine.apply(self.request(p))
        reboot=Engine(self.store,self.bus,clock=lambda:self.clock)
        self.assertEqual(reboot.status()["phase"],"interrupted")
        n=len(self.bus.sent)
        for _ in range(3):reboot.tick(True);self.clock+=.3
        self.assertEqual(len(self.bus.sent),n)
        reboot.reconcile(True,True)
        self.assertIsNone(reboot.status()["running_job"])

    def test_midpoint_is_optional_and_once(self):
        self.bus.devices["A"]["raw"][56]=2300;self.stable()
        p=self.engine.action_plan("midpoint",True,True,True);r=self.request(p)
        job=self.engine.apply(r)
        self.assertEqual(job["state"],"verified")
        self.assertEqual(self.bus.devices["A"]["raw"][56],2048)
        n=len(self.bus.sent);self.engine.apply(r)
        self.assertEqual(len(self.bus.sent),n)
        self.assertFalse(any(x.get("address") in (40,42) for x in self.bus.sent))

    def test_midpoint_ack_without_effect_not_success(self):
        self.bus.devices["A"]["raw"][56]=2300;self.bus.mid_noop=True;self.stable()
        p=self.engine.action_plan("midpoint",True,True,True)
        with self.assertRaisesRegex(Refused,"生效"):self.engine.apply(self.request(p))
        n=len(self.bus.sent);self.stable()
        self.assertEqual(len(self.bus.sent),n)
        self.assertTrue(self.engine.status()["needs_identity"])

    def test_unready_optional_actions_refused(self):
        self.stable()
        with self.assertRaises(Refused):self.engine.action_plan("motion",True,False,True)
        with self.assertRaises(Refused):self.engine.action_plan("midpoint",True,True,False)

    def test_motion_finite_and_off(self):
        self.stable();p=self.engine.action_plan("motion",True,True,True)
        self.engine.heartbeat()
        job=self.engine.apply(self.request(p))
        self.assertEqual(job["state"],"verified")
        self.assertEqual(self.bus.devices["A"]["raw"][40],0)
        self.assertLessEqual(max(abs(x["target_raw"]-2048) for x in job["samples"]),35)
        self.assertTrue(job["summary"]["simulated"])
        self.assertEqual(self.bus.devices["A"]["raw"][44],77)

    def test_motion_telemetry_does_not_renew_heartbeat(self):
        self.stable();p=self.engine.action_plan("motion",True,True,True)
        with self.assertRaisesRegex(Refused,"心跳"):self.engine.apply(self.request(p))
        self.assertFalse(any(x.get("address")==40 and x.get("value")==1 for x in self.bus.sent))

    def test_motion_current_fault_stops_and_reports(self):
        self.bus.devices["A"]["raw"][69]=100
        self.stable();p=self.engine.action_plan("motion",True,True,True);self.engine.heartbeat()
        with self.assertRaises(Refused):self.engine.apply(self.request(p))
        self.assertEqual(self.bus.devices["A"]["raw"][40],0)
        self.assertEqual(self.store.job("job")["cleanup"],"torque_off_readback")

    def test_parameter_rollback_restores_only_task_changes(self):
        self.apply();self.stable()
        p=self.engine.action_plan("rollback",True,True,True)
        job=self.engine.apply(self.request(p,"back"))
        self.assertEqual(job["state"],"verified")
        self.assertEqual(self.bus.devices["A"]["raw"][5],1)
        self.assertEqual(self.bus.devices["A"]["raw"][55],1)
        self.assertFalse(any(x.get("address")==42 for x in self.bus.sent))

    def test_guard_blocks_torque_voltage_temperature_fault(self):
        for addr,value in [(40,1),(62,100),(63,55),(65,8)]:
            old=self.bus.devices["A"]["raw"][addr]
            self.bus.devices["A"]["raw"][addr]=value;self.stable()
            with self.assertRaises(Refused):self.engine.plan(label="x",model_confirmed=True)
            self.bus.devices["A"]["raw"][addr]=old
        self.assertEqual(self.bus.sent,[])

    def test_two_viewers_one_writer(self):
        self.engine.claim("one")
        with self.assertRaises(Refused):self.engine.claim("two")
        self.clock=31;self.assertTrue(self.engine.claim("two")["owner"])
        with self.assertRaises(Refused):self.engine.require_writer("one")

    def test_robotd_reappears_blocks_without_service_changes(self):
        self.bus.robotd_active=True;self.engine.tick(True)
        self.assertEqual(self.engine.status()["connection_state"],"blocked")
        self.assertEqual(self.bus.sent,[])

    def test_multiple_devices_blocks_initialization(self):
        self.bus.create("B",2);self.bus.attached=["A","B"]
        # Quick detector finds current/source/default only; operator isolation is still necessary.
        self.bus.devices["B"]["raw"][5]=10
        self.engine.tick(True)
        self.assertEqual(self.engine.status()["connection_state"],"blocked")
        self.assertEqual(self.bus.sent,[])

    def test_pause_and_read_only_resume(self):
        self.plan();self.engine.pause()
        self.assertEqual(self.engine.status()["connection_state"],"paused")
        self.engine.pause(False);self.stable()
        self.assertIsNone(self.engine.status()["plan"])
        self.assertEqual(self.bus.sent,[])

    def test_progress_and_events_survive_store_reopen(self):
        self.apply()
        second=Store(self.tmp.name)
        try:
            e=Engine(second,self.bus,clock=lambda:self.clock)
            self.assertEqual(e.status()["configured"],1)
            self.assertTrue(second.events(e.active))
        finally:second.close()


class Protocols(unittest.TestCase):
    def test_official_dxl_ping_vector(self):
        self.assertEqual(packet("dxl2",1,1).hex(),"fffffd0001030001194e")

    def test_feetech_read_vector(self):
        self.assertEqual(packet("feetech",1,2,bytes([0x38,2])).hex(),"ffff0104023802be")

    def test_no_broadcast_packets(self):
        for kind in ("dxl2","feetech"):
            with self.assertRaises(Refused):packet(kind,254,3,b"")

    def test_fragmentation_noise_bad_checksum(self):
        good=bytes.fromhex("ffff0104001805dd")
        d=Decoder("feetech")
        self.assertEqual(d.feed(b"garbage"+good[:3]),[])
        self.assertEqual(d.feed(good[3:])[0]["data"],bytes([0x18,5]))
        bad=bytearray(good);bad[-1]^=1
        frames=d.feed(bad+good)
        self.assertEqual(len(frames),1);self.assertGreater(d.bad,0)

    def test_dxl_stuffing_crc_roundtrip(self):
        data=bytes([0x55,0])+b"\xff\xff\xfd\xfd"
        body=stuff(data);p=b"\xff\xff\xfd\x00\x01"+(len(body)+2).to_bytes(2,"little")+body
        p+=crc16(p).to_bytes(2,"little")
        self.assertEqual(Decoder("dxl2").feed(p)[0]["data"],b"\xff\xff\xfd\xfd")

    def test_hls_sign_and_units_not_dxl(self):
        m=metrics("hd1910",{"69":0x8002,"58":0x8001,"56":2048,"62":74,"63":25,"40":0,"65":0})
        self.assertEqual(m["current_ma"],-13)
        self.assertAlmostEqual(m["speed_deg_s"],-50*360/4096)

    def test_layouts_do_not_swap_sides(self):
        self.assertEqual(layout("microduck")[0]["name"],"right_hip_yaw")
        self.assertEqual(layout("xgo")[0]["name"],"left_hip_yaw")

    def test_model_and_options_are_fail_closed(self):
        self.assertFalse(identity("xl330",{"0":1190})["compatible"])
        self.assertFalse(identity("hd1910",{"0":3,"1":42,"2":0})["compatible"])
        with self.assertRaises(Refused):options("hd1910",{"shutdown52":True})
        with self.assertRaises(Refused):options("hd1910",{"protection_current_a":400})

    def test_stable_usb_identity_moved_port(self):
        a=dict(vid=1,pid=2,serial_number="a",location="one",device="COM3")
        self.assertEqual(select_port(a,[{**a,"device":"COM8"}],True)["device"],"COM8")

    def test_unknown_or_multiple_usb_does_not_guess(self):
        a=dict(vid=1,pid=2,serial_number=None,device="COM3")
        with self.assertRaises(Refused):select_port(a,[a],True)
        b={**a,"serial_number":"s"}
        with self.assertRaises(Refused):select_port(b,[b,{**b,"device":"COM4"}],True)

    def test_native_tx_guard_rejects_targets_and_reboots(self):
        b=SerialBus("hd1910",dict(device="TEST"))
        for addr in (40,42,44):
            with self.assertRaises(Refused):b.write(1,1000000,addr,profile("hd1910")["registers"][addr][0],1)
        with self.assertRaises(Refused):b.write(1,1000000,55,1,0,purpose="cleanup")


class DxlWorkflow(Fixture):
    def setUp(self):
        super().setUp()
        self.bus=SimBus("xl330");self.bus.attach("A")
        self.engine=Engine(self.store,self.bus,clock=lambda:self.clock)
        self.engine.create("xl330")

    def test_baud_and_knee_offset_mapping(self):
        p=self.plan(index=3)
        self.engine.apply(self.request(p))
        r=self.bus.devices["A"]["raw"]
        self.assertEqual((r[7],r[8],r[20]),(13,3,(-512)&0xffffffff))
        self.assertEqual(r[63],53)

    def test_no_hd_register_writes(self):
        self.apply()
        self.assertTrue(all(x["address"] not in (5,6,55) for x in self.bus.sent))

    def test_three_daemon_versions_have_explicit_presets(self):
        for v in ("0.16.3","0.17.9","0.18.3"):
            self.engine.create("xl330",version=v)
            self.assertEqual(self.engine.status()["version"],v)

    def test_dxl_motion_finishes_off_restores_watchdog(self):
        self.apply();self.stable()
        p=self.engine.action_plan("motion",True,True,True);self.engine.heartbeat()
        self.engine.apply(self.request(p,"test"))
        self.assertEqual(self.bus.devices["A"]["raw"][64],0)
        self.assertEqual(self.bus.devices["A"]["raw"][98],0)


if __name__=="__main__":unittest.main()
