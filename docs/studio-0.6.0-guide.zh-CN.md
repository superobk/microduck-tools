# Studio 0.6.0：安装、看状态、配置、停止与回退

## 0.18.3+calv3 → 0.23.0+calv3 / Tools r4 简明执行清单（2026-11-20）

**准备完成，板端未执行。** PR #9已合并到main：de34755a9e44d5299061c13b401bd4e64889ebf0。本次使用r4，不升级OS、不刷IMU固件、不启动舵机工作台、不执行归位/使能/动作。旧历史记录保留；本节是当前升级入口。

| 顺序 | 操作及意义 | 完成状态 |
| --- | --- | --- |
| 1 | Mac校验和上传：确保使用同一批已审阅文件 | 运行内容未改变 |
| 2 | 板端核实、隔离和完整备份：建立本次回退点 | 旧版可恢复，服务停止 |
| 3 | 放入候选及运行库：不覆盖旧版本 | 新文件就位，尚未生效 |
| 4 | 迁移校准、模型和程序路径：避免新旧混跑 | 保留横装、/16.4和15关节值 |
| 5 | 更新Tools/Studio：重建独立3.13venv及登记 | HTTP8790，控制/Camera关闭 |
| 6 | 先验证robotd，再启动网页 | 分别确认真实运行和界面 |
| 失败 | 从本次快照恢复 | 回到升级前0.18.3+calv3 |

**材料是否可用：** 原生包内16个Linux ARM64程序已经编译；r4 Tools为Python源码，Studio带普通GIL CPython3.13 ARM64离线wheels及浏览器资产；v10含10个ONNX文件。无需板端编译或另装Python3.11。离线测试通过，板端运行库加载、服务切换、界面、断电恢复和实机性能仍待验收。calv3是保留自定义校准的派生构建，不是官方签名原版。

### 1．Mac：校验材料，未来通知后再上传

~~~bash
cd /Users/bou/Workspace/Duckduck
NATIVE="$PWD/deliverables/microduck-board3-daemon-0.23.0-offline-20261119"
R4="$PWD/deliverables/microduck-tools-api68-0.23.0-20261120-r4"
FIX="$PWD/deliverables/microduck-0230-r4-execution-20261120"
python3 "$NATIVE/verify_offline.py"
(cd "$R4" && shasum -a 256 -c SHA256SUMS)
(cd "$FIX" && shasum -a 256 -c SHA256SUMS)
~~~

作用：验证原生套件哈希、官方来源签名、ARM64格式、校准保留，以及r4全部文件及安装补充件。必须全部通过。旧原生套件中的“Web未适配”是历史状态，由独立r4补齐，不改旧包字节。以下SSH/SCP均为待执行指令，本轮不执行。

~~~bash
# Mac默认zsh：填写第三板当前地址，核验既有SSH身份。
read -r "BOARD_IP?第三板当前IPv4: "
ssh -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.3.85 "root@$BOARD_IP" \
  'test ! -e /var/lib/microduck-upgrade/0230-r4 && install -d -m 700 /var/lib/microduck-upgrade/0230-r4'
scp -r -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.3.85 \
  "$NATIVE" "$R4" "$FIX" "root@$BOARD_IP:/var/lib/microduck-upgrade/0230-r4/"
ssh -t -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.3.85 "root@$BOARD_IP" bash
~~~

作用：新建独立阶段目录、上传原生、r4、安装补充件三个目录，再进入root Bash。阶段目录已存在时走末尾“中断恢复”；密钥变化先核实，不关闭身份校验。

### 2．板端：核实 → 隔离 → 备份

**现场前提：机身稳固支撑、输入释放、舵机卸力或动力断开。停/启robotd会接管总线；新版启动可能检查并修正舵机寄存器，不能称为只读。** 本轮不执行这些操作。

以下板端命令沿用同一个root Bash；失败即停止，不跳过报错继续。

~~~bash
set -euo pipefail
umask 077
export S=/var/lib/microduck-upgrade/0230-r4
export N="$S/microduck-board3-daemon-0.23.0-offline-20261119"
export T="$S/microduck-tools-api68-0.23.0-20261120-r4"
export P="$S/microduck-0230-r4-execution-20261120"
export B="$S/before"
export NEW=/opt/robot/daemon/releases/0.23.0-calv3-r4
export TR=/opt/microduck-tools/releases/0.6.0-api68-r4
export SP="$S/studio/microduck-studio-0.6.0"
read -r -p '本板网页访问IPv4: ' STUDIO_IP
export STUDIO_IP
test "$(uname -m)" = aarch64
test "$(sha256sum /etc/machine-id | cut -d' ' -f1)" = f96c1ca9913156142defdce965893f69071d1864ce83b1d9123bfdb3516204ee
/usr/bin/python3.13 -c 'import sys,sysconfig; assert sys.version_info[:2]==(3,13) and not sysconfig.get_config_var("Py_GIL_DISABLED"); print(sys.version)'
python3 "$N/verify_offline.py"
(cd "$T" && sha256sum -c SHA256SUMS)
(cd "$P" && sha256sum -c SHA256SUMS)
/opt/microduck-tools/native/current/bin/robotctl version --json
systemctl show robotd -p ExecStart -p MainPID -p DropInPaths
df -h / /var
du -sh /opt/robot /opt/microduck-tools /var/lib/robot
cd /opt/microduck-tools/current
/usr/bin/python3.13 -B -m studio.manage status --json
~~~

作用：确认登记的第三板、ARM64、Python3.13，核对当前实际0.18.3/API47、程序覆盖、传输和旧Studio登记。不是该板、版本不符、status有漂移/未完成交接都停止。为备份、候选及失败现场至少预留上述目录总量两倍再加1GiB；空间不足不继续。

<details>
<summary>展开执行：保存回退路径、增加持久维护条件、停服务和制作快照</summary>

~~~bash
test ! -e /var/lib/microduck-studio-handoff/phone.pending
test ! -e /var/lib/microduck-studio-handoff/return.intent
test ! -e "$B"
mkdir -m 700 "$B"
export U="robotd updaterd configd btd padd netpadd mediad tofd nfcd autod mapd duck-gateway robot-boot-check microduck-tools microduck-studio microduck-studio-helper"
export J="robot-boot-check.timer microduck-studio.slice"
declare -p S N T P B NEW TR SP STUDIO_IP U J > "$S/session.env"
systemctl show $U $J -p Id -p ActiveState -p UnitFileState -p ExecStart -p DropInPaths > "$B/services.before.txt"
printf '%s\n' etc/robot var/lib/robot opt/robot opt/microduck-tools \
  etc/microduck-tools etc/microduck-studio var/lib/microduck-tools \
  var/lib/microduck-studio var/lib/microduck-studio-install var/lib/microduck-studio-handoff > "$B/targets.txt"
for u in $U; do
  printf 'etc/systemd/system/%s.service\netc/systemd/system/%s.service.d\n' "$u" "$u"
done >> "$B/targets.txt"
for u in $J; do printf 'etc/systemd/system/%s\netc/systemd/system/%s.d\n' "$u" "$u"; done >> "$B/targets.txt"
while IFS= read -r p; do
  if test -e "/$p" || test -L "/$p"; then printf '%s\0' "$p"; fi
done < "$B/targets.txt" > "$B/present.nul"
for u in $U; do
  install -d -m 755 "/etc/systemd/system/$u.service.d"
  printf '[Unit]\nConditionPathExists=!%s/hold/%s\n' "$S" "$u" \
    > "/etc/systemd/system/$u.service.d/zzzzzz-0230-upgrade-hold.conf"
done
for u in $J; do
  install -d -m 755 "/etc/systemd/system/$u.d"
  printf '[Unit]\nConditionPathExists=!%s/hold/%s\n' "$S" "$u" \
    > "/etc/systemd/system/$u.d/zzzzzz-0230-upgrade-hold.conf"
done
install -d -m 700 "$S/hold"
for u in $U $J; do touch "$S/hold/$u"; done
systemctl daemon-reload
sync
for u in $U $J; do
  if test "$(systemctl show "$u" -p LoadState --value)" != not-found; then systemctl stop "$u"; fi
done
for u in $U $J; do
  case "$(systemctl show "$u" -p ActiveState --value)" in inactive|failed) ;; *) echo "未停止: $u"; exit 1;; esac
done
systemctl show $U $J -p Id -p Conditions > "$B/maintenance-conditions.txt"
cat "$B/maintenance-conditions.txt"
tar --acls --xattrs --exclude='*/zzzzzz-0230-upgrade-hold.conf' \
  -C / --null -T "$B/present.nul" -cpf "$B/rootfs.tar"
(cd "$B" && sha256sum rootfs.tar > rootfs.tar.sha256 && sha256sum -c rootfs.tar.sha256)
tar -tf "$B/rootfs.tar" > "$B/archive-files.txt"
touch "$B/snapshot.complete"
printf 'BACKED_UP\n' > "$S/phase"
sync
~~~

作用：固定回退清单，保存程序组/链接、全部模型与配置、Tools/Studio源码/venv/journal和各自unit/drop-in；维护条件跨重启保留，不改原enable/disable标志，不操作SSH/网络服务。逐项确认Conditions含对应hold路径，未被其他drop-in清空；不符合就停止。快照包含私有配置，只留私有备份，不上传Git。snapshot.complete出现前不能覆盖程序/配置。

</details>

### 3．放入候选，检查运行库（仍不启动服务）

~~~bash
test -f "$B/snapshot.complete"
test ! -e "$NEW" && test ! -e "$TR" && test ! -e "$S/studio"
test ! -e /opt/robot/runtimes/ort-1.23.2
test ! -e /opt/robot/runtimes/gst-v3
test ! -e /opt/robot/policies/releases/v10-r4
install -d -m 755 "$NEW" "$TR"
cp -a "$N/candidate/release/." "$NEW/"
install -d -m 755 /opt/robot/runtimes/ort-1.23.2 /opt/robot/runtimes/gst-v3 /opt/robot/policies/releases/v10-r4
cp -a "$N/runtimes/onnxruntime-1.23.2-arm64/." /opt/robot/runtimes/ort-1.23.2/
tar -xzf "$N/runtimes/microduck-gst-plugins-v3-aarch64.tar.gz" --strip-components=1 -C /opt/robot/runtimes/gst-v3
cp -a "$N/policies/v10/." /opt/robot/policies/releases/v10-r4/
tar -xzf "$T/microduck-tools-0.6.0-api68-runtime.tar.gz" --strip-components=1 -C "$TR"
mkdir "$S/studio"
tar -xzf "$T/studio-python313/microduck-studio-0.6.0-portable.tar.gz" -C "$S/studio"
for p in "$NEW"/bin/* /opt/robot/runtimes/ort-1.23.2/*.so* /opt/robot/runtimes/gst-v3/*.so; do ldd "$p"; done > "$S/ldd.txt" 2>&1
if grep -E 'not found|not a dynamic executable' "$S/ldd.txt"; then exit 1; fi
/usr/bin/python3.13 - <<'PY'
import ctypes
lib=ctypes.CDLL("/opt/robot/runtimes/ort-1.23.2/libonnxruntime.so.1.23.2")
class API(ctypes.Structure):
    _fields_=[("get_api",ctypes.c_void_p),("get_version",ctypes.CFUNCTYPE(ctypes.c_char_p))]
lib.OrtGetApiBase.restype=ctypes.POINTER(API)
v=lib.OrtGetApiBase().contents.get_version().decode()
assert v=="1.23.2",v
print("PASS ORT C library",v)
PY
"$NEW/bin/robotd" --version
"$NEW/bin/robotctl" --help
~~~

作用：安装完整16程序组、v10和r4到新目录；ORT/GStreamer使用私有版本目录，旧全局运行库不动。ldd检查全部程序/插件依赖，再实际dlopen ORT及检查C ABI版本。缺库立即停止；不盲跑会下载默认ORT1.28.0的联网setup。已有候选目录不覆盖，转中断恢复。version/help不打开舵机；库加载通过仍不是模型已加载。

### 4．迁移校准、模型和原生路径

~~~bash
export CAL="$TR/calibration/common/duck_calibrate.py"
/usr/bin/python3.13 - <<'PY'
from pathlib import Path
import tomllib
old=Path("/var/lib/robot/calibration/microduck-0.18.3.toml")
a=tomllib.loads(old.read_text())
b=tomllib.loads(Path("/etc/robot/robotd.toml").read_text())
u=tomllib.loads(Path("/var/lib/robot/robotd.toml").read_text())
assert u.get("bus",{}).get("calibration_path",b.get("bus",{}).get("calibration_path"))==str(old)
assert a["target_tag"]=="daemon-v0.18.3" and a["imu_mount_wxyz"]==[0,0,0,1]
assert a["gyro_raw_counts_per_dps"]==16.4 and a["gyro_profile"]=="custom_raw_div_16_4"
print("PASS current calibration pointer and confirmed horizontal /16.4")
PY
/usr/bin/python3.13 "$CAL" migrate --source /var/lib/robot/calibration/microduck-0.18.3.toml \
  --out "$S/microduck-0.23.0.toml" --target daemon-v0.23.0 \
  --confirm-same-hardware --confirm-firmware-scale
/usr/bin/python3.13 "$CAL" check --target daemon-v0.23.0 --calibration "$S/microduck-0.23.0.toml"
/usr/bin/python3.13 "$CAL" install --target daemon-v0.23.0 --generated "$S/microduck-0.23.0.toml" --confirm-firmware-scale
/usr/bin/python3.13 "$CAL" install --target daemon-v0.23.0 --generated "$S/microduck-0.23.0.toml" --confirm-firmware-scale --apply
~~~

作用依次为：复制升级时仍在使用的旧校准，仅改target；检查；预览安装；正式写入。保留15个关节值和IMU参数，旧文件/基础配置不改，仅更新用户层的校准指针，不重启。新路径及必须保留的字段：

~~~toml
# /var/lib/robot/calibration/microduck-0.23.0.toml；还须保留完整15关节数组
target_tag = "daemon-v0.23.0"
imu_mount_wxyz = [0.0, 0.0, 0.0, 1.0]
gyro_profile = "custom_raw_div_16_4"
gyro_raw_counts_per_dps = 16.4
# /var/lib/robot/robotd.toml
# [bus]
# calibration_path = "/var/lib/robot/calibration/microduck-0.23.0.toml"
~~~

方向仍为插座朝机尾、芯片面朝上；映射[-X,-Y,+Z]，角速度系数pi/(180×16.4)=0.0010642251536550791。网页不再次除16.4。旧文件如不是这个方向/比例，停止核实，不能用示例片段覆盖完整校准。

~~~bash
"$NEW/bin/robotctl" configure
~~~

作用：用0.23.0原生编辑器保存模型与自主开关到用户层，保留其余配置/绑定/调参，**不接受退出时的重启/重载提示**。本阶段不运行policy load/update，避免活跃模型重载引发归位。

| 编辑项 | 审阅后设置 |
| --- | --- |
| board.version、policy.mode | 保持现值；Zero3不能改beta；walk/roller不要互换 |
| policy.enabled | true，仅加载，不发使能命令 |
| policy.walk | walk用 /opt/robot/policies/current/velstand.onnx；roller用同目录roller.onnx |
| policy.stand | none，上述网络自己站立 |
| policy.sitstand | /opt/robot/policies/current/alpha_sitstand.onnx |
| policy.ground_pick | walk用同目录alpha_ground_pick.onnx；roller用roller_crouch.onnx |
| policy.kick_left、policy.kick_right | 同目录ball_kick_left.onnx、ball_kick_right.onnx，输入完整绝对路径 |
| policy.roulade | /opt/robot/policies/current/roulade.onnx |
| autonomous.enabled | false，保持自主入口关闭 |

这明确选择v10默认模式；若保持旧alpha_walking+alpha_stand方案，则选v10内同名文件，不能把网络视为相同步态。额外policy.skill保留并检查实际路径。10个文件安装不代表同时加载10个网络。

~~~bash
test -L /opt/robot/daemon/current
test -L /opt/microduck-tools/native/current
test -L /opt/robot/policies/current
ln -s "$NEW" /opt/robot/daemon/.current-r4
mv -Tf /opt/robot/daemon/.current-r4 /opt/robot/daemon/current
ln -s "$NEW" /opt/microduck-tools/native/.current-r4
mv -Tf /opt/microduck-tools/native/.current-r4 /opt/microduck-tools/native/current
ln -s /opt/robot/policies/releases/v10-r4 /opt/robot/policies/.current-r4
mv -Tf /opt/robot/policies/.current-r4 /opt/robot/policies/current
cp -a "$NEW/systemd/"*.service "$NEW/systemd/"*.timer /etc/systemd/system/
test -f /etc/systemd/system/robotd.service.d/zzzz-board3-0183.conf
mv /etc/systemd/system/robotd.service.d/zzzz-board3-0183.conf "$B/robotd-0183-dropin.disabled"
printf '[Service]\nRestart=no\nEnvironment=ORT_DYLIB_PATH=/opt/robot/runtimes/ort-1.23.2/libonnxruntime.so.1.23.2\n' \
  > /etc/systemd/system/robotd.service.d/zzzzz-0230-runtime.conf
printf '[Service]\nEnvironment=GST_PLUGIN_PATH=/opt/robot/runtimes/gst-v3\n' \
  > /etc/systemd/system/mediad.service.d/zzzzz-0230-runtime.conf
"$NEW/bin/robotctl" configure --list --json > "$S/config-effective.json" 2> "$S/config-warnings.txt"
cat "$S/config-warnings.txt"
~~~

作用：原子切换三个current链接、安装匹配服务，移走已知旧robotd覆盖，保持其他维护/硬件条件及相机JSON；为新robotd/mediad指定私有库；首轮robotd设置Restart=no，失败不循环重试。current若是实体目录、存在其他旧ExecStart/ExecCondition或旧mediad命令行参数，停下审阅，不删除未知覆盖。config警告含忽略字段或用户层拒绝必须解决，退出0不足以证明配置完整生效。

**升级入口边界：** 这是有快照的手动定制程序切换，不运行会自动启服务的官方postinstall，不伪造签名/updater安装日记。updaterd和robot-boot-check.timer继续隔离，暂不启用原生OTA，也不用robotctl rollback；回退用下方快照。后续启用OTA需另行处理定制版本与原生更新器登记一致性。旧0.18.3冻结duck管理器不能安装0.23.0。

### 5．更新Tools/Studio，沿用Python3.13及LAN HTTP

~~~bash
cd "$TR"
/usr/bin/python3.13 -B -m studio.manage status --json
/usr/bin/python3.13 -B -m studio.manage rollback --apply --confirm EXECUTE
mv /opt/microduck-tools/studio "$B/studio-after-uninstall"
mv /var/lib/microduck-studio-install "$B/studio-journal-after-uninstall"
test -L /opt/microduck-tools/current
ln -s "$TR" /opt/microduck-tools/.current-r4
mv -Tf /opt/microduck-tools/.current-r4 /opt/microduck-tools/current
printf '%s\n' '[Service]' 'ExecStart=' \
  'ExecStart=/usr/bin/python3.13 /opt/microduck-tools/current/calibration/integration-0.16.3/web/calibration_web.py --target auto --listen 127.0.0.1 --port 8765 --plugin-config /etc/microduck-tools/plugins.json --servo-health-dir /var/lib/microduck-tools/servo-health --camera-settings /var/lib/microduck-tools/camera.json --robotctl /opt/microduck-tools/native/current/bin/robotctl' \
  > /etc/systemd/system/microduck-tools.service.d/zzzzz-api68-r4.conf
install -m 644 "$P/studio/manage.py" "$SP/source/microduck-tools-0.6.0/studio/manage.py"
cd "$SP/source/microduck-tools-0.6.0"
/usr/bin/python3.13 -B -m studio.manage doctor --assets "$SP/runtime-assets" --wheels "$SP/wheels" --json
/usr/bin/python3.13 -B -m studio.manage setup --server-ip "$STUDIO_IP" --transport lan-http --port 8790 \
  --assets "$SP/runtime-assets" --wheels "$SP/wheels"
/usr/bin/python3.13 -B -m studio.manage setup --server-ip "$STUDIO_IP" --transport lan-http --port 8790 \
  --assets "$SP/runtime-assets" --wheels "$SP/wheels" --apply --confirm EXECUTE
/usr/bin/python3.13 -B -m studio.manage status --json
systemctl daemon-reload
systemctl show robotd microduck-tools microduck-studio microduck-studio-helper -p ExecStart -p DropInPaths -p Conditions
printf 'STAGED\n' > "$S/phase"
sync
~~~

作用：安装补充件仅修复r4安装器遗漏身份元数据的复制清单，在未安装的临时源码上应用，随新STUDIO-RELEASE登记哈希；不改冻结r4归档、不向正式版本补塞文件。先验证/撤销旧Studio托管文件，保留原source/venv/journal和快照，再从r4重建3.13venv及登记。不能覆盖旧0.6.0目录或手改journal哈希。Tools仅换源码及启动参数，插件、Camera方向JSON、健康规则/报告保留。doctor须ok=true，status须phase=installed且files_verified=true；setup预览后才apply，仍不启动。HTTP监听0.0.0.0:8790，基础Tools只在127.0.0.1:8765；控制/Camera保持关闭，8790冲突即停，不抢其他duck端口。旧Studio额外配置由快照保留，按新配置逐项审阅，不能整份覆盖新schema。

### 6．生效验证：原生通过后再开网页

**仍等现场通知。** 有15舵机和IMU才能验收总线与校准回执；无动力时可检查文件和缺失状态，不能记“硬件通过”。启动会运行新版总线初始化；不要执行robot init、enable或动作。

~~~bash
(
  trap 'touch "$S/hold/robotd"; sync' EXIT
  rm "$S/hold/robotd"
  systemctl start robotd
)
"$NEW/bin/robotctl" version --json
"$NEW/bin/robotctl" health --json
"$NEW/bin/robotctl" policy list --json
journalctl -u robotd -b -n 80 --no-pager
/usr/bin/python3.13 - <<'PY'
import hashlib,json,math,subprocess,tomllib
from pathlib import Path
pid=int(subprocess.check_output(["systemctl","show","robotd","-p","MainPID","--value"]))
r=json.loads(Path("/run/robotd/calibration-effective.json").read_text())
old=tomllib.loads(Path("/var/lib/robot/calibration/microduck-0.18.3.toml").read_text())
cal=tomllib.loads(Path("/var/lib/robot/calibration/microduck-0.23.0.toml").read_text())
assert pid>0 and r["pid"]==pid
assert hashlib.sha256(Path(f"/proc/{pid}/exe").read_bytes()).hexdigest()=="d631d2a64f72a937cf3356d435b5a56c8d985e89355e26d52efa7095cafeaddd"
assert r["target_tag"]=="daemon-v0.23.0" and r["daemon_version"]=="0.23.0"
assert r["source"]=="robotd:DynamixelIo:live" and r["bus_verified"] is True and r["calibration_active"] is True
assert r["user_layer_rejected"] is None and r["calibration_path"]=="/var/lib/robot/calibration/microduck-0.23.0.toml"
assert r["imu_mount_wxyz"]==cal["imu_mount_wxyz"]==old["imu_mount_wxyz"]==[0,0,0,1]
assert r["gyro_raw_counts_per_dps"]==cal["gyro_raw_counts_per_dps"]==old["gyro_raw_counts_per_dps"]==16.4
assert math.isclose(r["gyro_rad_per_raw_count"],math.pi/(180*16.4),rel_tol=1e-12)
for k in ("joint_zero_rad","joint_direction"): assert r[k]==cal[k]==old[k] and len(cal[k])==15
print("PASS actual PID/exe + live calibration + 15 joints + horizontal /16.4")
PY
~~~

作用：仅放行一次启动，随即补回hold（不停止已运行进程），阻止崩溃或重启后自动再启动；核对真实进程0.23.0/API68、56424efe-calv3-offline、exe哈希、各槽加载和同PID现场回执。还须检查health/monitor的IMU就绪、15关节新鲜、无持续总线错误、模型无加载错误、仍held且homed=false。保存成功、active或版本字符串不能代替这些证据。失败先touch对应hold再停robotd，按回退节处理。

~~~bash
rm "$S/hold/microduck-tools" "$S/hold/microduck-studio" "$S/hold/microduck-studio-helper"
systemctl start microduck-tools microduck-studio-helper microduck-studio
curl --fail --silent --show-error http://127.0.0.1:8765/api/status
curl --silent --output /dev/null --write-out '%{http_code}\n' "http://$STUDIO_IP:8790/studio/"
cd /opt/microduck-tools/studio/releases/0.6.0
/usr/bin/python3.13 -B -m studio.manage status --json
/opt/microduck-tools/studio/pair-code.sh
~~~

作用：启动网页和受限助手、检查HTTP与登记，最后生成5分钟一次性访问码，**码的输出不要写日志/截图**。访问 http://本板IP:8790/studio/ 并重新配对：

- 监控→身份、采样与资源：hello为0.23.0/API68，实际程序身份正确，不能沿用旧进程缓存。
- 校正与配置→IMU：保存及运行值均为横装四元数、C=16.4，runtime_verified=true、pending_restart=false，用户层及新校准路径正确。
- Policy：核对实际路径、加载结果；none是关闭的槽，文件存在不等于已加载。不点使能/动作。
- Camera JSON不重置，未来启mediad再检查方向；0.23.0正向PNG不再额外转置。相机/遥控及其余原生服务仍隔离。

普通Tools账号可能因/proc权限保持只读，应与上述root证据区分，不扩大权限“变绿”。本轮不恢复padd/netpadd、不新增自启；其余hold仍保留，不能宣称相机、手柄、OTA或整机重启已验收。

### 失败回退：恢复本次快照，先保持服务停止

步骤3后任何失败/中断，默认恢复，不重跑setup。现场再次核实后，在root Bash执行以下展开块。它只恢复快照固定清单，保留失败现场及维护条件。

<details>
<summary>展开执行：核验快照 → 停止 → 隔离失败文件 → 恢复</summary>

~~~bash
set -euo pipefail
source /var/lib/microduck-upgrade/0230-r4/session.env
test -f "$B/snapshot.complete"
(cd "$B" && sha256sum -c rootfs.tar.sha256)
for u in $U $J; do touch "$S/hold/$u"; done
systemctl daemon-reload
for u in $U $J; do
  if test "$(systemctl show "$u" -p LoadState --value)" != not-found; then systemctl stop "$u"; fi
done
for u in $U $J; do
  case "$(systemctl show "$u" -p ActiveState --value)" in inactive|failed) ;; *) exit 1;; esac
done
F="$S/failed-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$F"
while IFS= read -r p; do
  if test -d "/$p" && [[ "$p" == etc/systemd/system/*.d ]]; then
    mkdir -p "$F/$p"
    while IFS= read -r -d '' f; do
      mv -- "$f" "$F/$p/"
    done < <(find "/$p" -mindepth 1 -maxdepth 1 ! -name zzzzzz-0230-upgrade-hold.conf -print0)
  elif test -e "/$p" || test -L "/$p"; then
    mkdir -p "$F/$(dirname "$p")"
    mv -- "/$p" "$F/$p"
  fi
done < "$B/targets.txt"
tar --acls --xattrs -C / -xpf "$B/rootfs.tar"
systemctl daemon-reload
printf 'ROLLED_BACK_FILES_SERVICES_HELD\n' > "$S/phase"
sync
~~~

作用：回退同时恢复旧程序组/链接、模型、校准指针、Tools/Studio源码/venv/journal、相机和服务覆盖；新失败文件不删。维护条件未移走，恢复中断或重启仍保持隔离。**这不撤销新版启动已写入的舵机EEPROM/RAM**；本清单不提供EEPROM写回，未做寄存器前后采集就不能声称硬件状态全部恢复。恢复期间不得同时编辑或升级其他机器人文件。

</details>

~~~bash
(
  trap 'touch "$S/hold/robotd"; sync' EXIT
  rm "$S/hold/robotd"
  systemctl start robotd
)
/opt/microduck-tools/native/current/bin/robotctl version --json
/opt/microduck-tools/native/current/bin/robotctl health --json
/opt/microduck-tools/native/current/bin/robotctl policy list --json
cat /run/robotd/calibration-effective.json
~~~

作用：明确重启旧robotd并确认0.18.3/API47、同PID回执指向旧校准、横装+/16.4与15关节值和快照一致。旧robotd哈希须为f8fcb954492c095e14cb0f354150c174e82984f5992c3edd1c79a837221a7118。旧Studio先用studio.manage status核验原journal/源码/资产，再按步骤6另启网页。其他服务按services.before.txt逐项恢复；遥控交接另行确认，不自动恢复控制输入。

### 中断恢复入口及验收边界

~~~bash
source /var/lib/microduck-upgrade/0230-r4/session.env
cat "$S/phase"
systemctl show robotd microduck-tools microduck-studio -p ActiveState -p MainPID -p ExecStart -p Conditions
~~~

作用：恢复明确变量，查看停在哪一步及实际服务，不能仅凭文件存在判断成功。

- snapshot.complete之前：程序/配置尚未覆盖，只有维护条件；先补全快照或人工审阅条件，不能运行全量恢复。
- snapshot.complete之后、未验收：默认走上面的回退，不清journal、不重放安装或覆盖同版本目录。恢复未完成可再次从已核对快照恢复，仍保持hold。
- 要重试升级：保留本次快照/失败材料，另建唯一阶段目录并重新核验，不能覆盖0230-r4。
- 恢复后的旧服务、配置及运行证据通过，才算回退完成；恢复文件成功不等于实机功能验收。

本节本地验证覆盖命令语法、归档路径、真实CLI校准迁移和Mac临时目录的文件回退演练（含中断后重入）；GNU tar的ACL/xattr及Linux/systemd语义仍需上板验证。ARM64 ldd/ORT实际加载、原生编辑器交互、systemd启停、掉电恢复、真实Web/模型/相机和运动性能均NOT_RUN；PR合并与离线通过不代表已部署。


---

**第一板LAN HTTP已按原指南应用并启动，正式接口配对和真实反馈已验证；本人浏览器需新码登录。其他板部署须单独核对。** Studio是独立附加界面；0.16.3→0.18.3升级继续使用冻结0.5.0包的 [升级脚本](manual-upgrade-0183.zh-CN.md)，不要把新源码替换进旧管理器会话。

**首次升级并安装Studio推荐[统一安装套件](install-upgrade-simple.zh-CN.md)：一次上传、一个 `sudo ./duck.sh`，按N逐步继续。下文保留独立Studio命令及完整路径说明。**

## 2026-11-20：PR #9 复审后的使用变化（板端未执行）

最新Tools候选在 /Users/bou/Workspace/Duckduck/deliverables/microduck-tools-api68-0.23.0-20261120-r4；原r2仅作历史候选保留。沿用现有安装、启动和一次性访问码命令，Python3.13、LAN HTTP及默认关闭真实控制/Camera的设置不变。旧0.18.3冻结管理器仍不承担0.23.0升级。

- 横装仍为 [0,0,0,1]、raw÷16.4，15关节值保留。0.23.0文件与用户层指针仍采用上节路径，网页不再除一次16.4。
- daemon重启、PID改变或时间戳回退后，身份会暂时变为“未核验”，等待新数据和新证明；旧结果不能恢复控制权限。
- “校正与配置”分别显示已保存、运行参数和是否实际启用；文件存在或数值碰巧一致不能当作已生效。API68无法确认systemd PID时保持只读。
- 相机点击“应用”仍只处理mediad。页面会携带所审阅配置的修订号；若另一页面改过方向，先刷新、核对后再应用，不绕过冲突检查。0.23.0的正向PNG不再转置一次。
- API68模型下载、手柄配对/忘记经固定白名单助手执行，仍需配对登录、CSRF、现场确认、停车及手柄服务隔离。目标updater/configd版本或实际进程不匹配会拒绝，不通过放宽allow-user/allow-group或socket权限解决。
- API47保留原调用路径，旧核心功能不变。新助手不开放系统升级、任意命令、归位、使能或舵机写入。

本地双Python版本各506项、Node11项通过。原指南中的板端步骤尚未执行；等待用户另行通知后再核验实际安装、界面、运行库、服务交接、回退及性能。

## 2026-11-19：0.23.0 / API68 离线适配

Tools/Studio组件版本仍为0.6.0，以源码提交区分本次兼容构建；daemon为0.23.0，协议为API68。旧版校正和API47控制支持保留。板端尚未升级。

| 能力 | 0.18.3 / API47 | 0.23.0 / API68 |
| --- | --- | --- |
| 状态、15关节、IMU、ToF、健康、policy/手柄 | 保留 | 已审阅固定源码结构；保留5Hz共享状态/1Hz健康 |
| 校正保存及生效判断 | 匹配calv3及真实回执 | 匹配calv3、进程SHA、用户层、同PID回执及实际值；官方版不能应用自定义校正 |
| 相机 | 原官方mediad核验 | 固定官方/定制mediad哈希、实际Unix对端PID/UID及API68；PNG由mediad转正，网页不再转一次 |
| 原生自主/激光/自主足球 | 原功能 | 只读显示；启用或状态非法时禁止网页接管，不自动关闭原生模式 |
| 下载policy | 原接口 | 新版UID检查由受限助手只代理policy.fetch；不开放系统升级/任意RPC，不自动加载 |

### 横装和 /16.4 的配置与计算

本板已确认插座朝机尾、芯片面朝上。迁移仅改target，保留安装旋转、比例及15关节校正：

~~~toml
schema = 2
target_tag = "daemon-v0.23.0"
imu_mount_wxyz = [0.0, 0.0, 0.0, 1.0]
imu_module = "bus_imu200"
gyro_profile = "custom_raw_div_16_4"
gyro_raw_counts_per_dps = 16.4
~~~

计算仍为 raw × pi / (180 × 16.4)，再按 [-X,-Y,+Z] 映射。网页不对daemon已换算的rad/s再除一次16.4。完整文件还含15个零位/方向，不能用片段覆盖完整文件。

新文件：/var/lib/robot/calibration/microduck-0.23.0.toml。用户层 /var/lib/robot/robotd.toml 的 [bus].calibration_path 指向它；基础 /etc/robot/robotd.toml 和旧0.18.3校正文件保留。

以下仅在电脑迁移、检查文件，不连接、安装或重启机器人：

~~~sh
python3 calibration/common/duck_calibrate.py migrate \
  --source /绝对路径/原microduck-0.18.3.toml \
  --out /绝对路径/新microduck-0.23.0.toml \
  --target daemon-v0.23.0 --confirm-same-hardware --confirm-firmware-scale
python3 calibration/common/duck_calibrate.py check \
  --target daemon-v0.23.0 --calibration /绝对路径/新microduck-0.23.0.toml
~~~

两个confirm分别表示硬件未变、固件比例已确认。本板此前已由用户确认，不能推及其他IMU。

### 下次上板怎样安装和验证（全部NOT_RUN）

新交付：/Users/bou/Workspace/Duckduck/deliverables/microduck-tools-api68-0.23.0-20261119-r2，归档及源码提交以MANIFEST为准。daemon/v10/运行库仍取原0.23.0离线候选包，其字节未改。

1. 按原第二/第三板指南核实现场和身份，重新备份当时程序、配置、policy、Tools/Studio安装登记，隔离手柄。旧0.18.3冻结升级器只管理原路径，不能拿它安装0.23.0。
2. 基础Tools解到独立新runtime目录，保存原 /opt/microduck-tools/current 链接再明确切换。原unit的 --target auto 保留，127.0.0.1:8765供Studio代理，不扩大基础校正入口。
3. Studio已有安装不能直接setup覆盖。先status核对完整性并保存原登记/source/venv/资产；确认没有未完成手柄交接后，按原rollback步骤撤销旧托管文件，再从新便携包setup预览、明确安装。不能改journal哈希或将新源码塞入冻结目录。
4. 新包使用普通GIL CPython3.13 Linux ARM64离线wheels，不要求板端另装3.11。setup默认LAN HTTP 0.0.0.0:8790，控制和Camera关闭。实际安装/更新中断恢复仍待板端验收。
5. 监控页展开“身份、采样与资源”：identity.hello应为0.23.0/API68及匹配revision；identity.verified核验程序，calibration_verified核验配置/回执一致。native_activity和异常中心显示原生模式占用。
6. “校正与配置”→IMU/陀螺仪：核对目标0.23.0、用户层路径、保存及运行的 [0,0,0,1]、C=16.4、系数0.0010642251536550791。必须runtime_verified=true、pending_restart=false，并核对同PID的 /run/robotd/calibration-effective.json；保存成功不等于生效。普通Tools账号无/proc读取或配置写入权限时保留只读，不放宽系统权限。
7. policy页核对各槽位实际加载结果、skill和按键绑定。v10文件存在/下载成功不等于模型已加载，更不等于动作已完成。Camera明确开启后再检查画面方向。

安装完成后的日常命令仍为：

~~~sh
sudo systemctl start microduck-tools microduck-studio-helper microduck-studio
sudo /opt/microduck-tools/studio/pair-code.sh
~~~

第一条只启动工具，不接管手柄或启动工作台；第二条显示5分钟单次访问码，勿写入登记/截图。访问 http://实际板端IP:8790/studio/。本轮均未在板端执行。

回退先停止Studio/Tools，按各自原安装登记处理；daemon回退还须整体恢复程序组、systemd覆盖、policy链接及0.18.3校正指针。Tools的rollback不等于daemon回滚。原冻结包及备份独立保留。

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
| 进程配置 | 网页监控身份 + `systemctl show microduck-studio -p ExecStart -p MainPID` | 独立版本目录、匹配API47/0.18.3或API68/0.23.0的实际PID/SHA；真实控制通过identity+calibration_verified |
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
