# 0.18.3 校正、插件生效与部署审阅

> 当前部署入口：Tools 0.4.3 的[六步手工直升指南](manual-upgrade-0183.zh-CN.md)和[失败/中断恢复](manual-upgrade-recovery.zh-CN.md)。下面保留 0.4.0 适配记录；其默认 GLIBC2.35 构建示例不是本次 GLIBC2.30 目标产物的部署入口。

本页属于 Microduck Tools 0.4.0。固定官方源码 `b2b0d213a402caeac492dd662ea891faa17f67c0` / API47；配套原生候选 `b2b0d213-calv3-30452a086d1e`。本轮没有执行任何实机命令。

## 简单生效路径

```text
官方0.18.3套件（保留官方mediad等服务）
  → 独立安装匹配robotd/robotctl原生候选
  → 迁移实际旧校正到0.18.3文件
  → 用户层配置指向该文件
  → 现场批准重启robotd
  → 核对实际内存回执，再使用统一Web
```

默认非root工具服务用 `--target auto`。插件开关从 `/etc/microduck-tools/plugins.json` 在工具启动时读取；规则保存可立即更新插件；相机设置保存后另行应用才重启mediad；IMU/关节校正须原生robotd重新打开总线后加载，Web不自动重启它。

官方未打补丁的0.18.3可用于监测与相机，不能仅凭版本号放开IMU/关节校正。新原生回执除PID/exe、固定基线/候选revision和配置路径外，须含 `config_layering="user-over-base/v1"`、匹配的 `user_config_path`，且 `user_layer_rejected=null`。有拒绝记录时先修复用户层，工具保持禁写，不能把基础层回退当作校正成功。

## 文件位置

| 文件 | 用途与生效 |
|---|---|
| `/etc/robot/robotd.toml` | OS基础层，工具保存不改它 |
| `/var/lib/robot/robotd.toml` | 用户层；工具在此设置 `[bus].calibration_path`，保留其他键 |
| `/var/lib/robot/calibration/microduck-0.18.3.toml` | 本指南建议的校正文件，随持久存储保留 |
| `/run/robotd/calibration-effective.json` | 成功验证总线后由实际解码器发布的参数，不能用磁盘文件代替 |
| `/etc/microduck-tools/plugins.json` | 工具宿主配置，启动工具时读取；A/B镜像是否保留此自定义路径需部署时核实 |
| `/var/lib/microduck-tools/` | 插件规则/基线/报告/相机设置 |
| `/opt/microduck-tools/native/releases/<revision>/bin/` | 原生候选；不覆盖官方daemon/current；自定义目录在OS切槽后的存续也须单独确认 |

**只在标准基础路径分层。** 指定其他 `--config /path/to/bench.toml` 时按上游规则独立读取/写入该文件，不叠加 `/var/lib/robot/robotd.toml`。网页显示实际写入路径。两层都是TOML；表逐键合并，数组整体替换。

上游0.18.3自身可能在启动或编辑时把旧基础层修改收养到用户层；这是其已有迁移机制。升级前备份两层及旧校正。工具保存不主动搬迁其他设置，也不自动清除未知项；非法用户层会导致上游降级并在回执/健康中报告。已保存文件不等于该层已被接受。

## 本机重建（不部署）

从工具仓库根目录运行。使用本候选验证过的Rust/rustfmt 1.89.0，交叉构建需cargo-zigbuild、Zig及ARM64 sysroot。可用 `--runtime-root` 选择已有本地工具链。

```bash
git clone --branch daemon-v0.18.3 https://github.com/pollen-robotics/microduck.git ../microduck-native-0.18.3
python3 calibration/common/apply_source_patch.py --repo ../microduck-native-0.18.3 --target daemon-v0.18.3
python3 tools/check-native.py --source ../microduck-native-0.18.3 --target daemon-v0.18.3 --out ../native-build-0183 --stage fetch
python3 tools/check-native.py --source ../microduck-native-0.18.3 --target daemon-v0.18.3 --out ../native-build-0183 --stage fmt
python3 tools/check-native.py --source ../microduck-native-0.18.3 --target daemon-v0.18.3 --out ../native-build-0183 --stage host-test
python3 tools/check-native.py --source ../microduck-native-0.18.3 --target daemon-v0.18.3 --out ../native-build-0183 --stage cross-build
python3 tools/build-release.py --out ../microduck-tools-0.4.0.tar.gz
```

补丁SHA256为 `30452a086d1ed14cafa4ebb22d6adfab746f74e1c679856c8c3f92c71a4b334d`。构建检查器拒绝偏离已评审补丁的编译。只安装本候选robotd和配套robotctl，**保持官方0.18.3 mediad**，不能将整套自定义编译冒充官方签名发布。

## 迁移与安装预览

以下迁移只写新文件。确认同一实际关节映射和IMU后才使用 `--confirm-same-hardware`；非默认比例另需确认真实MCU契约。raw/16.4表示每°/s为16.4计数，raw164为10°/s、0.174532925rad/s；不设置量程寄存器，不重新融合四元数。

```bash
python3 calibration/common/duck_calibrate.py migrate \
  --source /path/to/actual-0.17.9-calibration.toml --target daemon-v0.18.3 \
  --out /path/to/new-0.18.3-calibration.toml --confirm-same-hardware
```

保持旧非默认比例时加 `--confirm-firmware-scale`；已核实并选择16.4时再加 `--gyro-profile custom_raw_div_16_4`。迁移保留实际零位、方向与安装四元数，禁止原地覆盖。示例文件不是现场校正。

板端之后执行安装预览，审阅输出的实际写入路径应为用户层，确认后另加 `--apply`；非默认比例需 `--confirm-firmware-scale`：

```bash
sudo python3 /opt/microduck-tools/current/calibration/common/duck_calibrate.py install \
  --target daemon-v0.18.3 --generated /path/to/new-0.18.3-calibration.toml \
  --config /etc/robot/robotd.toml --dest /var/lib/robot/calibration/microduck-0.18.3.toml
```

此命令仅设置用户层指针并安装校正文件，不重启服务。若基础层遗留旧版本校正指针，需在备份后按上游收养机制或经审阅迁移清理；不要让旧target_tag的文件成为新版基础校正。

## 现场部署顺序：NOT_RUN

1. 等待用户提供本次SSH及就绪确认：稳固支撑、舵机动力断开。记录现有release/current、unit/drop-in、两层配置、校正、工具状态与SHA。
2. 按已安装升级引擎核对固定签名0.18.3官方套件及运行库。不要使用latest脚本代替精确版本；不要自动调用os.apply/os.switch。A/B接口依赖支持的OS，Armbian不能仅靠daemon升级得到它。
3. 在批准窗口先创建持久校正目录（`sudo install -d -m 0755 /var/lib/robot/calibration`），然后现场停止robotd；安装本候选到独立native版本目录，核对二进制SHA，记录旧native/current，再切换本工具自己的指针。使用 `deploy/native-0.18.3.robotd.conf.example`，先审阅所有有效参数，只安装工具自有覆盖。
4. 迁移并预览/安装实际校正。首次静态验证保留 `--no-policy`；它不是断电或卸力开关，原生启动仍保留原有寄存器/扭矩行为。
5. 经现场批准 `daemon-reload`、重启robotd，检查回执：基线、候选revision、PID/exe、基础路径、用户层路径、层接受状态、实际counts/零位/方向/安装四元数。然后检查Web保存/运行值一致、没有待重启。
6. 升级工具到0.4.0，默认非root服务仍用auto。需网页保存时才短期开受控root维护进程，localhost+SSH隧道，指定上述基础配置与持久校正dest；结束后恢复常驻服务。root维护仍不能绕过原生契约/CSRF/比例与安全确认。
7. 新身份/配置摘要会使旧健康基线不再可比，须在人工确认正常工况后重新采集。缺少电流/速度时检查发布开关和测量能力，不能补零。
8. 若从0.16.3迁移，记录旧 `media.camera_rotate`，在新版页面保存同一角度到camera.json，再另行授权应用。保留官方mediad，核对新PID和实际画面；个人相机内参保持。
9. 单独审阅官方默认匿名使用数据选项；在官方控制台选择，或按固定上游文档管理updaterd开关。本工具不发出设置请求、不代用户更改选择。

## 分层回滚：NOT_RUN

- **移除0.18.3校正**：CLI `remove --target daemon-v0.18.3 --config /etc/robot/robotd.toml` 默认预览，核对后加 `--apply`。若基础层有指针，会在用户层写空路径遮蔽；直接删除用户键会重新启用基础层，不等于恢复默认。之后仍须人工重启核验。
- **回官方0.18.3**：停止robotd，只移除工具自有native覆盖，恢复同版本配置备份并检查有效ExecStart，再经批准重载/重启。保留其他覆盖，不重启mediad。
- **回0.17.9**：先移除0.18.3覆盖，按官方机制恢复成套旧版本和原有单层配置、旧target校正。旧版不读取新用户层，不能只回退程序而保留新版文件布局。
- **工具回退**：停止工具，恢复工具current与兼容状态备份；不重启robotd。旧工具不理解0.18.3用户层，不应继续执行新版原生保存。

所有实际升级、安装、重启、回滚、运行库加载、真实比例/方向和板端性能仍待另行通知。[功能差异与固定源码证据](daemon-0.18.3-comparison.zh-CN.md)。
