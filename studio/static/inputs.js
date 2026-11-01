/* Browser inputs never have a transport: the selected target decides the sink. */
export class Inputs {
  constructor() {
    this.source = "touch";
    this.held = false;
    this.touch = [0, 0, 0];
    this.keys = new Set();
    this.blocked = new Set();
    this.orientation = null;
    this.neutral = null;
    this.sensorAt = 0;
    this.activePad = null;
    this.padMap = { x: 0, y: 1, yaw: 2, hold: 4 };
    this.buttons = {};
    this.actionQueue = [];
    this.enabled = true;
    try {
      this.padMap = {
        ...this.padMap,
        ...JSON.parse(localStorage.getItem("studio-pad-map") || "{}"),
      };
    } catch {}
    window.addEventListener("keydown", (e) => {
      if (/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      if (
        [
          "KeyW",
          "KeyA",
          "KeyS",
          "KeyD",
          "KeyQ",
          "KeyE",
          "Space",
          "KeyX",
        ].includes(e.code)
      ) {
        e.preventDefault();
        if (e.code === "KeyX") {
          this.clear();
          this.onLost?.("keyboard stop");
          return;
        }
        if (this.blocked.has(e.code) && e.repeat) return;
        if (!e.repeat) this.blocked.delete(e.code);
        this.keys.add(e.code);
      }
    });
    window.addEventListener("keyup", (e) => {
      this.keys.delete(e.code);
      this.blocked.delete(e.code);
    });
    for (const name of ["blur", "pagehide"])
      window.addEventListener(name, () => {
        this.clear();
        this.onLost?.(name);
      });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        this.clear();
        this.onLost?.("hidden");
      }
    });
    window.addEventListener("gamepaddisconnected", () => {
      this.clear();
      this.onLost?.("gamepad disconnected");
    });
    window.addEventListener("deviceorientation", (e) => {
      if (Number.isFinite(e.beta) && Number.isFinite(e.gamma)) {
        this.orientation = {
          beta: e.beta,
          gamma: e.gamma,
          angle: screen.orientation?.angle ?? window.orientation ?? 0,
        };
        this.sensorAt = performance.now();
      }
    });
    screen.orientation?.addEventListener("change", () => {
      this.clear();
      this.neutral = null;
      this.onLost?.("screen orientation changed");
    });
  }
  clear() {
    this.held = false;
    this.touch = [0, 0, 0];
    for (const key of this.keys) this.blocked.add(key);
    this.keys.clear();
    this.actionQueue = [];
  }
  select(source) {
    this.clear();
    this.source = source;
    this.neutral = null;
    this.buttons = {};
  }
  async authorize() {
    if (typeof DeviceOrientationEvent === "undefined")
      throw Error("浏览器没有姿态接口");
    if (
      typeof DeviceOrientationEvent.requestPermission === "function" &&
      (await DeviceOrientationEvent.requestPermission()) !== "granted"
    )
      throw Error("姿态权限被拒绝");
    return "授权已请求，请稍微移动手机后校准";
  }
  center() {
    if (!this.orientation || performance.now() - this.sensorAt > 500)
      throw Error("尚无新姿态样本");
    this.neutral = { ...this.orientation };
    return "已校准中立位置";
  }
  mapAxis(value) {
    const dead = 0.12;
    return Math.abs(value) < dead
      ? 0
      : Math.sign(value) * Math.min(1, (Math.abs(value) - dead) / (1 - dead));
  }
  saveMapping(map) {
    for (const [k, v] of Object.entries(map)) {
      if (
        !["x", "y", "yaw", "hold"].includes(k) ||
        !Number.isInteger(v) ||
        v < 0 ||
        v > 31
      )
        throw Error("轴/键编号须为0–31");
    }
    this.padMap = { ...this.padMap, ...map };
    localStorage.setItem("studio-pad-map", JSON.stringify(this.padMap));
    this.clear();
  }
  sample() {
    let axes = [0, 0, 0],
      held = false,
      padInfo = null;
    if (this.source === "touch") {
      axes = this.touch;
      held = this.held;
    }
    if (this.source === "keyboard") {
      axes = [
        Number(this.keys.has("KeyW")) - Number(this.keys.has("KeyS")),
        Number(this.keys.has("KeyA")) - Number(this.keys.has("KeyD")),
        Number(this.keys.has("KeyQ")) - Number(this.keys.has("KeyE")),
      ];
      held = this.keys.has("Space");
    }
    if (this.source === "phone_imu") {
      held = this.held;
      if (
        !this.neutral ||
        !this.orientation ||
        performance.now() - this.sensorAt > 500
      ) {
        held = false;
        if (this.held) {
          this.clear();
          this.onLost?.("phone IMU stale");
        }
      } else {
        const angle = (this.orientation.angle * Math.PI) / 180;
        const a = (this.neutral.beta - this.orientation.beta) / 20,
          b = (this.neutral.gamma - this.orientation.gamma) / 20;
        axes = [
          this.mapAxis(a * Math.cos(angle) - b * Math.sin(angle)),
          this.mapAxis(b * Math.cos(angle) + a * Math.sin(angle)),
          this.touch[2],
        ];
      }
    }
    if (this.source === "gamepad") {
      const pads = Array.from(navigator.getGamepads?.() || []).filter(Boolean);
      const pad = pads.find((p) => p.index === this.activePad) || pads[0];
      if (!pad && this.activePad !== null) {
        this.activePad = null;
        this.clear();
        this.onLost?.("gamepad missing");
      }
      if (pad) {
        if (this.activePad !== null && this.activePad !== pad.index) {
          this.clear();
          this.onLost?.("gamepad changed");
        }
        this.activePad = pad.index;
        const m = this.padMap;
        axes = [
          this.mapAxis(-(pad.axes[m.y] || 0)),
          this.mapAxis(-(pad.axes[m.x] || 0)),
          this.mapAxis(-(pad.axes[m.yaw] || 0)),
        ];
        held = pad.buttons[m.hold]?.pressed === true;
        padInfo = {
          id: pad.id,
          mapping: pad.mapping,
          axes: pad.axes,
          buttons: pad.buttons.map((b) => b.pressed),
        };
        for (const [i, action] of [
          [0, "nod"],
          [1, "sit_toggle"],
          [2, "flap"],
          [3, "chirp"],
        ]) {
          const on = pad.buttons[i]?.pressed === true;
          if (on && !this.buttons[i]) this.actionQueue.push(action);
          this.buttons[i] = on;
        }
      }
    }
    axes = axes.map((x) =>
      Math.max(-1, Math.min(1, Number.isFinite(x) ? x : 0)),
    );
    return {
      source: this.source,
      held,
      values: axes.map((x, i) => x * [0.1, 0.05, 0.4][i]),
      pad: padInfo,
      neutral:
        !held &&
        axes.every((v) => Math.abs(v) < 0.001) &&
        (!padInfo || padInfo.buttons.every((x) => !x)),
      actions: this.actionQueue.splice(0, 4),
    };
  }
}
