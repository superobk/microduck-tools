# 0.16.3 → 0.18.3 与 Tools：原生命令指南

**本页为待执行审阅稿，本轮没有连接板端。** 主线：备份原盘 → 查运行库 → 官方更新器安装0.18.3 → 恢复原校正 → 安装Tools → 验证运行值。使用系统命令、官方robotctl/updaterd和Tools自身CLI，不用duck.sh/check.sh/install.sh等升级包装脚本。逐段执行、核对成功再继续，不整页粘贴。

| 对象 | 位置 | 产物情况 |
|---|---|---|
| 官方0.18.3整套 | `/opt/robot/daemon/current` | 13个ARM64程序已预编译，离线签名资产已备；最高GLIBC需求2.30 |
| 可选calv3校正候选 | `/opt/microduck-tools/native/current/bin` | robotd/robotctl已按GLIBC2.30目标编译，最高GLIBC需求2.29；自定义构建，不是官方签名版本 |
| Tools0.6.0当前源码 | `/opt/microduck-tools/current` | Python3.11+，不用板端编译；包括原Web、健康插件、Studio和独立工作台模块 |
| 工作台0.1.1 | 电脑便携包 / 可选板端代理 | 通用pyserial3.5 wheel已备；维护HD1910不等于官方daemon支持Feetech运动 |
| Studio离线材料 | 既有Studio交付runtime-assets、wheels | 包括浏览器模型/资源；wheels仅CPython3.11 Linux ARM64适用 |

已编译不等于本板能运行。必须核对动态加载、ONNX≥1.23/API23及其他运行库；不手换系统libc。先保留真实旧IMU方向、gyro比例及15关节零位/方向，不在升级时猜“平放”四元数或16.4。标准官方IMU、不需自定义校正时可保留官方robotd，仅装只读Tools；没有calv3回执，不能宣称软件校正生效。

## 0．先留完整退路

按[原生命令镜像备份/回退](native-upgrade-recovery.zh-CN.md)，在独立Linux救援系统制作并核验整盘镜像，放另一块介质。原盘所有分区必须未挂载。SHA、长度、cmp及救援启动能力都确认后，回原0.16.3系统。

现场另行授权后，先核对本次目标板、稳固支撑、舵机动力物理断开。`--no-policy`不代替断动力；启动robotd仍会访问硬件，不是纯软件测试。本流程不执行init/HOME/torque enable/运动或恢复手柄。

## 1．Mac上传固定材料

BOARD换成本次实际地址。下面只展示命令，**本轮没有运行SSH/SCP**。官方包/native复用原已核验交付，新Tools使用本轮运行包，旧完整升级包不改。

```bash
BOARD='REPLACE_WITH_USER_AT_BOARD_IP'
test "$BOARD" != REPLACE_WITH_USER_AT_BOARD_IP
OLD=/Users/bou/Workspace/Duckduck/deliverables/microduck-0183-manual-review-20261031/bundle
NEW=/Users/bou/Workspace/Duckduck/deliverables/microduck-servo-workbench-review-20261102
(cd "$NEW" && shasum -a 256 -c microduck-tools-0.6.0-workbench-runtime.tar.gz.sha256)
ssh "$BOARD" 'test ! -e /var/tmp/microduck-pr7-0183 && mkdir -m 700 /var/tmp/microduck-pr7-0183'
scp -rp "$OLD/official" "$OLD/native" "$OLD/inputs.json" "$OLD/robot-units.txt" "$NEW/microduck-tools-0.6.0-workbench-runtime.tar.gz" "$NEW/microduck-tools-0.6.0-workbench-runtime.tar.gz.sha256" "$BOARD":/var/tmp/microduck-pr7-0183/
ssh "$BOARD"
```

目标目录已存在时先审查中断情况，不覆盖旧会话。

## 2．板端建会话，查实际版本与运行库

进入root Bash，逐段运行。RUN保存备份/恢复记录，INBOX是上传材料，TOOL/NATIVE分别是新工具/校正程序目录。掉线后加载RUN/session.env，不重新建同名目录。

```bash
sudo -i
bash
set -euo pipefail
umask 077
INBOX=/var/tmp/microduck-pr7-0183
RUN=/var/backups/microduck/native-0183-pr7-r1
PYTHON=/usr/bin/python3.11
TOOL=/opt/microduck-tools/releases/0.6.0-pr7-workbench-0.1.1
NATIVE=/opt/microduck-tools/native/releases/b2b0d213-calv3-30452a086d1e-glibc230
test ! -e "$RUN"
test -d "$INBOX" && test ! -L "$INBOX"
install -d -m 0700 "$RUN" "$RUN/private"
chown -R root:root "$INBOX"
declare -p INBOX RUN PYTHON TOOL NATIVE > "$RUN/session.env"
hostname
uname -m
getconf GNU_LIBC_VERSION
"$PYTHON" --version
command -v flock readelf ldd zstd openssl
systemctl show robotd updaterd -p User -p ExecStart -p MainPID -p ActiveState
/opt/robot/daemon/current/bin/robotctl version --json
/opt/robot/daemon/current/bin/robotctl health --json
timeout 10 /opt/robot/daemon/current/bin/robotctl update status
PID=$(systemctl show robotd -p MainPID --value)
if [ "$PID" -gt 0 ]; then readlink -f "/proc/$PID/exe" > "$RUN/old-robotd-exe.txt"; sha256sum "/proc/$PID/exe" > "$RUN/old-robotd.sha256"; fi
UPDATER_STATE=$("$PYTHON" -c 'import tomllib;print(tomllib.load(open("/etc/robot/updater.toml","rb"))["state_dir"])')
case "$UPDATER_STATE" in /*) ;; *) exit 1;; esac
test -d "$UPDATER_STATE"
declare -p UPDATER_STATE >> "$RUN/session.env"
```

继续条件：Linux aarch64、确为本板0.16.3、Python3.11可用；updaterd为root（User空也表示默认root），实际配置入口是`/etc/robot/updater.toml`；没有pending/applying事务或不明安全hold/mask。不同入口先审查，不清理覆盖放行。缺Python先查看`apt-cache policy python3.11 python3.11-venv`，确认本OS仓库有包且依赖审阅通过后再另行安装；不改系统python3指针。

核对固定归档，解包到工作目录，不运行钩子：

```bash
(cd "$INBOX" && sha256sum -c microduck-tools-0.6.0-workbench-runtime.tar.gz.sha256)
printf '%s  %s\n' 335df81b88e3c895d234b436dc8dbf1a92b1b5832253e2ddce873c2fb55cccf4 "$INBOX/official/daemon-0.18.3.tar.zst" | sha256sum -c -
install -d "$RUN/official" "$RUN/tools-unpacked"
tar --zstd -xf "$INBOX/official/daemon-0.18.3.tar.zst" -C "$RUN/official"
tar -xzf "$INBOX/microduck-tools-0.6.0-workbench-runtime.tar.gz" -C "$RUN/tools-unpacked"
SOURCE="$RUN/tools-unpacked/microduck-tools-0.6.0"
declare -p SOURCE >> "$RUN/session.env"
"$PYTHON" - "$INBOX" "$RUN/official" "$SOURCE" <<'PY'
import sys,json,hashlib
from pathlib import Path
inbox,official,tool=map(Path,sys.argv[1:]);m=json.loads((inbox/'inputs.json').read_text())
assert m['base_revision']=='b2b0d213a402caeac492dd662ea891faa17f67c0'
assert len(m['official'])==13 and len(m['native'])==2
for group,root in [('official',official),('native',inbox/'native')]:
    for name,want in m[group].items():
        p=root/'bin'/name;assert hashlib.sha256(p.read_bytes()).hexdigest()==want,p
for row in json.loads((tool/'RUNTIME-MANIFEST.json').read_text())['files']:
    p=tool/row['path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256'],p
print('13 official + 2 native + Tools hashes PASS')
PY
for program in "$RUN"/official/bin/* "$INBOX"/native/bin/robotd "$INBOX"/native/bin/robotctl; do
  echo "$program"
  readelf -h "$program" | sed -n '/Machine:/p'
  readelf -l "$program" | sed -n '/interpreter/p'
  ldd -r "$program"
done | tee "$RUN/loader-before.txt"
if grep -Ei 'not found|undefined symbol|cannot open|symbol lookup error' "$RUN/loader-before.txt"; then exit 1; fi
timeout 15 "$PYTHON" - <<'PY'
import ctypes
lib=ctypes.CDLL('/usr/local/lib/libonnxruntime.so')
lib.OrtGetApiBase.restype=ctypes.POINTER(ctypes.c_void_p*2)
base=lib.OrtGetApiBase();assert base
get_api=ctypes.CFUNCTYPE(ctypes.c_void_p,ctypes.c_uint32)(base.contents[0])
get_version=ctypes.CFUNCTYPE(ctypes.c_char_p)(base.contents[1])
version=get_version();assert version and get_api(23)
assert tuple(map(int,version.decode().split(".")[:2])) >= (1,23)
print('ONNX Runtime:',version.decode(),'API23 available')
PY
```

ldd只对以上可信固定SHA文件使用，任何缺库/重定位错误/非零退出都停止。ONNX探针补足ldd看不到的dlopen/API；仍不证明策略推理或硬件通过。缺库时不以“安装钩子可能补齐”为理由跳过。

## 3．迁移真实校正，停服务并备份

从当前有效配置确定旧校正实际路径。没有可信文件或旧比例不明时停止，不能用demo文件。以下保留已有自定义校正；标准官方分支跳过校正迁移和native选择。

```bash
OLD_CAL='REPLACE_WITH_ACTUAL_0163_CALIBRATION_PATH'
test "$OLD_CAL" != REPLACE_WITH_ACTUAL_0163_CALIBRATION_PATH
cp -a "$OLD_CAL" "$RUN/private/original-calibration.toml"
"$PYTHON" "$SOURCE/calibration/common/duck_calibrate.py" check --target daemon-v0.16.3 --calibration "$OLD_CAL"
SCALE_FLAGS=()
"$PYTHON" "$SOURCE/calibration/common/duck_calibrate.py" migrate --source "$OLD_CAL" --target daemon-v0.18.3 --out "$RUN/calibration-0183.toml" --confirm-same-hardware "${SCALE_FLAGS[@]}"
declare -p OLD_CAL SCALE_FLAGS >> "$RUN/session.env"
```

默认比例时SCALE_FLAGS为空；原来使用已核实的非默认MCU比例时，先设为`SCALE_FLAGS=(--confirm-firmware-scale)`再迁移。这是确认旧契约，不是改成16.4。

先人工审阅服务及反向依赖，仅涉及本次机器人服务才执行停止；不明联动先停止操作：

```bash
{ cat "$INBOX/robot-units.txt"; find /opt/robot/daemon/current/systemd -maxdepth 1 -name '*.service' -printf '%f\n'; } | sort -u > "$RUN/units.txt"
cat "$RUN/units.txt"
systemctl list-dependencies --reverse robotd updaterd --no-pager
systemctl cat robotd updaterd > "$RUN/private/units-before.txt"
while read -r unit; do systemctl show "$unit" -p Id -p ActiveState -p UnitFileState; done < "$RUN/units.txt" > "$RUN/services-state-before.txt"
systemctl list-jobs --no-pager
systemctl list-timers --all --no-pager
flock --nonblock --close "$UPDATER_STATE/update.lock" systemctl stop updaterd
while read -r unit; do
  if [ "$(systemctl show "$unit" -p LoadState --value)" != not-found ]; then systemctl stop "$unit"; fi
done < "$RUN/units.txt"
systemctl list-jobs --no-pager
while read -r unit; do systemctl show "$unit" -p ActiveState -p MainPID -p ControlGroup; done < "$RUN/units.txt"
pgrep -a -x robotd || true
```

继续条件：相关服务inactive/failed、MainPID=0、无相关Job/手工robotd/子cgroup进程；ControlGroup非空时用`find /sys/fs/cgroup/实际组路径 -name cgroup.procs -exec cat {} \;`核查。锁忙不删锁、不停止正在升级的引擎；延迟重启timer等待原事务结束，不取消未知timer放行。

```bash
df -h /var/backups /opt /var/lib/robot
install -m 0755 /opt/robot/daemon/current/bin/robotctl "$RUN/old-robotctl"
readlink -f /opt/robot/daemon/current > "$RUN/old-release.txt"
paths=()
for p in etc/robot etc/systemd/system etc/microduck-tools opt/robot opt/microduck-tools var/lib/robot var/lib/microduck-tools; do
  if [ -e "/$p" ] || [ -L "/$p" ]; then paths+=("$p"); fi
done
tar --acls --xattrs --numeric-owner --exclude='var/lib/robot/manual-upgrades' -C / -czpf "$RUN/private/application-before.tar.gz" "${paths[@]}"
sha256sum "$RUN/private/application-before.tar.gz" > "$RUN/application-before.sha256"
for link in /opt/microduck-tools/current /opt/microduck-tools/native/current; do
  if [ -L "$link" ]; then readlink "$link"; elif [ -e "$link" ]; then echo 'current是真实目录，停止'; exit 1; else echo MISSING; fi
done > "$RUN/tool-links-before.txt"
```

备份成功后另存外部介质。其他位置的真实校正/固件也须另备份。应用tar用于逐文件审阅，不整包解压到`/`；完整回退靠镜像。

## 4．用原官方更新器安装0.18.3

复制维护配置，编辑器中**只把根级auto_apply设为"off"**，保留信任/来源/健康门控：

```bash
cp -a /etc/robot/updater.toml "$RUN/updater-maintenance.toml"
vi "$RUN/updater-maintenance.toml"
"$PYTHON" - "$RUN/updater-maintenance.toml" <<'PY'
import sys,tomllib
with open('/etc/robot/updater.toml','rb') as f:old=tomllib.load(f)
with open(sys.argv[1],'rb') as f:new=tomllib.load(f)
old['auto_apply']='off';assert old==new
print('Only root auto_apply changed to off')
PY
"$RUN/official/bin/updaterd" --config "$RUN/updater-maintenance.toml" --self-test
install -d /etc/systemd/system/updaterd.service.d /etc/systemd/system/robotd.service.d
test ! -e /etc/systemd/system/updaterd.service.d/zzzz-pr7-0183.conf
test ! -e /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf
printf '[Service]\nExecStart=\nExecStart=/opt/robot/daemon/current/bin/updaterd --config %s/updater-maintenance.toml --socket /run/updaterd.sock\n' "$RUN" > "$RUN/updater.conf"
cp "$SOURCE/deploy/manual-upgrade/robotd-official.conf" "$RUN/robotd-official.conf"
install -m 0644 "$RUN/updater.conf" /etc/systemd/system/updaterd.service.d/zzzz-pr7-0183.conf.new
mv -T /etc/systemd/system/updaterd.service.d/zzzz-pr7-0183.conf.new /etc/systemd/system/updaterd.service.d/zzzz-pr7-0183.conf
install -m 0644 "$RUN/robotd-official.conf" /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf.new
mv -T /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf.new /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf
systemctl daemon-reload
systemctl show robotd updaterd -p ExecStart -p ConditionResult
systemctl start updaterd
"$RUN/old-robotctl" update apply daemon --version 0.18.3 --from "$INBOX/official" --dry-run
```

有效ExecStart应是官方robotd+`--no-policy`、本次维护updater配置；不移除原Condition/mask等安全限制。启动updaterd会执行恢复逻辑，只有已确认无未完成事务才进入本段。dry-run由官方引擎验签、哈希和兼容性，不运行安装钩子/切版本，但会写暂存记录。

预演成功并经现场确认后，正式apply**只执行一次**：

```bash
touch "$RUN/apply.started"
"$RUN/old-robotctl" update apply daemon --version 0.18.3 --from "$INBOX/official" 2>&1 | tee "$RUN/apply.log"
/opt/robot/daemon/current/bin/robotctl update status | tee "$RUN/update-after.txt"
/opt/robot/daemon/current/bin/robotctl version --json | tee "$RUN/version-official.json"
readlink -f /opt/robot/daemon/current
systemctl list-jobs --no-pager
systemctl list-timers --all --no-pager
systemctl show robotd updaterd -p MainPID -p ExecStart -p ActiveState
```

钩子可能联网安装依赖、改服务/启动设置并启动服务，动力继续物理断开。只有事务完成、非pending/rolled_back、实际0.18.3、延迟重启完成才继续。用第2步相同校验，将official目录替换为`/opt/robot/daemon/current`，重新核对13个SHA、ldd/ONNX及PID的`/proc/PID/exe`和SHA；不只看链接。

失败/断线不再apply，按[恢复表](native-upgrade-recovery.zh-CN.md)查事实。旧引擎在正式apply前明确不兼容时，固定bootstrap是另行审阅的后备，不能把force当通用重试。

## 5．安装校正程序与Tools

官方事务及延迟任务结束后，按第3步同样的flock/停止/PID/Job/cgroup检查停服务。先看Tools自身校正CLI预览：

```bash
"$PYTHON" "$SOURCE/calibration/common/duck_calibrate.py" install --target daemon-v0.18.3 --generated "$RUN/calibration-0183.toml" --config /etc/robot/robotd.toml --dest /var/lib/robot/calibration/microduck-0.18.3.toml
```

预期只写持久用户层`/var/lib/robot/robotd.toml`的校正指针及目标校正文件，不覆盖基础配置。确认后：

```bash
"$PYTHON" "$SOURCE/calibration/common/duck_calibrate.py" install --target daemon-v0.18.3 --generated "$RUN/calibration-0183.toml" --config /etc/robot/robotd.toml --dest /var/lib/robot/calibration/microduck-0.18.3.toml --apply "${SCALE_FLAGS[@]}"
test ! -e "$NATIVE" && test ! -e "$TOOL"
install -d -m 0755 "$NATIVE/bin" "$TOOL"
install -m 0755 "$INBOX/native/bin/robotd" "$INBOX/native/bin/robotctl" "$NATIVE/bin/"
cp -a "$SOURCE/." "$TOOL/"
printf '%s  %s\n' f8fcb954492c095e14cb0f354150c174e82984f5992c3edd1c79a837221a7118 "$NATIVE/bin/robotd" 17f69c44252ab4ba2eb4683ed04426764f02111461396c58824cb0990b767a5b "$NATIVE/bin/robotctl" | sha256sum -c -
```

已有目录表示可能中断，先核对，不覆盖当空目录。用第2步runtime清单核对复制后的Tools。随后只切工具自有链接，不手改官方daemon/current：

```bash
install -d /opt/microduck-tools/native
for link in /opt/microduck-tools/current /opt/microduck-tools/native/current; do
  test ! -e "$link.next" && test ! -L "$link.next"
  if [ -e "$link" ] && [ ! -L "$link" ]; then echo 'current不是符号链接，停止'; exit 1; fi
done
ln -s "$NATIVE" /opt/microduck-tools/native/current.next
mv -Tf /opt/microduck-tools/native/current.next /opt/microduck-tools/native/current
ln -s "$TOOL" /opt/microduck-tools/current.next
mv -Tf /opt/microduck-tools/current.next /opt/microduck-tools/current
cp "$TOOL/deploy/manual-upgrade/robotd-native.conf" "$RUN/robotd-native.conf"
install -m 0644 "$RUN/robotd-native.conf" /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf.new
mv -T /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf.new /etc/systemd/system/robotd.service.d/zzzz-pr7-0183.conf
```

仅需官方监测的分支：跳过校正安装和native选择，仍复制/核验Tools并切Tools自有链接，保留第4步官方覆盖；下方服务的robotctl改成`/opt/robot/daemon/current/bin/robotctl`。

### 最小Web与健康插件

```bash
getent group robot
if ! getent passwd microduck-tools; then useradd --system --user-group --home-dir /var/lib/microduck-tools --shell /usr/sbin/nologin microduck-tools; fi
usermod -a -G robot microduck-tools
install -d -m 0755 /etc/microduck-tools
install -d -m 0700 -o microduck-tools -g microduck-tools /var/lib/microduck-tools
if [ ! -e /etc/microduck-tools/plugins.json ]; then install -m 0644 "$TOOL/deploy/plugins.example.json" /etc/microduck-tools/plugins.json; fi
if [ ! -e /etc/systemd/system/microduck-tools.service ]; then install -m 0644 "$TOOL/deploy/microduck-tools.service" /etc/systemd/system/microduck-tools.service; fi
install -d /etc/systemd/system/microduck-tools.service.d
test ! -e /etc/systemd/system/microduck-tools.service.d/zzzz-pr7-0183.conf
cat > "$RUN/tools.conf" <<'UNIT'
[Service]
ExecStart=
ExecStart=/usr/bin/python3.11 /opt/microduck-tools/current/calibration/integration-0.16.3/web/calibration_web.py --target auto --listen 127.0.0.1 --port 8765 --plugin-config /etc/microduck-tools/plugins.json --servo-health-dir /var/lib/microduck-tools/servo-health --camera-settings /var/lib/microduck-tools/camera.json --robotctl /opt/microduck-tools/native/current/bin/robotctl
UNIT
install -m 0644 "$RUN/tools.conf" /etc/systemd/system/microduck-tools.service.d/zzzz-pr7-0183.conf.new
mv -T /etc/systemd/system/microduck-tools.service.d/zzzz-pr7-0183.conf.new /etc/systemd/system/microduck-tools.service.d/zzzz-pr7-0183.conf
systemctl daemon-reload
systemctl show robotd microduck-tools -p ExecStart -p User -p Group -p SupplementaryGroups
```

已有服务先diff审阅额外参数与权限，不丢弃业务设置。预期非root、robot附加组、回环8765，插件配置`/etc/microduck-tools/plugins.json`，状态`/var/lib/microduck-tools`。常驻Web不授root，机器人校正写入用明确维护CLI。

## 6．检查“真的生效”

硬件访问仍待现场通知。先核对最终ExecStart，再启动：

```bash
systemctl start robotd microduck-tools
systemctl status robotd microduck-tools --no-pager
PID=$(systemctl show robotd -p MainPID --value)
test "$PID" -gt 0
readlink -f "/proc/$PID/exe"
sha256sum "/proc/$PID/exe"
/opt/microduck-tools/native/current/bin/robotctl version --json
/opt/microduck-tools/native/current/bin/robotctl health --json
cat /run/robotd/calibration-effective.json
curl --fail --silent --show-error http://127.0.0.1:8765/api/status > "$RUN/web-after.json"
curl --fail --silent --show-error http://127.0.0.1:8765/api/capabilities
journalctl -u robotd -u microduck-tools -b -n 100 --no-pager
```

校正分支：实际SHA=f8fcb954…，revision=`b2b0d213-calv3-30452a086d1e`，用户层被接受；回执路径正确，Web `runtime_verified=true`、`differences=[]`、`pending_restart=false`、demo/allow-inactive=false；gyro/mount/15组映射与迁移文件一致。健康enabled与配置一致，样本年龄和缺失项如实显示。

标准官方分支用官方robotctl/SHA，不期待calv3回执。无真实舵机/IMU只登记“安装通过，硬件待验收”，不塞fake/DEMO补通过。相机先保留旧角度，应用/重启mediad和新鲜画面另行验收。当前不恢复policy、手柄或自动更新。

Mac另开终端运行`ssh -N -L 8765:127.0.0.1:8765 "$BOARD"`，访问`http://127.0.0.1:8765/`。改插件JSON后明确重启Tools再对照API，不自动重启robotd。

## 7．独立Studio与舵机工作台

最小Web不依赖这两个模块。Studio离线资产/wheels来自`microduck-tools-studio-20261101`交付，按其manifest核对后上传至`/var/tmp/studio-offline/`。以下使用组件自身生命周期CLI（不是daemon升级脚本），普通服务、受限助手、证书和资源登记由该组件管理，不绕过登记手抄units：

```bash
cd "$TOOL"
"$PYTHON" -m studio.manage setup --server-ip REPLACE_WITH_LAN_IP --assets /var/tmp/studio-offline/runtime-assets --wheels /var/tmp/studio-offline/wheels
"$PYTHON" -m studio.manage setup --server-ip REPLACE_WITH_LAN_IP --assets /var/tmp/studio-offline/runtime-assets --wheels /var/tmp/studio-offline/wheels --apply --confirm EXECUTE
"$PYTHON" -m studio.manage status --json
"$PYTHON" -m studio.manage start --apply --confirm EXECUTE
"$PYTHON" -m studio.manage pair --apply --confirm EXECUTE
```

先替换实际IP，第一条仅预览；默认controls/camera关闭，不自动开机自启。已有Studio同版但不同源码时先status审查，不能覆盖版本目录；按[Studio恢复/回退指南](studio-0.6.0-guide.zh-CN.md)另行更新。HTTPS入口、CA指纹、一次性配对均核对；只取ca.crt，不取ca.key。

电脑工作台：解压本轮`microduck-servo-workbench-0.1.1`，以`python3 tools/setup-workbench.py`准备隔离环境，使用`.venv-workbench/bin/python -m servo_workbench serve --demo --open`演练；Windows用对应Scripts/python.exe。真实USB和板端代理安装另按[向导指南](servo-workbench-guide.zh-CN.md)，维护前必须人工交接、robotd停止且串口独占。0.1.1拒绝旧0.1.0代理；不自动更新代理、不恢复中位或动作。

## 来源与核查边界

官方固定revision为`b2b0d213a402caeac492dd662ea891faa17f67c0`。命令依据为保留的签名发布包内官方docs/deploy.md、systemd/钩子和既有源码核查；固定SHA见本文及deploy/manual-upgrade/inputs.json。本轮官方仓库连接器返回404，不声称重新在线核验仓库访问或latest；使用原始离线签名资产，不下载未知替代包。所有板端、动态加载、升级/回退、IMU/舵机与性能结果仍NOT_RUN。
