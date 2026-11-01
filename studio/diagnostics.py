"""Reviewed operator playbooks. No generated repairs and no execution of suggested commands."""

import re
import time
from . import VERSION

# code: title, checks, read-only command, expected, manual treatment, verification
BOOK = {
    "STATE_STALE": (
        "机器人状态断流",
        [
            "先看程序身份和健康查询是否仍可达",
            "区分全总线未启动与订阅中断；不要只看最后一帧",
        ],
        "robotctl health --json",
        "得到新健康回复，状态样本时间持续递增",
        "核对供电、连接、robotd日志；服务重启另行现场确认",
        "新状态持续更新，旧帧不再被当作新样本",
    ),
    "HEALTH_STALE": (
        "健康信息过期",
        ["检查状态是否仍更新", "检查robotd的健康RPC与socket权限"],
        "robotctl health --json",
        "健康回复恢复且年龄低于3秒",
        "修复权限/服务问题，勿以旧温度缓存判断当前正常",
        "取得新的健康回复后再评估告警",
    ),
    "DXL_STARTUP": (
        "电机总线尚未启动",
        [
            "核对整组缺失还是部分缺失ID",
            "稳固支撑后检查动力电源、DATA/VCC/GND拓扑和串口占用",
        ],
        "robotctl health --json",
        "缺失ID消失，startup_failures不再增长",
        "断动力检查接插件和线序；不要直接反复init或开扭矩",
        "总线就绪、IMU就绪且新状态持续更新",
    ),
    "DXL_MISSING": (
        "部分电机ID未响应",
        [
            "按缺失ID定位接线段",
            "断动力检查插头、地址冲突与独立供电；不把总线故障判成电机损坏",
        ],
        "robotctl health --json",
        "期望15个ID均响应",
        "现场修复接线/设备；逐项复核，禁止网页自动写寄存器",
        "缺失列表清空并观察持续稳定",
    ),
    "DXL_READ_ERRORS": (
        "运行中总线读取失败",
        ["看连续错误是否增长", "对照IMU与各关节是否同时停更"],
        "journalctl -u robotd -b -n 100 --no-pager",
        "没有持续串口或sync_read错误",
        "检查供电波动、数据线和串口独占；现场确认后处理",
        "连续错误恢复为0，新状态持续更新",
    ),
    "IMU_NOT_READY": (
        "IMU尚未就绪",
        ["查看ready、missing与总线状态", "不要把有效格式的四元数当成滤波器已就绪"],
        "robotctl health --json",
        "imu.ready=true且missing=false",
        "核对IMU供电、固件契约和总线连接；不猜测16.4比例",
        "ready恢复，收到可信新样本",
    ),
    "IMU_FROZEN": (
        "IMU方向连续陈旧",
        [
            "查看连续陈旧次数而非累计次数",
            "确认不是整个订阅断流；四元数不变本身不能证明冻结",
        ],
        "robotctl health --json",
        "新的读取使consecutive_stale_blocks回到0",
        "核对MCU/IMU供电与固件；校正与重启须现场审阅",
        "持续收到新样本，姿态与人工观察相符",
    ),
    "IMU_QUAT_INVALID": (
        "姿态四元数缺失或非法",
        ["核对四元数长度、数值和范数", "核对程序版本与传感器来源"],
        "robotctl monitor --hz 5 --json",
        "imu.quat是有限的w/x/y/z且新样本持续到来",
        "修复传感器/解码契约；不要用陀螺积分伪造实际姿态",
        "六方向人工验证，恢复真实姿态显示",
    ),
    "LOOP_LOW": (
        "控制循环频率下降",
        ["区分控制Hz、网页接收Hz和渲染FPS", "检查温度、CPU降频、missed及相机额外负载"],
        "robotctl health --json",
        "achieved_hz接近target_hz且漏周期不持续增加",
        "先关闭相机识别/预览、降低非控制显示负载；不修改控制安全阈值",
        "在同工况比较关闭/开启Studio的控制频率",
    ),
    "CURRENT_UNKNOWN": (
        "电流或速度未公开",
        ["检查15维向量是否存在", "可能是发布选项关闭、fake或版本未提供，不等于零负载"],
        "robotctl monitor --hz 5 --json",
        "currents_ma/velocities包含15个有效值",
        "按部署指南核对发布能力；不推测电机已损坏",
        "缺失值保持未知，恢复数据后再评估",
    ),
    "TRACKING_ERROR": (
        "关节跟随偏差",
        [
            "对照目标、实际角度、电流、速度及policy",
            "承重与静态姿态可有偏差，单独误差不能判定卡滞",
        ],
        "robotctl monitor --hz 5 --json",
        "误差恢复并有新样本，负载未持续异常",
        "先停车并现场检查负载和装配；不要自动增益或反复动作",
        "同一工况持续观察；结合既有舵机健康参考基线",
    ),
    "TEMPERATURE": (
        "报告温度偏高",
        ["核对最高温与最热关节", "当前汇总接口不能验证原始温度采样新鲜度"],
        "robotctl health --json",
        "新报告回落，现场确认散热及负载",
        "停车并检查承重/卡滞与散热；历史报告不能当实时温度",
        "新报告及现场复核均通过；解除不代表完成检修",
    ),
    "VOLTAGE": (
        "供电报告超出参考范围",
        [
            "区分平滑汇总值与逐电机维护读数",
            "XL330参考输入为3.7–6.0V，不因未关扭矩就判定电压合规",
        ],
        "robotctl health --json",
        "报告与实际测量均回到允许范围",
        "断动力核对供电与线压降；保护参数只审阅不自动改",
        "新读数、实际测量与维护阈值复核",
    ),
    "POLICY_DISABLED": (
        "启动配置未加载policy",
        [
            "查看robot.policies.enabled和实际启动命令",
            "--no-policy是维护状态，网页不能解除",
        ],
        "systemctl show robotd -p ExecStart --no-pager",
        "已审阅运行配置允许加载policy",
        "按运行模式指南准备差异并现场执行，不自动覆盖旧管理器drop-in",
        "实际加载槽位、归位与反馈一致后再授权控制",
    ),
    "NOT_HOMED": (
        "尚未完成归位",
        ["看homed及daemon拒绝原因", "指令接受不等于归位完成"],
        "robotctl health --json",
        "homed=true且健康状态可用",
        "不要反复发动作；按现场支撑与动力条件审阅",
        "真实回执归位完成后再尝试",
    ),
    "POLICY_LOAD": (
        "模型或skill加载异常",
        [
            "核对配置路径、实际加载文件及error",
            "模型须匹配实际model API；按键绑定的是skill",
        ],
        "journalctl -u robotd -b -n 100 --no-pager",
        "实际槽位/skill与请求一致且error为空",
        "只恢复本次修改或明确选择默认；不整体覆盖配置",
        "读回policies/skills/bindings再测试",
    ),
    "PAD_DRIVER": (
        "手柄驱动或连接异常",
        [
            "分开核对配对、连接、padd驱动、输入帧、绑定",
            "手机接管时驱动暂停是正常交接状态",
        ],
        "robotctl pad status",
        "connected、驱动与当前控制模式一致",
        "先看驱动日志；不要把paired当作HID已接管",
        "实际轴/按键输入恢复，再验证skill",
    ),
    "PAD_NO_INPUT": (
        "手柄未报告输入",
        ["确认设备连接的是板端还是本机", "本机手柄须系统先连接并在前台按键激活网页"],
        "robotctl pad status",
        "输入帧递增且轴键映射正确",
        "核对系统HID支持、标准映射或校准；切换前释放全部按键",
        "手动移动摇杆，确认输入预览变化",
    ),
    "TOF_UNAVAILABLE": (
        "ToF未提供",
        [
            "查看传感器名称及unavailable原文",
            "未安装与读取失败分开，不代表整个机器人故障",
        ],
        "journalctl -u tofd -b -n 100 --no-pager",
        "传感器被识别并产生有效帧",
        "检查服务和硬件；不通过网页启动或重新配置传感器",
        "seq和时间戳递增，正确区分无目标/无效",
    ),
    "TOF_STALE": (
        "ToF帧过期或格式异常",
        ["检查seq、时间戳、64分区距离和status", "无目标不等于前方安全"],
        "journalctl -u tofd -b -n 100 --no-pager",
        "得到完整且不断更新的8×8帧",
        "检查源服务与传输，不用旧帧继续变化氛围",
        "新样本到来并和姿态同步",
    ),
    "CAMERA": (
        "相机预览/识别不可用",
        [
            "检查mediad实际身份、服务状态及返回错误",
            "快照会按需取帧并编码PNG，不是免费缓存",
        ],
        "journalctl -u mediad -b -n 100 --no-pager",
        "已有mediad可以返回有界的新PNG",
        "先暂停视觉；检查相机/运行库，现场审阅后处理",
        "新快照与方向人工核对通过",
    ),
    "VISION": (
        "环境语义未启用、过期或待稳定",
        [
            "检查模型包、帧ID、置信差距与图像年龄",
            "低置信不代表传感器损坏，环境估计不能参与运动控制",
        ],
        "",
        "客户端Worker完成匹配当前帧的结果",
        "等待稳定图像或改手动主题；客户端过慢时暂停，不迁回板端",
        "不同新帧结果稳定，界面显示真实来源和年龄",
    ),
    "CONTROL": (
        "控制被阻断或停车",
        [
            "看具体拒绝码、控制权与输入来源",
            "检查松手/失焦/心跳/反馈年龄；不扩大超时掩盖问题",
        ],
        "robotctl health --json",
        "状态可信、控制源唯一、接管后归中",
        "按当前原因处理并重新接管，禁止自动恢复移动",
        "释放后停车、重新按住后才产生新指令",
    ),
    "RESOURCE": (
        "Studio资源预算需检查",
        ["区分客户端FPS与板端CPU/RSS", "相机导致的mediad增量也要记录"],
        "systemctl show microduck-studio.slice -p CPUUsageNSec -p MemoryCurrent --no-pager",
        "资源限制实际生效，控制循环保持基线",
        "先停视觉/相机，再降显示频率或二维；不自动提升限额",
        "同工况比较资源与控制循环，板端结果另行登记",
    ),
    "IDENTITY_UNVERIFIED": (
        "程序/校正身份未核验",
        ["检查实际PID/exe哈希、版本、API47和校正回执", "文件已保存不证明运行时已加载"],
        "robotctl version --json",
        "官方固定构建或匹配calv3、用户层及实际回执一致",
        "按既有部署指南审阅，禁止用版本号单独开放控制",
        "实际进程与配置回执一致",
    ),
}


def scrub(text):
    text = str(text)[:2000]
    return re.sub(
        r"(?i)(token|cookie|authorization|password|pair_code)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )


BOOK["MOTION_UNOBSERVED"] = (
    "输入已报告但未观察到运动",
    [
        "比较requested、applied及limited_by，不以提交成功判断移动",
        "检查policy、归位、控制来源和安全状态；先释放允许键",
    ],
    "robotctl monitor --hz 5 --json",
    "真实新样本与期望运动相符，或明确显示限制原因",
    "先停车核对配置和现场机械条件；不要反复init或加大速度",
    "重新明确允许后，小幅操作并对照实体与关节/里程计反馈",
)


class Diagnostics:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.incidents = {}
        self.motion_stamp = None
        self.motion_anchor = None
        self.motion_since = None

    def evaluate(self, store, extra=()):
        active = []
        h = store.health
        s = store.state or {}
        bus = h.get("bus") or {}
        imu = h.get("imu") or {}

        def add(code, raw, evidence, level="warning"):
            item = self.incidents.setdefault(
                code, dict(first_seen=time.time(), count=0)
            )
            item.update(
                last_seen=time.time(), last_active=self.clock(), count=item["count"] + 1
            )
            book = BOOK.get(code, BOOK["CONTROL"])
            active.append(
                dict(
                    code=code,
                    title=book[0],
                    severity=level,
                    raw=scrub(raw),
                    evidence=evidence,
                    source="live",
                    version=dict(tool=VERSION, daemon=store.hello),
                    ages=ages,
                    first_seen=item["first_seen"],
                    last_seen=item["last_seen"],
                    observations=item["count"],
                    checks=book[1],
                    command=book[2],
                    expected=book[3],
                    treatment=book[4],
                    verification=book[5],
                    automatic_repair=False,
                )
            )

        ages = dict(
            state_ms=store.age(store.state_at),
            health_ms=store.age(store.health_at),
            tof_ms=store.age(store.tof_at),
        )
        if not store.identity_verified:
            add(
                "IDENTITY_UNVERIFIED",
                "真实控制关闭",
                dict(hello=store.hello, ages=ages),
                "info",
            )
        if ages["state_ms"] is None or ages["state_ms"] > 600:
            add("STATE_STALE", "无新机器人状态", ages)
        if ages["health_ms"] is None or ages["health_ms"] > 3000:
            add("HEALTH_STALE", "健康来源过期", ages)
        else:
            if bus.get("missing"):
                add(
                    "DXL_MISSING",
                    "缺失电机ID",
                    dict(
                        missing=bus["missing"],
                        startup_failures=bus.get("startup_failures"),
                    ),
                    "error",
                )
            elif bus.get("startup_failures", 0) > 0 and store.state is None:
                add("DXL_STARTUP", h.get("reason", "总线未启动"), bus)
            if bus.get("consecutive_errors", 0) > 0:
                add("DXL_READ_ERRORS", "连续读取失败", bus)
            if imu.get("ready") is not True:
                add("IMU_NOT_READY", "滤波器尚未就绪", imu)
            if imu.get("consecutive_stale_blocks", 0) >= 25:
                add("IMU_FROZEN", "IMU重复返回旧块", imu, "error")
            loop = h.get("control_loop") or {}
            rate = loop.get("achieved_hz")
            target = loop.get("target_hz")
            if (
                isinstance(rate, (int, float))
                and isinstance(target, (int, float))
                and rate < target * 0.9
            ):
                add("LOOP_LOW", h.get("reason", "低于目标90%"), loop)
            motors = h.get("motors") or {}
            temp = motors.get("max_c")
            if isinstance(temp, (int, float)) and temp >= 55:
                add("TEMPERATURE", "汇总温度报告偏高，原始采样年龄未知", motors)
        battery = h.get("battery") or {}
        volts = battery.get("volts")
        if isinstance(volts, (int, float)) and not 3.7 <= volts <= 6:
            add(
                "VOLTAGE",
                "汇总平滑电压超出XL330参考范围；核对实际型号及供电，不等同逐电机实时读数",
                battery,
            )
        if s and s.get("imu", {}).get("quat") is None:
            add("IMU_QUAT_INVALID", "四元数缺失/非法", dict(ages=ages))
        if store.tof is None:
            add("TOF_UNAVAILABLE", "未提供8×8数据", dict(ages=ages), "info")
        elif ages["tof_ms"] > 1500:
            add("TOF_STALE", "ToF超过1.5秒", dict(ages=ages))
        if s.get("joints") and s.get("targets"):
            deviations = [
                dict(joint=i, error_deg=(b - a) * 180 / 3.141592653589793)
                for i, (a, b) in enumerate(zip(s["joints"], s["targets"]))
                if abs(b - a) > 0.174533
            ]
            if deviations:
                add(
                    "TRACKING_ERROR",
                    "跟随误差超过工程初始值10度，不能单独判断卡滞",
                    dict(joints=deviations, ages=ages),
                )
        if any(
            slot.get("error")
            for slot in store.policies.get("slots", [])
            if isinstance(slot, dict)
        ) or store.policies.get("change_error"):
            add("POLICY_LOAD", "实际加载报告存在错误", store.policies)
        if s and s.get("currents_ma") is None:
            add(
                "CURRENT_UNKNOWN",
                "没有完整电流向量",
                dict(velocities_available=s.get("velocities") is not None),
                "info",
            )
        if store.policies and store.policies.get("enabled") is not True:
            add("POLICY_DISABLED", "启动时policy未加载", store.policies, "info")
        stamp = s.get("t_ns")
        pos = s.get("odom", {}).get("position")
        joints = s.get("joints")
        movement = s.get("movement", {})
        if (
            stamp
            and stamp != self.motion_stamp
            and ages["state_ms"] is not None
            and ages["state_ms"] <= 600
        ):
            requested = movement.get("requested")
            if (
                not requested
                or not any(abs(v) > 0.001 for v in requested)
                or pos is None
                or joints is None
                or (self.motion_stamp and stamp < self.motion_stamp)
            ):
                self.motion_since = None
                self.motion_anchor = None
            else:
                actual = pos + joints
                if self.motion_anchor is None or any(
                    abs(a - b) > 0.001 for a, b in zip(actual, self.motion_anchor)
                ):
                    self.motion_since = self.clock()
                    self.motion_anchor = actual
                elif (
                    self.motion_since is not None
                    and self.clock() - self.motion_since >= 2
                ):
                    add(
                        "MOTION_UNOBSERVED",
                        "连续新样本中，已报告输入但未观察到关节/里程计变化；不等于硬件故障",
                        dict(
                            requested=requested,
                            applied=movement.get("applied"),
                            limited_by=movement.get("limited_by"),
                            duration_s=self.clock() - self.motion_since,
                            t_ns=stamp,
                        ),
                        "info",
                    )
            self.motion_stamp = stamp
        for code, raw, evidence in extra:
            add(code, raw, evidence)
        for err in list(store.errors)[-4:]:
            add("CONTROL", err, dict(ages=ages), "info")
        return dict(
            version=1,
            issues=active,
            ages=ages,
            inference="建议为排查顺序，未发现异常不证明硬件完好；不自动修复",
            hardware_acceptance="NOT_RUN",
        )
