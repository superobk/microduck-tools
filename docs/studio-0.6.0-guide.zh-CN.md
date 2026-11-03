# Studio 0.6.0：安装、看状态、配置、停止与回退

**第一板LAN HTTP已按原指南应用并启动，正式接口配对和真实反馈已验证；本人浏览器需新码登录。其他板部署须单独核对。** Studio是独立附加界面；0.16.3→0.18.3升级继续使用冻结0.5.0包的 [升级脚本](manual-upgrade-0183.zh-CN.md)，不要把新源码替换进旧管理器会话。

**首次升级并安装Studio推荐[统一安装套件](install-upgrade-simple.zh-CN.md)：一次上传、一个 `sudo ./duck.sh`，按N逐步继续。下文保留独立Studio命令及完整路径说明。**

## 先在电脑看演练

在解包后的源码根目录，用Python≥3.11：

```sh
python3 -m venv .venv-studio
.venv-studio/bin/pip install -r studio/requirements.txt
.venv-studio/bin/python -m studio.manage demo --assets /绝对路径/runtime-assets
```

前两步仅创建本地隔离依赖。第三步只监听 `http://127.0.0.1:8790/studio/`，终端显示五分钟一次性码，网页输入后进入。资产来自交付目录，不运行npm、不访问机器人。Ctrl+C退出演练。源码仓库不含大型模型/浏览器二进制；缺3D资产会二维回退，仍不会读取真实硬件。

## 板端五个主要动作

解开 `microduck-studio-0.6.0-portable.tar.gz` 后进入其中 `microduck-studio-0.6.0`。以下都从该目录执行；IP用实际固定局域网IPv4替换，不照抄示例。

| 动作 | 命令 | 意义与预期 |
|---|---|---|
| 安装预览 | `sudo ./studio.sh setup --server-ip 192.168.1.56 --assets "$PWD/runtime-assets" --wheels "$PWD/wheels"` | 只显示将安装，不改服务。先核对CPython3.11或3.13 ARM64及匹配的wheel/锁文件、systemd、robot组、离线wheels、venv可用；默认HTTP不需要证书。 |
| 明确安装 | 上一条末尾加 `--apply --confirm EXECUTE` | 独立复制版本、离线建venv、默认0.0.0.0:8790 HTTP、安装pair-code.sh、写三份Studio unit和两个手柄启动条件保护；不启动Studio或robotd。已有未知文件不覆盖。 |
| 启动 | `sudo ./studio.sh start --apply --confirm EXECUTE` | 只启动Studio及其助手，不开舵机、不自动接管手柄；也不设开机自启。 |
| 查看 | `sudo ./studio.sh status` | 看安装阶段、登记哈希、入口、控制/相机开关、两个进程、slice限额和交接状态。异常时停止，不盲目重装。 |
| 登录 | `sudo /opt/microduck-tools/studio/pair-code.sh` | 生成5分钟一次性登录码，不是蓝牙配对。每台显示端各生成一次，最多两个活跃显示端。 |

默认真实控制和Camera均关闭。只看状态不需要打开它们。要为后续现场验收配置开关：

```sh
sudo ./studio.sh configure --controls on --camera on
sudo ./studio.sh configure --controls on --camera on --apply --confirm EXECUTE
sudo ./studio.sh start --apply --confirm EXECUTE
sudo ./studio.sh status
```

第一条预览。第二条先停Studio，再只改独立配置、更新登记哈希；保持停止。第三条明确重新启动读取配置。第四条核对磁盘设置；网页仍须通过运行身份、健康与校正核验才开放真实控制。关闭开关用 `--controls off --camera off`。不要手改已登记文件绕过校验，否则status/start/rollback会因外部漂移拒绝。

## 默认局域网HTTP与一键访问码

新源码setup默认监听0.0.0.0:8790，打开http://实际板端IP:8790/studio/；无需SSH转发或证书。0.0.0.0是绑定地址，不是浏览器地址。--port可指定其他空闲端口；新配置拒绝8443/8765/8766，分别保留给mediad/Tools/USB工作台。实际占用检查失败会拒绝继续，不停止其他服务。精确Host/Origin、配对及CSRF保留，未登录数据接口401。

~~~sh
sudo /opt/microduck-tools/studio/pair-code.sh
~~~

一条命令在本人终端显示页面及5分钟单次码，使用安装登记中的venv，不固定Python小版本。每个显示端各配对一次，最多两个活跃端，会话8小时；服务重启后重新配对。码不进入Git、日志或截图。

已有冻结Studio不要重跑setup。从本轮新源码解包目录执行：

~~~sh
sudo ./deploy/studio/http-configure.sh ACTUAL_BOARD_IPV4
sudo ./tools/studio.sh start --apply --confirm EXECUTE
sudo ./tools/studio.sh status
~~~

第一条只改Studio网络并保持停止；不修改原不可变运行源码/venv/daemon，不改变功能开关。网络中断恢复或回退用同一新源码：

~~~sh
sudo ./tools/studio.sh network-rollback --apply --confirm EXECUTE
~~~

仅恢复先前网络字段和unit，不卸载Studio，不覆盖独立功能设置；回退后保持停止，明确start再生效。network-before.json保存在安装登记目录；外部漂移会拒绝覆盖。第一板迁移已执行并验证；实际网络回退仍NOT_RUN。

HTTP只用于可信局域网，配对及会话不经TLS加密，不暴露公网。手机姿态/Gamepad等需要安全上下文的能力仍受浏览器限制，不能将HTTP当作解除权限限制。

HTTPS仍可选择：新安装setup增加--transport https，默认8790，也可--port指定空闲端口。仅HTTPS安装生成CA/叶证书并打印位置/指纹；旧HTTPS配置无transport字段也继续识别为HTTPS。通过已信任SSH取回/etc/microduck-studio/ca.crt并核对指纹，浏览器信任后再验收权限；不忽略证书警告，不复制ca.key。IP变化/证书过期需另核对和维护SAN。

冻结旧归档保留原HTTPS默认及端口/证书问题的历史快照，不能当作已得到新默认。

## 网页怎么用

1. **总览**：先看顶部 LIVE/SIMULATION、身份和帧龄。真实姿态/位置来自机器人，环境为估计。选择七个场景只改显示。控制循环Hz来自daemon；网页订阅5Hz和客户端FPS分别显示，不能当成DXL采样率。
2. **控制**：先选“虚拟演练”练习。触屏按住方向键；键盘选中后点一下3D画面，按住Space+WASD/QE，X停车；手机IMU先授权→中立校准→按住允许→轻轻倾斜；本机手柄先系统连接→前台按键→选Gamepad，默认LB按住允许。非标准手柄到“手柄”页设置轴/允许键。姿态角、方向与实际机身轴向仍须现场核对。
3. **实体接管**：现场明确允许后，切到实体→点击接管→阅读暂停板端手柄提示→确认→全部松开并归中→重新按住允许。接管不启用policy。`--no-policy`、未归位、IMU/身份未知时会说明阻断原因，不自动解除。不要同时运行直接写入robot.move的其他客户端。
4. **Policy**：读取实际配置→选择槽位→已存在绝对ONNX路径或明确Hub获取→设置槽位→查看任务结果与实际槽位error。自定义skill填写名称/文件/持续时间/命令，再到手柄页绑定skill。恢复默认仅针对本槽位/按键。提交不等于加载成功，更不等于动作完成。
5. **手柄**：板端扫描/配对/连接与本机Gamepad分开。板端手柄必须先明确交还才能恢复原padd驱动。显示paired/trusted/connected/driver/输入帧，不把已配对当可控制。蓝牙操作和绑定须接管且停车，演练只预览，不实际配对。
6. **监控**：看ToF网格、15关节角/误差/速度/电流、身份/健康龄。展开异常卡可以看到原文、证据、只读命令、预期、处理方法和恢复验证；日志只读且限量。导出JSON不含登录码或会话令牌。电流不是准确扭矩、旧温度缓存不能冒充新读数。
7. **环境分析**：只让一台客户端点击“本设备执行环境分析”；首次加载本地约24MB模型。姿态晃动/遮挡/低支持时保持手动主题。自动主题还需另勾“允许稳定语义自动切换”。切后台或停止分析会释放分析权。低性能时暂停视觉、开启低功耗或使用二维，不迁回板端推理。
8. **原校正**：点击“校正与配置”（手机页尾亦可）。已有loopback原Web须运行；保留IMU安装/16.4、关节、相机、健康插件的原流程和运行回执核对。Studio不会自动升级、启动维护Web或替你重启robotd。详见既有各版本校正指南。

## 怎样确认“真的生效”

磁盘路径只说明保存。按顺序看：

| 层 | 检查 | 应看到 |
|---|---|---|
| 安装 | `sudo ./studio.sh status` | installed、哈希一致、Studio/helper active、CPU20%/MemoryHigh64MiB/Max96MiB实际值 |
| 进程配置 | 网页监控身份 + `systemctl show microduck-studio -p ExecStart -p MainPID` | 独立版本目录、API47/0.18.3实际PID/SHA；真实控制通过identity+calibration_verified |
| Studio开关 | 网页控制状态和Camera状态 | 与configure后重启一致；开启不代表现场动作获准 |
| 校正 | 原校正页运行回执 | 保存值、实际进程、用户层路径、native回执及有效值一致；calv3是自定义构建，非官方功能声明 |
| Policy/绑定 | 网页“读取实际配置”及任务readback | slot/path/origin/overridden/error、skill和button→skill符合修改 |
| 实体反馈 | requested/applied/实际姿态/关节/估计位移 | 数据年龄有效且真实观测相符；没有运动不能只凭“提交成功”验收 |

没有真实舵机时可以运行演练；既有三版本无舵机dry-run继续用冻结包独立目录。真实控制不能用fake数据通过身份/健康校验，也不为演练自动停止正式robotd。

## 停止、恢复、回退——分别处理

```sh
sudo ./studio.sh stop --apply --confirm EXECUTE
```

只停止Studio。网关正常退出会尝试发停车；助手、持久暂停标记及保护继续保留，不自动恢复手柄。浏览器网络失联也一样，重连不会自动走。

```sh
sudo ./studio.sh hand-back --apply --confirm NEUTRAL
```

**现场明确全部按键释放、摇杆归中后**执行。助手按已记录状态交还原手柄来源，不启动robotd。网页还在线时优先点击“停车并交还手柄”。异常中断可先点击“中断后恢复暂停”，核对状态再交还。不能通过删除pending文件或强启padd绕过保护。手柄交还是控制源恢复动作，须现场许可。

```sh
sudo ./studio.sh rollback --apply --confirm EXECUTE
```

没有待交接记录且登记无漂移才停止Studio/helper、卸下本附加服务/配置/两个自有条件文件。保留版本文件、venv、安装证据、专用用户和数据目录，不删除账号，不切换robotd/旧Tools，不自动开机自启任何服务。

```sh
sudo ./studio.sh recover --apply --confirm EXECUTE
```

安装/configure中断时用。按持久意图核对文件，只撤销本附加安装；未知修改保留并报错。未完成版本目录改名保留供审阅；完成的同版版本只在逐文件核验一致后重用。若有手柄交接记录仍先处理明确交还，不删除保护。不把recover当robotd恢复命令。全部失败原文和下一步应先由status/日志确认。

## 文件与程序准备情况

| 内容 | 位置 / 状态 |
|---|---|
| 独立源码/venv/浏览器资产 | `/opt/microduck-tools/studio/releases/0.6.0/`；setup后创建，尚未板端执行 |
| Studio网络/功能开关/可选HTTPS路径 | `/etc/microduck-studio/config.json` |
| 独立服务 | `/etc/systemd/system/microduck-studio{,-helper}.service`、`microduck-studio.slice` |
| 普通服务数据/配对哈希 | `/var/lib/microduck-studio/`；码/会话不进Git或证据 |
| 安装登记/独占锁 | `/var/lib/microduck-studio-install/install.json`、`lifecycle.lock` |
| 手柄持久交接 | `/var/lib/microduck-studio-handoff/phone.pending`、`return.intent` |
| 自有手柄条件保护 | `/etc/systemd/system/{padd,netpadd}.service.d/90-microduck-studio-handoff.conf` |
| 原工具插件 | `/etc/microduck-tools/plugins.json`，原IMU/关节校正仍按旧指南指定路径；不会自动改写 |
| 原daemon配置层 | `/etc/robot/robotd.toml`、`/var/lib/robot/robotd.toml`及其指向的校正文件；Studio不迁移/覆盖 |

Python/Web为源码，不需要板端C/Rust编译。交付已准备Three/Transformers/ORT浏览器文件、固定q8模型和**CPython3.11 Linux ARM64**原有二进制wheels，以及本轮独立准备的**CPython3.13 Linux ARM64普通GIL** wheels（10个固定依赖、逐文件哈希）。它们没有在本机执行ARM64动态加载；Python其他ABI/32位/不同Linux运行库不能宣称可用，脚本直接拒绝不匹配Python。板端需有与所选解释器一致的venv/ensurepip（例如python3.13-venv）、systemd/systemd-analyze、robot组（可选HTTPS另需openssl）及native Unix socket权限；脚本不自动apt安装系统包。官方13个程序与两个native校正候选沿用冻结0.5.0包，已准备/编译，但本轮不重编译或替换，现场动态库及物理验收仍未执行。


## Python3.13板端安装增量（2026-11-03）

原先的3.11限制位于Linux ARM64实际安装门控，不是Mac专属。新安装器按当前解释器选择3.11原锁或独立requirements-linux-arm64-py313.lock，拒绝PyPy、free-threaded ABI及其他未核验小版本。板端已有Python3.13时无需另装3.11；使用/usr/bin/python3.13 -m studio.manage显式执行，创建的Studio venv也来自同一3.13解释器，不能全局pip安装依赖。Mac端只需Python3.11+，无需恰好3.11。

旧CPython3.11归档及锁文件保持原样；新的3.13包独立，构建时用--python-version 3.13并核对全部10个wheel。板端动态加载和真实服务验收仍须上板后完成，不以本地跨平台pip解析替代。第一板逐条命令见同目录board1-tools-workbench-step-by-step.zh-CN.md。
