# 失败时怎么回退、掉线后从哪里继续

> 当前Tools0.5.0会话：先 `sudo ./status.sh`，中断用 `sudo ./recover.sh --apply`，同版官方回退用 `sudo ./rollback.sh --to official --apply`。完整回原0.16.3走救援环境的 `sudo ./image.sh restore --apply`。说明见[脚本升级路径](manual-upgrade-0183.zh-CN.md)。下文为0.4.3手工会话历史，不得对新会话加载旧session.env。

先记住两条：**不要因为命令没返回就再 apply 一次；不要把旧备份整包解压到 /。** 本页板端操作均 NOT_RUN。

## 掉线后先看事实，不看标记猜结果

重新登录 root Bash。下面只加载原会话变量、查看链接、进程、任务和日志，不启动更新器。启动 updaterd 会执行恢复逻辑，因此不能把“start updaterd”当只读检查。

```bash
set -euo pipefail
source /var/backups/microduck/0183-r2/session.env
cat "$RUN/old-release.txt"
readlink -f /opt/robot/daemon/current
systemctl show robotd updaterd -p MainPID -p ActiveState -p ExecStart
systemctl list-jobs --no-pager
journalctl -u updaterd -u robotd -b -n 100 --no-pager
if systemctl is-active --quiet updaterd; then
  timeout 10 "$RUN/old-robotctl" update status || echo '状态查询失败，不代表事务已经完成'
fi
```

| 看到什么 | 它意味着什么 | 下一步 |
|---|---|---|
| session.env 不存在/不完整 | 初始化中断，尚不能确定维护目录内容 | 查看目录；有备份/日志就保留，不删除、不重建同名会话 |
| current 仍是 0.16.3 | 可能尚未升级，也可能已自动回退；安装钩子可能已经改系统 | 查升级记录和 apply.log；不能仅凭没有 apply.started 判断没升级过 |
| current 是 0.18.3，但更新器仍忙/pending | 链接已切，不等于事务完成 | 不起第二个更新器、不改配置、不停止正在执行的引擎；等待/查看原事务 |
| current 是 0.18.3，事务已完成、官方身份/runtime 检查通过 | 官方升级阶段完成 | 从主指南第 5 步继续，绝不再次 apply |
| 校正文件/用户指针只写了一半，服务仍停着 | 尚不能启动 native | 核对迁移原件后重跑同一 install --apply；它先写完整校正，再原子写指针；完成后重新核验 |
| native/Tools 新目录只复制了一部分 | 文件目录不能作为可用版本 | 不切 current；将未被任何 current 引用的本次新目录改名留存，再重新复制并验 SHA |
| 两个工具 current 只切了一个 | 工具选择未全部完成 | 服务保持停止；核验两个目录后重跑同样的 tool-link，已完成的链接会保持不变 |
| 正式 unit 文件仍旧，旁边有 .new | 原子替换前中断，正式文件未变 | 对照本次模板；确认 .new 完整后再 rename，然后 daemon-reload；陌生内容不覆盖 |
| current 不可解析/陌生版本，或没有可信备份 | 无法证明一致性 | 停止尝试；用串口/独立救援介质恢复已核验的系统镜像 |

apply.started/verified.completed 只是记录，断电可能丢失，也可能留下与现实不符的标记。恢复判断必须以实际 current、PID/exe、更新器事务和配置内容交叉确认。`upgrade-review.py recovery` 只给出提示，不执行恢复；即使标记丢失也不会建议自动重试。

## 只想撤回校正候选：回到同版官方 0.18.3

适用：官方升级已完成且没有进行中的更新，但 native/Web 阶段失败。它不把系统降回 0.16.3，也不证明官方版能正确解释自定义 IMU 比例；继续保持 no-policy 和断动力。

**意义：停服务后，只把本次 robotd 覆盖换回已经审阅的官方入口，保留用户校正作诊断。** 先完成上面的状态检查；服务列表停止后仍须确认没有遗留任务。对照当前覆盖确属本次模板，陌生修改时停止。

```bash
python3 "$REVIEW" deferred --units "$RUN/units.txt"
flock --nonblock --close "$UPDATER_STATE/update.lock" systemctl stop updaterd
while read -r unit; do if systemctl is-active --quiet "$unit"; then systemctl stop "$unit"; fi; done < "$RUN/units.txt"
python3 "$REVIEW" stopped --units "$RUN/units.txt"
cmp /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf "$BUNDLE/deploy/robotd-native.conf"
install -m 0644 "$BUNDLE/deploy/robotd-official.conf" /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf.new
mv -T /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf.new /etc/systemd/system/robotd.service.d/zzzz-manual-0183.conf
systemctl daemon-reload
systemctl show robotd -p ExecStart
systemctl start robotd
/opt/robot/daemon/current/bin/robotctl version --json
/opt/robot/daemon/current/bin/robotctl health --json
```

若正式覆盖已经是官方模板，cmp native 会失败；核对它确实等于官方模板后跳过替换，直接查状态，不删除目录。若官版本身也不能启动，转镜像恢复。不要用旧工具对 0.18.3 用户层执行不兼容写入。

## 要把整机退回升级前 0.16.3：恢复镜像

这是本交付的**完整版本回退方法**：[系统镜像恢复](manual-upgrade-image.zh-CN.md)。从独立救援系统恢复原盘，重新启动后核对旧 current、实际 robotd SHA、配置/插件及原服务状态。

`update rollback daemon` 只表示“前一个发布”，重复执行可能切换；`select` 也只选择发布，不能撤销 apt、/usr/local、账号和 /boot 变化。上游后装的 service 文件可能留在系统中。因此，本指南不把这两条命令包装成“完整回到 0.16.3”，也不再提供整体移动 /etc/systemd/system、/var/lib/robot 的快速恢复循环。

自动健康回滚触发后，也须检查留下的服务/依赖副作用。若只想针对性恢复个别配置，应从 application-before.tar.gz 解到独立审阅目录，逐文件比较，由现场确认范围；不在本指南中自动覆盖系统目录。

## 只有旧更新器在正式 apply 前不兼容时，才考虑 bootstrap

这不是正常步骤，也不是 apply 失败后的通用重试。必须确认：没有已开始/未完成的更新；不是签名、硬件、安全 hold、权限或错误目标造成的拒绝；完整镜像可用；第 2 步 ABI、官方文件 SHA 及新 updater self-test 已通过。

**意义：用固定 0.18.3 更新引擎读取同一离线签名包，避免依赖旧引擎。** 以下依然验签/哈希/硬件，但 `install --force` 关闭本次安装的健康门控，**没有自动健康回滚**。钩子仍可能启动服务，所以不能把它当纯拷贝。

```bash
python3 "$REVIEW" deferred --units "$RUN/units.txt"
flock --nonblock --close "$UPDATER_STATE/update.lock" systemctl stop updaterd
while read -r unit; do if systemctl is-active --quiet "$unit"; then systemctl stop "$unit"; fi; done < "$RUN/units.txt"
python3 "$REVIEW" stopped --units "$RUN/units.txt"
cmp "$BUNDLE/official/updaterd-bootstrap-aarch64" "$RUN/official/bin/updaterd"
"$BUNDLE/official/updaterd-bootstrap-aarch64" --config "$RUN/updater-manual.toml" --self-test
"$BUNDLE/official/updaterd-bootstrap-aarch64" --config "$RUN/updater-manual.toml" install --component daemon --from "$BUNDLE/official" --force --dry-run
```

仅当上述条件经现场审阅且预演通过，才执行下面一次真实安装。不是无参数启动，不使用 --check-only；后者会推进恢复状态。

```bash
touch "$RUN/bootstrap.started"
"$BUNDLE/official/updaterd-bootstrap-aarch64" --config "$RUN/updater-manual.toml" install --component daemon --from "$BUNDLE/official" --force 2>&1 | tee "$RUN/bootstrap.log"
/opt/robot/daemon/current/bin/robotctl version --json
/opt/robot/daemon/current/bin/robotctl health --json
python3 "$REVIEW" runtime --phase after --official /opt/robot/daemon/current --native "$BUNDLE/native" --inputs "$BUNDLE/inputs.json"
```

确认事务、身份和依赖后才能进入主指南第 5 步。失败/断电后回到本页开头查事实；不能继续 force 重试，无法证明一致性时恢复镜像。
