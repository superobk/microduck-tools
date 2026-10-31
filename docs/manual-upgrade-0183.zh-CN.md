# 0.16.3 直升 0.18.3：按六步手工操作

> 已有更简明的[菜单与任务脚本](script-manager.zh-CN.md)，当前建议从该入口使用。本文保留为0.4.3手工审阅历史；不要与新会话混用。

**这次做三件事：先换官方整套程序，再接回校正程序，最后打开 Tools Web。** 原来的零位、IMU 方向和比例先保持不变。IMU 改平放和 16.4 调整在升级验收后分别进行。

本文属于 Tools **0.4.3**。板端全部 **NOT_RUN**，收到另行执行通知后才使用。不要整篇粘贴，不运行 latest 安装器，不运行初始化/站立/动作。机器人须稳固支撑、舵机动力物理断开；`--no-policy` 不能代替断动力。

| 先理解这三个对象 | 放在哪里 | 为什么分开 |
|---|---|---|
| 官方 0.18.3 套件 | `/opt/robot/daemon/current` | robotd、mediad、updaterd 等一起配套升级 |
| 本工具校正程序（calv3） | `/opt/microduck-tools/native/current` | 让 robotd 读取 IMU/关节软件校正；不是官方签名程序 |
| Tools 0.4.3 | `/opt/microduck-tools/current` | 提供 Web、相机和健康插件；不自行升级或重启 robotd |

本包的两个 calv3 程序已按 GLIBC 2.30 目标重新编译，ELF 版本需求最高为 2.29。旧包要求 2.35 的产物保留作历史。**仍须通过该板的动态加载和 ONNX 检查**，不是承诺任意系统均可运行。

## 操作总览：每一步到底在做什么

| 步骤 | 要解决的问题 | 会改变什么 | 什么时候可以继续 |
|---|---|---|---|
| 1 留退路 | 升级坏了如何回到原系统 | 只在外部介质写镜像 | 镜像完整、摘要和原盘比较通过 |
| 2 查兼容 | 新程序能否在这块板加载 | 上传/解包/检查记录，不切版本 | 15个程序、动态库和ONNX检查通过 |
| 3 准备 | 避免丢校正、后台抢升级 | 停机器人服务、做备份，增加临时维护覆盖 | 原校正可迁移、备份完整、新更新器接受配置 |
| 4 换官方套件 | 把整套daemon升级到0.18.3 | 安装依赖、切官方发布、重启官方服务 | 事务完成、实际身份正确、延迟重启完成 |
| 5 接回工具 | 恢复软件校正和统一Web | 写新用户层/校正，安装独立native/Tools | 文件摘要和有效服务入口正确 |
| 6 看实际效果 | 磁盘设置是否真的被加载 | 启动已批准的服务并读取实际状态 | 回执/Web一致；缺实物的项目继续待验收 |

## 第 1 步：给旧系统留一条确定的退路

**意义：官方升级会安装依赖、改服务甚至启动设置。只有恢复升级前的整盘镜像，才能完整撤销这些变化。** 先按[镜像备份与恢复](manual-upgrade-image.zh-CN.md)在独立救援环境制作并核验原盘镜像。应用 tar 只用于查找和恢复个别配置，不能当整盘回滚。

确认镜像在外部介质、SHA/cmp 已通过、救援介质能启动后，重新启动当前 0.16.3 系统。没有这条退路就不进入正式 apply。

## 第 2 步：把材料放上板，先检查它能不能运行

**在 Mac：这段只核对文件和上传。** BOARD 填本次真实 SSH 地址，不能沿用旧 IP。以下支持普通 SSH 账号的 sudo；不需要修改 SSH 登录策略。

```bash
set -euo pipefail
LOCAL=/Users/bou/Workspace/Duckduck/deliverables/microduck-0183-manual-review-20261031/bundle
BOARD='REPLACE_WITH_USER_AT_BOARD_IP'
test "$BOARD" != REPLACE_WITH_USER_AT_BOARD_IP
(cd "$LOCAL" && shasum -a 256 -c SHA256SUMS)
ssh "$BOARD" 'sudo test ! -e /var/lib/robot/manual-upgrades/manual-0183-r2 && sudo install -d -m 0700 -o "$(id -un)" /var/lib/robot/manual-upgrades/manual-0183-r2'
scp -rp "$LOCAL/." "$BOARD":/var/lib/robot/manual-upgrades/manual-0183-r2/
ssh "$BOARD"
```

**在板端进入 root Bash，再逐段运行。** 新会话目录保存在 /var/backups，不能放进将要备份的 /var/lib/robot；session.env 保存后续命令所用的路径，掉线后无需猜变量。

```bash
sudo -i
bash
set -euo pipefail
umask 077
BUNDLE=/var/lib/robot/manual-upgrades/manual-0183-r2
RUN=/var/backups/microduck/0183-r2
TOOL=/opt/microduck-tools/releases/0.4.3
REVIEW="$BUNDLE/review.py"
NATIVE=/opt/microduck-tools/native/releases/b2b0d213-calv3-30452a086d1e-glibc230
test ! -e "$RUN"
install -d -m 0700 "$RUN" "$RUN/private"
printf 'BUNDLE=%q\nRUN=%q\nTOOL=%q\nREVIEW=%q\nNATIVE=%q\n' "$BUNDLE" "$RUN" "$TOOL" "$REVIEW" "$NATIVE" > "$RUN/session.env"
(cd "$BUNDLE" && sha256sum -c SHA256SUMS)
chown -R root:root "$BUNDLE"
```

**下面解包到工作区、检查动态库，不安装官方程序、不运行安装钩子。** `runtime` 会核对 15 个 ELF 的固定 SHA，再运行 ldd -r 检查库和重定位符号，并在有超时的子进程中检查真实 ONNX 库和 API 23。

```bash
hostname
uname -m
getconf GNU_LIBC_VERSION
python3 --version
command -v flock
getent passwd microduck-tools
getent group robot
timeout 10 /opt/robot/daemon/current/bin/robotctl update status | tee "$RUN/update-before.txt"
robotctl version --json > "$RUN/version-before.json"
robotctl health --json > "$RUN/health-before.json"
systemctl cat robotd updaterd > "$RUN/private/units-before.txt"
UPDATER_STATE=$(python3 -c 'import tomllib;print(tomllib.load(open("/etc/robot/updater.toml","rb"))["state_dir"])')
test -d "$UPDATER_STATE"
case "$UPDATER_STATE" in /*) ;; *) echo "非绝对state_dir，停止"; exit 1;; esac
declare -p UPDATER_STATE >> "$RUN/session.env"
readlink -f /opt/robot/daemon/current > "$RUN/old-release.txt"
PID=$(systemctl show robotd -p MainPID --value)
if [ "$PID" -gt 0 ]; then readlink -f "/proc/$PID/exe" > "$RUN/old-robotd-exe.txt"; sha256sum "/proc/$PID/exe" > "$RUN/old-robotd.sha256"; fi
python3 "$REVIEW" remember-links --out "$RUN/links-before.json"
install -d "$RUN/official"
tar --zstd -xf "$BUNDLE/official/daemon-0.18.3.tar.zst" -C "$RUN/official"
python3 "$REVIEW" runtime --phase before --official "$RUN/official" --native "$BUNDLE/native" --inputs "$BUNDLE/inputs.json" > "$RUN/runtime-before.json"
```

**继续条件：**确为本次板、Linux ARM64、当前套件 0.16.3、Python3.11+；runtime 退出 0；更新器状态可查询且没有 pending/applying/未完成事务；实际 updaterd 的 --config 确为 /etc/robot/updater.toml；已有工具账号/robot组。旧安全 hold、mask、Condition、陌生服务覆盖均已核对，不通过删除来放行。缺库/ONNX/GStreamer 时先停止修环境，不手工替换系统 libc，也不以“钩子可能会补齐”代替此次通过。

## 第 3 步：准备旧校正和备份，然后关闭自动更新

**先把真实旧校正迁移成新文件，尚不安装。** 这样不会等升级完成后才发现旧比例/文件不符合要求。OLD_CAL 填实际生效路径，不填 demo 文件。没有旧校正或使用固定编译的 imu16p4 时，先核对其真实参数，不能凭空填默认值。

```bash
OLD_CAL='REPLACE_WITH_ACTUAL_0163_CALIBRATION_PATH'
test "$OLD_CAL" != REPLACE_WITH_ACTUAL_0163_CALIBRATION_PATH
test -f "$OLD_CAL"
cp -a "$OLD_CAL" "$RUN/private/original-calibration.toml"
python3 "$BUNDLE/tool/calibration/common/duck_calibrate.py" check --target daemon-v0.16.3 --calibration "$OLD_CAL"
```

先读 check 输出的 gyro_profile 和 counts/dps。原来是默认 st_500dps 时，下面 SCALE_FLAGS 保持空数组；原来是非默认比例且已核实 MCU 契约时，将这一行改为 `SCALE_FLAGS=(--confirm-firmware-scale)`。它的意义是明确确认旧比例，不是把比例改成 16.4。未知时停止。

```bash
SCALE_FLAGS=()
python3 "$BUNDLE/tool/calibration/common/duck_calibrate.py" migrate --source "$OLD_CAL" --target daemon-v0.18.3 --out "$RUN/calibration-0183.toml" --confirm-same-hardware "${SCALE_FLAGS[@]}"
declare -p SCALE_FLAGS >> "$RUN/session.env"
```

默认 SCALE_FLAGS 为空。若检查确认原文件为非默认比例，且实际 MCU 契约已经核实，先设 `SCALE_FLAGS=(--confirm-firmware-scale)` 再运行 migrate；未确认就停止。迁移保留原 15 组零位、方向、安装四元数和比例，绝不在这里试填“平放”或 16.4。

**下面只停止已安装、正在运行的机器人服务，然后检查它们确实停干净。** 服务清单包含本次官方服务和旧发布携带的服务，不涉及 SSH/网络；先人工审阅它们的反向依赖，未知联动不要继续。

```bash
{ cat "$BUNDLE/robot-units.txt"; find /opt/robot/daemon/current/systemd -maxdepth 1 -name '*.service' -printf '%f\n'; } | sort -u > "$RUN/units.txt"
systemctl list-dependencies --reverse robotd updaterd --no-pager | tee "$RUN/reverse-dependencies.txt"
```

先审阅上面的依赖列表；确认只影响本次机器人服务后，再执行停止命令。

```bash
python3 "$REVIEW" deferred --units "$RUN/units.txt"
flock --nonblock --close "$UPDATER_STATE/update.lock" systemctl stop updaterd
while read -r unit; do
  if systemctl is-active --quiet "$unit"; then systemctl stop "$unit"; fi
done < "$RUN/units.txt"
python3 "$REVIEW" stopped --units "$RUN/units.txt"
```

flock 用两版源码一致的 update.lock 与升级引擎互斥：只有拿到锁才停止 updaterd；锁忙即拒绝、不打断事务，也不删除锁文件。其他机器人服务随后停止。反向依赖应在执行 stop 前审阅；如果有非机器人服务，停下单独评估。`stopped` 拒绝残留 PID、Job、子 cgroup 任务或 unit 别名；不要删除 Job 或杀未知进程来通过。

**下面保存旧程序、配置和工具，并保留一份固定的旧 robotctl。** 后续明确用它调用旧更新器，避免 PATH 指向别的自定义程序。先确认空间足够；备份报错就不升级。

```bash
df -h /var/backups /opt /var/lib/robot
install -m 0755 /opt/robot/daemon/current/bin/robotctl "$RUN/old-robotctl"
paths=()
for p in etc/robot etc/systemd/system etc/microduck-tools opt/robot opt/microduck-tools var/lib/robot var/lib/microduck-tools; do
  if [ -e "/$p" ] || [ -L "/$p" ]; then paths+=("$p"); fi
done
tar --acls --xattrs --numeric-owner --exclude='var/lib/robot/manual-upgrades' -C / -czpf "$RUN/private/application-before.tar.gz" "${paths[@]}"
sha256sum "$RUN/private/application-before.tar.gz" > "$RUN/application-before.sha256"
```

实际 native/校正若在这些目录以外，也须备份并登记。私有归档另存外部介质。**不能把这个 tar 直接解压到 /，更不能整体搬走 /etc/systemd/system。** 完整版本回退走镜像恢复。

**关闭本次维护中的自动更新：**生成临时配置，只把 auto_apply 改为 off，保留原信任、来源和健康门控；不改 /etc/robot/updater.toml。临时服务覆盖保存到磁盘，因此掉线/重启后仍使用同一维护配置。

```bash
python3 "$REVIEW" manual-config --source /etc/robot/updater.toml --out "$RUN/updater-manual.toml"
"$RUN/official/bin/updaterd" --config "$RUN/updater-manual.toml" --self-test
install -d -m 0755 /etc/systemd/system/updaterd.service.d /etc/systemd/system/robotd.service.d
test ! -e /etc/systemd/system/updaterd.service.d/zzzz-manual-0183.conf
test ! -e /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf
printf '[Service]\nExecStart=\nExecStart=/opt/robot/daemon/current/bin/updaterd --config %s/updater-manual.toml --socket /run/updaterd.sock\n' "$RUN" > "$RUN/updater.conf"
install -m 0644 "$RUN/updater.conf" /etc/systemd/system/updaterd.service.d/zzzz-manual-0183.conf.new
mv -T /etc/systemd/system/updaterd.service.d/zzzz-manual-0183.conf.new /etc/systemd/system/updaterd.service.d/zzzz-manual-0183.conf
install -m 0644 "$BUNDLE/deploy/robotd-official.conf" /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf.new
mv -T /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf.new /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf
systemctl daemon-reload
systemctl show robotd updaterd -p ExecStart -p ConditionResult
```

核对有效 ExecStart：robotd 指官方 current 并带 --no-policy；updaterd 指本次 updater-manual.toml。未知覆盖不能删除。临时文件完成后 rename，避免中断留下半截正式 unit 文件。

## 第 4 步：只升级官方整套 0.18.3

**这一步是正式升级入口。先验签预演，再执行。** 使用固定离线包，不依赖 GitHub latest/发布列表。dry-run 只验签、解包和兼容检查，不运行依赖钩子、不切版本；它也不是“完全无写入”，会留下升级记录/暂存。

```bash
systemctl start updaterd
"$RUN/old-robotctl" update apply daemon --version 0.18.3 --from "$BUNDLE/official" --dry-run
```

退出 0 才继续。若被签名、硬件、安全条件或旧引擎兼容问题拒绝，不加 force 重试；看[失败处理](manual-upgrade-recovery.zh-CN.md)。**下面会运行官方安装钩子，可能联网安装依赖并启动服务。** 首次保持 no-policy，但动力仍必须断开。

```bash
touch "$RUN/apply.started"
"$RUN/old-robotctl" update apply daemon --version 0.18.3 --from "$BUNDLE/official" 2>&1 | tee "$RUN/apply.log"
/opt/robot/daemon/current/bin/robotctl update status > "$RUN/update-after.txt"
/opt/robot/daemon/current/bin/robotctl version --json > "$RUN/version-official.json"
/opt/robot/daemon/current/bin/robotctl health --json > "$RUN/health-official.json"
python3 "$REVIEW" runtime --phase after --official /opt/robot/daemon/current --native "$BUNDLE/native" --inputs "$BUNDLE/inputs.json" > "$RUN/runtime-after.json"
python3 "$REVIEW" deferred --units "$RUN/units.txt"
```

**继续条件：**current 为 0.18.3，事务完成而非 pending/rolled_back，所有实际官方程序与固定 SHA 一致，runtime 再次通过，真实进程版本已核对。更新器会用短暂的 systemd timer 延迟重启自己/btd；deferred 必须通过后才停服务进入下一步。若提示仍待重启，等待它自然完成，再只读重查 deferred 和版本，不取消未知 timer、不重跑 apply。不能只看磁盘链接。无舵机 degraded 不表示真实校正通过。

## 第 5 步：把原校正和 Tools 接回来

**先停服务，安装校正文件，再选择新 native。** 这是单独阶段；失败时可退回同版官方 0.18.3，不需要立刻降级整个系统。

```bash
python3 "$REVIEW" deferred --units "$RUN/units.txt"
flock --nonblock --close "$UPDATER_STATE/update.lock" systemctl stop updaterd
while read -r unit; do if systemctl is-active --quiet "$unit"; then systemctl stop "$unit"; fi; done < "$RUN/units.txt"
python3 "$REVIEW" stopped --units "$RUN/units.txt"
install -d -m 0755 /var/lib/robot/calibration
python3 "$BUNDLE/tool/calibration/common/duck_calibrate.py" install --target daemon-v0.18.3 --generated "$RUN/calibration-0183.toml" --config /etc/robot/robotd.toml --dest /var/lib/robot/calibration/microduck-0.18.3.toml
```

先看预览：它应写 /var/lib/robot/robotd.toml 用户层，不能覆盖整个基础配置。确认后加 --apply；若旧比例已确认非默认，沿用第 3 步的 SCALE_FLAGS。

```bash
python3 "$BUNDLE/tool/calibration/common/duck_calibrate.py" install --target daemon-v0.18.3 --generated "$RUN/calibration-0183.toml" --config /etc/robot/robotd.toml --dest /var/lib/robot/calibration/microduck-0.18.3.toml --apply "${SCALE_FLAGS[@]}"
test ! -e "$NATIVE"
test ! -e "$TOOL"
install -d -m 0755 "$NATIVE/bin" /opt/microduck-tools/native "$TOOL"
install -m 0755 "$BUNDLE/native/bin/robotd" "$BUNDLE/native/bin/robotctl" "$NATIVE/bin/"
cp -a "$BUNDLE/tool/." "$TOOL/"
python3 "$REVIEW" runtime --phase after --official /opt/robot/daemon/current --native "$NATIVE" --inputs "$BUNDLE/inputs.json" > "$RUN/runtime-installed.json"
```

**意义：先验证复制后的真实文件，再切工具自己的链接。** 新版本目录应原先不存在；若已经有文件，先检查中断状态和摘要，不能把旧目录当空目录覆盖。命令从 links-before.json 读取原链接，不需要手工猜 MISSING；真实目录不能强行当链接替换。

```bash
python3 "$REVIEW" tool-link --link /opt/microduck-tools/native/current --target "$NATIVE" --expect-file "$RUN/links-before.json"
python3 "$REVIEW" tool-link --link /opt/microduck-tools/current --target "$TOOL" --expect-file "$RUN/links-before.json"
install -m 0644 "$BUNDLE/deploy/robotd-native.conf" /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf.new
mv -T /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf.new /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf
```

链接切换可重复：已指向相同目录则不再改动；未切换则要求旧链接仍匹配 expect。中断后先核验目录内容，再重跑相同链接命令。此助手拒绝修改官方 daemon/current。

**工具服务：**已有 microduck-tools 服务/账号/插件配置时保持原权限与数据，只核对有效 ExecStart 使用新工具目录，并明确指定配套 robotctl。初次安装或自定义服务不一致时使用[工具服务配置](manual-upgrade-tools.zh-CN.md)，不要为省事常驻 root。插件 JSON 在 /etc/microduck-tools/plugins.json，状态在 /var/lib/microduck-tools；启动参数读取插件开关，保存校正不自动重启 robotd。

## 第 6 步：看实际运行值，不能只看文件

**这里才让新 native 重新加载校正。** 服务配置核对无误后启动。

```bash
systemctl daemon-reload
systemctl show robotd microduck-tools -p ExecStart
systemctl start robotd microduck-tools
/opt/microduck-tools/native/current/bin/robotctl version --json
cat /run/robotd/calibration-effective.json
curl --fail --silent --show-error http://127.0.0.1:8765/api/status > "$RUN/web-after.json"
```

验收应同时满足：运行 revision 为 b2b0d213-calv3-30452a086d1e、实际 PID/exe 正确；用户层被接受；校正路径是 /var/lib/robot/calibration/microduck-0.18.3.toml；Web runtime_verified=true、differences=[]、pending_restart=false，且 demo/allow-inactive 均为 false。15 组零位/方向、安装四元数和比例与迁移文件一致。插件 enabled 与配置一致；数据过期/缺失不能当正常。

没有真实舵机/IMU时可能无运行回执，此时只能登记“程序已安装，硬件验收待执行”。不得用 fake/demo 补成通过。相机旧角度须在相机页保存并另行授权应用，再看新鲜画面；个人内参保留。IMU 改平放、16.4、运动和性能验收继续另开步骤。

核验完成才写 `touch "$RUN/verified.completed"`。该标记只是记录，不代替实际状态。维护配置 auto_apply=off、no-policy 先保留；恢复自动更新、其他服务开机状态及动作须后续审阅，不能让旧 native 覆盖悄悄遮住未来官方版本。

**掉线后的第一步只有这一条：**重新登录 root Bash 后 `source /var/backups/microduck/0183-r2/session.env`，再按[中断恢复表](manual-upgrade-recovery.zh-CN.md)查状态；不要从第 2 步重新建目录或重新 apply。
