import test from "node:test";
import assert from "node:assert/strict";
const handlers = {};
globalThis.window = {
  addEventListener: (n, f) => (handlers[n] ??= []).push(f),
};
globalThis.document = {
  hidden: false,
  addEventListener: (n, f) => (handlers[n] ??= []).push(f),
};
globalThis.screen = { orientation: { angle: 0, addEventListener: () => {} } };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, "navigator", {
  value: { getGamepads: () => [] },
  configurable: true,
});
const { Inputs } = await import("../static/inputs.js");
const input = new Inputs();
const event = (n, code, repeat = false) => {
  for (const f of handlers[n] || [])
    f({ code, repeat, target: { tagName: "CANVAS" }, preventDefault() {} });
};
test("keyboard needs deadman; blur cannot autorepeat to resume", () => {
  input.select("keyboard");
  event("keydown", "KeyW");
  assert.equal(input.sample().held, false);
  event("keydown", "Space");
  assert.equal(input.sample().held, true);
  input.clear();
  event("keydown", "Space", true);
  event("keydown", "KeyW", true);
  assert.equal(input.sample().held, false);
  event("keyup", "Space");
  event("keyup", "KeyW");
  event("keydown", "Space");
  event("keydown", "KeyW");
  assert.equal(input.sample().held, true);
});
test("switching sources clears held axes", () => {
  input.select("touch");
  input.touch = [1, 0, 0];
  input.held = true;
  assert.equal(input.sample().values[0], 0.1);
  input.select("phone_imu");
  assert.equal(input.sample().held, false);
});
test("gamepad requires neutral all buttons and limits nonfinite axes", () => {
  input.select("gamepad");
  navigator.getGamepads = () => [
    {
      index: 0,
      id: "fake",
      mapping: "standard",
      axes: [NaN, -100, 0],
      buttons: [{ pressed: true }, ...Array(4).fill({ pressed: false })],
    },
  ];
  let s = input.sample();
  assert.equal(s.neutral, false);
  assert.equal(s.values[0], 0.1);
  assert.ok(s.values.every(Number.isFinite));
  navigator.getGamepads = () => [];
  assert.equal(input.sample().held, false);
});
