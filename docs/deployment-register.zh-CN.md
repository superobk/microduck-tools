# 部署登记

| 工具版本 | 目标 | 准备和软件验证 | 板端状态 |
|---|---|---|---|
| 0.6.0 / 2026-11-01 | 固定0.18.3/API47的独立移动Studio | 独立HTTPS/助手/资源组与安装恢复登记；手机/桌面界面、七场景、真实反馈、Policy/手柄、离线Worker语义；40项Studio+236项既有Python、11项Node通过；测试见Studio验收登记；原0.5.0套件冻结 | 所有板端安装/配对/交接/运动/资源与实际设备验收NOT_RUN |
| 0.5.0 / 2026-10-31 | 固定0.16.3→0.18.3的菜单/任务管理 | 十个入口脚本、默认预览、分层运行状态、外部备份、独占更新锁、安装意图日志、中断恢复、同版官方/安装前撤销/救援镜像回退；236项Python通过无跳过，其中46项管理器测试；原生程序及核心Web/校正未改 | 板端安装、服务、总线、镜像与物理验收全部NOT_RUN |
| 0.4.3 / 2026-10-31 | 当前0.16.3直接到固定0.18.3 | 六步手工指南；故障/中断/镜像恢复；13+2程序固定输入；GLIBC2.30目标重建native，ELF最高要求2.29；190项Python通过无跳过，含26项本轮助手/文档测试和两处真实本地SIGKILL；原生补丁和核心校正/Web未改 | SSH、运行库加载、安装、服务、升级、回退、相机/IMU/性能均NOT_RUN，等另行通知 |

**当前入口：[0.16.3→0.18.3脚本升级路径](manual-upgrade-0183.zh-CN.md) / [菜单与任务脚本](script-manager.zh-CN.md)**，完整交付目录 `/Users/bou/Workspace/Duckduck/deliverables/microduck-tools-manager-20261031`，在bundle里运行 `sudo ./duck.sh`。独立check/backup/install/configure/status/services/rollback/recover/image脚本共用持久会话；旧手工包保留历史，不混用。普通Web非root；校正写入使用有时限的临时维护Web，退出恢复普通服务，robotd仅在明确reload时重启。

历史手工审阅入口：[0.4.3手工主流程](manual-upgrade-0183-0.4.3-history.zh-CN.md)、[回退与中断](manual-upgrade-recovery.zh-CN.md)、[镜像恢复](manual-upgrade-image.zh-CN.md)、[审查问题与修复](manual-upgrade-review.zh-CN.md)。固定程序SHA与源码身份在 [inputs.json](../deploy/manual-upgrade/inputs.json)。

程序准备：官方13个ARM64程序来自固定官方签名归档；两个自定义校正程序已交叉编译；Tools为Python/Web源码运行包，无需板端编译。上述软件可交付不等于该板已经可运行，前后动态库/ONNX和进程身份仍须实测。

当前完整安装包位于 `/Users/bou/Workspace/Duckduck/deliverables/microduck-tools-manager-20261031`；旧0.4.3手工包为 `/Users/bou/Workspace/Duckduck/deliverables/microduck-0183-manual-review-20261031`，仅留历史。按现有仓库规则只提交源码、手册、模板、输入摘要和测试，不提交真实配置/账号/系统镜像、巨大编译缓存或二进制；本地交付包含已编译候选、固定官方包和证据。旧0.4.2及第一版直升归档保留作历史，不再执行其中整体搬移系统目录的恢复循环。

2026-10-31 文档入口同步：README、0.18.3部署页与旧回退/镜像/Tools页统一指向简明指南的脚本命令；旧六步全文单列历史。脚本和固定程序未改，继续使用冻结的0.5.0完整bundle，不替换正在进行会话的tool目录。26项既有升级审查测试通过，新增文档链接/命令入口本地校验通过；指南增量、校验和源码快照留存在 `deliverables/microduck-tools-upgrade-path-20261031`；板端仍NOT_RUN。

2026-11-01 Studio 0.6.0：新增独立候选包 `/Users/bou/Workspace/Duckduck/deliverables/microduck-tools-studio-20261101`；入口为包内 `studio.sh`，源码入口 `tools/studio.sh`。配置 `/etc/microduck-studio/config.json`，运行版本 `/opt/microduck-tools/studio/releases/0.6.0`，安装登记 `/var/lib/microduck-studio-install`，与旧 `/opt/microduck-tools/current`、manager会话及robotd/current分离。源码在 `feat/mobile-studio-0.6.0` 供PR审阅，不自动合并。详见[统一设计](studio-0.6.0-plan.zh-CN.md)、[操作指南](studio-0.6.0-guide.zh-CN.md)、[软件验收及现场清单](studio-0.6.0-validation.zh-CN.md)。旧0.5.0归档SHA仍为 `d17bc4444d8c3fd817e2ed212bdb7001330680ad0fbe97fe30ec5417135d8790`，原生二进制/补丁与核心校正Web未修改。

2026-11-01 安装流程增量：推荐[一个入口的简明方案](install-upgrade-simple.zh-CN.md)。新 `microduck-install-kit-0.6.0` 将字节不变的冻结0.5.0机器人升级器与当前Studio完整离线包置于独立子目录，根目录 `sudo ./duck.sh` 按N逐步完成，不迁移原状态引擎。新增37项向导/预检/打包测试，与40项Studio、236项原回归及11项Node通过；正在执行/中断/文件已保存/真实回执分开显示。修复Studio早期失败恢复误停不存在服务、未知同名服务晚期发现、依赖失败缺少原文；增加只读结构化状态和互斥锁忙态。新交付 `/Users/bou/Workspace/Duckduck/deliverables/microduck-install-flow-20261101`；旧包保留不变。板端、镜像、升级/回退、配对/运动和资源验收全部NOT_RUN，继续等另行通知。
# 2026-11-02：独立双协议舵机工作台0.1.0

新增电脑本地15关节持久向导、XL330/HD1910独立驱动、固定SSH板端代理、只读自动重连与未知写入核对、整组掉电复核、可选中位/台架/参数回退，以及Studio历史报告联动。使用入口见[向导指南](servo-workbench-guide.zh-CN.md)，设计/恢复/来源/测试分别登记。Tools仍0.6.0，现有校正/daemon原生补丁/冻结升级器保持不变。仅本地模拟、假串口和环回HTTP验证；真实USB、Windows硬件、SSH板端、供电、EEPROM、中位、动作及性能全部NOT_RUN，等待用户另行通知。本轮交付与提交信息见最终验证记录。

## 2026-11-02：PR #7工作台0.1.1安全复审

修复Feetech回显/错误读数、事务跨连接、恢复确认顺序、非默认ID/速率扫描与回退续检、台架心跳/反馈/越界保护及整组跨连接验收。提供[原生命令直升与Tools安装](native-upgrade-0163-to-0183.zh-CN.md)、[完整回退](native-upgrade-recovery.zh-CN.md)、[复审](servo-workbench-safety-review.zh-CN.md)。无真实USB/SSH/板端/舵机操作；原daemon、校正核心、冻结升级器/包不变，最终计数、提交、CI与交付以本轮release-record登记。


## 2026-11-03：第一板Python3.13安装支持与逐条指南

第一板0.18.3+calv3已由Robot init guideline完成，前置报告在工作区logs/board1-0183-postboot-20261103T080757Z；本轮没有重新上板。Tools/Studio仍0.6.0，工作台仍0.1.1。Studio原门控确实限制板端CPython3.11，本轮增加3.13普通GIL/ARM64支持及独立锁，拒绝PyPy/free-threaded及其他未核验ABI；旧3.11锁/旧归档不改。全局系统Python指针不改，Studio/代理各自venv仍源自板端同一3.13解释器。

[第一板逐条安装指引](board1-tools-workbench-step-by-step.zh-CN.md)采用“命令/用途/为什么/预期/待执行”格式，包含Tools Web、Studio、电脑工作台及可选板端代理、停止/恢复/回退。当前本地433Python（52Studio、37安装、108工作台、236原校正）及11Node通过；10个wheel跨平台离线解析和哈希通过，板端3.13 ARM64动态加载、服务、USB/串口与动作仍NOT_RUN。新本地交付为/Users/bou/Workspace/Duckduck/deliverables/microduck-tools-board1-install-guide-20261103，不能用原3.11包替代。原style源指南与calv3/daemon及核心校正代码未改。

真实材料核验另修复预检误将自包含Three0.170要求three.core.js的问题：仅对实际引用core的拆分模块要求该文件，新增缺失拒绝测试；未修改场景渲染逻辑。

2026-11-03原文校验登记增量：第一板安装指南第1、2步已在Mac真实执行，176份上传文件与更新前225条完整交付校验全部通过；电脑工作台离线安装/模拟网页/status/export在隔离目录通过，服务正常停止。直接在原指南写入输出索引与“完成2步/待上板72步”，不新增其他指南；传输payload仍为36de9bb不重编。原始证据在工作区logs/tools-python313-guide-20261103T083409Z/mac-validation-20261103T092153Z。板端SSH/SCP/安装和硬件均未执行。


## 2026-11-03：电脑USB工作台0.1.2安装准备

用户确认电脑USB和XL330；最新授权要求保留原Homing Offset模板（ID13/23=-512，其余0），忽略逐颗标定。0.1.2补齐Drive Mode=0；图示快捷选择显式包含Shutdown=52，完整8项预览区分已一致/将写入。114项工作台本地回归通过（含6项图示与中断回归）。初次默认沙箱运行受本地监听权限限制且默认解释器缺SDK；改用已有SDK测试venv并允许localhost监听后全通过，不能将环境限制算成产品失败或省略。Tools0.6.0和旧0.1.1冻结包不变。正式安装及界面证据将在本节追加，板端不参与。

正式电脑安装已完成：0.1.2便携包42文件校验通过、pyserial3.5离线安装通过；LIVE http://127.0.0.1:8766/?preset=xl330-image，PID64560，同用户运行，不自启动。真实状态目录work/board1-workbench-0.1.2-20261103/usb-real-state。隔离8776模拟向导8行完整预览通过并已停止。实际USB枚举发现/dev/cu.usbserial-FTBIHT7E，尚未绑定，不以USB枚举证明半双工接线正确。用户要求先一只真实配置，目标ID和现场条件确认待回复；不执行其余14槽位。板端Tools/Studio/代理均NOT_RUN。本次只更新已有指南，没有新增独立指南。完整证据logs/tools-workbench-usb-install-20261103T095404Z/installation-result.json。

最终原生发包复核：发现新增Drive Mode计划尚需同时加入原生配置白名单，已补齐寄存器10；先用假描述符复现失败，再115项回归通过。正式安装42文件再次校验后已重启为PID69941，旧预发布包及旧安装文件留在同一logs证据目录。用户已指定单只ID24并确认现场条件，仅授权先只读；自动只读计划被harness/audit.py:308硬件门控拒绝，未打开串口。操作者可在正式UI手动绑定并预览；不绕过审计门控，写入/运动仍NOT_RUN。


2026-11-03板端实际部署完成：Tools/Studio0.6.0，Python3.13.5；175负载/137源码校验，ToolsPID4352，StudioPID7135、助手5242。Studio改8444以保留mediad8443，严格TLS CA/叶扩展经Journal更新，Mac3.14严格验证HTTP200；公开CA指纹82:2F:8C:82:A1:95:52:D8:15:86:C5:09:BE:FE:B8:D4:FE:49:35:3D:57:5A:78:13:34:B0:58:C4:34:1E:23:E3。原daemonPID1007/mediad1208及native哈希、unit、robot配置和校正回执不变。控件/Camera关闭，15关节健康新鲜；原生身份跨UID核验被/proc拒绝，校正保存保持只读，未放宽权限。USB工作台已停、0配置；无舵机写入或运动，未设开机自启；Studio显示端信任CA及配对仍需本人完成。冻结36de9bb安装器端口与初次CA问题按原指南受控补充处理，不宣称原setup可直接无补充复用。证据：/Users/bou/Workspace/Duckduck/logs/tools-board1-install-20261103T104500Z/summary.json

用户要求暂停并保存：后续远程操作、测试、推送停止；已有服务保持当前状态。板端部署已完成的摘要与待办见原逐条指南及logs/tools-board1-install-20261103T104500Z/summary.json。外层便携归档尚未包含本轮板端实装登记，待通知继续后再刷新；当前目录清单按暂停保存状态更新。


## 2026-11-03恢复完成：SSH回环HTTP、实际配对与ToF故障

新IP192.168.1.56，原主机密钥核验一致。用户选择电脑优先SSH+本机HTTP，Studio入口http://127.0.0.1:8790/studio/；板端仅127.0.0.1:8790，mediad8443保留。新增deploy/studio/studio-ssh-http.py和pair-code.sh，经原Journal登记启动器、unit和配置；137份冻结源码不改，files_verified=true。controls/camera关闭，普通账号和资源组保留；58项Studio测试及shell语法通过，HTTP200/未登录401/跨Origin403通过。本人执行pair-code.sh并在实际IAB配对，LIVE总览、3D/IMU/15关节反馈已验证，无控制权，请求/应用速度均0。命令和配置路径直接更新原逐条指南，不新增指南。

Tools active PID2174、Studio10262/助手2185；robotd1071、mediad1220未被本轮重启，原生程序/配置哈希保持，回执跨此前重启仅pid变化。普通Tools原生校正验证跨UID/proc拒绝保持只读，不放宽权限。未启动工作台，未写舵机、未运动，未设置开机自启。

ToF未通过：tofd0.18.3 PID753 active/NRestarts0，曾发现VL53L5CX/0x29并15Hz测距，板端19:49:22记录停止回应；当前tof.stream返回“the sensor stopped answering; retrying”，不是单纯前端断开。fuser快照仅见tofd持有i2c-3；尚不能归因升级或确定供电/接线/I2C/驱动根因。Studio通用不可用未呈现完整原文，登记为限制。ToF重启、总线扫描和硬件恢复NOT_RUN，后续在板端初始化范围继续；active或工具安装通过不能冒充传感器验收。

此前HTTPS显式CA严格验证通过；macOS信任命令超时、CLI NOT_TRUSTED，后来IAB曾可打开HTTPS配对页，不宣称全部信任检查通过。用户选择HTTP后无需继续CA设置；原证书/私钥与https-before-loopback-http备份保留，HTTPS8444停止监听。证据logs/tools-board1-resume-20261103T114916Z/resume-status.json；回归321dd5c5842448f3、HTTP部署f78d01181e0e45ae、ToF原文079a551e5eea4aa3、持有者b5180ec832cd4a9f。外层历史tar.gz未刷新，本地解包交付清单已更新；本轮不推送。

## 2026-11-03：默认LAN HTTP与一键访问码（待上板）

用户要求默认HTTP/0.0.0.0、不与duck服务冲突、推送PR；随后确认板端暂时关闭，先完成代码/PR。本轮首次SSH超时，未进行远端变更。新setup默认lan-http/8790，--port实际生效、拒绝8443/8765/8766并检查占用；默认不生成CA，HTTPS可选、旧无transport的HTTPS配置兼容。精确Host/Origin、认证和CSRF保留，HTTP仅可信局域网，不能替代安全上下文。

已有Studio用http-configure.sh登记兼容启动器和pair-code.sh，冻结运行包/venv/资产不覆盖，功能开关不变；保持停止，明确start。network-rollback只恢复网络字段/unit，保留独立功能设置，中断和漂移均验证；不触碰robotd/tofd/mediad/工作台。76项Studio回归和本地冻结源码HTTP认证全流程通过；首次夹具失败和审计误判均保留。目标板LAN访问/应用/回退NOT_RUN，ToF未修复。

原逐条指南三份同步，未新增指南；本轮网络源码归档置于既有交付packages/studio-lan-http-20261103，原transfer/旧包保留。验证logs/tools-studio-lan-http-20261103T132518Z/http-smoke.json；审计20261103T132518Z-01cb64a497cc。PR沿用#8并重写说明，不合并。

交付收尾：本轮更新[PR #8](https://github.com/superobk/microduck-tools/pull/8)，待审阅、未合并。首版HTTP代码95faba5的[Python安装CI](https://github.com/superobk/microduck-tools/actions/runs/41647543406)和[工作台CI](https://github.com/superobk/microduck-tools/actions/runs/41647543423)通过；随后发现并修正可选SSH安装的监听绑定，当前76项Studio本地测试通过。最终源码提交、142负载网络包SHA和最新CI链接以交付packages/studio-lan-http-20261103/NETWORK-UPDATE.json及PR Checks为准；之前的网络包保留在该目录history中，不能混作当前包。原176/175传输清单保持，板端仍关闭、LAN应用/回退NOT_RUN。指南同时列出microduck-tools启动以供原校正代理使用，不设开机自启。
