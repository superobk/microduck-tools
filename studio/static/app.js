import { Inputs } from "./inputs.js";
import { Simulator } from "./simulation.js";
import { Scene, THEMES } from "./scene.js";
import { Atmosphere, tofEvidence } from "./feedback.js";
const $ = (id) => document.getElementById(id),
  P = "/api/studio/v1";
const names = [
  "左髋偏航",
  "左髋侧倾",
  "左髋俯仰",
  "左膝",
  "左踝",
  "颈俯仰",
  "头俯仰",
  "头偏航",
  "头侧倾",
  "嘴",
  "右髋偏航",
  "右髋侧倾",
  "右髋俯仰",
  "右膝",
  "右踝",
];
const ids = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14];
let csrf = "",
  ws = null,
  connection = null,
  requestId = 0,
  pending = new Map(),
  demo = false,
  mode = "simulation",
  live = {},
  packet = {},
  own = false,
  seq = 0,
  needNeutral = true,
  lastHeld = false,
  lastIdle = 0,
  model = {},
  modelEpoch = null,
  modelFetch = 0,
  queryData = {},
  serverAssets = {},
  tofPacket = null,
  lastDiagnostic = 0,
  controlPending = false,
  discoveredPads = [];
let vision = false,
  visionReady = false,
  visionBusy = false,
  worker = null,
  lastVision = 0,
  lastVisionFrame = null,
  visionPing = 0,
  visionTimeout = null,
  modelTimeout = null;
const input = new Inputs(),
  sim = new Simulator(),
  scene = new Scene($("world")),
  atmosphere = new Atmosphere();
const pretty = (v) => JSON.stringify(v, null, 2),
  text = (id, v) => ($(id).textContent = v);
const fmt = (x, d = 2) => (Number.isFinite(x) ? x.toFixed(d) : "未知");
const arr = (v, d = 2) =>
  Array.isArray(v) ? v.map((x) => fmt(x, d)).join(" / ") : "未知";
function toast(s) {
  text("toast", String(s));
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("toast").hidden = true), 4500);
}
async function api(path, body) {
  if (body === undefined && connection)
    path +=
      (path.includes("?") ? "&" : "?") +
      "connection_id=" +
      encodeURIComponent(connection);
  const r = await fetch(P + path, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Studio-CSRF": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await r.json();
  if (!r.ok) throw Error((result.code || r.status) + ": " + result.error);
  return result;
}
function call(op, data = {}) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      reject(Error("连接未就绪"));
      return;
    }
    const id = ++requestId,
      timer = setTimeout(
        () => {
          pending.delete(id);
          reject(Error(op + "等待超时；不自动重试"));
        },
        op === "claim" ? 20000 : 5000,
      );
    pending.set(id, { resolve, reject, timer, op });
    ws.send(JSON.stringify({ op, id, ...data }));
  });
}
async function stop(reason = "用户停车") {
  input.clear();
  needNeutral = true;
  lastHeld = false;
  if (mode === "live" && own) {
    try {
      await call("stop");
    } catch (e) {
      toast(e.message);
    }
  }
  text("leaseStatus", reason + "；重新移动前释放按键并归中");
}
input.onLost = (reason) => {
  stop(reason);
  if (vision) disableVision();
  if (ws?.readyState === 1)
    call("visible", { visible: !document.hidden }).catch(() => {});
};
scene.onStatus = (s) => text("renderStatus", s);
function tab(name) {
  document.body.dataset.tab = name;
  document
    .querySelectorAll(".tab")
    .forEach((x) => x.classList.toggle("active", x.id === name));
  document
    .querySelectorAll("nav button")
    .forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
  text(
    "pageTitle",
    {
      overview: "小鸭的数字世界",
      control: "明确接管，轻松控制",
      monitor: "看见数据，理解异常",
      policy: "配置、绑定与生效",
      pad: "每一条输入都有来源",
    }[name],
  );
  if (["policy", "pad"].includes(name)) refreshQuery();
  if (name === "monitor") refreshDiagnostics();
  if (connection && mode === "live")
    call("details", { pad: name === "pad" }).catch(() => {});
}
$("navigation").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) tab(b.dataset.tab);
});
document
  .querySelectorAll("[data-jump]")
  .forEach((b) => (b.onclick = () => tab(b.dataset.jump)));
async function boot() {
  try {
    const s = await api("/status");
    csrf = s.csrf;
    demo = s.demo;
    mode = demo ? "simulation" : "live";
    serverAssets = s.assets;
    live = s;
    text(
      "banner",
      demo
        ? "虚拟演练：合成/运动学数据，不连接真实机器人。"
        : "实体反馈：等待新数据与身份核验。",
    );
    $("mode").value = mode;
    $("mode").querySelector("[value=live]").disabled = demo;
    $("login").hidden = true;
    $("workspace").hidden = false;
    connect();
    await scene.init();
    refreshQuery();
    refreshDiagnostics();
  } catch (e) {
    $("login").hidden = false;
    $("workspace").hidden = true;
    text("pairError", e.message);
  }
}
$("pairButton").onclick = async () => {
  try {
    const r = await api("/pair", { code: $("pairCode").value.trim() });
    csrf = r.csrf;
    $("pairCode").value = "";
    await boot();
  } catch (e) {
    text("pairError", e.message);
  }
};
function connect() {
  if (ws) ws.close();
  ws = new WebSocket(
    (location.protocol === "https:" ? "wss:" : "ws:") +
      "//" +
      location.host +
      P +
      "/stream",
  );
  ws.onopen = () => {
    text("connection", "已连接显示端");
    $("connection").className = "pill good";
  };
  ws.onclose = () => {
    text("connection", "连接已断开");
    $("connection").className = "pill bad";
    own = false;
    needNeutral = true;
    input.clear();
    connection = null;
    disableVision(false);
    for (const p of pending.values()) {
      clearTimeout(p.timer);
      p.reject(Error("连接断开，先查看状态，不自动恢复控制"));
    }
    pending.clear();
    text(
      "banner",
      "连接已断开，实体画面将冻结。刷新或重新配对；不会自动恢复运动。",
    );
  };
  ws.onmessage = ({ data }) => {
    let m;
    try {
      m = JSON.parse(data);
    } catch {
      return;
    }
    if (m.type === "welcome") {
      connection = m.connection_id;
      $("exportEvidence").href =
        P +
        "/diagnostics?export=1&connection_id=" +
        encodeURIComponent(connection);
      if (mode !== m.mode)
        call("mode", { mode }).catch((e) => toast(e.message));
      return;
    }
    if (m.type === "reply" || m.type === "error") {
      const p = pending.get(m.id);
      if (p) {
        clearTimeout(p.timer);
        pending.delete(m.id);
        if (m.type === "error") p.reject(Error(m.code + ": " + m.error));
        else p.resolve(m.result);
      }
      return;
    }
    if (m.type === "frame") {
      live = { ...live, ...m.data };
      own = m.data.control?.you === true;
      if (own && m.data.control.armed === false) needNeutral = true;
      if (mode === "live") {
        packet = live;
        if (m.data.tof) tofPacket = { ...live, received: performance.now() };
        scene.push(live);
        renderData(live);
        if (
          modelEpoch !== live.epoch &&
          performance.now() - modelFetch > 1200
        ) {
          modelFetch = performance.now();
          api("/model")
            .then((r) => {
              model = r;
              if (r.tof_beams) modelEpoch = live.epoch;
            })
            .catch(() => {});
        }
      }
    }
  };
}
$("mode").onchange = async () => {
  const next = $("mode").value;
  await stop("切换目标");
  disableVision();
  try {
    await call("mode", { mode: next });
    mode = next;
    own = false;
    needNeutral = true;
    scene.history = [];
    tofPacket = null;
    atmosphere.kind = "unknown";
    text(
      "banner",
      mode === "simulation"
        ? "虚拟演练：不访问真实控制、蓝牙和相机；数值为合成/运动学示例。"
        : "实体反馈模式：只有接管并明确允许后才能控制。",
    );
    refreshQuery();
    refreshDiagnostics();
  } catch (e) {
    $("mode").value = mode;
    toast(e.message);
  }
};
$("inputSource").onchange = async () => {
  await stop("切换输入源");
  input.select($("inputSource").value);
  if (input.source === "keyboard") $("world").focus({ preventScroll: true });
  seq = 0;
  if (mode === "live" && own)
    try {
      await call("switch_source", { source: input.source });
    } catch (e) {
      toast(e.message);
    }
};
$("authorizeImu").onclick = () =>
  input
    .authorize()
    .then(toast)
    .catch((e) => toast(e.message));
$("centerImu").onclick = () => {
  try {
    toast(input.center());
  } catch (e) {
    toast(e.message);
  }
};
function holdButton(button, start, end) {
  button.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    button.setPointerCapture(e.pointerId);
    button.classList.add("held");
    start();
  });
  const release = () => {
    button.classList.remove("held");
    end();
  };
  button.addEventListener("pointerup", release);
  button.addEventListener("pointercancel", release);
  button.addEventListener("lostpointercapture", release);
}
document.querySelectorAll("[data-drive]").forEach((button) =>
  holdButton(
    button,
    () => {
      input.touch = button.dataset.drive.split(",").map(Number);
      input.held = true;
    },
    () => {
      input.touch = [0, 0, 0];
      input.held = false;
      stop("方向键释放");
    },
  ),
);
holdButton(
  $("hold"),
  () => (input.held = true),
  () => {
    input.held = false;
    stop("允许按键释放");
  },
);
$("globalStop").onclick = () => stop();
$("claim").onclick = async () => {
  if (mode === "simulation") {
    toast("当前为虚拟演练，直接按住允许输入；不会暂停板端服务");
    return;
  }
  if (
    !confirm(
      "确认现场条件允许、没有其他直接控制客户端？将暂停售柄服务并接管。先松开全部按键、摇杆归中。不会自动启用policy。",
    )
  )
    return;
  try {
    await call("claim", { source: input.source, confirm: true });
    own = true;
    needNeutral = true;
    seq = 0;
    toast("已接管，先归中并释放，再按住允许");
  } catch (e) {
    toast(e.message);
  }
};
$("returnPad").onclick = async () => {
  if (mode === "simulation") {
    toast("演练不会交接真实手柄");
    return;
  }
  if (
    !confirm("确认摇杆归中、全部按键释放？将停车并恢复原先应运行的手柄服务。")
  )
    return;
  try {
    await stop();
    await call("return", { confirm: true });
    own = false;
    toast("交还请求完成，请查看实际驱动/输入状态");
  } catch (e) {
    toast(e.message);
  }
};
$("park").onclick = async () => {
  if (mode === "simulation") return toast("演练不操作服务");
  if (!confirm("保持手柄暂停并核对中断记录，不恢复移动？")) return;
  try {
    await call("park", { confirm: true });
    own = false;
    toast("已恢复暂停；可明确重新接管或交还");
  } catch (e) {
    toast(e.message);
  }
};
$("theme").onchange = () => {
  $("autoTheme").checked = false;
  scene.setTheme($("theme").value);
};
$("view").onchange = () => (scene.cameraMode = $("view").value);
$("lowPower").onclick = () => {
  scene.low = true;
  scene.targetFps = 15;
  scene.renderer?.setPixelRatio(1);
  toast("低功耗15FPS；不改变控制超时或原生采样");
};
$("simFault").onchange = () => {
  sim.fault = $("simFault").value;
  refreshDiagnostics();
};
function renderData(p) {
  const s = p.state || {},
    a = p.ages || {},
    isSim = p.source === "simulation";
  text("sourceBadge", isSim ? "SIMULATION · 无硬件" : "LIVE · 实体反馈");
  $("sourceBadge").className = "pill " + (isSim ? "" : "good");
  text("loopHz", fmt(s.loop?.hz, 1) + " Hz");
  text(
    "loopDetail",
    "missed " +
      (s.loop?.missed ?? "未知") +
      " · 状态帧龄 " +
      fmt(a.state_ms, 0) +
      "ms",
  );
  const imu = p.health?.imu;
  let imuStatus = imu?.ready === true ? "已就绪" : "未知 / 未就绪";
  if (imu?.consecutive_stale_blocks >= 25) imuStatus = "冻结";
  if (a.state_ms == null || a.state_ms > 600) imuStatus = "状态过期";
  text("imuReady", imuStatus);
  text(
    "imuDetail",
    "连续陈旧 " +
      (imu?.consecutive_stale_blocks ?? "未知") +
      " · 健康龄 " +
      fmt(a.health_ms, 0) +
      "ms",
  );
  text("position", arr(s.odom?.position) + " m");
  text(
    "userRequested",
    mode === "simulation"
      ? arr(s.movement?.requested) + "（模拟）"
      : own
        ? arr(p.control?.requested)
        : "本端未持有实体控制权",
  );
  text("requested", arr(s.movement?.requested));
  text("applied", arr(s.movement?.applied));
  text("currentPolicy", s.policy || "未知");
  text("limited", s.movement?.limited_by?.join(", ") || "无报告 / 未观测");
  text(
    "leaseStatus",
    isSim
      ? "虚拟演练：无实机控制权"
      : own
        ? "本端持有 · " +
          input.source +
          " · " +
          (needNeutral ? "先归中/释放" : "等待按住允许")
        : p.control?.occupied
          ? "其他显示端持有控制权"
          : "未接管；真实控制" +
            (p.control?.enabled ? "已配置开启" : "默认关闭"),
  );
  if (!isSim)
    text(
      "banner",
      p.control?.reason ||
        (!p.identity?.verified
          ? "实际身份尚未核验，真实控制禁用；可查看已有反馈。"
          : "真实反馈模式：提交、应用和实际观测分别显示。"),
    );
  text(
    "poseBadge",
    scene.poseValid
      ? isSim
        ? "模拟姿态"
        : "姿态来自实体反馈"
      : "姿态未知 / 冻结 / 待确认",
  );
  for (let i = 0; i < 15; i++) {
    const item = document.querySelector(`[data-motor="${i}"]`),
      value = s.joints?.[i],
      target = s.targets?.[i],
      error =
        Number.isFinite(value) && Number.isFinite(target)
          ? ((target - value) * 180) / Math.PI
          : null;
    item.querySelector("[data-pos]").textContent =
      fmt(Number.isFinite(value) ? (value * 180) / Math.PI : null) + "°";
    item.querySelector("[data-error]").textContent = fmt(error) + "°";
    item.querySelector("[data-speed]").textContent =
      fmt(s.velocities?.[i]) + "rad/s";
    item.querySelector("[data-current]").textContent =
      fmt(s.currents_ma?.[i], 0) + "mA";
    item.classList.toggle(
      "alert",
      Number.isFinite(error) && Math.abs(error) > 10,
    );
  }
  const f = p.tof;
  if (f) {
    for (let i = 0; i < 64; i++) {
      const el = $("tofGrid").children[i],
        v = f.distance_mm?.[i],
        status = f.status?.[i],
        valid = [5, 9].includes(status) && v > 0;
      el.textContent = valid
        ? (v / 1000).toFixed(2)
        : status === 255
          ? "·"
          : "×";
      el.style.background = valid
        ? `hsl(${Math.max(15, Math.min(115, v / 20))} 35% 22%)`
        : status === 255
          ? "#252d2b"
          : "#46243e";
      el.title = `分区${i} · status=${status} · ${v}mm`;
    }
  }
  let evidence = { kind: "unavailable", reason: "未提供ToF" };
  if (isSim) evidence = tofEvidence(p, demoModel());
  else if (tofPacket) {
    const age =
      (tofPacket.ages?.tof_ms ?? 9999) +
      (performance.now() - tofPacket.received);
    evidence = tofEvidence(
      { ...tofPacket, ages: { ...tofPacket.ages, tof_ms: age } },
      model,
    );
  }
  const kind = atmosphere.update(evidence, f?.t_ns, performance.now());
  scene.atmosphere = kind;
  text(
    "tofStatus",
    `${kind} · ${evidence.reason || ""} · 距离${fmt(evidence.distance)}m · 有效${evidence.count ?? 0}/64`,
  );
  const env = p.environment || {};
  text(
    "environment",
    isSim ? "演练ToF氛围" : env.fresh ? env.result?.label : "等待稳定环境证据",
  );
  text(
    "environmentDetail",
    `${kind} · 视觉帧龄 ${fmt(Number.isFinite(env.frame_age_ms) ? env.frame_age_ms / 1000 : null, 1)}秒 · 估计不控制运动`,
  );
  if (
    !isSim &&
    $("autoTheme").checked &&
    env.fresh &&
    THEMES[env.theme] &&
    scene.theme !== env.theme
  ) {
    scene.setTheme(env.theme);
    $("theme").value = env.theme;
  }
  text(
    "identityDetails",
    pretty({
      source: p.source,
      epoch: p.epoch,
      identity: p.identity,
      ages: p.ages,
      calibration: "保存不等于生效；原校正页仍独立核验",
      model: model.asset ?? null,
    }),
  );
  text("boardPadState", pretty(p.pad || {}));
  text("padInput", pretty(p.pad_input || "未订阅/未提供输入"));
}
function demoModel() {
  const beams = [];
  for (let r = 0; r < 8; r++)
    for (let c = 0; c < 8; c++) {
      const e = ((((3.5 - r) * Math.PI) / 180) * 45) / 8,
        a = ((((3.5 - c) * Math.PI) / 180) * 45) / 8;
      beams.push([
        Math.cos(e) * Math.cos(a),
        Math.cos(e) * Math.sin(a),
        Math.sin(e),
      ]);
    }
  return { tof_beams: beams, trunk_height_m: 0.12, asset: "SIMULATION" };
}
for (let i = 0; i < 15; i++) {
  const item = document.createElement("article");
  item.className = "motor";
  item.dataset.motor = i;
  const b = document.createElement("b");
  b.textContent = names[i] + " · ID " + ids[i];
  item.append(b);
  const dl = document.createElement("dl");
  for (const [label, key] of [
    ["实际", "pos"],
    ["误差", "error"],
    ["速度", "speed"],
    ["电流", "current"],
  ]) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.dataset[key] = "";
    dd.textContent = "未知";
    dl.append(dt, dd);
  }
  item.append(dl);
  $("motors").append(item);
}
for (let i = 0; i < 64; i++) {
  const el = document.createElement("div");
  el.className = "tof-cell";
  el.textContent = "·";
  $("tofGrid").append(el);
}
let lastTick = performance.now();
function animate(now) {
  requestAnimationFrame(animate);
  if (document.hidden || $("workspace").hidden) return;
  const dt = Math.min(0.05, (now - lastTick) / 1000);
  lastTick = now;
  if (mode === "simulation") {
    const sampled = input.sample();
    for (const action of sampled.actions) sim.do(action);
    packet = sim.step(sampled, dt);
    scene.push(packet);
    if (now - (animate.lastData || 0) > 200) {
      renderData(packet);
      text(
        "inputPreview",
        pretty({
          source: sampled.source,
          held: sampled.held,
          requested: sampled.values,
        }),
      );
      if (sampled.pad) text("localPadState", pretty(sampled.pad));
      animate.lastData = now;
    }
  }
  scene.render(now);
  if (now - (animate.lastStats || 0) > 1000) {
    text(
      "renderStats",
      `${fmt(scene.fps, 0)}FPS · ${scene.resources.calls ?? "--"}绘制 · ${scene.resources.triangles ?? "--"}三角形`,
    );
    animate.lastStats = now;
  }
}
requestAnimationFrame(animate);
setInterval(async () => {
  if (
    $("workspace").hidden ||
    document.hidden ||
    mode === "simulation" ||
    controlPending
  )
    return;
  const sampled = input.sample();
  text(
    "inputPreview",
    pretty({
      source: sampled.source,
      held: sampled.held,
      requested_m_s_rad_s: sampled.values,
    }),
  );
  if (sampled.pad) text("localPadState", pretty(sampled.pad));
  if (mode !== "live" || !own) return;
  for (const action of needNeutral ? [] : sampled.actions) {
    const map = { nod: "a", sit_toggle: "b", flap: "x", chirp: "y" },
      binding = queryData.bindings?.bindings?.find(
        (b) => b.button === map[action],
      );
    if (binding?.skill) performSkill(binding.skill);
    else toast("该本机按键尚无有效板端skill绑定，未执行");
  }
  if (needNeutral) {
    if (!sampled.neutral) return;
    try {
      controlPending = true;
      await call("input", {
        source: sampled.source,
        seq: ++seq,
        held: false,
        values: sampled.values,
      });
      needNeutral = false;
    } catch (e) {
      if (e.message !== animate.lastError) {
        toast(e.message);
        animate.lastError = e.message;
      }
    } finally {
      controlPending = false;
    }
    return;
  }
  if (!sampled.held && !lastHeld && performance.now() - lastIdle < 1000) return;
  lastHeld = sampled.held;
  if (!sampled.held) lastIdle = performance.now();
  try {
    controlPending = true;
    await call("input", {
      source: sampled.source,
      seq: ++seq,
      held: sampled.held,
      values: sampled.values,
    });
  } catch (e) {
    needNeutral = true;
    input.clear();
    if (e.message !== animate.lastError) {
      toast(e.message);
      animate.lastError = e.message;
    }
  } finally {
    controlPending = false;
  }
}, 50);
function addText(parent, tag, content) {
  const e = document.createElement(tag);
  e.textContent = content;
  parent.append(e);
  return e;
}
function renderIssues(issues) {
  $("diagnostics").replaceChildren();
  $("issueSummary").replaceChildren();
  if (!issues.length) {
    text("diagnostics", "当前没有可列出的规则异常；不代表硬件已验收。");
    text(
      "issueSummary",
      mode === "simulation"
        ? "当前为模拟场景，无真实硬件结论"
        : "未发现当前规则异常；仍需核对检测覆盖。",
    );
    return;
  }
  for (const issue of issues) {
    const d = document.createElement("details");
    d.className = "issue " + (issue.severity || "");
    const summary = document.createElement("summary");
    summary.textContent = issue.code + " · " + issue.title;
    d.append(summary);
    addText(d, "p", "原始信息：" + issue.raw);
    addText(
      d,
      "pre",
      pretty({
        source: issue.source,
        version: issue.version,
        ages: issue.ages,
        evidence: issue.evidence,
        first_seen: issue.first_seen,
        last_seen: issue.last_seen,
        observations: issue.observations,
      }),
    );
    addText(d, "h3", "按顺序排查");
    const ol = document.createElement("ol");
    for (const check of issue.checks || []) addText(ol, "li", check);
    d.append(ol);
    if (issue.command) {
      addText(d, "p", "可复制的只读命令（不会自动执行）");
      addText(d, "pre", issue.command);
    }
    addText(d, "p", "预期：" + issue.expected);
    addText(d, "p", "处理：" + issue.treatment);
    addText(d, "p", "恢复验证：" + issue.verification);
    $("diagnostics").append(d);
    if ($("issueSummary").children.length < 4)
      addText($("issueSummary"), "p", issue.title + " · " + issue.raw);
  }
}
async function refreshDiagnostics() {
  try {
    const r = await api("/diagnostics");
    let issues = r.issues || [];
    if (mode === "simulation") {
      issues = [];
      if (sim.fault !== "none") {
        const code = {
            imu: "IMU_FROZEN",
            tof: "TOF_STALE",
            stream: "STATE_STALE",
          }[sim.fault],
          b = r.playbooks?.[code];
        if (b)
          issues = [
            {
              code,
              title: b[0],
              raw: "合成故障案例，非实机结果",
              source: "simulation",
              severity: "warning",
              evidence: { case: sim.fault },
              checks: b[1],
              command: b[2],
              expected: b[3],
              treatment: b[4],
              verification: b[5],
            },
          ];
      }
    }
    renderIssues(issues);
    lastDiagnostic = performance.now();
  } catch (e) {
    toast(e.message);
  }
}
$("refreshDiagnostics").onclick = refreshDiagnostics;
$("readLogs").onclick = async () => {
  try {
    text("logs", pretty(await api("/logs?unit=robotd")));
  } catch (e) {
    toast(e.message);
  }
};
setInterval(() => {
  if (!$("workspace").hidden && !document.hidden) {
    if (performance.now() - lastDiagnostic > 5000) refreshDiagnostics();
    api("/resources")
      .then((r) => text("resourceDetails", pretty(r)))
      .catch(() => {});
  }
}, 5000);
async function refreshQuery() {
  try {
    queryData = await api("/query");
    if (mode === "simulation")
      queryData = {
        policies: { enabled: false, source: "SIMULATION" },
        skills: {
          skills: [{ name: "nod" }, { name: "flap" }, { name: "sit_toggle" }],
        },
        bindings: {
          bindings: ["a", "b", "x", "y", "lb", "rb"].map((button, i) => ({
            button,
            skill: ["nod", "sit_toggle", "flap", "", "", ""][i],
          })),
        },
        pad: { pads: [] },
      };
    text("policyState", pretty(queryData.policies));
    renderSkills();
    renderBindings();
    renderPads(
      [...discoveredPads, ...(queryData.pad?.pads || [])].filter(
        (r, i, a) => a.findIndex((v) => v.mac === r.mac) === i,
      ),
    );
  } catch (e) {
    toast(e.message);
  }
}
function renderSkills() {
  const box = $("skillButtons");
  box.replaceChildren();
  const skills = queryData.skills?.skills || [];
  for (const s of skills) {
    const b = addText(box, "button", s.name);
    b.onclick = () => performSkill(s.name);
  }
}
async function performSkill(name) {
  if (mode === "simulation") {
    sim.do(name);
    return;
  }
  await runAction("skill", { skill: name });
}
function renderBindings() {
  const box = $("bindings");
  box.replaceChildren();
  for (const row of queryData.bindings?.bindings || []) {
    const div = document.createElement("div");
    div.className = "binding-row";
    addText(div, "b", row.button.toUpperCase());
    const select = document.createElement("select");
    for (const [label, value] of [
      ["禁用", ""],
      ...(queryData.skills?.skills || []).map((s) => [s.name, s.name]),
    ]) {
      const o = document.createElement("option");
      o.textContent = label;
      o.value = value;
      select.append(o);
    }
    if (
      row.skill &&
      !Array.from(select.options).some((o) => o.value === row.skill)
    ) {
      const o = document.createElement("option");
      o.value = row.skill;
      o.textContent = row.skill + "（未知）";
      select.append(o);
    }
    select.value = row.skill || "";
    div.append(select);
    const save = addText(div, "button", "保存");
    save.onclick = () =>
      runAction("bind", { button: row.button, skill: select.value });
    const reset = addText(div, "button", "恢复默认");
    reset.onclick = () => runAction("bind", { button: row.button });
    box.append(div);
    if (row.error) addText(box, "p", "绑定错误：" + row.error);
  }
}
function renderPads(rows) {
  const select = $("boardPad"),
    previous = select.value;
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "选择扫描/配对设备";
  select.append(empty);
  for (const row of rows) {
    if (!row.mac) continue;
    const o = document.createElement("option");
    o.value = row.mac;
    o.textContent = `${row.name ?? "gamepad"} · ${row.mac} · ${row.connected ? "已连接" : "未连接"}`;
    select.append(o);
  }
  select.value = previous;
}
async function runAction(operation, params) {
  if (mode === "simulation") {
    const r = {
      source: "simulation",
      operation,
      params,
      hardware_call: false,
      message: "演练预览：未改实机配置，未调用蓝牙或控制服务",
    };
    text("actionResult", pretty(r));
    text("padActionResult", pretty(r));
    toast("演练预览，未操作真实设备");
    return r;
  }
  if (!own) {
    toast("先在控制页明确接管并释放移动输入");
    return;
  }
  const effects = {
    enable: "启用可能上电/归位；停用会返回归位姿态。",
    skill: "执行真实动作。",
    sound: "播放机器人声音。",
    pair: "配对板端蓝牙手柄。",
    forget: "删除指定板端手柄配对。",
    scan: "限时蓝牙发现，可能影响无线链路。",
  };
  if (
    !confirm(
      `${effects[operation] || "修改真实配置/连接。"}\n${pretty(params)}\n确认现场条件允许？`,
    )
  )
    return;
  try {
    await stop("配置/动作操作前停车");
    const result = await api("/action", {
      connection_id: connection,
      operation,
      params,
      confirm: true,
    });
    text("actionResult", pretty(result));
    let job;
    do {
      await new Promise((r) => setTimeout(r, 600));
      job = await api("/jobs/" + result.job_id);
      text("actionResult", pretty(job));
      text("padActionResult", pretty(job));
    } while (job.state === "running");
    if (job.state === "failed") toast(job.error);
    else {
      toast("已返回操作结果；请核对读回与实际反馈");
      if (operation === "scan") discoveredPads = job.result.devices || [];
      if (operation === "fetch" && job.result?.path)
        $("modelPath").value = job.result.path;
      await refreshQuery();
    }
    return job;
  } catch (e) {
    toast(e.message);
  }
}
$("refreshPolicy").onclick = refreshQuery;
$("enablePolicy").onclick = () => runAction("enable", { on: true });
$("disablePolicy").onclick = () => runAction("enable", { on: false });
document.querySelectorAll("[data-sound]").forEach(
  (b) =>
    (b.onclick = () => {
      if (mode === "simulation") {
        toast("模拟声音提示（未播放实机）");
        sim.do("chirp");
      } else runAction("sound", { tag: b.dataset.sound });
    }),
);
$("loadPolicy").onclick = () =>
  runAction("load_policy", {
    slot: $("slot").value,
    path: $("modelPath").value.trim(),
  });
$("resetPolicy").onclick = () =>
  runAction("load_policy", { slot: $("slot").value });
$("fetchPolicy").onclick = () => {
  const p = { repo: $("hubRepo").value.trim() };
  if ($("hubRevision").value.trim()) p.revision = $("hubRevision").value.trim();
  if ($("hubFile").value.trim()) p.file = $("hubFile").value.trim();
  runAction("fetch", p);
};
$("saveSkill").onclick = () => {
  try {
    const p = {
      name: $("skillName").value.trim(),
      duration: Number($("skillDuration").value),
      command: JSON.parse($("skillCommand").value),
    };
    if ($("skillPath").value.trim()) p.path = $("skillPath").value.trim();
    runAction("set_skill", p);
  } catch (e) {
    toast(e.message);
  }
};
$("removeSkill").onclick = () =>
  runAction("remove_skill", { name: $("skillName").value.trim() });
const selectedMac = () => $("padMac").value.trim() || $("boardPad").value;
$("scanPad").onclick = () => runAction("scan", {});
$("pairPad").onclick = () =>
  runAction(
    "pair",
    selectedMac()
      ? { mac: selectedMac(), timeout_seconds: 45 }
      : { timeout_seconds: 45 },
  );
for (const [id, op] of [
  ["connectPad", "connect"],
  ["disconnectPad", "disconnect"],
  ["forgetPad", "forget"],
])
  $(id).onclick = () => runAction(op, { mac: selectedMac() });
$("savePadMap").onclick = () => {
  try {
    input.saveMapping({
      x: Number($("mapX").value),
      y: Number($("mapY").value),
      yaw: Number($("mapYaw").value),
      hold: Number($("mapHold").value),
    });
    needNeutral = true;
    toast("本机映射已保存，输入已清空");
  } catch (e) {
    toast(e.message);
  }
};
function disableVision(notify = true) {
  vision = false;
  $("enableVision").disabled = false;
  visionReady = false;
  visionBusy = false;
  clearTimeout(visionTimeout);
  clearTimeout(modelTimeout);
  worker?.terminate();
  worker = null;
  if (notify && ws?.readyState === 1)
    call("analysis", { enabled: false }).catch(() => {});
  text("visionStatus", "环境分析已暂停；不会影响姿态/ToF或控制安全检查");
}
$("disableVision").onclick = () => disableVision();
$("enableVision").onclick = async () => {
  if (vision || $("enableVision").disabled) return;
  if (mode === "simulation")
    return toast("演练不读取真实Camera；语义模型可通过交付的独立样本测试验证");
  if (!serverAssets.model_ready || !serverAssets.model_sha)
    return toast("本地量化模型包缺失，保持手动主题");
  if (
    !confirm(
      "本设备将加载约24MB模型（首次含WASM/JS约47MB局域网传输），并低频处理机器人相机图像。不上传云端；可能增加本设备耗电。确认启用？",
    )
  )
    return;
  try {
    $("enableVision").disabled = true;
    await call("analysis", { enabled: true });
    vision = true;
    lastVision = 0;
    worker = new Worker("/static/semantic-worker.js", { type: "module" });
    text("visionStatus", "正在加载本地模型，不会在板端推理…");
    modelTimeout = setTimeout(() => {
      disableVision();
      toast("模型加载超过120秒，已停止。请检查本地资产和浏览器能力");
    }, 120000);
    worker.onmessage = ({ data }) => {
      if (data.type === "ready") {
        clearTimeout(modelTimeout);
        visionReady = true;
        text("visionStatus", "模型就绪：本设备低频推理，分数是估计支持度");
        return;
      }
      if (data.type === "progress") {
        text(
          "visionStatus",
          "本地模型加载：" +
            data.status +
            " " +
            (data.progress == null ? "" : Math.round(data.progress) + "%"),
        );
        return;
      }
      if (data.type === "result") {
        clearTimeout(visionTimeout);
        visionBusy = false;
        text("semanticResult", pretty(data));
        call("semantic", data).catch((e) => toast(e.message));
        text(
          "visionStatus",
          "低频推理完成 " +
            Math.round(data.elapsed_ms) +
            "ms；环境估计不参与控制",
        );
        if (data.elapsed_ms > 3000) lastVision = performance.now() + 20000;
        return;
      }
      if (data.type === "error") {
        disableVision();
        toast("环境分析失败：" + data.error);
      }
    };
    worker.onerror = (e) => {
      disableVision();
      toast("分析Worker错误：" + e.message);
    };
    worker.postMessage({
      op: "load",
      model_revision: serverAssets.model_revision,
      model_sha: serverAssets.model_sha,
    });
  } catch (e) {
    disableVision(false);
    toast(e.message);
  }
};
setInterval(async () => {
  if (!vision || mode !== "live" || !connection || document.hidden) return;
  if (performance.now() - visionPing > 5000) {
    visionPing = performance.now();
    call("analysis_ping").catch((e) => {
      disableVision(false);
      toast(e.message);
    });
  }
  if (!visionReady || visionBusy || performance.now() - lastVision < 10000)
    return;
  visionBusy = true;
  lastVision = performance.now();
  try {
    const r = await fetch(
      P + "/camera?connection_id=" + encodeURIComponent(connection),
      { credentials: "same-origin" },
    );
    if (!r.ok) {
      const error = await r.json();
      throw Error(error.code + ": " + error.error);
    }
    const frameId = r.headers.get("X-Frame-Id");
    if (frameId === lastVisionFrame) {
      visionBusy = false;
      return;
    }
    lastVisionFrame = frameId;
    const blob = await r.blob(),
      bitmap = await createImageBitmap(blob);
    const canvas = document.createElement("canvas");
    canvas.width = 224;
    canvas.height = 224;
    const c = canvas.getContext("2d", { willReadFrequently: true });
    c.drawImage(bitmap, 0, 0, 224, 224);
    bitmap.close();
    const pixels = c.getImageData(0, 0, 224, 224).data;
    if ($("cameraImage").src.startsWith("blob:"))
      URL.revokeObjectURL($("cameraImage").src);
    $("cameraImage").src = URL.createObjectURL(blob);
    $("cameraImage").hidden = false;
    text(
      "cameraAge",
      "获取时刻 " +
        new Date().toLocaleTimeString() +
        "；曝光精确时间未知；PNG按需编码开销计入预算",
    );
    worker.postMessage(
      {
        op: "classify",
        frame_id: frameId,
        pixels: pixels.buffer,
        width: 224,
        height: 224,
      },
      [pixels.buffer],
    );
    visionTimeout = setTimeout(() => {
      disableVision();
      toast("推理超过10秒，已终止；仍保留姿态、ToF和手动主题");
    }, 10000);
  } catch (e) {
    visionBusy = false;
    text("visionStatus", "本轮视觉降级：" + e.message);
  }
}, 1000);
window.addEventListener("beforeunload", () => {
  input.clear();
  if (ws?.readyState === 1)
    ws.send(JSON.stringify({ op: "visible", visible: false }));
  worker?.terminate();
});
Object.defineProperty(window, "studioDebug", {
  get: () => ({
    mode,
    source: packet.source,
    poseValid: scene.poseValid,
    theme: scene.theme,
    render: scene.resources,
    fps: scene.fps,
    vision,
    own,
    connection: !!connection,
  }),
});
boot();

$("exportEvidence").onclick = async (e) => {
  if (mode !== "simulation") return;
  e.preventDefault();
  try {
    const report = await api("/diagnostics");
    const blob = new Blob(
        [
          JSON.stringify(
            {
              source: "simulation",
              state: packet,
              diagnostics: report,
              hardware_acceptance: "NOT_RUN",
              policy_dynamics_verified: false,
            },
            null,
            2,
          ),
        ],
        { type: "application/json" },
      ),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = "studio-simulation-evidence.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    toast(error.message);
  }
};
