# 第一块板 Tools + Studio + 舵机工作台：逐条命令、原因与预期结果

日期：2026-11-03（Asia/Shanghai）。当前目标：**root@192.168.1.56**，已通过原SSH主机密钥核验为同一Radxa ZERO 3。Debian13/aarch64/glibc2.41，板端继续使用现有 **Python3.13.5**，不增加3.11环境。robotd已是0.18.3+calv3，本页只管理Tools/Studio，不重跑daemon升级。

**当前结论：Tools与Studio已安装运行，用户已完成配对，实际浏览器已显示LIVE实体反馈。ToF单独存在传感器无回应故障，尚未恢复。** 电脑舵机工作台保持停止；本轮无舵机写入、扭矩/Policy/HOME或运动操作。

**日常使用只需三个入口**

1. 打开 [Studio真实总览](http://127.0.0.1:8790/studio/)。当前已配对，不必重复生成码。
2. 若Mac重启或SSH通道断开，在Mac终端运行下面命令，并保持该终端打开：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -L 127.0.0.1:8790:127.0.0.1:8790 root@192.168.1.56
```

用途：把Mac的8790端口安全转发到板端回环8790。终端无输出属正常；Ctrl+C仅结束这条通道。若提示端口已占用，先使用现有入口，不重复开通道。网络传输由SSH加密，HTTP只在两端回环使用，无需导入证书。此入口仅供当前电脑，手机不能直接访问板端8790。

3. 只有网页要求重新配对时，另开Mac终端执行：

```bash
ssh -t root@192.168.1.56 /opt/microduck-tools/studio/pair-code.sh
```

用途：本人生成5分钟、单次使用的配对码，只填入Studio页面并点“配对此显示端”。不发到聊天、Git、截图或日志。显示端会话有效期8小时；服务重启后需重新配对。当前已由本人完成并验证，不再额外生成码。

基础校正/健康工具仍在 [Tools页面](http://127.0.0.1:8765/)。当前通道已连接；断开后可在另一个Mac终端恢复：

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:127.0.0.1:8765 root@192.168.1.56
```

用途：访问原有Tools，与Studio分开运行。本轮只查看，不保存/应用校正。SSH可直接使用已核验的新IP，不需要HostKeyAlias，也不忽略主机密钥告警。

**本次实际验证**

| 项目 | 实际结果及含义 |
|---|---|
| 原生身份 | robotd0.18.3+calv3，revision=b2b0d213-calv3-30452a086d1e，API47；robotdPID1071、mediadPID1220未被本轮重启 |
| 完整性 | daemon/CLI/robotd unit/robotd.toml哈希与备份相同；此前板端重启后校正回执仅pid1007→1071，其余字段一致 |
| Tools | microduck-tools.service active，PID2174，回环8765，15关节数据已连接；约7.2V汇总平滑电压告警保留，原始采样新鲜度未知 |
| Studio/助手 | active，PID10262/2185；安装登记files_verified=true、problems=[]；普通账号运行 |
| 实际界面 | 已连接显示端、LIVE·实体反馈；3D小鸭、真实IMU和15关节；无控制权，请求/应用速度均0；未用模拟数据替换 |
| ToF | **未通过**：8×8无有效数据；原服务报告“the sensor stopped answering; retrying”，详见下节 |
| 权限 | controls_enabled=false、camera_enabled=false；普通Tools跨UID读取/proc/1071/exe被拒绝，校正保存保持禁用，不放宽系统权限 |
| HTTP | 页面200，未登录status401，跨Origin配对请求403；实际浏览器配对成功 |
| 回归 | 本轮58项Studio测试通过，含6项HTTP回环/只读约束测试；pair-code.sh语法通过 |
| 资源范围 | 页面一次快照约12FPS/24绘制；CPU/RSS仅现场快照，未完成正式性能、多客户端或运动验收 |
| 未执行 | 工作台启动、舵机写入/动作、IMU或相机配置应用、ToF重启/总线探测、开机自启 |

板端已安装，不要重复下面历史setup。板端重启后若工具未启动，先核对状态，再仅启动三个工具服务：

```bash
ssh root@192.168.1.56 'systemctl status microduck-tools microduck-studio microduck-studio-helper --no-pager'
```

用途：查看工具状态，inactive时可能返回非0；先读具体原因，不把它当作daemon需要重装。

```bash
ssh root@192.168.1.56 'systemctl start microduck-tools microduck-studio-helper microduck-studio'
```

用途：仅启动已安装工具，不启动工作台或重启robotd。本轮已active，无需再执行；仍未设置开机自启。

**ToF：进程运行，传感器没有正常出帧**

官方tofd0.18.3曾识别VL53L5CX，地址0x29，/dev/i2c-pihat→/dev/i2c-3，启动15Hz测距；日志于2026-11-03 19:49:22（板端显示时间）报告“lost the sensor / the sensor stopped answering”。当前tof.stream状态为“the sensor stopped answering; retrying”。tofdPID753、NRestarts=0；fuser快照仅发现tofd持有i2c-3，未见robotd/mediad持有该设备，不能据此排除所有瞬时总线冲突。

```bash
ssh root@192.168.1.56 'systemctl status tofd --no-pager -l'
```

用途：查看进程与最近错误。**active只证明进程存活，不证明有传感器帧。**

```bash
ssh root@192.168.1.56 'journalctl -u tofd -b -n 30 --no-pager'
```

用途：查看识别、测距及断流证据。健康验收须恢复新鲜ToF帧、有效测距或明确无目标状态，不能仅以active为准。0.18.3没有“robotctl tof”子命令；原生monitor可通过--tof-socket选项读取独立服务。

当前仅定位到传感器停止回应，**尚不能判定由升级导致，也未证实供电、接线、I2C或驱动中的哪一项是根因**。Studio能提示不可用，但现版本未直接显示上述完整服务原文，登记为诊断展示限制。本轮不重启tofd、不运行i2cdetect、不改I2C/供电；后续板端初始化排查另行确认。工具安装完成不等于ToF通过。

**配置与回退材料**

| 路径 | 当前作用 |
|---|---|
| /etc/microduck-studio/config.json | listen=127.0.0.1，port=8790，public_origin=http://127.0.0.1:8790，transport=ssh-loopback-http，demo=false；控制/Camera关闭 |
| /opt/microduck-tools/studio/launchers/studio-ssh-http.py | 新增受控启动补充，复用原create_app，拒绝非回环、模拟和控制/Camera启用 |
| /opt/microduck-tools/studio/pair-code.sh | 本人SSH生成一次性配对码 |
| /etc/systemd/system/microduck-studio.service | 已登记为上述启动器；普通账号、原资源限制保留 |
| /opt/microduck-tools/studio/releases/0.6.0 | 原Studio版本/venv；原137份源码负载保持不变 |
| /var/lib/microduck-studio-install | 安装Journal与哈希，status已通过；不得手改install.json绕过核验 |
| /etc/robot/robotd.toml | 原daemon配置未改；[head_imu]未启用，不代表机身IMU故障 |

电脑优先模式由用户明确选择：HTTPS8444已停止监听，mediad8443保持原样。此前新IP证书SAN与显式CA严格验证通过，但macOS信任命令两次等待授权超时，CLI仍NOT_TRUSTED；后来IAB曾可打开HTTPS配对页。不把这写成系统信任全部通过，也不再要求其作为HTTP入口前置条件。原CA、私钥和证书保留，未绕过浏览器安全警告。

切换前配置/unit保存在板端 /var/backups/microduck/tools-install-board1-20261103/resume-20261103T114916Z/https-before-loopback-http/。未来恢复局域网HTTPS需经同一Journal受控更新并验证，不能仅覆盖配置文件；本轮未恢复。停止工具不代表实体断电。

补充源码/测试在交付目录packages/studio-ssh-http-board1-20261103/，供审阅，不含密钥，不自动应用其他板。transfer和原运行包仍冻结36de9bb；外层历史tar.gz尚未刷新，当前文档/清单以解包目录为准。

本轮真实输出与审计索引：[resume-status.json](/Users/bou/Workspace/Duckduck/logs/tools-board1-resume-20261103T114916Z/resume-status.json)。原完整安装输出：[summary.json](/Users/bou/Workspace/Duckduck/logs/tools-board1-install-20261103T104500Z/summary.json)。下方首次安装与USB内容只保留历史，不是当前待执行清单。

**首次安装时的使用说明（历史；当前状态以上面的新IP恢复记录为准）**

- **基础Tools：[直接打开](http://127.0.0.1:8765/)**。本次已建立Mac→板端SSH回环通道。浏览器实际页面已验证；可查看IMU、摄像头方向草稿、15关节映射、舵机健康和运行状态。本轮不保存/应用机器人校正；普通账号无法读取root的/proc/PID/exe，因此保存按钮保持禁用并显示原因，不放宽系统权限。
- **Studio：[HTTPS入口](https://172.19.8.218:8444/studio/)**。注意是**8444**，原8443属于mediad。Mac Python3.14默认严格X509、CA链和IP校验均已通过，HTTP200。尚未修改Mac/手机系统信任，也未生成或记录配对码；首次浏览器登录仍需本人完成下面两步。
- 公开CA：[board1-studio-ca-20261103.crt](/Users/bou/Workspace/Duckduck/work/board1-studio-ca-20261103.crt)。导入本人Mac/手机的受信任证书设置前，核对SHA256指纹：`82:2F:8C:82:A1:95:52:D8:15:86:C5:09:BE:FE:B8:D4:FE:49:35:3D:57:5A:78:13:34:B0:58:C4:34:1E:23:E3`。不要信任旧29:3D开头的初次证书，也不要导出ca.key。
- 在自己的SSH终端进入 `/opt/microduck-tools/releases/0.6.0-py313-board1-20261103`，执行第48步 `/usr/bin/python3.13 -m studio.manage pair --apply --confirm EXECUTE`，把5分钟一次性码只填入Studio登录页；不发送到聊天、截图、Git或日志。服务端已就绪不等于该显示端已登录。
- 当前Tools/Studio为明确手动启动，**未设开机自启**。若MacSSH通道退出，按第36步重建；若板端重启，按第32、44步启动工具。原robotd不需要重装或重启。

**首次安装时的板端结果（历史）**

| 核对项 | 本次实测 |
|---|---|
| 系统/解释器 | ZERO3、Debian13/aarch64、glibc2.41；Python3.13.5；补齐venv/ensurepip，同3.13.5 Debian安全修订 |
| 文件完整性 | 接收负载175/175；安装源码137/137；Studio登记files_verified=true、problems=[] |
| 基础Tools | active，PID4352，非root账号microduck-tools，回环8765，target0.18.3/API47 |
| 舵机健康 | enabled=true，15关节，stale=false，快照数据年龄约92ms；未采集参考基线，不能称硬件全部健康 |
| Studio/助手 | active，最终PID7135/5242；controls_enabled=false，camera_enabled=false |
| 资源约束 | 独立slice CPU上限200ms/秒，MemoryHigh64MiB、Max96MiB；一次快照约38.3MiB，不是完整性能验收 |
| 原服务 | robotdPID1007、mediadPID1208未变；daemon/CLI/robotd unit/robotd.toml哈希不变，校正回执逐字节相同 |
| 尚未执行 | 舵机写入、扭矩/Policy/HOME/运动、相机应用、IMU设置修改；Studio用户端CA信任/配对登录 |

实际原始输出和失败记录：[板端summary.json](/Users/bou/Workspace/Duckduck/logs/tools-board1-install-20261103T104500Z/summary.json)。它登记每个命令审计step、退出码及完整stdout/stderr。这里同时保留首次ensurepip缺失、8443冲突和严格TLS失败，均已完成本板修正，不把第一次start返回成功当作可用。

**冻结安装器的两项部署补充**

本板沿用36de9bb冻结源码与离线资产，不改原包字节。其setup默认8443，且初次CA缺少keyUsage；原--port只用于demo，不能拿它改变setup端口。本板采用同一安装器的Journal.update_config接口，在独占生命周期锁中更新端口8444以及具备critical basicConstraints/keyUsage、SKI/AKI的CA/叶证书，并保留原私钥、权限和登记哈希。原配置及旧公开证书在 `/var/backups/microduck/tools-install-board1-20261103/`。完整已执行的端口修正见审计step `6d5a3f0a40a24785`，证书修正见 `74db5881f92b4b5c`；不得手改install.json绕过核验，也不要重复setup。后续新板部署须包含这两项补充，不能只复制下面冻结setup原文。


**此前Mac安装材料校验（历史证据，仍保留）**

| 校验项目 | 实际结果 | 证明范围 |
|---|---|---|
| 第1步进入交付目录 | 退出0，pwd为本文交付目录 | 路径真实可用 |
| 第2步上传材料SHA | **176/176 OK，失败0，退出0** | 上传前文件完整，尚未执行SCP |
| 原交付完整SHA（更新文档前） | 225/225 OK，退出0 | 更新前的原包完整性 |
| Mac环境及原生命令 | Python3.14.7；shasum/tar/ssh/scp/openssl/bash均存在 | Mac不要求额外安装Python3.11；仅ssh -V，没有连接主机 |
| 工作台解包及离线setup | 41份负载哈希通过，pyserial3.5安装成功，退出0 | 在隔离目录验证；没有占用第五部分的正式运行目录 |
| 工作台纯模拟命令 | help/status/export均退出0，网页HTTP200，导出硬件验收=false | 未用--real或--ssh；测试服务已正常停止 |
| 基础Web与Studio CLI | 两个--help均退出0 | 可加载，不代表Linux服务已安装 |
| 浏览器/模型及3.13 wheel | 真实资产SHA通过；10个依赖离线解析通过 | Mac使用只读预检跳过Linux/ARM64运行门控；不是板端动态加载通过 |

全部原始stdout/stderr及退出码保存在[本轮原始输出目录](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/)。没有另写一份指南。下方第1、2步已填实际结果；其余步骤仍是待部署指令。安装payload仍固定为36de9bb源码；transfer内随代码冻结的文档是构建时快照，实时执行状态以本篇为准。

本轮隔离演练实际路径为/Users/bou/Workspace/Duckduck/work/tools-mac-validation-20261103T092153Z/desktop/microduck-servo-workbench-0.1.1，临时端口61021。为了不覆盖正式批次记录，后文正式Mac运行目录尚未创建，--open也未触发真实浏览器。离线setup出现一次“pip缓存目录不可写，已禁用缓存”警告，实际安装退出0；不需要用sudo pip处理。

版本分别为：Tools/Studio0.6.0、Servo Workbench0.1.1、robotd0.18.3+calv3。本次新包包含Python3.13安装适配；**不要使用旧cp311归档代替**。Studio的旧3.11限制确实在板端安装门控；本轮已改为支持3.11/3.13普通GIL版本，并按解释器选择独立锁。旧3.11归档/锁不改。普通Web只需标准库；Studio和可选板端代理各建一套3.13 venv隔离依赖，共用已有3.13解释器，不增加3.11。Mac工作台只需Python3.11+，不要求恰好3.11。

先完成一至五。第六是可选板端UART代理；有电脑USB适配器时无需安装它。整个安装不修改官方daemon/current、native/current、robotd启动参数、原用户校正、padd/netpadd维护条件，不启用控制/Camera、不设置开机自启。常驻Web不授root。真实串口连接、EEPROM、Reset Mid及台架动作另行开展，不能因安装通过就执行。

任何一步失败先停在该步，保留错误；不要整页粘贴、不要从头重复、不要使用旧升级菜单再apply0.18.3。



**电脑USB工作台历史记录（当前已停止；本轮不启动）**

打开[正式USB工作台](http://127.0.0.1:8766/?preset=xl330-image)，选择“使用图示 XL330 预设”。本次安装位置是 `/Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/microduck-servo-workbench-0.1.2`，真实状态独立保存在同级 `usb-real-state/`。

1. 先仅连接一只、固定且卸除负载，使用符合XL330规格的电源。刷新端口，核对实际USB适配器，点“绑定并只读连接”。不选蓝牙耳机、Bluetooth-Incoming-Port或debug-console。
2. 选XL330-M288、Microduck、0.18.3，确认Shutdown=52已选。现有按钮“建立15舵机批次”仅建立关节记录，不批量发包；本次只选择目标关节卡片，其他14槽不执行。
3. 等待稳定，核对实物标签、型号与单只连接声明，展开完整预览。确认本只之后，只写这一只并逐项读回；不自动开启扭矩、不发位置目标。
4. 只读回通过就停在这里，导出本只已执行证据，不点击“下一只”、整组复核、台架或中位。单只报告保留其余槽位未配置，不能当整组验收通过。

| 参数 / 地址 | 已实现计划 |
|---|---|
| ID / 7 | 按本只关节选择：10–14 / 20–24 / 30–34 |
| Baud Rate / 8 | 3 = 1,000,000 bps |
| Return Delay / 9 | 0 |
| Drive Mode / 10 | 0 |
| Operating Mode / 11 | 3 |
| Homing Offset / 20 | **按用户最新要求沿用原模板**：ID13/23=-512，其余0；忽略图片的逐颗标定 |
| PWM Slope / 62 | 255 |
| Shutdown / 63 | 52 / 0x34；图示预设明确选中 |

完整预览会同时显示“已一致 · 不写入”和“将写入并读回”。Homing Offset模板不代表机械标定完成。Shutdown=52会取消输入电压故障触发关闭扭矩的位，XL330供电仍须3.7–6.0V；[官方寄存器说明](https://emanual.robotis.com/docs/en/dxl/x/xl330-m288/)。

**本次单只目标已确认：ID24（左踝）。** 操作者已确认单只、固定、卸除负载和电源条件，授权先只读。自动检查在打开串口前被现有审计器hardware门控拒绝；没有改门控、没有改用其他入口绕过。请由操作者在正式界面完成只读连接并选择ID24。参数写入仍NOT_RUN，先提供实际8行预览再确认。

本轮安装记录：[installation-result.json](/Users/bou/Workspace/Duckduck/logs/tools-workbench-usb-install-20261103T095404Z/installation-result.json)。115项软件回归通过，浏览器模拟完整8参数预览通过；正式安装核查时状态为LIVE、尚未绑定、尚无真实批次；后续界面状态以实时读数为准。真实舵机写入和运动目前NOT_RUN。原0.1.1包与Tools0.6.0离线payload保留；0.1.2是独立工作台组件版本，不是daemon版本。

**日后重新启动（停止现有后端后再用）**

```bash
cd /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/microduck-servo-workbench-0.1.2
./workbench.sh serve --real --state-dir /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/usb-real-state --open
```

用途：打开同一真实状态库；不能另起第二个后台或删除锁。预期显示LIVE与本机8766入口。前台运行用Ctrl+C停止后端；网页“停止当前任务”只取消当前操作，不退出后端，也不是物理断电。此前曾后台启动，PID69941；现已停止，不能再按历史PID发信号。历史停止操作为：停止前先用 `ps -p 69941 -o command=` 核对其包含本篇usb-real-state绝对路径，再用 `kill -INT 69941`，不能对重用的PID误发信号。状态库不删除，重新启动会只读恢复且旧预览失效。


**一、只在本地准备完成后，才连接第一板**

**1. 进入本次交付目录（Mac终端）**

```bash
cd /Users/bou/Workspace/Duckduck/deliverables/microduck-tools-board1-install-guide-20261103
```

用途：固定本轮安装材料入口。

为什么：本包是完成0.18.3+calv3后的增量，不重跑机器人升级。

预期结果：目录存在；README、transfer、证据及SHA齐全。

本次结果：**已执行通过，退出0。** 在cd后额外用pwd确认，实际输出如下：

```text
/Users/bou/Workspace/Duckduck/deliverables/microduck-tools-board1-install-guide-20261103
```

[完整stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/step-1-cwd-stdout.log)；[退出码收据](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/step-1-cwd-result.json)。

**2. 核对将上传的全部文件（Mac终端）**

```bash
shasum -a 256 -c transfer-SHA256SUMS
```

用途：验证源码、资源、3.13依赖及工作台包。

为什么：防止误用旧cp311离线包或传送半成品。

预期结果：所有条目为OK；任何失败都停止，不上传。

本次结果：**已执行通过：176条全部OK，失败0，退出0。** 以下为输出摘录，完整176条已登记：

```text
transfer/SHA256SUMS: OK
transfer/board-config/microduck-tools-py313.conf: OK
transfer/wheels313/aiohttp-3.13.2-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl: OK
transfer/workbench/microduck-servo-workbench-0.1.1.tar.gz: OK
transfer/workbench/microduck-servo-workbench-0.1.1.tar.gz.sha256: OK
```

[完整176条stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/step-2-transfer-sha-stdout.log)；[stderr](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/step-2-transfer-sha-stderr.log)；[退出码收据](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/step-2-transfer-sha-result.json)。

下一步：板端可继续关机；等开始部署时再执行第3步。此处没有验证SSH可达性、主机密钥或SCP传输。

**3. 连接已完成升级的第一板（Mac终端）**

```bash
ssh root@172.19.8.218
```

用途：进入此前指南的第一板。

为什么：板端本轮可先关机；待本地准备完成再开机，核对主机指纹，不忽略host-key告警。

预期结果：进入该板；若IP变化先确认身份，不试探旧设备。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**4. 确认使用板端原有Python3.13（板端root终端）**

```bash
/usr/bin/python3.13 --version
```

用途：确认固定解释器。

为什么：无需另装3.11，也不修改/usr/bin/python3指针。

预期结果：Python3.13.x；此前报告为3.13.5。不存在时停止核对路径。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**5. 检查venv基础模块（板端root终端）**

```bash
/usr/bin/python3.13 -c 'import venv,ensurepip; print("PASS: Python3.13 venv/ensurepip available")'
```

用途：确认Studio和可选代理可以创建隔离依赖环境。

为什么：venv是同一3.13解释器下的包隔离，并非另一套3.11。

预期结果：打印PASS；缺模块时先审阅 apt-cache policy python3.13-venv；仅在确认发行版包匹配后安装该包，再重做检查，不安装python3.11。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

补充实测：首次缺ensurepip；已通过apt仅安装3个venv依赖及4个同3.13.5安全修订，修复后检查通过，robotdPID未变。

**6. 核对现有calv3真实版本（板端root终端）**

```bash
/opt/microduck-tools/native/current/bin/robotctl version --json
```

用途：确认现有运行基线。

为什么：本流程只安装Tools，不能顺便替换已经验收的robotd。

预期结果：robotd0.18.3，完整revision为b2b0d213-calv3-30452a086d1e。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**7. 确认维护策略状态（板端root终端）**

```bash
/opt/microduck-tools/native/current/bin/robotctl policy list --json
```

用途：核对策略保持关闭。

为什么：安装不需要HOME、使能、扭矩或动作；no-policy不能代替实际动力断开。

预期结果：enabled=false、homed=false；不为页面亮绿灯而enable。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**8. 检查本次目录和服务没有既有安装（板端root终端）**

```bash
/usr/bin/python3.13 -c 'from pathlib import Path; import pwd; names=["/var/lib/microduck-tools-install/board1-20261103","/var/backups/microduck/tools-install-board1-20261103","/opt/microduck-tools/releases/0.6.0-py313-board1-20261103","/opt/microduck-tools/current","/etc/systemd/system/microduck-tools.service","/etc/microduck-tools/plugins.json"]; occupied=[n for n in names if Path(n).exists() or Path(n).is_symlink()]; assert not occupied,occupied; assert not any(p.pw_name=="microduck-tools" for p in pwd.getpwall()),"existing microduck-tools account: inspect first"; print("PASS: new install paths and account are free")'
```

用途：拒绝覆盖未知的旧Tools或中断状态。

为什么：本篇只适用于该板首次安装这些组件；已有文件时先审查，不从头重复。

预期结果：PASS；发现旧安装时停止，保留材料，按文末恢复表处理。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。


**二、建立本次安装记录并上传**

**9. 创建独立输入和证据目录（板端root终端）**

```bash
install -d -m 700 /var/lib/microduck-tools-install/board1-20261103 /var/backups/microduck/tools-install-board1-20261103
```

用途：分别保存安装输入和本次基线。

为什么：不清理此前升级备份；它仍包含在用的updater-maintenance.toml。

预期结果：两个新目录仅root可访问。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**10. 记录安装前的服务状态（板端root终端）**

```bash
systemctl show robotd updaterd padd netpadd -p Id -p MainPID -p ActiveState -p ExecStart > /var/backups/microduck/tools-install-board1-20261103/services-before.txt
```

用途：记录现有daemon和手柄维护状态。

为什么：之后只比较，不根据旧PID硬编码判断成功。

预期结果：文件已保存；原robotd启动项继续含--no-policy，手柄维护条件保留。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**11. 保存当前校正回执（板端root终端）**

```bash
cp -p /run/robotd/calibration-effective.json /var/backups/microduck/tools-install-board1-20261103/calibration-effective-before.json
```

用途：保留本次安装前的实际内存参数证据。

为什么：当前calv3已运行但calibration_active=false，不把默认参数当物理校正已完成。

预期结果：原回执保存成功；本流程不创建bus.calibration_path。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**12. 记录既有native链接（板端root终端）**

```bash
readlink -f /opt/microduck-tools/native/current > /var/backups/microduck/tools-install-board1-20261103/native-current-before.txt
```

用途：固定当前calv3目录。

为什么：后续只新建Tools自己的current，不能改native/current或官方daemon/current。

预期结果：与原指南的b2b0d213-calv3…-glibc230目录一致。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**13. 上传整套固定材料（Mac另开终端）**

```bash
scp -rp /Users/bou/Workspace/Duckduck/deliverables/microduck-tools-board1-install-guide-20261103/transfer root@172.19.8.218:/var/lib/microduck-tools-install/board1-20261103/
```

用途：一次上传Tools源码、Studio资产、cp313 wheels及工作台包。

为什么：使用本地已核验固定材料，板端无需在线pip下载。

预期结果：上传成功；不发送原板备份、CA私钥或任何凭据。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**14. 进入上传目录（板端root终端）**

```bash
cd /var/lib/microduck-tools-install/board1-20261103/transfer
```

用途：在正确位置做相对路径校验。

为什么：sha清单中的路径均相对本transfer目录。

预期结果：目录含tools、runtime-assets、wheels313、workbench及SHA256SUMS。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**15. 核对板端收到的所有字节（板端root终端）**

```bash
sha256sum -c SHA256SUMS
```

用途：检查传输后文件完整性。

为什么：Mac通过不代表板端收到的文件也完整。

预期结果：全部OK后才复制安装；任一失败保留目录，不忽略校验。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。


**三、安装基础Tools Web和只读健康插件**

**16. 确认Web可被3.13加载（板端root终端）**

```bash
/usr/bin/python3.13 /var/lib/microduck-tools-install/board1-20261103/transfer/tools/microduck-tools-0.6.0/calibration/integration-0.16.3/web/calibration_web.py --help
```

用途：只解析CLI和加载标准库。

为什么：目录名integration-0.16.3是沿用的宿主路径；实际target会明确选择0.18.3。

预期结果：显示--target、--plugin-config、--robotctl等选项并退出，不连接设备。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**17. 创建独立源码版本目录（板端root终端）**

```bash
install -d -m 755 /opt/microduck-tools/releases/0.6.0-py313-board1-20261103
```

用途：放置本轮Python3.13安装支持源码。

为什么：不覆盖旧版本、native二进制或官方release。

预期结果：新目录创建成功；若执行中断后已存在，先核验，不直接重跑本篇。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**18. 复制已校验源码（板端root终端）**

```bash
cp -a /var/lib/microduck-tools-install/board1-20261103/transfer/tools/microduck-tools-0.6.0/. /opt/microduck-tools/releases/0.6.0-py313-board1-20261103/
```

用途：安装Python/前端源码。

为什么：基础Web只有标准库依赖，不做系统级pip安装。

预期结果：复制完成；源文件保持root拥有。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**19. 再次核验安装目录源码（板端root终端）**

```bash
/usr/bin/python3.13 -c 'import hashlib,json; from pathlib import Path; r=Path("/opt/microduck-tools/releases/0.6.0-py313-board1-20261103"); rows=json.loads((r/"RUNTIME-MANIFEST.json").read_text())["files"]; assert all((r/x["path"]).stat().st_size==x["bytes"] and hashlib.sha256((r/x["path"]).read_bytes()).hexdigest()==x["sha256"] for x in rows); print("PASS: runtime files",len(rows))'
```

用途：验证复制后的实际源码。

为什么：只有校验完成后才建立current指针。

预期结果：打印PASS与文件数。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**20. 确认原生socket访问组（板端root终端）**

```bash
getent group robot
```

用途：核对已有robot组。

为什么：沿用已有Unix socket访问控制，不chmod777或开放串口权限。

预期结果：robot组存在；缺失时停止，由初始化会话核对。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**21. 创建普通Web服务账号（板端root终端）**

```bash
useradd --system --user-group --home-dir /var/lib/microduck-tools --shell /usr/sbin/nologin microduck-tools
```

用途：建立无登录权限的专用普通账号。

为什么：常驻Web不使用root，不接管舵机串口。

预期结果：账号创建成功；若已存在不重复执行，先核对来源。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**22. 授权读取原生socket（板端root终端）**

```bash
usermod -a -G robot microduck-tools
```

用途：只增加robot附加组。

为什么：不覆盖既有组列表，不授任意sudo或串口维护权。

预期结果：新启动的Tools进程带robot附加组。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**23. 创建插件配置目录（板端root终端）**

```bash
install -d -m 755 /etc/microduck-tools
```

用途：保存Tools自己的插件宿主设置。

为什么：插件设置不写robotd.toml。

预期结果：目录存在并由root管理。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**24. 创建Web独立状态目录（板端root终端）**

```bash
install -d -m 700 -o microduck-tools -g microduck-tools /var/lib/microduck-tools
```

用途：保存健康规则、日志和相机草稿。

为什么：隔离机器人配置、Studio状态和工作台数据库。

预期结果：仅服务账号可以读写。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**25. 安装默认插件配置（板端root终端）**

```bash
install -m 644 /opt/microduck-tools/releases/0.6.0-py313-board1-20261103/deploy/plugins.example.json /etc/microduck-tools/plugins.json
```

用途：启用只读servo_health插件。

为什么：模板schema=1且servo_health.enabled=true；这是监测开关，不会写舵机寄存器。

预期结果：文件内容与已核验模板一致。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**26. 建立Tools源码入口（板端root终端）**

```bash
ln -s /opt/microduck-tools/releases/0.6.0-py313-board1-20261103 /opt/microduck-tools/current
```

用途：选择本轮Tools源码。

为什么：仅首次创建该链接，不触碰/opt/microduck-tools/native/current。

预期结果：链接指向0.6.0-py313-board1-20261103。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**27. 安装普通Web服务模板（板端root终端）**

```bash
install -m 644 /opt/microduck-tools/releases/0.6.0-py313-board1-20261103/deploy/microduck-tools.service /etc/systemd/system/microduck-tools.service
```

用途：使用仓库已有加固模板。

为什么：非root、回环监听、私有临时目录，写入范围限定为自身状态目录。

预期结果：模板安装成功；未知同名unit存在时应在前置核查阶段停止。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**28. 创建本板服务覆盖目录（板端root终端）**

```bash
install -d -m 755 /etc/systemd/system/microduck-tools.service.d
```

用途：保存本板的明确解释器和CLI路径。

为什么：不要为选择Python而替换系统默认指针。

预期结果：新目录可用。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**29. 明确使用3.13和现有calv3 CLI（板端root终端）**

```bash
install -m 644 /var/lib/microduck-tools-install/board1-20261103/transfer/board-config/microduck-tools-py313.conf /etc/systemd/system/microduck-tools.service.d/zzzz-board1-py313.conf
```

用途：固定服务所用解释器、目标和只读遥测CLI。

为什么：不启用--demo、--allow-inactive、--enable-camera-apply等绕过/应用入口。

预期结果：ExecStart为本板Python3.13；没有改robotd的任何启动参数。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**30. 静态校验unit（板端root终端）**

```bash
systemd-analyze verify /etc/systemd/system/microduck-tools.service
```

用途：提前发现unit语法和路径错误。

为什么：unit被systemd接受不等于实际服务启动成功，二者分开验证。

预期结果：无本unit的致命错误；其他依赖告警保留原文审查。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**31. 让systemd读取新服务（板端root终端）**

```bash
systemctl daemon-reload
```

用途：重新加载unit及drop-in。

为什么：这条命令不重启robotd，也不启动新服务。

预期结果：执行成功。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**32. 启动Tools Web（板端root终端）**

```bash
systemctl start microduck-tools
```

用途：仅启动Tools进程和共享只读监测。

为什么：不启用policy、不自动恢复手柄、不重启robotd。

预期结果：服务启动；首次本流程不设置开机自启。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**33. 查看服务状态和实际解释器（板端root终端）**

```bash
systemctl show microduck-tools -p ActiveState -p MainPID -p User -p SupplementaryGroups -p ExecStart
```

用途：检查是否真正运行。

为什么：安装文件存在不能当作服务已经生效。

预期结果：active、非零PID、User=microduck-tools、ExecStart含/usr/bin/python3.13。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**34. 查看插件实际启用状态（板端root终端）**

```bash
curl --fail --silent --show-error http://127.0.0.1:8765/api/capabilities
```

用途：检查运行中插件开关，而非只看JSON文件。

为什么：enabled=true仅证明启动了插件；仍要核对数据新鲜度和分析覆盖。

预期结果：servo_health.enabled=true，resolved_target为daemon-v0.18.3；保存权限按普通账户实际显示。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**35. 查看状态与校正边界（板端root终端）**

```bash
curl --fail --silent --show-error http://127.0.0.1:8765/api/status
```

用途：查看真实target、遥测和校正回执情况。

为什么：本板尚无自定义校正文件；不要求假造calibration_active=true。

预期结果：demo=false、allow_inactive=false；runtime内calibration_active仍按真实回执。普通账户若被/proc权限阻止核验，runtime_reason保留PermissionError，不能据此认定calv3未安装，更不能放宽系统权限；按文末权限说明复核。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**36. 建立仅本机可见的Web通道（Mac新终端并保持运行）**

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:127.0.0.1:8765 root@172.19.8.218
```

用途：把板端回环Web转发到Mac。

为什么：不用开放板端0.0.0.0 HTTP端口。

预期结果：终端持续等待；端口冲突应换明确的新本地端口，不关闭未知程序。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**37. 打开基础Web（Mac另开终端）**

```bash
open http://127.0.0.1:8765/
```

用途：查看原校正页、相机配置和舵机健康页。

为什么：页面访问不等于授权保存校正、重启相机或执行维护。

预期结果：看到实际0.18.3目标和新鲜监测；本次只浏览，不保存IMU/关节/相机设置。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。


**四、安装Studio，继续使用板端3.13**

浏览器验收：已用agent-browser实际打开并取得页面元素与截图，不执行保存。

**38. 进入已安装源码目录（板端root终端）**

```bash
cd /opt/microduck-tools/releases/0.6.0-py313-board1-20261103
```

用途：使用本次支持3.13的Studio安装器。

为什么：不能从旧0.6.0包复制一个入口就假定也支持3.13。

预期结果：目录为本次校验过的源码。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**39. 先做只读预检（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage doctor --assets /var/lib/microduck-tools-install/board1-20261103/transfer/runtime-assets --wheels /var/lib/microduck-tools-install/board1-20261103/transfer/wheels313
```

用途：检查板型、3.13 ABI、venv、systemd和10个离线wheel。

为什么：旧cp311包即使文件完整，也必须被3.13的独立锁拒绝。

预期结果：ok=true且所有checks通过；此命令不创建账号、不写unit、不接管手柄。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**40. 预览Studio安装（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage setup --server-ip 172.19.8.218 --assets /var/lib/microduck-tools-install/board1-20261103/transfer/runtime-assets --wheels /var/lib/microduck-tools-install/board1-20261103/transfer/wheels313
```

用途：展示待执行安装计划。

为什么：未加--apply的预览不会真正做安装；doctor通过仍须审阅目标IP和目录。

预期结果：提示预览，没有修改；IP用于HTTPS证书。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**41. 安装独立Studio（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage setup --server-ip 172.19.8.218 --assets /var/lib/microduck-tools-install/board1-20261103/transfer/runtime-assets --wheels /var/lib/microduck-tools-install/board1-20261103/transfer/wheels313 --apply --confirm EXECUTE
```

用途：离线创建3.13 venv、普通用户服务、受限助手和证书。

为什么：使用组件自己的安装登记，不手抄/跳过它管理的units。首次创建，未知旧安装会拒绝覆盖。

预期结果：安装成功但尚未启动；controls_enabled=false、camera_enabled=false，不开机自启。若失败，停止并看status，不重复setup撞目录。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**42. 确认Studio隔离环境仍是3.13（板端root终端）**

```bash
/opt/microduck-tools/studio/releases/0.6.0/venv/bin/python --version
```

用途：核对实际venv解释器。

为什么：隔离环境不应偷偷依赖另一套Python3.11。

预期结果：Python3.13.x，与本板基础解释器小版本相符。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**43. 核对安装登记和运行开关（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage status --json
```

用途：验证源码/配置登记哈希以及阶段。

为什么：不手工编辑已登记config.json绕过完整性检查。

预期结果：phase=installed；哈希无漂移，控制/Camera关闭；尚未运行的服务正常显示停止。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**44. 启动独立Studio（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage start --apply --confirm EXECUTE
```

用途：启动Studio和受限助手。

为什么：仍不恢复padd，不启用机器人动力，也不解除--no-policy。

预期结果：提示已启动，HTTPS端口8443；用status再次确认服务实际状态。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

补充实测：首次8443被mediad占用导致失败，随后经登记改为8444并修正严格TLS；最终HTTP200和active已核验。

**45. 查看CA证书指纹（板端root终端）**

```bash
openssl x509 -in /etc/microduck-studio/ca.crt -noout -fingerprint -sha256
```

用途：取得可信通道中的CA身份。

为什么：证书名称相同不代表内容相同；只复制公开ca.crt，绝不复制ca.key。

预期结果：记录SHA256指纹以便与Mac文件对照。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**46. 只取公开CA证书（Mac终端）**

```bash
scp root@172.19.8.218:/etc/microduck-studio/ca.crt /Users/bou/Workspace/Duckduck/work/board1-studio-ca-20261103.crt
```

用途：把公开证书取回Mac。

为什么：本文件须等板端安装后才会存在；它不是本轮本地准备生成的私钥。

预期结果：文件获取成功；不要把任何.key文件放交付包或聊天。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**47. 核对取回CA指纹（Mac终端）**

```bash
openssl x509 -in /Users/bou/Workspace/Duckduck/work/board1-studio-ca-20261103.crt -noout -fingerprint -sha256
```

用途：与板端指纹逐字对照。

为什么：一致后再由本人在Mac/手机受信证书设置安装CA；不使用忽略证书错误作为验收。

预期结果：指纹一致。公开CA保存在work，属于后续部署证据；不修改本次封存交付。

本次结果：**已执行对应板端安装/核验操作**；分组命令、真实输出和退出码见本页板端summary。目录/复制等部分操作以等价组合执行，不冒充逐字逐条执行。

**48. 生成一次性网页登录码（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage pair --apply --confirm EXECUTE
```

用途：为当前显示端生成5分钟登录码。

为什么：这是网页配对，不是蓝牙配对。码不要发到Git、公共截图或聊天日志。

预期结果：使用该码登录一次；另一个显示端需要另发一次。

本次结果：待执行；本轮仅本地核对指引。

**49. 打开Studio（Mac终端）**

```bash
open https://172.19.8.218:8444/studio/
```

用途：通过可信HTTPS访问手机/桌面Studio。

为什么：先完成CA信任及一次性登录；本次保持控制和Camera关闭。

预期结果：可信HTTPS、实际反馈或明确未知状态；无policy时“控制阻断”是正确结果，不为解除提示而enable。

本次结果：待执行；本轮仅本地核对指引。


**五、电脑安装正式USB舵机工作台**

**50. 创建独立工作台运行目录（Mac终端）**

```bash
mkdir /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103
```

用途：为电脑组件选择独立运行位置。

为什么：交付包不混入venv缓存，真实批次状态以后需要保留。目录已有时先审查，不覆盖。

预期结果：新目录创建成功。

本次结果：**已完成对应安装/校验操作。** 0.1.2正式目录已校验42文件，离线setup通过，LIVE后端PID69941；实际状态与完整输出见本页顶部安装记录。除第52步setup与第56步status外，部分目录/解包/启动操作由等价Python安装器执行，不冒充逐字运行本代码块。

**51. 解开固定工作台包（Mac终端）**

```bash
tar -xzf /Users/bou/Workspace/Duckduck/deliverables/microduck-tools-board1-install-guide-20261103/packages/microduck-servo-workbench-0.1.2.tar.gz -C /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103
```

用途：安装电脑端源码和通用pyserial wheel。

为什么：该组件仍为0.1.2，独立于Tools0.6.0；不替换板端daemon。

预期结果：出现microduck-servo-workbench-0.1.2子目录。

本次结果：**已完成对应安装/校验操作。** 0.1.2正式目录已校验42文件，离线setup通过，LIVE后端PID69941；实际状态与完整输出见本页顶部安装记录。除第52步setup与第56步status外，部分目录/解包/启动操作由等价Python安装器执行，不冒充逐字运行本代码块。

**52. 准备电脑隔离环境（Mac终端）**

```bash
python3 /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/microduck-servo-workbench-0.1.2/tools/setup-workbench.py
```

用途：在电脑组件目录建立.venv-workbench并离线安装pyserial3.5。

为什么：电脑解释器只需3.11+，可为3.13/3.14，不要求恰好3.11；板端统一3.13不强制Mac同版本。

预期结果：提示准备完成，无真实USB/串口访问。

本次结果：**已完成对应安装/校验操作。** 0.1.2正式目录已校验42文件，离线setup通过，LIVE后端PID69941；实际状态与完整输出见本页顶部安装记录。除第52步setup与第56步status外，部分目录/解包/启动操作由等价Python安装器执行，不冒充逐字运行本代码块。

**53. 进入工作台源码目录（Mac终端）**

```bash
cd /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/microduck-servo-workbench-0.1.2
```

用途：让python -m能找到本地servo_workbench模块。

为什么：venv只安装pyserial，工作台源码没有全局pip安装；绝对解释器路径不会自动改变当前目录。

预期结果：当前目录是microduck-servo-workbench-0.1.2。

本次结果：**已完成对应安装/校验操作。** 0.1.2正式目录已校验42文件，离线setup通过，LIVE后端PID69941；实际状态与完整输出见本页顶部安装记录。除第52步setup与第56步status外，部分目录/解包/启动操作由等价Python安装器执行，不冒充逐字运行本代码块。

**54. 启动正式USB向导（初始未绑定设备）（Mac终端并保持运行）**

```bash
/Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/microduck-servo-workbench-0.1.2/.venv-workbench/bin/python -B -m servo_workbench serve --real --state-dir /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/usb-real-state --open
```

用途：打开127.0.0.1:8766正式USB向导。

为什么：无需板端；启动后尚未绑定设备，必须在界面选择适配器才进入只读连接。

预期结果：明确显示LIVE；保持该终端，停止用Ctrl+C。

本次结果：**已完成对应安装/校验操作。** 0.1.2正式目录已校验42文件，离线setup通过，LIVE后端PID69941；实际状态与完整输出见本页顶部安装记录。除第52步setup与第56步status外，部分目录/解包/启动操作由等价Python安装器执行，不冒充逐字运行本代码块。

**55. 为状态和导出命令进入同一源码目录（Mac另开终端）**

```bash
cd /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/microduck-servo-workbench-0.1.2
```

用途：让新终端也能加载工作台模块。

为什么：每个新终端的当前目录独立，不能假定继承了运行后端的终端。

预期结果：新终端也位于工作台源码目录，另一个终端的正式后端保持运行。

本次结果：**已完成对应安装/校验操作。** 0.1.2正式目录已校验42文件，离线setup通过，LIVE后端PID69941；实际状态与完整输出见本页顶部安装记录。除第52步setup与第56步status外，部分目录/解包/启动操作由等价Python安装器执行，不冒充逐字运行本代码块。

**56. 查看正在运行的工作台状态（Mac另开终端）**

```bash
/Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/microduck-servo-workbench-0.1.2/.venv-workbench/bin/python -B -m servo_workbench status
```

用途：读取电脑本机后端状态。

为什么：status不会另起一个串口后台。

预期结果：返回当前真实连接/批次状态；先在网页建立15关节批次再查看进度。

本次结果：**已完成对应安装/校验操作。** 0.1.2正式目录已校验42文件，离线setup通过，LIVE后端PID69941；实际状态与完整输出见本页顶部安装记录。除第52步setup与第56步status外，部分目录/解包/启动操作由等价Python安装器执行，不冒充逐字运行本代码块。

**57. 本只完成后导出真实批次证据（当前待执行）（Mac另开终端）**

```bash
/Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/microduck-servo-workbench-0.1.2/.venv-workbench/bin/python -B -m servo_workbench export --out /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.2-20261103/first-usb-report.json
```

用途：保存本只执行和其余未配置槽位的真实报告。

为什么：本只真实读回通过后再导出；已有同名文件拒绝覆盖，不能用模拟结果替代真实写入证据。

预期结果：报告保留本只写入/读回证据，其余14槽位待配置，不能宣称15只整组验收通过。

本次结果：待执行；本轮仅本地核对指引。


**六、历史0.1.1板端代理准备（本次USB不执行）**

以下原0.1.1安装命令仍对应冻结transfer包，尚未执行，不可与本次0.1.2电脑端配对。今后改用板端UART时需先准备同版本代理并单独核对部署计划；本轮不安装板端。

**58. 查看robotd实际持有的设备（板端root终端）**

```bash
ls -l /proc/$(systemctl show robotd -p MainPID --value)/fd
```

用途：找到与配置对应的真实舵机UART。

为什么：不猜/dev/ttyS2；不能把IMU串口或别的板的端口当舵机总线。

预期结果：人工对照robotd实际配置，记录唯一正确的/dev/...；不清理占用者。

本次结果：待执行；本轮仅本地核对指引。

**59. 输入已经核对的UART（板端root终端）**

```bash
read -r -p '请输入已核对的舵机UART完整路径：' WB_UART
```

用途：保存唯一尚需现场确认的端口。

为什么：这一项不能提前展开为臆测端口；其他固定路径均按本板写出。

预期结果：变量仅在当前终端有效；值必须为已确认的字符设备绝对路径。

本次结果：待执行；本轮仅本地核对指引。

**60. 检查UART确实存在（板端root终端）**

```bash
test -c "$WB_UART"
```

用途：避免将普通文件或空值当总线。

为什么：此检查不打开串口，也不证明接线/协议正确。

预期结果：退出0才继续；否则停止。

本次结果：待执行；本轮仅本地核对指引。

**61. 解开代理安装材料（板端root终端）**

```bash
tar -xzf /var/lib/microduck-tools-install/board1-20261103/transfer/workbench/microduck-servo-workbench-0.1.1.tar.gz -C /var/lib/microduck-tools-install/board1-20261103
```

用途：准备同版本板端代理安装源。

为什么：0.1.1电脑端拒绝0.1.0代理；不混用历史包。

预期结果：生成${B}/microduck-servo-workbench-0.1.1；已有目录先核验，不覆盖。

本次结果：待执行；本轮仅本地核对指引。

**62. 预览XL330代理安装（板端root终端）**

```bash
/usr/bin/python3.13 /var/lib/microduck-tools-install/board1-20261103/microduck-servo-workbench-0.1.1/deploy/workbench/install-agent.py --kind xl330 --port "$WB_UART"
```

用途：查看固定协议、端口和目标目录。

为什么：当前Microduck默认XL330；若实际维护HD1910，先明确更换专用总线/协议，再将kind改为hd1910，不能套用XL330。

预期结果：preview_only、version0.1.1、robotd_changed=false；不安装也不打开串口。

本次结果：待执行；本轮仅本地核对指引。

**63. 安装代理文件和3.13隔离环境（板端root终端）**

```bash
/usr/bin/python3.13 /var/lib/microduck-tools-install/board1-20261103/microduck-servo-workbench-0.1.1/deploy/workbench/install-agent.py --kind xl330 --port "$WB_UART" --apply
```

用途：安装代理源码、通用pyserial和固定配置。

为什么：本步骤不会停止robotd、不会启动代理，也不装自启动服务；与真实串口维护分开。

预期结果：/opt/microduck-servo-workbench/current指向0.1.1，autostart=false、robotd_changed=false。失败时保留部分目录，不自动重试。

本次结果：待执行；本轮仅本地核对指引。

**64. 验证代理解释器和依赖，不运行代理（板端root终端）**

```bash
/opt/microduck-servo-workbench/current/.venv-workbench/bin/python -c 'import sys,serial; print(sys.version); print("pyserial",serial.VERSION)'
```

用途：确认板端代理也使用3.13。

为什么：导入serial不会打开设备；不要在安装验收时运行agent --stdio抢用总线。

预期结果：Python3.13.x、pyserial3.5。

本次结果：待执行；本轮仅本地核对指引。


**七、状态、停止与回退；按需要单独选择**

**65. 查看Tools近期错误（板端root终端）**

```bash
journalctl -u microduck-tools -b -n 80 --no-pager
```

用途：查看真实错误原文。

为什么：启动失败先查权限、路径、端口和依赖；不通过重启robotd或改变IMU参数解决工具安装问题。

预期结果：无持续崩溃；socket拒绝、/proc权限等按原文排查。

本次结果：待执行；本轮仅本地核对指引。

**66. 停止Studio但保留安装（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage stop --apply --confirm EXECUTE
```

用途：停止Studio及其受限助手。

为什么：不等于回滚robotd，也不自动交还/启动手柄。

预期结果：Studio停止，原daemon和源码/证据保留。命令需在本轮源码目录执行。

本次结果：待执行；本轮仅本地核对指引。

**67. Studio安装中断时先查看状态（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage status --json
```

用途：读取安装阶段和问题。

为什么：不可直接删登记或重复setup；恢复和回退按文末分支。

预期结果：如实显示未安装、安装中或已安装；未知同名unit及漂移必须先处理。

本次结果：待执行；本轮仅本地核对指引。

**68. 撤销本次Studio安装（板端root终端）**

```bash
/usr/bin/python3.13 -m studio.manage rollback --apply --confirm EXECUTE
```

用途：撤销组件自己登记的服务和配置，保留版本/证据。

为什么：不手删受管理文件；当前无控制交接，若后续出现handoff先单独处理。

预期结果：仅Studio登记撤销，robotd、calv3、Tools链接不变。

本次结果：待执行；本轮仅本地核对指引。

**69. 停止基础Tools Web（板端root终端）**

```bash
systemctl stop microduck-tools
```

用途：关闭Web和只读遥测订阅。

为什么：不停止robotd，不改变舵机配置。

预期结果：Tools MainPID=0；SSH隧道可在Mac按Ctrl+C结束。

本次结果：待执行；本轮仅本地核对指引。

**70. 撤回基础Web前确认只涉及本次文件（板端root终端）**

```bash
/usr/bin/python3.13 -c 'from pathlib import Path; a=Path("/etc/systemd/system/microduck-tools.service"); b=Path("/etc/systemd/system/microduck-tools.service.d/zzzz-board1-py313.conf"); assert a.read_bytes()==Path("/opt/microduck-tools/releases/0.6.0-py313-board1-20261103/deploy/microduck-tools.service").read_bytes(); assert b.read_bytes()==Path("/var/lib/microduck-tools-install/board1-20261103/transfer/board-config/microduck-tools-py313.conf").read_bytes(); assert Path("/opt/microduck-tools/current").is_symlink() and Path("/opt/microduck-tools/current").readlink()==Path("/opt/microduck-tools/releases/0.6.0-py313-board1-20261103"); assert not any(Path("/var/backups/microduck/tools-install-board1-20261103/"+n).exists() for n in ["tools-current.disabled","tools-service.disabled","tools-dropin.disabled"]); print("PASS: only this installation will be moved")'
```

用途：检查服务已停止后撤回的文件归属。

为什么：外部修改或重复回退不能被本篇命令覆盖；发现差异先停。

预期结果：PASS；Tools必须已停止，本流程没有enable过Tools。

本次结果：待执行；本轮仅本地核对指引。

**71. 留存本轮Tools链接（板端root终端）**

```bash
mv /opt/microduck-tools/current /var/backups/microduck/tools-install-board1-20261103/tools-current.disabled
```

用途：撤下本次文件并保留恢复证据。

为什么：不删除源码、状态和回滚材料，也不触碰native/current。

预期结果：只在前一步PASS后执行；目标不存在，不覆盖历史。

本次结果：待执行；本轮仅本地核对指引。

**72. 留存本轮Web服务（板端root终端）**

```bash
mv /etc/systemd/system/microduck-tools.service /var/backups/microduck/tools-install-board1-20261103/tools-service.disabled
```

用途：撤下本次文件并保留恢复证据。

为什么：不删除源码、状态和回滚材料，也不触碰native/current。

预期结果：只在前一步PASS后执行；目标不存在，不覆盖历史。

本次结果：待执行；本轮仅本地核对指引。

**73. 留存本轮服务覆盖（板端root终端）**

```bash
mv /etc/systemd/system/microduck-tools.service.d/zzzz-board1-py313.conf /var/backups/microduck/tools-install-board1-20261103/tools-dropin.disabled
```

用途：撤下本次文件并保留恢复证据。

为什么：不删除源码、状态和回滚材料，也不触碰native/current。

预期结果：只在前一步PASS后执行；目标不存在，不覆盖历史。

本次结果：待执行；本轮仅本地核对指引。

**74. 让systemd读取Tools撤回结果（板端root终端）**

```bash
systemctl daemon-reload
```

用途：完成基础Web服务文件撤回。

为什么：无需重启robotd或整板。

预期结果：Tools已停止，源码/账号/状态仍保留，robotd仍是原0.18.3+calv3。

本次结果：待执行；本轮仅本地核对指引。

**安装后在界面上怎样确认**

基础Web先看目标版本、数据年龄、健康插件是否enabled，再看具体缺失项。当前没有自定义校正文件，configured=false、saved=null、calibration_active=false是已知基线；pending_restart可为null。differences中出现missing_saved_or_active说明缺少保存的自定义校正，不等于本次安装失败。不能伪造一份默认校正让界面“全绿”。

普通Web的/proc权限说明：源码用/proc/PID/exe核验原生身份；跨用户读取可能被Linux拒绝。此时runtime_verified=false、can_save=false应保留明确原因。root终端的robotctl版本、实际/proc/PID/exe SHA与回执PID仍可独立核对。不要chmod /proc、setcap或把常驻Web改成root。需要真正写IMU/关节校正时，另开有时限的维护流程；本篇安装不授权写入。

Studio登录后检查默认controls_enabled=false、camera_enabled=false，保持无policy阻断。只浏览总览、监控、主题和历史报告；无真实反馈时显示未知/冻结，不用模拟画面冒充实机。基础Web的健康配置与Studio控制开关是两个独立配置。

电脑演练依次选型号/布局/预设→建立15关节批次→接入模拟舵机→稳定后确认配置→保存标签→模拟下一只→整组模拟复核→导出。关闭重开保留演练批次。中位、保护参数和台架按钮不包含在默认配置确认中。

**真实USB与SSH模式暂不执行**

电脑USB真实模式在结束演练后、并在工作台源码目录中，用同一.venv-workbench/bin/python -m servo_workbench serve --real --state-dir /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.1-20261103/usb-real-state --open；先在网页选适配器并“绑定并只读连接”。必须确认专用总线和电压，避免与仍运行的robotd共同接到同一串口总线；演练库不能当真实库。

板端代理安装完成不代表已连接。后续SSH模式的固定远端命令是sudo -n -- /opt/microduck-servo-workbench/current/agent.sh --stdio；需先核对主机指纹、非交互SSH和唯一命令的权限。不授任意sudo，不在本篇复制SSH私钥或自动写sudoers。确认串口维护交接后，电脑才运行 --ssh 已登记别名；robotd活跃或串口被占用时代理拒绝，不替用户停服务。客户端与代理必须都是0.1.1。

**中断和回退的分支**

| 当前事实 | 正确下一步 |
|---|---|
| 当前板端关机或SSH不可达 | 保持本地准备；不探测其他IP、不重复升级 |
| 输入/源码目录已存在但还没有current | 读取原阶段、核验全部哈希；保留半成品，不当空目录覆盖 |
| Tools端口/权限错误 | journalctl及实际ExecStart核对；不改robotd/IMU、不放宽socket或/proc权限 |
| Studio doctor失败 | 按缺项修复3.13模块/对应wheel；不能复制旧cp311 wheel或跳过检查 |
| Studio setup中断 | 在本轮源码目录先status，再 /usr/bin/python3.13 -m studio.manage recover --apply --confirm EXECUTE；恢复会保留归档证据，之后重新doctor/setup |
| Studio已装但需撤销 | 用本篇rollback；外部文件漂移会拒绝，先保留并核对 |
| 要撤掉基础Tools | 先stop；确认首次安装前确无旧current后，将本轮Tools链接、unit及zzzz-board1-py313.conf移动到/var/backups/microduck/tools-install-board1-20261103留存，再daemon-reload；不删除状态/源码，不动native/current |
| 工作台已运行但需停用 | Ctrl+C停止电脑后端；不删SQLite/WAL/SHM或authority.lock强开第二个后台 |
| 代理安装中断 | 保留部分release和agent.json，核验current是否已切；不自动重放、不删除重来，不通过启动代理“测试”占用中的总线 |

**重要路径**

| 内容 | 路径 |
|---|---|
| 普通Web入口 | http://127.0.0.1:8765/（Mac SSH隧道） |
| Studio入口 | https://172.19.8.218:8444/studio/（可信局域网HTTPS） |
| Tools插件宿主 | /etc/microduck-tools/plugins.json |
| Tools状态 | /var/lib/microduck-tools/ |
| Studio配置 | /etc/microduck-studio/config.json（由组件CLI管理） |
| Studio安装登记 | /var/lib/microduck-studio-install/ |
| Studio源码/3.13 venv | /opt/microduck-tools/studio/releases/0.6.0/ |
| 本轮Tools源码 | /opt/microduck-tools/releases/0.6.0-py313-board1-20261103 |
| 已有calv3程序 | /opt/microduck-tools/native/current/bin/（保持原样） |
| 可选板端代理配置 | /etc/microduck-servo-workbench/agent.json |
| 可选板端代理权威批次 | /var/lib/microduck-servo-workbench/ |
| 电脑演练批次 | /Users/bou/Workspace/Duckduck/work/board1-workbench-0.1.1-20261103/demo-state/ |
| 本次输入/备份 | /var/lib/microduck-tools-install/board1-20261103；/var/backups/microduck/tools-install-board1-20261103 |

**证据与验收边界**

本地433项Python、11项Node回归通过，包括11项新增安装预检测试。pip按CPython3.13 Linux ARM64目标对10个wheel做了离线完整依赖及require-hashes解析；5个cp313扩展文件SHA与PyPI官方文件页对照，5个通用wheel与旧已核验锁一致。原3.11锁和旧冻结包保持原始字节。本机没有安装Linux ARM64 Python3.13，不能把本地Python3.14回归和跨平台解析写成板端动态加载通过；后续真正setup内的import及服务验收仍是上板步骤。

本篇不重做0.18.3升级、不安装真实校正、不设置16.4或猜IMU安装四元数。完整源代码身份、材料SHA、语法检查和后续CI状态以本交付release-record.json、manifest.json及evidence为准。参考风格源文件、旧交付包和既有板端报告保持不变。


**本轮完整输出登记（2026-11-03，Mac离线执行）**

每行的stdout、stderr和返回码均为原始记录；摘要不是对全量输出的替代。Linux上的/usr/bin/python3.13、systemctl、账号/权限、真实UART与服务启动仍等待上板验证。

| 实际执行项目 | 完整输出与结果 |
|---|---|
| 原交付完整SHA | [225条stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/original-delivery-sha-stdout.log) · [返回码](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/original-delivery-sha-result.json) |
| Mac环境/命令版本 | [stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/mac-environment-stdout.log) · [stderr](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/mac-environment-stderr.log) · [返回码](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/mac-environment-result.json) |
| 工作台解包与41文件校验 | [stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/workbench-unpack-stdout.log) · [返回码](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/workbench-unpack-result.json) |
| 实际离线setup | [stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/workbench-offline-setup-stdout.log) · [stderr](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/workbench-offline-setup-stderr.log) · [返回码](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/workbench-offline-setup-result.json) |
| 模拟CLI help | [stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-help-stdout.log) · [stderr](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-help-stderr.log) |
| 模拟CLI status | [完整JSON stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-status-stdout.log) · [stderr](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-status-stderr.log) |
| 模拟CLI export | [stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-export-stdout.log) · [stderr](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-export-stderr.log) |
| 演练网页启动与正常停止 | [服务stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-server-stdout.log) · [服务stderr](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-server-stderr.log) · [汇总含exit=0](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/desktop-smoke.json) |
| 基础Web CLI加载 | [stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/web-cli-help-stdout.log) · [返回码](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/web-cli-help-result.json) |
| Studio CLI加载 | [stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/studio-cli-help-stdout.log) · [返回码](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/studio-cli-help-result.json) |
| 真实资产及3.13锁预检 | [完整JSON stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/assets-lock-preflight-stdout.log) · [返回码](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/assets-lock-preflight-result.json) |
| 3.13 ARM64离线依赖解析 | [stdout](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/arm64-pip-dryrun-stdout.log) · [stderr](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/arm64-pip-dryrun-stderr.log) · [返回码](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/arm64-pip-dryrun-result.json) |
| 本轮结构化汇总 | [summary.json](/Users/bou/Workspace/Duckduck/logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z/summary.json) |
