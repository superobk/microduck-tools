# daemon 0.17.9 → 0.18.3 功能对比与工具适配

固定源码比较：0.17.9 `96b89d8613f06722c1d64ce75ab0d7124fed9d8f`，0.18.3 `b2b0d213a402caeac492dd662ea891faa17f67c0`。以下“上游变化”由两棵源码树逐项核对，不以版本号推断；“本工具”指 Microduck Tools 0.4.0。

| 功能 | 官方 0.17.9 | 官方 0.18.3 | 本工具的处理 |
|---|---|---|---|
| IPC | API44 | API47；增加 OS 更新、匿名使用数据接口，含 API45 画廊扩展 | 评审已有 hello/model/health/state 契约后支持47；未知API仍降级 |
| 配置位置 | 单个 robotd.toml | `/var/lib/robot/robotd.toml` 用户层覆盖 `/etc/robot/robotd.toml` 基础层 | 读取合并值，保存只改用户层，基线摘要覆盖两层 |
| 自定义配置路径 | 独立文件 | 非标准路径仍独立，避免隐式叠加板端用户层 | 按同一规则选择写入路径，并校验原生回执 |
| OS 更新保留设置 | 尚无本次用户层机制 | 用户配置与登录状态放持久存储；可收养旧基础层修改 | 升级前备份两层；保留上游收养行为，工具不主动搬运其他设置 |
| 未知配置键 | 严格解析会拒绝未知项 | 忽略并提示未知键，编辑器保留；坏用户层降级到基础层 | 不删除无关键；坏TOML或原生报告用户层拒绝时禁用原生写入 |
| 电流/速度/目标/位置 | 已有；电流为绝对值mA | 字段和单位保持，关闭发布或无测量后端仍可缺失 | 复用同一个5Hz流；缺失显示未知，不补零；没有新增逐舵机温压接口 |
| IMU/关节软件校正 | 官方没有本工具校正契约 | 官方仍没有本工具校正契约 | 两版各用独立固定原生补丁，支持零位/方向、安装四元数与raw/16.4 |
| 相机方向 | CLI `--rotate`，0/90/180/270 | CLI契约保留；配置改为分层 | 按版本/revision成对验证，保留各版官方mediad，只应用工具自有覆盖 |
| GC2093 beta内参 | 尚无beta家族实测内参 | 增加家族内参，个人实测内参优先 | 不覆盖上游或个人内参；相机方向与几何标定分别核验 |
| 弱链路视频 | 可降低分辨率 | 优先降低帧率，保留分辨率 | 不改变媒体码率策略；仍通过新鲜PNG人工核对安装方向 |
| 跌倒预测 | 旧的触发/重新触发规则 | 先在站立范围保持一段时间后布防，触发后需重新布防 | 原样保留0.18.3安全规则，不把升级适配变成控制策略回退 |
| 蓝牙手柄 | 没有本次后台重连扫查 | 对已绑定但断开的手柄重连，间隔从12s退避至120s | 不改绑定/蓝牙配置；不保证解决错误配对密钥或所有HID问题 |
| 启动恢复判断 | 旧启动计时方式 | 等待服务稳定后尽早做一次启动检查 | 不改官方恢复引擎；本地原生覆盖仍须独立登记 |
| 社区策略下载 | 可下载其他来源策略 | 新增 `policy.allow_community`，默认false，限制下载而非已安装策略执行 | 不修改开关，不下载或运行策略 |
| A/B OS更新 | 无API46接口 | `os.status/check/apply/switch`；依赖Yocto/RAUC及板端脚本 | 不加入工具RPC白名单；Armbian板不能因daemon升级就获得A/B能力 |
| 匿名使用数据 | 没有本次上报器 | updaterd默认开启，向PostHog EU上报匿名使用事件，可关闭 | 工具不新增上报、不替用户更改选择，部署审阅单单列此项 |

0.18.3 的上报事件含随机ID、板型/架构、daemon与OS版本、策略来源/槽位等；官方实现排除图像、音频、日志、账户及硬件序列号。可通过官方控制台开关或 `telemetry.set` 关闭，也支持 updaterd 环境的 `MICRODUCK_TELEMETRY=0` / `DO_NOT_TRACK=1`。这是上游行为，不等于本工具的本地健康日志；本轮未启动 updaterd、未发出这些上报，也未代用户设置开关。[固定上游说明](https://github.com/pollen-robotics/microduck/blob/b2b0d213a402caeac492dd662ea891faa17f67c0/docs/robot/telemetry.md)

## 哪些已实现，哪些仍待实机

已实现：API47只读监测、用户层持久化、合并值/生效值区分、回退时遮蔽基础层旧校正路径、精确0.18.3原生身份核验、迁移、相机四方向及旧版本回归。原生候选为 `b2b0d213-calv3-30452a086d1e`。

软件验证：115项Python通过无跳过；724项Rust通过、23项上游测试忽略；ARM64 Linux robotd/robotctl编译通过；0.17.9/0.18.3补丁都从干净源码重建且字节一致；九组浏览器交互和三尺寸实际渲染通过。原生层用真实Rust测试验证用户层校正优先级、非法用户校正被拒绝并回退，及默认比例/16.4三轴单位。

实机升级、加载运行库、MCU比例、IMU安装/关节方向、相机画面、蓝牙重连效果、CPU/RSS/控制环及运动验收均未执行。新增上游能力不构成这台板已经通过验收。

## 固定源码入口

- [API版本及字段](https://github.com/pollen-robotics/microduck/blob/b2b0d213a402caeac492dd662ea891faa17f67c0/duck-ipc-proto/src/lib.rs)
- [配置加载/合并/拒绝回退](https://github.com/pollen-robotics/microduck/blob/b2b0d213a402caeac492dd662ea891faa17f67c0/robotd-params/src/lib.rs) / [编辑与旧修改收养](https://github.com/pollen-robotics/microduck/blob/b2b0d213a402caeac492dd662ea891faa17f67c0/robotd-params/src/edit.rs)
- [A/B接口与能力检查](https://github.com/pollen-robotics/microduck/blob/b2b0d213a402caeac492dd662ea891faa17f67c0/updater/src/os.rs)
- [跌倒布防](https://github.com/pollen-robotics/microduck/blob/b2b0d213a402caeac492dd662ea891faa17f67c0/duck-control/src/fall.rs) / [手柄重连](https://github.com/pollen-robotics/microduck/blob/b2b0d213a402caeac492dd662ea891faa17f67c0/configd/src/main.rs)

[部署、生效与分层回滚](daemon-0.18.3-calibration.zh-CN.md)仍须在执行通知后按实际板端环境核对。
