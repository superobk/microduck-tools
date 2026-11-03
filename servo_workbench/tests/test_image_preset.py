"""Image-reference regression; synthetic bus only, no serial devices."""
import tempfile
import unittest
from servo_workbench.engine import Engine
from servo_workbench.store import Store
from servo_workbench.simulation import SimBus, Offline
from servo_workbench.profiles import Refused


class ImagePreset(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.addCleanup(self.store.close)
        self.bus=SimBus("xl330");self.bus.attach("A");self.t=0.
        self.e=Engine(self.store,self.bus,clock=lambda:self.t)
        self.e.create("xl330",opts={"shutdown52":True})

    def stable(self):
        for _ in range(3):self.t+=.3;self.e.tick(True)

    def plan(self):
        self.stable()
        return self.e.plan(label="DUCK-physical-label",model_confirmed=True)

    def request(self,p,job="image"):
        return dict(job_id=job,revision=p["revision"],
                    connection_epoch=self.e.status()["connection_epoch"],
                    plan_hash=p["plan"]["hash"])

    def test_all_eight_image_rows_are_explicit_even_if_already_equal(self):
        p=self.plan()["plan"]
        self.assertEqual(p["desired"],{"7":10,"8":3,"9":0,"10":0,"11":3,
                                      "20":0,"62":255,"63":52})
        self.assertEqual(p["binding"]["10"],0)
        self.assertFalse(any(x["address"]==10 for x in p["changes"]))
        self.assertEqual(self.bus.sent,[])

    def test_drive_mode_is_corrected_and_no_motion_or_torque_written(self):
        self.bus.devices["A"]["raw"][10]=1
        p=self.plan();self.e.apply(self.request(p))
        raw=self.bus.devices["A"]["raw"]
        self.assertEqual(raw[10],0)
        self.assertEqual(raw[63],52)
        self.assertEqual([x["address"] for x in self.bus.sent][-2:],[7,8])
        self.assertFalse(any(x["address"] in (64,100,116) for x in self.bus.sent))

    def test_original_offset_template_for_all_fifteen_survives_power_cycle(self):
        for i in range(15):
            if i:
                self.bus.attach(None);self.e.tick(True)
                self.bus.attach(str(i))
            p=self.plan()
            target=p["plan"]["target_id"]
            expected=(-512 if target in (13,23) else 0)&0xffffffff
            self.assertEqual(p["plan"]["desired"]["20"],expected)
            self.e.apply(self.request(p,str(i)))
        self.bus.attached=list(self.bus.devices)
        self.bus.power_cycle()
        rows=self.e.verify_all(True,True)["rows"]
        self.assertEqual(sum(x["ok"] for x in rows),15)
        self.assertFalse(self.e.report()["hardware_acceptance"])

    def test_shutdown_is_preserved_without_explicit_choice(self):
        self.e.create("xl330")
        self.e.apply(self.request(self.plan()))
        self.assertEqual(self.bus.devices["A"]["raw"][63],53)
        self.assertFalse(any(x["address"]==63 for x in self.bus.sent))

    def test_torque_enabled_refuses_before_write(self):
        self.bus.devices["A"]["raw"][64]=1
        with self.assertRaises(Refused):self.plan()
        self.assertEqual(self.bus.sent,[])

    def test_interrupted_drive_mode_write_does_not_replay(self):
        self.bus.devices["A"]["raw"][10]=1
        p=self.plan()
        self.bus.fail_at=next(i+1 for i,c in enumerate(p["plan"]["changes"]) if c["address"]==10)
        with self.assertRaises(Offline):self.e.apply(self.request(p))
        n=len(self.bus.sent);self.bus.fail_at=None;self.stable()
        self.assertEqual(len(self.bus.sent),n)
        self.e.reconcile(True,True)
        p=self.e.plan(label="DUCK-physical-label",model_confirmed=True,same_servo=True)
        self.assertFalse(any(c["address"]==10 for c in p["plan"]["changes"]))
        self.e.apply(self.request(p,"remaining"))
        self.assertEqual(sum(x["address"]==10 for x in self.bus.sent),1)
