export const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
export function rotate(q, v) {
  if (!q || q.length !== 4) return null;
  const [w, x, y, z] = q;
  const t = [
    2 * (y * v[2] - z * v[1]),
    2 * (z * v[0] - x * v[2]),
    2 * (x * v[1] - y * v[0]),
  ];
  return [
    v[0] + w * t[0] + y * t[2] - z * t[1],
    v[1] + w * t[1] + z * t[0] - x * t[2],
    v[2] + w * t[2] + x * t[1] - y * t[0],
  ];
}
export const enginePosition = (p) => [-p[1], p[2], -p[0]];
export function tofEvidence(packet, model) {
  const f = packet.tof,
    s = packet.state,
    age = packet.ages?.tof_ms;
  if (!f) return { kind: "unavailable", reason: "ToF未提供" };
  if (age == null || age > 1500)
    return { kind: "stale", reason: "ToF超过1.5秒" };
  if (!s?.t_ns || !f.t_ns || Math.abs(s.t_ns - f.t_ns) > 150e6)
    return { kind: "unknown", reason: "ToF/姿态不同步（>150ms）" };
  const pose = s.tof_pose,
    g = s.safety?.gravity,
    beams = model?.tof_beams;
  if (
    !pose?.quat ||
    !pose.pos ||
    !g ||
    !beams ||
    beams.length !== 64 ||
    !s.imu?.quat ||
    packet.health?.imu?.ready !== true ||
    packet.health?.imu?.consecutive_stale_blocks >= 25
  )
    return { kind: "unknown", reason: "缺少可信姿态/传感器几何，保留原始网格" };
  const norm = Math.hypot(...g);
  if (norm < 0.5) return { kind: "unknown", reason: "重力无效" };
  const down = g.map((x) => x / norm);
  const dot = (a, b) => a.reduce((sum, x, i) => sum + x * b[i], 0);
  const height = s.odom?.position?.[2];
  if (!Number.isFinite(height) || height <= 0 || s.safety.picked_up)
    return { kind: "unknown", reason: "高度未知或被拿起，无法可靠排除地面" };
  const sensorHeight = height - dot(pose.pos, down),
    ranges = [];
  let floor = 0,
    close = 0,
    invalid = 0,
    noTarget = 0;
  for (let i = 0; i < 64; i++) {
    if (f.status[i] === 255) {
      noTarget++;
      continue;
    }
    if (![5, 9].includes(f.status[i]) || !(f.distance_mm[i] > 0)) {
      invalid++;
      continue;
    }
    const r = f.distance_mm[i] / 1000,
      dir = rotate(pose.quat, beams[i]),
      d = dot(dir, down);
    if (sensorHeight > 0 && d > 0 && r * d >= sensorHeight * 0.85) {
      floor++;
      continue;
    }
    const horizontal = r * Math.sqrt(Math.max(0, 1 - d * d));
    if (horizontal < 0.1) {
      close++;
      continue;
    }
    ranges.push(horizontal);
  }
  if (ranges.length < 4)
    return {
      kind: noTarget === 64 ? "no_target" : "unknown",
      reason: "可信非地面分区不足4个",
      count: ranges.length,
      floor,
      close,
      invalid,
      noTarget,
    };
  ranges.sort((a, b) => a - b);
  const distance = ranges[Math.floor((ranges.length - 1) * 0.25)];
  return {
    kind: distance < 0.6 ? "near" : distance > 0.8 ? "far" : "between",
    distance,
    count: ranges.length,
    floor,
    close,
    invalid,
    noTarget,
    reason: "P25水平距离；仅视觉参考，不是避障",
  };
}
export class Atmosphere {
  constructor() {
    this.kind = "unknown";
    this.candidate = null;
    this.since = 0;
    this.lastStamp = null;
  }
  update(evidence, stamp, now) {
    if (
      ["unknown", "stale", "no_target", "unavailable"].includes(evidence.kind)
    ) {
      this.kind = evidence.kind;
      this.candidate = null;
      return this.kind;
    }
    if (evidence.kind === "between") return this.kind;
    if (stamp === this.lastStamp) return this.kind;
    this.lastStamp = stamp;
    if (this.candidate !== evidence.kind) {
      this.candidate = evidence.kind;
      this.since = now;
    } else if (now - this.since >= 600) this.kind = evidence.kind;
    return this.kind;
  }
}
