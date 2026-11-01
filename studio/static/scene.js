import { enginePosition, clamp } from "./feedback.js";
export const THEMES = {
  garden: { name: "橙光庭院", sky: 0x111b18, ground: 0x263a28, prop: 0x6a9249 },
  harbor: { name: "星夜码头", sky: 0x080e29, ground: 0x143b58, prop: 0xeaba70 },
  forest: { name: "萤火森林", sky: 0x09170f, ground: 0x14341d, prop: 0x367d44 },
  candy: { name: "糖果小镇", sky: 0x251529, ground: 0x53354c, prop: 0xeaa5c5 },
  snow: { name: "极光雪原", sky: 0x10243a, ground: 0xb8d2df, prop: 0x72d0c5 },
  workshop: {
    name: "微缩工坊",
    sky: 0x201712,
    ground: 0x5b4535,
    prop: 0xc99153,
  },
  space: { name: "太空营地", sky: 0x080818, ground: 0x392e42, prop: 0xd46d42 },
};
export class Scene {
  constructor(canvas) {
    this.canvas = canvas;
    this.history = [];
    this.epoch = null;
    this.theme = "garden";
    this.atmosphere = "unknown";
    this.stale = true;
    this.poseValid = false;
    this.frames = 0;
    this.fps = 0;
    this.cameraMode = "follow";
    this.angle = 2.6;
    this.lastPaint = 0;
    this.low = false;
    this.failed = false;
    this.model = null;
    this.lastState = null;
    this.targetFps = 30;
    this.resources = {};
    this.interactions = false;
    canvas.addEventListener("webglcontextlost", (e) => {
      e.preventDefault();
      this.failed = true;
      this.onStatus?.("WebGL上下文丢失，二维回退");
    });
    canvas.addEventListener("pointerdown", (e) => {
      canvas.focus({ preventScroll: true });
      this.drag = { x: e.clientX, angle: this.angle };
      canvas.setPointerCapture?.(e.pointerId);
    });
    canvas.addEventListener("pointermove", (e) => {
      if (this.drag) {
        this.cameraMode = "orbit";
        this.angle = this.drag.angle + (e.clientX - this.drag.x) * 0.01;
      }
    });
    const end = () => (this.drag = null);
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
  }
  async init() {
    try {
      this.T = await import("/assets/three/three.module.js");
      this.build();
      this.failed = false;
      this.canvas.style.display = "block";
      this.fallback?.remove();
      this.fallback = null;
      this.onStatus?.("3D已就绪：手机端渲染");
    } catch (e) {
      this.failed = true;
      this.onStatus?.("二维回退：WebGL2或本地3D资源不可用");
    }
  }
  build() {
    const T = this.T;
    this.renderer = new T.WebGLRenderer({
      canvas: this.canvas,
      antialias: false,
      alpha: false,
      powerPreference: "low-power",
    });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    this.renderer.shadowMap.enabled = false;
    this.world = new T.Scene();
    this.world.fog = new T.FogExp2(0x111b18, 0.28);
    this.camera = new T.PerspectiveCamera(43, 1, 0.01, 12);
    this.camera.position.set(0.55, 0.4, 0.8);
    this.world.add(new T.HemisphereLight(0xffedcc, 0x233c42, 2.4));
    this.light = new T.DirectionalLight(0xffb45f, 2.8);
    this.light.position.set(1, 2, 1);
    this.world.add(this.light);
    this.root = new T.Group();
    this.root.position.y = 0.12;
    this.world.add(this.root);
    this.intentArrows = [0xff8a2b, 0x92ca9a].map((color) => {
      const arrow = new T.ArrowHelper(
        new T.Vector3(0, 0, -1),
        new T.Vector3(),
        0.15,
        color,
        0.025,
        0.015,
      );
      arrow.visible = false;
      this.world.add(arrow);
      return arrow;
    });
    const material = (color) => new T.MeshLambertMaterial({ color });
    this.orange = material(0xffc64e);
    this.beakMat = material(0xff7b16);
    this.black = material(0x19171a);
    this.white = material(0xfff2c8);
    this.sphere = new T.SphereGeometry(1, 12, 8);
    this.box = new T.BoxGeometry(1, 1, 1);
    this.cone = new T.ConeGeometry(1, 1, 7);
    const shape = (parent, geometry, mat, pos, scale) => {
      const m = new T.Mesh(geometry, mat);
      m.position.set(...pos);
      m.scale.set(...scale);
      parent.add(m);
      return m;
    };
    this.shape = shape;
    shape(
      this.root,
      this.sphere,
      this.orange,
      [0, 0, 0.006],
      [0.057, 0.058, 0.073],
    );
    this.head = new T.Group();
    this.head.position.set(0, 0.065, -0.028);
    this.root.add(this.head);
    shape(
      this.head,
      this.sphere,
      this.orange,
      [0, 0, 0],
      [0.047, 0.045, 0.045],
    );
    shape(
      this.head,
      this.box,
      this.beakMat,
      [0, -0.009, -0.047],
      [0.045, 0.014, 0.034],
    );
    this.jaw = shape(
      this.head,
      this.box,
      this.beakMat,
      [0, -0.019, -0.042],
      [0.04, 0.009, 0.025],
    );
    this.eyes = [];
    for (const x of [-0.026, 0.026]) {
      shape(
        this.head,
        this.sphere,
        this.white,
        [x, 0.011, -0.032],
        [0.013, 0.014, 0.009],
      );
      this.eyes.push(
        shape(
          this.head,
          this.sphere,
          this.black,
          [x, 0.011, -0.04],
          [0.007, 0.009, 0.004],
        ),
      );
      shape(
        this.root,
        this.sphere,
        this.orange,
        [x * 2.05, 0, 0],
        [0.012, 0.03, 0.045],
      );
    }
    this.legs = [];
    this.knees = [];
    this.ankles = [];
    for (const x of [-0.033, 0.033]) {
      const hip = new T.Group();
      hip.position.set(x, -0.034, 0.005);
      this.root.add(hip);
      shape(hip, this.box, this.beakMat, [0, -0.018, 0], [0.011, 0.036, 0.012]);
      const knee = new T.Group();
      knee.position.y = -0.036;
      hip.add(knee);
      shape(knee, this.box, this.beakMat, [0, -0.015, 0], [0.01, 0.03, 0.012]);
      const ankle = new T.Group();
      ankle.position.y = -0.03;
      knee.add(ankle);
      shape(
        ankle,
        this.sphere,
        this.beakMat,
        [0, -0.004, -0.011],
        [0.025, 0.008, 0.029],
      );
      this.legs.push(hip);
      this.knees.push(knee);
      this.ankles.push(ankle);
    }
    this.track = [];
    this.trackGeometry = new T.BufferGeometry();
    this.trackGeometry.setAttribute(
      "position",
      new T.BufferAttribute(new Float32Array(256 * 3), 3),
    );
    this.trackGeometry.setDrawRange(0, 0);
    this.trail = new T.Line(
      this.trackGeometry,
      new T.LineBasicMaterial({ color: 0xff821f }),
    );
    this.world.add(this.trail);
    shape(
      this.root,
      this.cone,
      this.orange,
      [0, 0.005, 0.073],
      [0.027, 0.04, 0.025],
    );
    this.groundMat = material(0x263a28);
    this.floor = new T.Mesh(new T.PlaneGeometry(5, 5), this.groundMat);
    this.floor.rotation.x = -Math.PI / 2;
    this.world.add(this.floor);
    this.grid = new T.GridHelper(5, 25, 0x876344, 0x314134);
    this.grid.position.y = 0.001;
    this.world.add(this.grid);
    this.props = new T.Group();
    this.world.add(this.props);
    this.setTheme("garden", true);
    const C = new T.Matrix4().set(
      0,
      -1,
      0,
      0,
      0,
      0,
      1,
      0,
      -1,
      0,
      0,
      0,
      0,
      0,
      0,
      1,
    );
    this.basis = new T.Quaternion().setFromRotationMatrix(C);
    this.inverse = this.basis.clone().invert();
    this.q = new T.Quaternion();
    this.target = new T.Vector3();
  }
  setTheme(name, initial = false) {
    if (!THEMES[name]) return;
    this.theme = name;
    if (!this.T || !this.world) return;
    const T = this.T,
      t = THEMES[name];
    if (initial) {
      this.world.background = new T.Color(t.sky);
      this.world.fog.color.setHex(t.sky);
      this.groundMat.color.setHex(t.ground);
    } else
      this.transition = {
        at: performance.now(),
        from: this.world.background.clone(),
        ground: this.groundMat.color.clone(),
        sky: new T.Color(t.sky),
        toGround: new T.Color(t.ground),
      };
    for (const mesh of this.props.children) mesh.dispose?.();
    this.props.clear();
    if (this.propMat) this.propMat.dispose();
    this.propMat = new T.MeshLambertMaterial({ color: t.prop });
    const boxes = new T.InstancedMesh(this.box, this.propMat, 20),
      tops = new T.InstancedMesh(this.cone, this.propMat, 20),
      dots = new T.InstancedMesh(this.sphere, this.beakMat, 32);
    const d = new T.Object3D();
    for (let i = 0; i < 20; i++) {
      const a = i * 2.39996,
        r = 0.4 + (i % 5) * 0.24,
        x = Math.cos(a) * r,
        z = Math.sin(a) * r;
      d.position.set(x, 0.055, z);
      d.rotation.set(0, a, 0);
      d.scale.set(0.07, 0.11, 0.07);
      if (name === "harbor") d.scale.set(0.035, 0.13, 0.035);
      if (name === "workshop") d.scale.set(0.15, 0.08, 0.08);
      if (name === "space") d.scale.set(0.13, 0.07, 0.11);
      d.updateMatrix();
      boxes.setMatrixAt(i, d.matrix);
      d.position.y = name === "forest" ? 0.18 : 0.14;
      d.scale.set(0.12, name === "forest" ? 0.23 : 0.09, 0.12);
      d.updateMatrix();
      tops.setMatrixAt(i, d.matrix);
    }
    for (let i = 0; i < 32; i++) {
      d.position.set(
        Math.sin(i * 3.1) * 1.2,
        0.06 + (i % 8) * 0.04,
        Math.cos(i * 2.7) * 1.2,
      );
      d.scale.setScalar(name === "forest" ? 0.004 : 0.009);
      d.updateMatrix();
      dots.setMatrixAt(i, d.matrix);
    }
    this.props.add(boxes, tops, dots);
    this.decor = dots;
    const accents = new T.InstancedMesh(
      name === "space" ? this.sphere : this.box,
      this.white,
      12,
    );
    for (let i = 0; i < 12; i++) {
      const a = i * 2.4,
        r = 0.5 + (i % 4) * 0.26;
      d.rotation.set(0, a, 0);
      d.position.set(Math.cos(a) * r, 0.12, Math.sin(a) * r);
      d.scale.set(0.012, 0.015, 0.025);
      if (name === "garden") {
        d.position.y = 0.08;
        d.scale.set(0.035, 0.005, 0.035);
      }
      if (name === "harbor") {
        d.position.set((i - 6) * 0.13, 0.004, 0.32);
        d.scale.set(0.12, 0.012, 0.28);
      }
      if (name === "snow") {
        d.position.set((i - 6) * 0.13, 0.65, -0.8);
        d.rotation.z = Math.sin(i) * 0.4;
        d.scale.set(0.18, 0.04, 0.01);
      }
      if (name === "space") {
        d.position.y = 0.45 + (i % 3) * 0.12;
        d.scale.setScalar(0.018 + (i % 4) * 0.008);
      }
      if (name === "workshop") {
        d.position.y = 0.16;
        d.scale.set(0.045, 0.012, 0.012);
      }
      if (name === "candy") {
        d.position.y = 0.07;
        d.scale.set(0.04, 0.025, 0.08);
      }
      if (name === "forest") {
        d.scale.setScalar(0.003);
        d.position.y = 0.25 + (i % 4) * 0.04;
      }
      d.updateMatrix();
      accents.setMatrixAt(i, d.matrix);
    }
    this.props.add(accents);
    this.propMat.transparent = !initial;
    this.propMat.opacity = initial ? 1 : 0;
  }
  push(packet) {
    const s = packet.state;
    const stamp = s?.t_ns,
      now = performance.now();
    if (
      packet.epoch !== this.epoch ||
      (this.history.length && stamp < this.history.at(-1).stamp)
    ) {
      this.history = [];
      this.track = [];
      this.trackGeometry?.setDrawRange(0, 0);
      this.epoch = packet.epoch;
    }
    this.packet = packet;
    this.currentInvalid = !s || !Number.isFinite(stamp) || stamp <= 0;
    if (this.currentInvalid) {
      this.poseValid = false;
      return;
    }
    if (!this.history.length || stamp !== this.history.at(-1).stamp) {
      this.history.push({ stamp, at: now, s, packet });
      while (this.history.length > 7) this.history.shift();
    }
    this.packet = packet;
    const pos = s.odom?.position;
    if (pos && packet.ages?.state_ms <= 600 && s.imu?.quat) {
      const p = enginePosition(pos),
        old = this.track?.at(-1);
      if (
        this.track &&
        (!old || Math.hypot(p[0] - old[0], p[2] - old[2]) > 0.008)
      ) {
        this.track.push(p);
        if (this.track.length > 256) this.track.shift();
        if (this.trackGeometry) {
          const a = this.trackGeometry.attributes.position;
          this.track.forEach((v, i) => a.setXYZ(i, v[0], 0.003, v[2]));
          a.needsUpdate = true;
          this.trackGeometry.setDrawRange(0, this.track.length);
        }
      }
    }
  }
  sample(now) {
    if (this.currentInvalid) {
      this.poseValid = false;
      return null;
    }
    const last = this.history.at(-1);
    if (!last) return null;
    this.stale =
      last.packet.ages?.state_ms == null ||
      last.packet.ages.state_ms + (now - last.at) > 600;
    const imu = last.packet.health?.imu;
    this.poseValid =
      (last.packet.ages?.health_ms ?? 9999) + (now - last.at) <= 3000 &&
      !this.stale &&
      !!last.s.imu?.quat &&
      imu?.ready === true &&
      imu?.consecutive_stale_blocks < 25;
    if (!this.poseValid) return null;
    if (last.packet.source === "simulation" || this.history.length < 2)
      return { a: last.s, b: last.s, t: 1 };
    const target = last.stamp - 200e6 + Math.min(now - last.at, 200) * 1e6;
    let before = this.history[0],
      after = last;
    for (const item of this.history) {
      if (item.stamp <= target) before = item;
      if (item.stamp >= target) {
        after = item;
        break;
      }
    }
    return {
      a: before.s,
      b: after.s,
      t:
        before.stamp === after.stamp
          ? 1
          : clamp((target - before.stamp) / (after.stamp - before.stamp), 0, 1),
    };
  }
  render(now) {
    if (document.hidden || now - this.lastPaint < 1000 / this.targetFps) return;
    const dt = now - this.lastPaint;
    this.lastPaint = now;
    const pose = this.sample(now);
    if (pose) this.lastState = pose.b;
    if (!this.renderer && !this.failed) return;
    if (this.failed) {
      this.drawFallback(pose);
      return;
    }
    const T = this.T,
      width = this.canvas.clientWidth,
      height = this.canvas.clientHeight;
    if (width < 1 || height < 1) return;
    if (
      this.canvas.width !== Math.round(width * this.renderer.getPixelRatio()) ||
      this.canvas.height !== Math.round(height * this.renderer.getPixelRatio())
    ) {
      this.renderer.setSize(width, height, false);
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
    }
    this.orange.color.setHex(this.poseValid ? 0xffc64e : 0x797779);
    if (pose) {
      const { a, b, t } = pose,
        qa = new T.Quaternion(
          a.imu.quat[1],
          a.imu.quat[2],
          a.imu.quat[3],
          a.imu.quat[0],
        ),
        qb = new T.Quaternion(
          b.imu.quat[1],
          b.imu.quat[2],
          b.imu.quat[3],
          b.imu.quat[0],
        );
      qa.slerp(qb, t);
      this.root.quaternion.copy(this.basis).multiply(qa).multiply(this.inverse);
      if (a.odom?.position && b.odom?.position) {
        const pos = a.odom.position.map(
          (v, i) => v + (b.odom.position[i] - v) * t,
        );
        this.root.position.set(...enginePosition(pos));
      }
      const j = b.joints;
      if (j) {
        this.head.rotation.set(-j[5] - j[6], j[7], -j[8]);
        for (let i = 0; i < 2; i++) {
          const k = i === 0 ? 0 : 10;
          this.legs[i].rotation.set(-j[k + 2], j[k], -j[k + 1]);
          this.knees[i].rotation.x = -j[k + 3];
          this.ankles[i].rotation.x = -j[k + 4];
        }
        this.jaw.rotation.x = clamp(j[9], -0.5, 0.5);
      }
    }
    const p = this.root.position;
    for (let i = 0; i < 2; i++) {
      const values =
          this.lastState?.movement?.[i === 0 ? "requested" : "applied"],
        arrow = this.intentArrows[i];
      const mag = values ? Math.hypot(values[0], values[1]) : 0;
      arrow.visible = this.poseValid && mag > 0.001;
      if (arrow.visible) {
        const dir = new T.Vector3(...enginePosition([values[0], values[1], 0]))
          .normalize()
          .applyQuaternion(this.root.quaternion);
        arrow.position.copy(p).add(new T.Vector3(0, 0.16 + i * 0.025, 0));
        arrow.setDirection(dir);
        arrow.setLength(Math.min(0.3, mag * 1.5), 0.025, 0.015);
      }
    }
    this.floor.position.set(p.x, 0, p.z);
    this.grid.position.set(
      Math.round(p.x / 0.2) * 0.2,
      0.001,
      Math.round(p.z / 0.2) * 0.2,
    ); // Props stay in the same world frame as estimated odometry.
    if (this.transition) {
      const tr = this.transition,
        t = clamp((now - tr.at) / 800, 0, 1);
      this.world.background.copy(tr.from).lerp(tr.sky, t);
      this.world.fog.color.copy(this.world.background);
      this.groundMat.color.copy(tr.ground).lerp(tr.toGround, t);
      this.propMat.opacity = t;
      if (t === 1) {
        this.transition = null;
        this.propMat.transparent = false;
      }
    }
    const near = this.atmosphere === "near";
    this.world.fog.density +=
      ((near ? 0.5 : 0.23) - this.world.fog.density) * 0.03;
    this.light.intensity += ((near ? 3.5 : 2.8) - this.light.intensity) * 0.02;
    if (this.cameraMode === "top") {
      this.camera.position.set(p.x, p.y + 1, p.z + 0.02);
    } else {
      const angle = this.cameraMode === "orbit" ? this.angle : 2.6;
      this.camera.position.set(
        p.x + Math.sin(angle) * 0.65,
        p.y + 0.32,
        p.z + Math.cos(angle) * 0.65,
      );
    }
    this.camera.lookAt(p.x, p.y + 0.03, p.z);
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches && !this.low) {
      const blink = Math.sin(now * 0.0017) > 0.995 ? 0.15 : 1;
      for (const eye of this.eyes) eye.scale.y = 0.009 * blink;
      this.decor.rotation.y = Math.sin(now * 0.00008) * 0.015;
    }
    this.renderer.render(this.world, this.camera);
    this.frames++;
    if (!this.fpsAt) this.fpsAt = now;
    if (now - this.fpsAt > 2000) {
      this.fps = (this.frames * 1000) / (now - this.fpsAt);
      this.frames = 0;
      this.fpsAt = now;
      this.resources = {
        calls: this.renderer.info.render.calls,
        triangles: this.renderer.info.render.triangles,
      };
      if (this.fps < 20 && !this.low) {
        this.low = true;
        this.targetFps = 15;
        this.renderer.setPixelRatio(1);
        this.onStatus?.("低功耗15FPS：已降低像素比");
      }
    }
  }
  drawFallback(pose) {
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer = null;
    }
    let canvas = this.fallback;
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "fallback-canvas";
      this.canvas.after(canvas);
      this.canvas.style.display = "none";
      this.fallback = canvas;
    }
    canvas.width = canvas.clientWidth || 500;
    canvas.height = canvas.clientHeight || 350;
    const c = canvas.getContext("2d");
    c.fillStyle = "#101713";
    c.fillRect(0, 0, canvas.width, canvas.height);
    c.save();
    c.translate(canvas.width / 2, canvas.height / 2);
    const q = (pose?.b || this.lastState)?.imu?.quat;
    if (q)
      c.rotate(
        Math.atan2(
          2 * (q[0] * q[1] + q[2] * q[3]),
          1 - 2 * (q[1] ** 2 + q[2] ** 2),
        ),
      );
    c.fillStyle = this.poseValid ? "#ffca57" : "#777";
    c.beginPath();
    c.ellipse(0, 10, 65, 45, 0, 0, 7);
    c.fill();
    c.beginPath();
    c.arc(36, -30, 32, 0, 7);
    c.fill();
    c.fillStyle = "#ff7915";
    c.fillRect(58, -31, 30, 13);
    c.fillStyle = "#111";
    c.beginPath();
    c.arc(43, -40, 5, 0, 7);
    c.fill();
    c.restore();
    c.fillStyle = "#ccc";
    c.font = "14px sans-serif";
    c.fillText(
      this.poseValid
        ? "二维姿态回退 / 数据来源见顶部"
        : "姿态未知或过期，保持最后可信显示",
      16,
      canvas.height - 20,
    );
  }
}
