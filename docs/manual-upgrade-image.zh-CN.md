# 完整退回 0.16.3：先备份原盘，再用原镜像恢复

> 当前Tools0.5.0入口：在独立Linux救援环境的完整bundle中用 `sudo ./image.sh backup --apply` / `sudo ./image.sh restore --apply`，由向导询问原盘和外部镜像。新管理器需要image.sh生成的 `.proof.json`。下文为0.4.3手工镜像历史；不要混用证明格式。当前升级入口见[脚本升级路径](manual-upgrade-0183.zh-CN.md)。

**这是兜底的完整回退方法，不是普通升级时反复执行的步骤。** 必须用独立救援 microSD/USB 启动该板，或将原系统盘接到另一台 Linux 主机。被备份/恢复的原盘不能正在运行或挂载。板端本轮全部 NOT_RUN。

需要：Linux、Python3.11+、GNU dd/coreutils、lsblk；外部备份介质容量大于原盘；稳定供电。先把本包 review.py 复制到救援系统或外部介质，不能为了读取它而挂载原盘。镜像包含密钥/网络/账号，私有保存，不放 GitHub。

## 备份：只读取原盘，写到另一块介质

**先识别设备，绝不假设 mmcblk0 就是原盘。** 下面仅列出盘、挂载点、型号和序列号，按现场情况填写变量。

```bash
sudo -i
bash
set -euo pipefail
umask 077
lsblk -p -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
findmnt /
DISK=/dev/REPLACE_WITH_ORIGINAL_WHOLE_DISK
MEDIA=/mnt/REPLACE_WITH_EXTERNAL_BACKUP_MOUNT
REVIEW=/root/REPLACE_WITH_REVIEW_PY
IMAGE="$MEDIA/microduck-0163-before-0183.img"
test "$DISK" != /dev/REPLACE_WITH_ORIGINAL_WHOLE_DISK
mountpoint -q "$MEDIA"
python3 "$REVIEW" disk-check --mode backup --disk "$DISK" --image "$IMAGE" > "$MEDIA/image-check.json"
cat "$MEDIA/image-check.json"
```

继续前：人工核对型号/序列号确为原盘。disk-check 必须退出 0，报告整盘、无挂载/子设备占用、镜像不在同一盘、外部空间足够。若源分区被自动挂载，先按实际分区安全卸载再检查；不自动批量卸载未知设备。

**这一段才读取整盘。** oflag=excl 拒绝覆盖已有文件；不使用 noerror/sync 掩盖读盘故障。然后对原盘再次比较全部字节，并生成与挂载路径无关的摘要清单。

```bash
dd if="$DISK" of="$IMAGE" bs=4M status=progress conv=fsync oflag=excl
BYTES=$(blockdev --getsize64 "$DISK")
test "$(stat -c %s "$IMAGE")" -eq "$BYTES"
cmp -n "$BYTES" "$IMAGE" "$DISK"
(cd "$MEDIA" && sha256sum "$(basename "$IMAGE")" > "$(basename "$IMAGE").sha256")
lsblk -p -o NAME,SIZE,TYPE,MODEL,SERIAL "$DISK" > "$MEDIA/original-disk.txt"
sync
```

dd、长度检查、cmp 均成功才登记 VERIFIED。任一中断/失败的镜像均不使用；保留为未完成副本，另选新文件名重新从头备份，不跳过坏块、不续传偏移。镜像在外部介质保管好后，正常关机，再从原盘启动 0.16.3 执行主指南。

## 恢复：覆盖原盘，回到升级前的系统

升级失败后先用同一救援方式启动。磁盘编号可能变化，重新识别原盘。**不是在正在运行的原系统里覆盖自己。** 没有可信完整镜像就不要执行。

```bash
sudo -i
bash
set -euo pipefail
lsblk -p -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
findmnt /
DISK=/dev/REPLACE_WITH_CONFIRMED_ORIGINAL_DISK
MEDIA=/mnt/REPLACE_WITH_EXTERNAL_BACKUP_MOUNT
REVIEW=/root/REPLACE_WITH_REVIEW_PY
IMAGE="$MEDIA/microduck-0163-before-0183.img"
test "$DISK" != /dev/REPLACE_WITH_CONFIRMED_ORIGINAL_DISK
(cd "$MEDIA" && sha256sum -c "$(basename "$IMAGE").sha256")
cat "$MEDIA/original-disk.txt"
python3 "$REVIEW" disk-check --mode restore --disk "$DISK" --image "$IMAGE"
```

必须核对摘要、原盘身份、镜像在另一块介质、目标无挂载、目标与原镜像容量一致。换盘、扩容、LVM 特殊布局不是本次自动支持范围；遇到不明结果停下，不绕过检查。

**下面是实际覆盖写入。** 输入完整目标路径是防选错盘机制；本轮不会执行。写完再次逐字节比较。

```bash
read -r -p "确认覆盖原盘 $DISK，请完整输入路径：" CONFIRM
test "$CONFIRM" = "$DISK"
dd if="$IMAGE" of="$DISK" bs=4M status=progress conv=fsync
sync
cmp -n "$(stat -c %s "$IMAGE")" "$IMAGE" "$DISK"
```

恢复中断时不要尝试启动半写原盘。重新从救援系统进入、重新验镜像和目标，再从 byte 0 重写整个镜像并 cmp；不猜续写偏移。成功后正常关机，移除救援启动介质，再启动原盘。

启动后确认：old-release.txt 中的版本、old-robotd.sha256 的摘要、旧配置/插件和原安全覆盖。原程序原本未运行时保持停止，不为“验收”强行启用。系统镜像不回滚舵机 EEPROM、MCU 固件或机械变化；本升级流程本身不安排这些写入。
