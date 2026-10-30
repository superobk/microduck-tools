# 0.17.9 完整校正适配与部署审阅

Microduck Tools 0.3.0 提供配套 **0.17.9-calv3 原生候选**，让 IMU 比例、安装四元数与关节软件校正在采集/命令转换层实际生效。官方 0.17.9 仍可直接用于监测和相机；IMU/关节校正需要本候选。源码基线固定 `96b89d8613f06722c1d64ce75ab0d7124fed9d8f`，IPC API 44 不变。

本页命令用于未来维护窗口审阅。当前仅本地构建和测试；板端安装、重启、回滚、MCU 比例核实、真实相机方向及运动验收均为 `NOT_RUN`。

## 实际使用路径

1. 板端先升级并核对官方 0.17.9 套件，保留其 `mediad` 和其他服务，另安装匹配的 `robotd`/`robotctl` 原生候选。
2. 迁移实际旧校正文件，保留 15 关节顺序、零位、方向和 IMU 安装四元数；安装新的校正文件和 `[bus].calibration_path`。`16.4` 仅在 MCU 原始输出比例已人工核实时选用。
3. 现场批准后重启候选 `robotd`；它在总线/IMU 验证后发布 `/run/robotd/calibration-effective.json`。Web 核对 PID、可执行文件、基线 revision、版本、契约和配置路径，再展示“运行时真实值”。
4. 日常通过统一 Web 查看、预览、下载。默认非 root 服务只读原生校正；维护入口具备文件权限且通过上述核验时才可保存。保存会备份文件并显示待重启，**不会自动重启 robotd**。现场人工重启后，再核对内存回执与保存值一致。

| 配置 | 谁读取 | 生效时机 |
|---|---|---|
| `/etc/microduck-tools/plugins.json` | Web 后端 | 启动/重启工具服务时；不重启 robotd |
| 监测规则、参考基线 | 舵机健康插件 | Web 保存/采集后；校正或 daemon 身份变化时需重新建立可比基线 |
| `camera.json` | 相机插件 | 保存为工具设置；另行启用“应用”后才更新工具自有媒体 drop-in 并重启 mediad |
| 校正 TOML + `bus.calibration_path` | 配套原生 robotd | robotd 启动/重连打开总线时；Web 以实际内存回执确认 |

“插件式”是独立工具宿主与独立配置，不是向官方 daemon 动态装载 Python/Rust 插件。原生配置不是网页 CSS 变换，也不会设置 MCU 量程寄存器。

## 16.4 的含义

选择 `gyro_profile="custom_raw_div_16_4"` 和 `gyro_raw_counts_per_dps=16.4` 时：

```text
gyro(rad/s) = signed_raw / 16.4 × π / 180
raw=164 -> 10 °/s -> 0.1745329252 rad/s
```

这是 MCU 原始计数每 °/s 的软件解码比例，不是每 rad/s，也不是确认 MCU 型号/固件的检测方法。未配置校正文件时保持官方 `0.0175 °/s/LSB`。原来的滤波、融合四元数和重力流程保留；修改比例不重新融合 MCU 四元数。更换量程或硬件后须重新核对比例与校正，不可仅凭旧文件确认正确。

## 本机重建

从工具仓库根目录执行，目标是新的空目录。本候选固定 Rust/rustfmt 1.89.0、Cargo 锁定依赖；交叉构建另需 cargo-zigbuild、Zig 和 ARM64 sysroot。`--runtime-root` 可指向已经准备好的本地交叉运行环境；不是板端目录。

```bash
git clone --branch daemon-v0.17.9 https://github.com/pollen-robotics/microduck.git ../microduck-native-0.17.9
git -C ../microduck-native-0.17.9 rev-parse HEAD
python3 calibration/common/apply_source_patch.py --repo ../microduck-native-0.17.9 --target daemon-v0.17.9
python3 tools/check-native.py --source ../microduck-native-0.17.9 --out ../native-build --stage fetch
python3 tools/check-native.py --source ../microduck-native-0.17.9 --out ../native-build --stage fmt
python3 tools/check-native.py --source ../microduck-native-0.17.9 --out ../native-build --stage host-test
python3 tools/check-native.py --source ../microduck-native-0.17.9 --out ../native-build --stage cross-build
python3 tools/build-release.py --out ../microduck-tools-0.3.0.tar.gz
```

辅助脚本拒绝错误上游基线、六个补丁文件以外的修改及与发布补丁不一致的构建，记录补丁 SHA、构建 revision、完整日志和返回码。候选 revision 为 `96b89d86-calv3-<补丁SHA前12位>`；本发布精确允许 `96b89d86-calv3-f76915b8568d`，其他补丁 revision 需重新评审登记。编译产物在 `native-build/target/aarch64-unknown-linux-gnu/release/`。完整补丁与 `native-build.json` 可独立审阅；发布候选时必须匹配其中 SHA，不能只凭文件名。

只构建/部署 `robotd` 和配套 `robotctl`。**保留官方 mediad 0.17.9 二进制**，使现有相机 CLI/revision 验证继续成立。不要把整个自定义 workspace 当成官方签名发布包，也不要冒充已通过官方 updater 的签名或健康门控。

## 迁移旧文件

下列命令仅生成新文件，不连接设备，不安装或重启。`--confirm-same-hardware` 表示操作者已核对实际关节映射和 IMU；未核对前不要使用。默认保留旧比例；旧比例非默认时仍需明确确认。

```bash
python3 calibration/common/duck_calibrate.py migrate \
  --source /path/to/actual-old-calibration.toml --target daemon-v0.17.9 \
  --out /path/to/new-0.17.9-calibration.toml --confirm-same-hardware
```

若已确认同一 MCU 输出为 raw/16.4，在迁移命令中额外加入：

```text
--gyro-profile custom_raw_div_16_4 --confirm-firmware-scale
```

已有自定义比例且保持不变时加入 `--confirm-firmware-scale` 即可。工具拒绝原地迁移和默认覆盖已有输出。`raw16.4.example.toml` 的零位是合成示例，不作为现场校正。

## 板端部署顺序（未执行）

正式操作前须取得目标 SSH、稳固支撑与舵机动力断开确认，并核对实际服务、路径、权限和板卡运行库。`--no-policy` 不是断电/卸力开关，原生启动仍可能进行原有寄存器/扭矩操作；维护期间保持舵机动力隔离。

1. 记录现有官方 release/current、robotd/mediad 实际路径和 SHA、unit/drop-in、配置、校正、工具状态及旧工具 current；备份完整目录和权限。历史 0.16.3 校正原件另存，不原地 retag。
2. 在独立确认的官方升级流程中精确选择 0.17.9，并核对签名、健康记录、实际 revision 与服务套件。不要用 `main` 或会选择 latest 的 provision/install 命令冒充固定 0.17.9 升级，也不要用本候选替换 updaterd。此工具不实现/绕过官方升级引擎。
3. 验证新套件后，在批准的维护窗口停止 robotd，安装候选到 `/opt/microduck-tools/native/releases/<native-build.json中的build_revision>/bin/`，文件归 root，模式 0755；核对传输前后 SHA。将 `native/current` 指向该不可变目录。此步骤不修改官方 `/opt/robot/daemon/current`。
4. 对实际旧校正运行迁移并检查。先执行以下安装预览，复核后在同一命令额外加入 `--apply`；非默认比例需 `--confirm-firmware-scale`。配置和校正均保留时间戳备份。

```bash
sudo python3 /opt/microduck-tools/current/calibration/common/duck_calibrate.py install \
  --target daemon-v0.17.9 --generated /path/to/new-0.17.9-calibration.toml \
  --config /etc/robot/robotd.toml --dest /etc/robot/robot-calibration-0.17.9.toml
```

5. 审阅有效 unit 与所有已有 drop-in，确认模板完整保留了现场必要参数。没有冲突后才安装 `deploy/native-0.17.9.robotd.conf.example` 为 `/etc/systemd/system/robotd.service.d/90-microduck-tools-native.conf`。它只覆盖 ExecStart，并加 `--no-policy` 用于后续静态验收；不删除他人覆盖文件。随后由现场人员执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart robotd.service
sudo systemctl status robotd.service --no-pager
cat /run/robotd/calibration-effective.json
/opt/microduck-tools/native/current/bin/robotctl health
```

6. 默认工具服务升级到 0.3.0、`--target auto`，保留非 root 沙箱和插件状态。常驻入口没有 `/etc/robot` 写权限是预期行为。短期维护确需通过网页保存时，先停止常驻工具，使用受控 root 前台进程、localhost 与 SSH 隧道；所有原生身份、工况、比例和 CSRF 校验仍保留：

```bash
sudo systemctl stop microduck-tools.service
sudo python3 /opt/microduck-tools/current/calibration/integration-0.16.3/web/calibration_web.py \
  --target daemon-v0.17.9 --listen 127.0.0.1 --port 8765 \
  --config /etc/robot/robotd.toml --dest /etc/robot/robot-calibration-0.17.9.toml \
  --robotctl /opt/microduck-tools/native/current/bin/robotctl \
  --plugin-config /etc/microduck-tools/plugins.json \
  --servo-health-dir /var/lib/microduck-tools/servo-health \
  --camera-settings /var/lib/microduck-tools/camera.json
```

维护结束 Ctrl-C 后恢复常驻工具。root 保存保留已有文件所有者；首次创建位于工具状态目录的文件继承目录所有者。相机应用默认仍关闭，需要它时另行审阅并启用 `--enable-camera-apply`。

7. 核对运行回执 contract、0.17.9、基线、候选 revision、PID/exe、配置路径、实际 `16.4` 与预期校正；核对 Web “已保存/运行时”一致且无待重启。对比 API44 遥测、重力、关节角、相机四方向、舵机健康插件；重新采集与新身份匹配的基线。CPU/RSS、控制环抖动与真实比例/运动验收另行记录，不由本机软件测试推定。

候选 overlay 固定 robotd 0.17.9。以后升级其他 daemon 套件前，须先按下节移除该覆盖、恢复官方一致套件，评审新版本补丁后再启用；不可在保留旧覆盖时自动升级套件。

## 分层回滚（未执行）

- **仅校正回退**：保持当前匹配 native，现场停止 robotd，恢复 0.17.9 的配置/校正备份，或用 CLI `remove --config ...` 预览、再加 `--apply` 删除校正路径，使下一次启动用官方默认比例。不能把 0.16.3 文件直接指给 0.17.9 native。
- **回到官方 0.17.9**：现场停止 robotd；只移走工具自己的 `90-microduck-tools-native.conf`，恢复官方 0.17.9 配置副本，检查有效 ExecStart 回到官方路径，daemon-reload 后由现场人员批准重启。保留工具与候选文件供审阅，不重启 mediad。
- **回到旧版套件**：使用升级前备份和官方恢复机制，还原成套版本与对应配置、校正；先移除本工具 0.17.9 overlay，不能让旧套件混跑新候选。旧版控制/媒体补丁仍是单独候选，不等于官方 release。
- **仅工具回退**：停止工具，恢复工具 current 和兼容状态备份，再启动工具。无需重启 robotd；旧工具可能无法编辑新的原生校正，应保持只读。

未知 daemon/API 只能按已有只读能力降级。后续原生版本必须固定上游、重建补丁、验证运行契约并完成独立测试，不能只扩大版本白名单。
