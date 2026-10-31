# 无舵机板端：三版本可执行演练

Microduck Tools **0.4.2** 新增 `tools/microduck-dryrun.py`，支持0.16.3、0.17.9、0.18.3准备、切换、验证、循环与回退。板端执行继续等待另行通知，本发布不执行SSH或板端安装。

## 范围与前提

脚本固定管理 `/var/lib/microduck-tools-dryrun`、`microduck-dryrun.service` 和独立 `run/robotd.sock`。没有可指向正式目录/服务的CLI选项；不改正式current，不调用正式robotd、mediad或updater，不执行daemon的init/enable运动命令。所有修改默认JSON预览，**加 `--apply` 才执行**。预览不证明板端验证通过。

执行要求：Linux ARM64 root、systemd（须提供本工具所核验的核心属性，参考249及以上）、Python3.11+、已有非root `microduck-tools` 用户，以及每版匹配的ELF64 AArch64 robotd/robotctl。脚本不下载程序、不创建用户、不安装官方套件。fake服务固定以非root用户和 `--fake --no-policy` 启动，隐藏设备、保护系统目录，仅允许写演练run目录。init调用一次 `systemctl daemon-reload`，不重启其他服务，也不启用开机自启。

输入SHA须来自已核验官方发布/签名或已评审native manifest；对未知文件计算哈希再填入，不等于确认可信。脚本验证SHA，不替代签名验证。已有0.18.3 native产物包含GLIBC_2.35符号需求，旧运行库须重建兼容产物。

| 版本 | API | 官方固定revision | 已交付native revision |
|---|---|---|---|
| 0.16.3 | 41 | `0b2b82f56ac1e32d69018837ac1cd7d4c687f5a0` | 按历史候选真实构建记录填写 |
| 0.17.9 | 44 | `96b89d8613f06722c1d64ce75ab0d7124fed9d8f` | `96b89d86-calv3-f76915b8568d` |
| 0.18.3 | 47 | `b2b0d213a402caeac492dd662ea891faa17f67c0` | `b2b0d213-calv3-30452a086d1e` |

`--fake`绕过真实总线，不能验证电流、温度、扭矩、IMU raw/16.4、关节零位/方向或机械状态。单独 `--no-policy` 仍可能访问真实总线。fake服务不能证明官方自动回滚、完整套件或OS A/B成功。

## 1. 初始化：未来在板端逐段执行

独立解压0.4.2运行包，确保根目录包含tools、deploy、calibration。示例位置 `/opt/microduck-tools-test-0.4.2`，不切正式工具current。

```bash
export DRY_TOOL=/opt/microduck-tools-test-0.4.2/tools/microduck-dryrun.py
id microduck-tools
uname -m
getconf GNU_LIBC_VERSION
python3 --version
python3 "$DRY_TOOL" init
# 审阅预览后：
sudo python3 "$DRY_TOOL" init --apply
```

init仅指创建**演练环境**，不调用 `robotd init`。目录及测试unit必须均不存在；不接管旧指南手工创建的环境。若已有，先由现场审阅、保留备份并移走旧测试环境；不要删除正式服务。中途初始化失败时保留内容和错误，先检查，不反复强行覆盖。脚本没有删除整个目录的命令。

## 2. 准备三个版本

每版提供对应源目录、两个可信SHA及精确hello revision。下面占位值须换成真实内容：

```bash
sudo python3 "$DRY_TOOL" stage --version 0.18.3 \
  --bin-dir /实际解包目录/native/bin \
  --robotd-sha256 '<可信清单中的robotd SHA256>' \
  --robotctl-sha256 '<可信清单中的robotctl SHA256>' \
  --revision b2b0d213-calv3-30452a086d1e
# 审阅后，在同一命令末尾增加 --apply。
```

分别对0.16.3/0.17.9再执行，不能只改version而沿用另一版文件和哈希。脚本拒绝SHA不符、非ELF64 ARM64、源软链接、版本目录已存在；复制后再次核验。准备只复制文件，不执行输入二进制。合成配置关闭音频、头部IMU和策略，不复制正式配置/实际校正。各版本目录不可覆盖；同版换候选须另行审阅，不绕过拒绝。

## 3. 切换和验证

先关闭测试Web前台进程，避免切换时混读身份。脚本不寻找/终止其他Python进程。

```bash
python3 "$DRY_TOOL" switch --version 0.16.3
sudo python3 "$DRY_TOOL" switch --version 0.16.3 --apply
sudo python3 "$DRY_TOOL" verify
sudo python3 "$DRY_TOOL" status
```

切换顺序：检查目录/unit/无额外drop-in、别名/依赖联动/待重载状态，目标及回退文件 → 保存pending事务 → 停止测试服务并确认MainPID/ControlPID归零、状态停止、Job结束及cgroup无残留任务 → 原子切测试current → 启动 → 验证 → 保存成功版本。未准备/被改动目标在停止服务前拒绝；并发写操作被锁拒绝。

验证要求：实际MainPID、`/proc/PID/exe`及完整argv匹配固定fake命令；测试socket的hello版本、精确revision、API和15关节拓扑匹配；health为healthy；Linux SO_PEERCRED确认socket对端就是MainPID，拒绝软链接socket；5Hz订阅至少收到3帧递增 `t_ns`、15维有限关节/目标数据、held policy；前后PID一致。

只发出 `hello`、`robot.model`、`robot.health`、`robot.subscribe`。不使用可能同时查询updaterd的CLI health/version充当隔离验收。验证通过只表示fake软件路径，不代表舵机完好。

## 4. 一次循环及回退

```bash
python3 "$DRY_TOOL" cycle
sudo python3 "$DRY_TOOL" cycle --apply
python3 "$DRY_TOOL" rollback
sudo python3 "$DRY_TOOL" rollback --apply
sudo python3 "$DRY_TOOL" verify
python3 "$DRY_TOOL" stop
sudo python3 "$DRY_TOOL" stop --apply
```

cycle预先检查三版文件，固定执行 **0.16.3 → 0.17.9 → 0.18.3 → 0.17.9 → 0.16.3**；每步验证，失败即停止后续循环。

- 正常rollback切回上一个成功验证版本并再次验证；无历史则拒绝。重复switch当前版不会抹掉previous。
- 新版验证失败自动尝试恢复进入该步之前的版本。即使恢复成功，原switch/cycle仍返回非零、`ok=false`，包含restored证据，不记为升级成功。
- 首次启动失败且无旧版时停止测试服务、清除测试current，不拿正式版本作回退。
- 进程中断或恢复失败保留pending，下一次switch拒绝；审阅后显式rollback。systemctl超时可能留下后台Job，先等待并检查Job结束，再rollback；脚本不取消未知任务、不更换仍在运行实例的current。回退文件损坏时拒绝使用，不能跳过SHA检查。
- stop只停止测试服务，保留版本/历史，不清理文件，不停止正式服务。

这是目录切换和失败恢复；官方更新器健康回滚、签名安装、完整套件和OS切槽须单独验收。

## 5. 连接统一Web

```bash
sudo python3 "$DRY_TOOL" web-command
```

只打印可复制的启动命令，不启动Web。命令固定非root用户、localhost18765、测试配置/回执/状态/socket及 `robotctl-dryrun` 包装入口。包装入口只接受精确的 `monitor --hz 5 --json`，其他命令/额外参数直接拒绝；它为monitor补 `--robot-socket`，避免Web健康与遥测连接不同实例。

已获授权后在板端执行打印出的命令；Mac用本次确认地址建隧道：

```bash
ssh -N -L 18765:127.0.0.1:18765 用户@本次板端地址
```

浏览器访问 `http://127.0.0.1:18765/`。不启用相机应用、DEMO或allow-inactive。fake无真实总线回执，原生校正应禁写；缺电流/速度应显示未知。相机状态可能只读探测正式mediad，不在本演练保存/应用，不算隔离媒体验收。

0.18.3测试配置是非标准路径，因此**不启用正式基础/用户层叠加**。分层已有软件单测，标准路径与启动收养须独立可恢复系统环境验证；不能伪造回执宣称校正已生效。

## 6. 证据与退出码

成功修改及switch/cycle恢复结果保存到root私有 `evidence/<UTC时间>-<随机值>.json`，记录版本/API/revision、二进制SHA、PID/exe、health、时间戳和回退结果。预览不写证据。前置校验错误输出stderr；调用者也应保存stdout/stderr和退出码。进程日志查看 `journalctl -u microduck-dryrun.service`。

退出0：命令成功或输出预览，须区分preview与fake执行；退出1：校验/操作失败，或新版失败但恢复成功；参数语法错误退出2；SIGINT/SIGTERM中断返回130，不自动回退，先status再按pending决定是否rollback。SIGKILL无法捕获，依靠持久pending恢复。报告始终 `hardware_acceptance=false`、`automatic_updater_rollback=NOT_RUN`。

状态JSON/链接采用唯一临时文件、原子替换及父目录同步；旧中断临时文件不会阻塞新写入，不自动删除旧残留。日志磁盘失败时明确返回evidence_error及execution_ok，已验证切换不会因报告写入失败而偷偷回退，cycle停止后续步骤。掉电/存储硬件故障仍须板端独立验证。

已初始化0.4.1的环境不会被0.4.2静默接管：新版模板显式要求KillMode=control-group、包装入口收紧。若存在旧测试环境，须先按旧版停止测试Web/服务，备份并由现场审阅迁移模板；未审阅前新版会拒绝操作，不修改正式服务。

本地测试覆盖模拟systemd三版本循环、首次/切换/恢复失败、中断恢复、哈希/架构/服务配置/路径/进程/遥测拒绝、Unix socket只读请求及运行包独立导入。它们不代表Linux板端systemd已经验收。

正式部署另行按[0.17.9指南](daemon-0.17.9-calibration.zh-CN.md)、[0.18.3指南](daemon-0.18.3-calibration.zh-CN.md)及[功能比较](daemon-0.18.3-comparison.zh-CN.md)执行，0.16.3沿用历史交付指南。**所有板端操作继续等待用户另行通知。**

[0.4.2审查结论、故障矩阵与边界](dryrun-review.zh-CN.md)。即便隔离检查通过，测试进程仍占用CPU/内存；不宣称控制时序开销为零。板端systemd图/权限/停止行为需要后续实测，不能仅凭本地mock保证任意第三方配置下无影响。
