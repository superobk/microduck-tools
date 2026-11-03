# 原生命令升级：中断、分层回退与整盘恢复

配套[主指南](native-upgrade-0163-to-0183.zh-CN.md)。全部为待执行审阅命令，本轮未上板。

## 掉线后先只读检查

```bash
sudo -i
bash
set -euo pipefail
source /var/backups/microduck/native-0183-pr7-r1/session.env
readlink -f /opt/robot/daemon/current
readlink -f /opt/microduck-tools/current
readlink -f /opt/microduck-tools/native/current
systemctl show robotd updaterd microduck-tools -p ActiveState -p MainPID -p ExecStart
systemctl list-jobs --no-pager
systemctl list-timers --all --no-pager
journalctl -u updaterd -u robotd -b -n 150 --no-pager
if systemctl is-active --quiet updaterd; then timeout 10 "$RUN/old-robotctl" update status; fi
```

| 事实 | 下一步 |
|---|---|
| session.env缺失/不完整 | 保留目录、查看初始化阶段，不重建同名会话 |
| current仍0.16.3 | 可能未升级或已自动回退，不能证明钩子未改系统 |
| applying/pending、相关Job/timer未结束 | 等原事务，不杀更新器、不删锁、不重复apply；启动updaterd本身会执行恢复，不是只读查询 |
| 0.18.3且事务/SHA/库/实际进程正确 | 从主指南第5步继续，不再次升级 |
| native/Tools目录或.next链接不完整 | 保持停止，核对旧链接及新目录哈希；半成品留审，不覆盖后当成功 |
| 校正指针一半 | 比对原件，先预览同一Tools install，确认后才重做；先完整文件再原子指针，不拿demo替代 |
| 陌生current、库失败或状态不明 | 停止尝试，进入救援镜像恢复，不force重试 |

apply.started只表示发起过，不证明结果。须交叉核对更新器事务、磁盘、PID/exe/SHA和配置回执。

## 只撤回校正候选：保留官方0.18.3

先确认官方事务和延迟重启完成，再按主指南flock/停止清单核查PID、Job和cgroup。仅替换本次已知覆盖，cmp失败就停止：

```bash
cmp /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf "$RUN/robotd-native.conf"
install -m 0644 "$RUN/robotd-official.conf" /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf.new
mv -T /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf.new /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf
systemctl daemon-reload
systemctl show robotd -p ExecStart
```

应为官方current+no-policy，Tools保持停止，用户校正留作诊断。现场再次确认后才启动robotd并查官方version/health；官方版不会自动应用本工具校正，自定义MCU比例也未因此变正确。

只回退Tools源码：先停Tools，核对`$RUN/tool-links-before.txt`两行（Tools、native）与旧目录哈希，分别以`ln -s 已核实旧绝对路径 current.next`、`mv -Tf current.next current`原子切回自有链接。以前MISSING只撤下确认属于本轮的新链接/覆盖，不猜旧目录；保存本轮覆盖至RUN再恢复原有效配置。源码、robotd启动入口和校正用户层是不同层，不能用一个链接宣称全部回退。未正式apply时撤销维护，也须对照`services-state-before.txt`，现场授权后仅恢复原先运行的已知服务，不自动恢复手柄/policy。

## 完整回到0.16.3：独立救援镜像

官方钩子可能改依赖、账号、服务和/boot。`robotctl update rollback daemon`只选前一发布，重复调用可能切换，不完整撤销系统副作用；应用tar不能整包解到`/`。完整回退使用升级前整盘镜像。

### 升级前备份

独立Linux救援系统启动，原盘所有分区未挂载，不预设mmcblk0：

```bash
sudo -i
bash
set -euo pipefail
umask 077
lsblk -p -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
findmnt /
DISK=/dev/REPLACE_WITH_ORIGINAL_WHOLE_DISK
MEDIA=/mnt/REPLACE_WITH_EXTERNAL_BACKUP_MOUNT
test "$DISK" != /dev/REPLACE_WITH_ORIGINAL_WHOLE_DISK
mountpoint -q "$MEDIA"
lsblk -p -o NAME,TYPE,MOUNTPOINTS,MODEL,SERIAL "$DISK"
findmnt -T "$MEDIA"
IMAGE="$MEDIA/microduck-0163-before-0183.img"
BYTES=$(blockdev --getsize64 "$DISK")
df -B1 "$MEDIA"
```

人工确认原盘型号/序列号、TYPE=disk，所有子分区未挂载、无swap/LVM/crypt/holder占用，备份介质在另一块物理盘且剩余空间≥BYTES。不能判定就停止，不自动批量卸载未知分区。下面才读取整盘：

```bash
dd if="$DISK" of="$IMAGE" bs=4M status=progress conv=fsync oflag=excl
test "$(stat -c %s "$IMAGE")" -eq "$BYTES"
cmp -n "$BYTES" "$IMAGE" "$DISK"
(cd "$MEDIA" && sha256sum "$(basename "$IMAGE")" > "$(basename "$IMAGE").sha256")
lsblk -p -o NAME,SIZE,TYPE,MODEL,SERIAL "$DISK" > "$MEDIA/original-disk.txt"
sync
```

oflag=excl拒绝覆盖已有镜像；长度、cmp和SHA全通过才登记有效。中断/失败副本不使用，不加noerror掩盖，不猜续传偏移，另取新名从头备份。镜像含账号/网络/密钥，私有保管，不放Git。

### 恢复（实际覆盖整盘，需后续现场授权）

仍用独立救援系统，重新识别设备并设置实际DISK/MEDIA/IMAGE；完成相同的未挂载/holder、原盘身份和不同物理介质核对，不能覆盖当前运行盘。核对镜像SHA和目标容量：

```bash
(cd "$MEDIA" && sha256sum -c "$(basename "$IMAGE").sha256")
cat "$MEDIA/original-disk.txt"
test "$(blockdev --getsize64 "$DISK")" -eq "$(stat -c %s "$IMAGE")"
read -r -p "确认覆盖原盘，请输入完整目标路径 $DISK：" CONFIRM
test "$CONFIRM" = "$DISK"
dd if="$IMAGE" of="$DISK" bs=4M status=progress conv=fsync
sync
cmp -n "$(stat -c %s "$IMAGE")" "$IMAGE" "$DISK"
```

恢复中断不启动半写盘；重新救援、核对，从byte0重写并cmp。成功关机、移除救援介质、启动原盘，核对原current、实际robotd SHA、旧配置和原服务状态；原本停止的服务保持停止。系统镜像不回滚舵机EEPROM、MCU固件和机械装配；本升级过程不安排这些写入。

## 固定bootstrap后备入口

仅限正式apply之前确认旧引擎不支持该离线发布，不是签名/目标/安全hold拒绝，无未完成事务；完整镜像、ABI、self-test通过，bootstrap与签名归档内updaterd一致。按主指南同样的flock/停止/PID/Job/cgroup核查后单独审阅：

```bash
cmp "$INBOX/official/updaterd-bootstrap-aarch64" "$RUN/official/bin/updaterd"
"$INBOX/official/updaterd-bootstrap-aarch64" --config "$RUN/updater-maintenance.toml" --self-test
"$INBOX/official/updaterd-bootstrap-aarch64" --config "$RUN/updater-maintenance.toml" install --component daemon --from "$INBOX/official" --force --dry-run
```

force仍验签/哈希，但关闭本次健康门控，**没有自动健康回滚**。现场另行明确批准后，才把最后一条去掉dry-run执行一次并留完整日志；失败后查事实或镜像恢复，不再次force。不是无参数启动，不用check-only代替安装预演。
