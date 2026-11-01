import test from "node:test";
import assert from "node:assert/strict";
import {
  rotate,
  enginePosition,
  tofEvidence,
  Atmosphere,
} from "../static/feedback.js";
import { Simulator } from "../static/simulation.js";
const frame = (range = 500, status = 5) => ({
  ages: { tof_ms: 10 },
  state: {
    t_ns: 1e9,
    tof_pose: { pos: [0, 0, 0.02], quat: [1, 0, 0, 0] },
    imu: { quat: [1, 0, 0, 0] },
    odom: { position: [0, 0, 0.12] },
    safety: { gravity: [0, 0, -1], picked_up: false },
  },
  tof: {
    t_ns: 1e9,
    distance_mm: Array(64).fill(range),
    status: Array(64).fill(status),
  },
  health: { imu: { ready: true, consecutive_stale_blocks: 0 } },
});
const model = { tof_beams: Array.from({ length: 64 }, () => [1, 0, 0]) };
test("coordinate change is consistent; no extra odometry yaw", () => {
  assert.deepEqual(enginePosition([1, 2, 3]), [-2, 3, -1]);
  const p = rotate([Math.SQRT1_2, 0, 0, Math.SQRT1_2], [1, 0, 0]);
  assert.ok(Math.abs(p[0]) < 1e-10);
  assert.ok(Math.abs(p[1] - 1) < 1e-10);
});
test("ToF near/far and status9/255 are distinct", () => {
  assert.equal(tofEvidence(frame(), model).kind, "near");
  assert.equal(tofEvidence(frame(1000, 9), model).kind, "far");
  assert.equal(tofEvidence(frame(0, 255), model).kind, "no_target");
  assert.equal(tofEvidence(frame(500, 4), model).kind, "unknown");
});
test("ToF rejects old, unsynchronized and floor returns", () => {
  let p = frame();
  p.ages.tof_ms = 1600;
  assert.equal(tofEvidence(p, model).kind, "stale");
  p = frame();
  p.tof.t_ns += 151e6;
  assert.equal(tofEvidence(p, model).kind, "unknown");
  assert.equal(
    tofEvidence(frame(500), { tof_beams: Array(64).fill([0, 0, -1]) }).count,
    0,
  );
});
test("ToF cannot classify without actual geometry/pose/height", () => {
  for (const key of ["tof_pose", "imu", "odom"]) {
    const p = frame();
    p.state[key] = null;
    assert.equal(tofEvidence(p, model).kind, "unknown");
  }
  const p = frame();
  p.state.safety.picked_up = true;
  assert.equal(tofEvidence(p, model).kind, "unknown");
});
test("dwell uses distinct sensor frames", () => {
  const a = new Atmosphere();
  assert.equal(a.update({ kind: "near" }, 1, 0), "unknown");
  assert.equal(a.update({ kind: "near" }, 1, 1000), "unknown");
  assert.equal(a.update({ kind: "near" }, 2, 1001), "near");
  assert.equal(a.update({ kind: "stale" }, 2, 1002), "stale");
});
test("simulation is explicitly synthetic and never supplies fake load", () => {
  const sim = new Simulator(),
    input = { held: true, values: [0.1, 0, 0.1] };
  const p = sim.step(input, 0.05);
  assert.equal(p.source, "simulation");
  assert.ok(p.state.odom.position[0] > 0);
  assert.equal(p.state.currents_ma, null);
  assert.ok(Math.abs(Math.hypot(...p.state.imu.quat) - 1) < 1e-12);
  sim.fault = "imu";
  assert.equal(sim.step(input, 0.05).state.imu.quat, null);
});

import { imageQuality } from "../static/vision-quality.js";
test("flat/dark/overexposed images cannot switch themes", () => {
  for (const level of [0, 120, 255]) {
    const px = new Uint8ClampedArray(224 * 224 * 4).fill(level);
    assert.equal(imageQuality(px, 224, 224).ok, false);
  }
  assert.throws(() => imageQuality(new Uint8Array(3), 224, 224));
});

import { Scene } from "../static/scene.js";
test("new daemon epoch clears interpolation even before its first state; missing timestamp freezes", () => {
  const scene = new Scene({ addEventListener() {} });
  const p = frame();
  p.epoch = "a";
  scene.push(p);
  assert.equal(scene.history.length, 1);
  scene.poseValid = true;
  scene.push({ epoch: "b", state: null });
  assert.equal(scene.history.length, 0);
  assert.equal(scene.poseValid, false);
  p.epoch = "b";
  p.state.t_ns = null;
  scene.push(p);
  assert.equal(scene.sample(performance.now()), null);
});
