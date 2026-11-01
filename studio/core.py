"""Bounded Studio state and control decisions. No I/O, serial access or autonomous repair."""

import math
import time
import uuid
from collections import deque

JOINTS = [
    "left_hip_yaw",
    "left_hip_roll",
    "left_hip_pitch",
    "left_knee",
    "left_ankle",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "mouth",
    "right_hip_yaw",
    "right_hip_roll",
    "right_hip_pitch",
    "right_knee",
    "right_ankle",
]
IDS = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14]
SCENES = {
    "garden": "橙光庭院",
    "harbor": "星夜码头",
    "forest": "萤火森林",
    "candy": "糖果小镇",
    "snow": "极光雪原",
    "workshop": "微缩工坊",
    "space": "太空营地",
}
LABEL_SCENES = {
    "workspace": "workshop",
    "home": "garden",
    "greenery": "forest",
    "urban": "candy",
    "waterside": "harbor",
    "snow": "snow",
}
INPUTS = {"touch", "keyboard", "phone_imu", "gamepad"}


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def vector(v, n):
    return (
        list(v)
        if isinstance(v, list) and len(v) == n and all(number(x) for x in v)
        else None
    )


def quaternion(v):
    v = vector(v, 4)
    if v is None:
        return None
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if 0.5 < norm < 1.5 else None


def compact_state(s):
    if not isinstance(s, dict):
        raise Refused("STATE_FORMAT", "状态不是对象")
    safety = s.get("safety") if isinstance(s.get("safety"), dict) else {}
    imu = s.get("imu") if isinstance(s.get("imu"), dict) else {}
    odom = s.get("odom") if isinstance(s.get("odom"), dict) else {}
    movement = s.get("move") if isinstance(s.get("move"), dict) else {}
    loop = s.get("loop") if isinstance(s.get("loop"), dict) else {}
    frames = s.get("frames") if isinstance(s.get("frames"), dict) else {}
    tof_pose = frames.get("tof") if isinstance(frames.get("tof"), dict) else {}
    return dict(
        uptime_s=s.get("t") if number(s.get("t")) and s["t"] >= 0 else None,
        t_ns=s.get("t_ns") if type(s.get("t_ns")) is int and s["t_ns"] > 0 else None,
        imu=dict(quat=quaternion(imu.get("quat")), gyro=vector(imu.get("gyro"), 3)),
        odom=dict(
            position=vector(odom.get("position"), 3),
            yaw=odom.get("yaw") if number(odom.get("yaw")) else None,
        ),
        joints=vector(s.get("joints"), 15),
        targets=vector(s.get("targets"), 15),
        velocities=vector(s.get("velocities"), 15),
        currents_ma=vector(s.get("currents_ma"), 15),
        head=vector(s.get("head"), 4),
        policy=str(s.get("policy", "unknown"))[:100],
        safety={
            **{
                k: safety.get(k) if type(safety.get(k)) is bool else None
                for k in ("fallen", "picked_up", "limp")
            },
            "gravity": vector(safety.get("gravity"), 3),
            "gain": safety.get("gain") if number(safety.get("gain")) else None,
        },
        loop={
            k: loop.get(k) if number(loop.get(k)) else None for k in ("hz", "missed")
        },
        movement=dict(
            requested=vector(movement.get("requested"), 3),
            applied=vector(movement.get("applied"), 3),
            limited_by=(
                [str(x)[:100] for x in movement.get("limited_by", [])[:12]]
                if isinstance(movement.get("limited_by"), list)
                else []
            ),
        ),
        tof_pose=dict(
            pos=vector(tof_pose.get("pos"), 3), quat=quaternion(tof_pose.get("quat"))
        ),
    )


def compact_tof(s):
    if (
        not isinstance(s, dict)
        or type(s.get("rows")) is not int
        or type(s.get("cols")) is not int
    ):
        raise Refused("TOF_FORMAT", "测距尺寸缺失")
    n = s["rows"] * s["cols"]
    if s["rows"] != 8 or s["cols"] != 8:
        raise Refused("TOF_GEOMETRY", "首版仅核对8×8 ToF")
    d, status = s.get("distance_mm"), s.get("status")
    if (
        not isinstance(d, list)
        or not isinstance(status, list)
        or len(d) != n
        or len(status) != n
        or any(type(x) is not int for x in d + status)
    ):
        raise Refused("TOF_VECTOR", "ToF长度/数值非法")
    if any(not -32768 <= x <= 32767 for x in d) or any(
        not 0 <= x <= 255 for x in status
    ):
        raise Refused("TOF_RANGE", "ToF原始值越界")
    return {
        k: s.get(k) for k in ("seq", "t_ns", "rows", "cols", "distance_mm", "status")
    }


class Store:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.epoch = uuid.uuid4().hex
        self.state = None
        self.tof = None
        self.health = {}
        self.model = {}
        self.hello = {}
        self.policies = {}
        self.pad = {}
        self.pad_input = None
        self.state_at = None
        self.tof_at = None
        self.health_at = None
        self.identity_verified = False
        self.calibration_verified = False
        self.errors = deque(maxlen=16)
        self.listeners = 0
        self.state_hz = 5
        self.tof_hz = 2

    def reset(self):
        self.epoch = uuid.uuid4().hex
        self.state = None
        self.tof = None
        self.state_at = None
        self.tof_at = None
        self.health = {}
        self.policies = {}
        self.health_at = None
        self.identity_verified = False
        self.calibration_verified = False

    def ingest(self, sample):
        state = compact_state(sample)
        old = self.state.get("t_ns") if self.state else None
        stamp = state["t_ns"]
        old_uptime = self.state.get("uptime_s") if self.state else None
        restarted = (
            old_uptime is not None
            and state["uptime_s"] is not None
            and state["uptime_s"] < old_uptime - 1e-6
        )
        if restarted or old is not None and stamp is not None and stamp < old:
            self.reset()
        if old is not None and stamp == old:
            return False
        self.state = state
        self.state_at = self.clock()
        return True

    def ingest_tof(self, sample):
        frame = compact_tof(sample)
        if (
            self.tof
            and frame.get("seq") == self.tof.get("seq")
            and frame.get("t_ns") == self.tof.get("t_ns")
        ):
            return False
        self.tof = frame
        self.tof_at = self.clock()
        return True

    def age(self, at):
        return None if at is None else max(0, round((self.clock() - at) * 1000))

    def ready(self, movement=False):
        if not self.identity_verified:
            raise Refused(
                "IDENTITY_UNVERIFIED", "实际程序、版本或API47身份未核验，真实控制禁用"
            )
        if not self.calibration_verified:
            raise Refused("CALIBRATION_UNVERIFIED", "实际校正/官方配置声明尚未核验")
        if (
            self.state is None
            or self.age(self.state_at) > 600
            or self.state["t_ns"] is None
        ):
            raise Refused("STATE_STALE", "机器人状态无可信新样本或超过600ms")
        if self.health_at is None or self.age(self.health_at) > 3000:
            raise Refused("HEALTH_STALE", "健康信息超过3秒")
        imu = self.health.get("imu") or {}
        if (
            imu.get("ready") is not True
            or type(imu.get("consecutive_stale_blocks")) is not int
        ):
            raise Refused("IMU_NOT_READY", "IMU未就绪或新鲜度未知")
        if imu["consecutive_stale_blocks"] >= 25:
            raise Refused("IMU_FROZEN", "daemon报告IMU连续陈旧读取达到25次")
        if self.state["imu"]["quat"] is None:
            raise Refused("IMU_QUAT_INVALID", "没有有效姿态四元数")
        if self.health.get("healthy") is not True:
            raise Refused(
                "ROBOT_UNHEALTHY",
                str(self.health.get("reason") or "机器人健康检查未通过"),
            )
        if self.policies.get("enabled") is not True:
            raise Refused(
                "POLICY_DISABLED",
                "启动配置未加载policy（可能有--no-policy）；不能网页解除",
            )
        if movement:
            if self.policies.get("homed") is not True:
                raise Refused("NOT_HOMED", "尚未报告完成归位")
            if any(
                self.state["safety"].get(k) is not False
                for k in ("fallen", "picked_up", "limp")
            ):
                raise Refused(
                    "SAFETY_HOLD", "跌倒、被拿起、limp或安全状态未知，移动暂停"
                )

    def snapshot(self):
        return dict(
            source="live",
            epoch=self.epoch,
            state=self.state,
            tof=self.tof,
            health=self.health,
            ages=dict(
                state_ms=self.age(self.state_at),
                tof_ms=self.age(self.tof_at),
                health_ms=self.age(self.health_at),
            ),
            identity=dict(
                hello=self.hello,
                verified=self.identity_verified,
                calibration_verified=self.calibration_verified,
            ),
            policies=self.policies,
            pad=self.pad,
            pad_input=self.pad_input,
            errors=list(self.errors),
        )


class Lease:
    """Only fresh, explicitly held input renews motion. A socket, not a login, owns it."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.owner = None
        self.source = None
        self.seq = -1
        self.last = None
        self.last_packet = None
        self.value = [0.0, 0.0, 0.0]
        self.armed = False

    def claim(self, owner, source):
        if source not in INPUTS:
            raise Refused("INPUT_SOURCE", "输入来源未知")
        if self.owner is not None:
            raise Refused("CONTROL_BUSY", "已有控制端；先停车并交还")
        self.owner = owner
        self.source = source
        self.seq = -1
        self.last = None
        self.last_packet = None
        self.value = [0.0, 0.0, 0.0]
        self.armed = False

    def submit(self, owner, seq, source, held, values):
        if owner != self.owner or source != self.source:
            raise Refused("LEASE_MISMATCH", "控制权或输入源不匹配")
        if type(seq) is not int or seq <= self.seq:
            raise Refused("SEQUENCE", "拒绝重复/乱序输入")
        if type(held) is not bool:
            raise Refused("DEADMAN", "缺少明确按住状态")
        values = vector(values, 3)
        if values is None:
            raise Refused("MOVE_INVALID", "速度必须为3个有限数值")
        if any(abs(x) > limit for x, limit in zip(values, (0.1, 0.05, 0.4))):
            raise Refused("MOVE_LIMIT", "超出初始限速0.1/0.05m/s、0.4rad/s")
        now = self.clock()
        if held and self.last_packet is not None and now - self.last_packet < 0.045:
            raise Refused("INPUT_RATE", "输入超过20Hz，未刷新心跳")
        self.seq = seq
        self.last_packet = now
        if not self.armed:
            if held or any(abs(x) > 0.001 for x in values):
                raise Refused("NEUTRAL_REQUIRED", "接管后先释放按键并归中")
            self.armed = True
        self.value = values if held else [0.0, 0.0, 0.0]
        self.last = now if held else None
        return self.value

    def expire(self):
        if self.last is not None and self.clock() - self.last > 0.3:
            self.value = [0.0, 0.0, 0.0]
            self.last = None
            self.armed = False
            return True
        return False

    def clear(self):
        self.owner = None
        self.source = None
        self.value = [0.0, 0.0, 0.0]
        self.last = None
        self.armed = False

    def status(self):
        return dict(
            occupied=self.owner is not None,
            source=self.source,
            armed=self.armed,
            requested=self.value,
        )


class Environment:
    """Display-only analysis lease and frame-bound proposals; never a control input."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.owner = None
        self.heartbeat = None
        self.result = None
        self.candidate = None
        self.last_frame = None
        self.count = 0
        self.theme = "garden"
        self.changed = -30.0
        self.result_at = None
        self.frame_at = None

    def claim(self, owner):
        if (
            self.owner
            and self.owner != owner
            and self.heartbeat is not None
            and self.clock() - self.heartbeat < 15
        ):
            raise Refused("ANALYSIS_BUSY", "另一个显示端正在分析环境")
        self.owner = owner
        self.heartbeat = self.clock()

    def submit(self, owner, result, frame, model_sha):
        if owner != self.owner:
            raise Refused("ANALYSIS_OWNER", "没有环境分析权")
        if (
            not frame
            or self.clock() - frame["at"] > 60
            or result.get("frame_id") != frame["id"]
        ):
            raise Refused("VISION_FRAME", "图像不匹配或超过60秒")
        if result.get("model_sha") != model_sha:
            raise Refused("VISION_MODEL", "模型哈希不匹配")
        label = result.get("label")
        support = result.get("support")
        margin = result.get("margin")
        if (
            label not in {*LABEL_SCENES, "unknown"}
            or not number(support)
            or not number(margin)
            or not 0 <= support <= 1
            or not 0 <= margin <= 1
        ):
            raise Refused("VISION_RESULT", "语义结果非法")
        if self.heartbeat is None or self.clock() - self.heartbeat >= 15:
            raise Refused("ANALYSIS_EXPIRED", "分析权心跳已过期")
        self.heartbeat = self.clock()
        self.result_at = self.clock()
        self.frame_at = frame["at"]
        self.result = {
            k: result[k]
            for k in ("frame_id", "model_sha", "label", "support", "margin")
        }
        if result["frame_id"] == self.last_frame:
            return self.theme
        self.last_frame = result["frame_id"]
        if label == "unknown" or support < 0.65 or margin < 0.15:
            self.candidate = None
            self.count = 0
            return self.theme
        self.count = self.count + 1 if label == self.candidate else 1
        self.candidate = label
        if self.count >= 2 and self.clock() - self.changed >= 30:
            self.theme = LABEL_SCENES[label]
            self.changed = self.clock()
        return self.theme

    def status(self):
        fresh = (
            self.result_at is not None
            and self.frame_at is not None
            and self.clock() - self.frame_at <= 60
            and self.clock() - self.result_at <= 60
        )
        return dict(
            theme=self.theme,
            result=self.result,
            fresh=fresh,
            estimated=True,
            result_age_ms=(
                None
                if self.result_at is None
                else round((self.clock() - self.result_at) * 1000)
            ),
            frame_age_ms=(
                None
                if self.frame_at is None
                else round((self.clock() - self.frame_at) * 1000)
            ),
            reason=(
                "客户端语义估计，不参与控制"
                if fresh
                else "视觉未启用、过期或待稳定；主题保持"
            ),
            analysis_busy=bool(
                self.owner
                and self.heartbeat is not None
                and self.clock() - self.heartbeat < 15
            ),
        )
