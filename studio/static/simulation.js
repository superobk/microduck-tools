/* Explicit kinematic demo, not a policy/torque simulator. No fetch, WebSocket or native IPC. */
export class Simulator {
  constructor() {
    this.t = 0;
    this.x = 0;
    this.y = 0;
    this.yaw = 0;
    this.action = null;
    this.seq = 0;
    this.fault = "none";
  }
  step(input, dt) {
    dt = Math.min(0.05, Math.max(0, dt));
    this.t += dt;
    const cmd = input.held ? input.values : [0, 0, 0];
    const c = Math.cos(this.yaw),
      s = Math.sin(this.yaw);
    this.x += (cmd[0] * c - cmd[1] * s) * dt;
    this.y += (cmd[0] * s + cmd[1] * c) * dt;
    this.yaw += cmd[2] * dt;
    const stride = Math.abs(cmd[0]) + Math.abs(cmd[1]);
    let roll = 0,
      pitch = 0;
    if (this.action && this.t - this.action.at < 1.3) {
      if (this.action.name === "nod")
        pitch = 0.25 * Math.sin((this.t - this.action.at) * 7);
      if (this.action.name === "flap")
        roll = 0.1 * Math.sin((this.t - this.action.at) * 10);
    }
    const cy = Math.cos(this.yaw / 2),
      sy = Math.sin(this.yaw / 2),
      cp = Math.cos(pitch / 2),
      sp = Math.sin(pitch / 2),
      cr = Math.cos(roll / 2),
      sr = Math.sin(roll / 2);
    const joints = Array(15).fill(0);
    if (stride > 0.005) {
      joints[2] = 0.25 * Math.sin(this.t * 7);
      joints[12] = -joints[2];
      joints[3] = Math.max(0, joints[2]);
      joints[13] = Math.max(0, -joints[2]);
    }
    let q = [
      cr * cp * cy + sr * sp * sy,
      sr * cp * cy - cr * sp * sy,
      cr * sp * cy + sr * cp * sy,
      cr * cp * sy - sr * sp * cy,
    ];
    if (this.fault === "imu") q = null;
    const distances = Array.from({ length: 64 }, (_, i) =>
      Math.round(
        (0.9 + 0.65 * Math.sin(this.x * 2 + this.t * 0.1) + (i % 8) * 0.02) *
          1000,
      ),
    );
    return {
      source: "simulation",
      epoch: "simulation",
      ages: {
        state_ms: this.fault === "stream" ? 1500 : 0,
        tof_ms: this.fault === "tof" ? 2000 : 0,
        health_ms: 0,
      },
      state: {
        t_ns: Math.round(this.t * 1e9),
        imu: { quat: q, gyro: [0, 0, cmd[2]] },
        odom: { position: [this.x, this.y, 0.12], yaw: this.yaw },
        joints,
        targets: [...joints],
        velocities: null,
        currents_ma: null,
        head: [0, 0, 0, 0],
        policy: stride ? "SIMULATED WALK" : "SIMULATED STAND",
        movement: { requested: cmd, applied: cmd, limited_by: [] },
        loop: { hz: 50, missed: 0 },
        safety: {
          gravity: [0, 0, -1],
          fallen: false,
          picked_up: false,
          limp: false,
        },
        tof_pose: { pos: [0.05, 0, 0.04], quat: [1, 0, 0, 0] },
      },
      tof: {
        seq: ++this.seq,
        t_ns: Math.round(this.t * 1e9),
        rows: 8,
        cols: 8,
        distance_mm: distances,
        status: Array(64).fill(5),
      },
      health: {
        healthy: true,
        imu: {
          ready: true,
          consecutive_stale_blocks: this.fault === "imu" ? 30 : 0,
        },
        control_loop: { target_hz: 50, achieved_hz: 50 },
      },
      policies: {
        enabled: true,
        homed: true,
        skills: ["nod", "flap", "sit_toggle"],
      },
      identity: { verified: false },
      environment: { theme: "garden", fresh: false, estimated: true },
      pad: { pads: [] },
    };
  }
  do(name) {
    this.action = { name, at: this.t };
  }
}
