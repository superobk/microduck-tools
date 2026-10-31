# 用菜单管理安装、配置、状态和回滚

**日常只记一个入口：在完整交付包的 `bundle` 文件夹运行 `sudo ./duck.sh`。** 菜单显示当前状态、失败原因和下一步。无需维护环境变量，不再照旧六步指南逐段粘贴。仓库和轻量runtime包只有源码；正式安装请用deliverables内含官方/native程序的完整bundle。

本指南对应 Tools **0.5.0**，固定 **0.16.3 → 0.18.3**。本轮只做本地开发与测试；板端安装、服务、镜像、硬件全部 **NOT_RUN**，仍等另行通知。原三版本隔离演练继续用 `microduck-dryrun.py`，不要与正式管理目录混用。

## 第一次使用

1. **先有退路。** 用独立 Linux 救援系统启动，原盘保持未挂载；在完整包的 `bundle` 内运行 `sudo ./image.sh backup --apply`。向导列盘，询问原盘和外部镜像路径，执行读盘、完整SHA、逐字节比较。镜像和 `.proof.json` 一起保存在另一块物理介质。显示dd进度，可能较久。
2. **回到当前0.16.3系统。** 完整 `bundle` 放到板端 `/var/lib/robot/manual-upgrades/manager-0.5.0`，接回镜像介质。需要 Python3.11+、systemd、GNU tar/zstd、ldd、lsblk、blockdev、dd、cmp；脚本不自动安装基础工具。上传使用本次确认的SSH地址，不能沿用旧地址。
3. **打开菜单。** 以下均为板端待执行命令，本轮未执行。

```bash
cd /var/lib/robot/manual-upgrades/manager-0.5.0
sudo ./duck.sh
```

正常顺序：**1检查 → 2备份 → 3安装 → 4配置 → 5启动**。每次完成会重新显示状态；不必一次做完。`0`退出，断线后重新打开同一包。

备份会停止机器人服务，将应用/配置保存到外部镜像旁。原校正必须是真实可迁移的文件；脚本不拿demo替代，不猜旧定制程序里编译的16.4。原比例非默认时需核实MCU契约，再按提示输入 `SCALE-CONFIRMED`。只知道“芯片面朝上”不足以生成安装四元数，升级保持原方向与比例。

执行会显示操作范围并要求输入 `EXECUTE`。机器人须稳固支撑、舵机动力物理断开。启动robotd会打开总线；no-policy不能代替断动力。

## 不用菜单时，每个脚本只负责一类事

除check和status外，默认只预览，加 `--apply` 才执行，仍需现场确认。无 `--yes` 或强制绕过入口。

| 想做什么 | bundle内命令 | 意义与影响 |
|---|---|---|
| 菜单 | `sudo ./duck.sh` | 选任务、看状态和下一步 |
| 兼容检查 | `sudo ./check.sh` | 核对包、动态库/符号/真实ONNX API、服务与旧校正；只写诊断缓存，不停服务 |
| 应用备份 | `sudo ./backup.sh --apply` | 询问已核验的原系统镜像，停止服务，保存外部应用备份；不换版本 |
| 安装官方版 | `sudo ./install.sh --apply` | 原可信更新器验证签名、预演并安装固定0.18.3；维护期间自动升级关闭，原updater配置保留 |
| 接入校正和Tools | `sudo ./configure.sh --apply` | 迁移真实校正、安装配套native/Tools、设置用户层指针；保持停止，等待启动 |
| 启动 | `sudo ./services.sh start --apply` | 启动所选robotd；校正模式同时启动Tools并设其开机自启；不init/enable/home/运动 |
| 查看 | `sudo ./status.sh` | 只读检查文件、实际进程、回执与插件 |
| 持续查看 | `sudo ./status.sh --watch` | 每2秒显示状态，Ctrl-C退出；另一终端也可查看执行进度 |
| 加载保存的校正 | `sudo ./services.sh reload --apply` | 明确停止再启动所选robotd/Tools，重新打开总线；然后核对回执 |
| 停止 | `sudo ./services.sh stop --apply` | 互斥停止相关机器人服务；保留版本与配置，不等于物理断动力 |
| 中断恢复 | `sudo ./recover.sh --apply` | 核对实际版本与未完成事务，不再次提交安装 |
| 退回同版官方 | `sudo ./rollback.sh --to official --apply` | 使用官方0.18.3 robotd、保留no-policy；Tools停止，校正文件保留 |
| 安装前撤销 | `sudo ./rollback.sh --to session --apply` | 只在尚未提交安装且仍为原0.16.3时可用；恢复本工具文件和原先运行服务；备份中途磁盘满也可先recover再撤销 |
| 完整回原系统 | `sudo ./image.sh restore --apply` | 只在独立救援系统运行，校验原盘和镜像后覆盖；不在线降级冒充完整回退 |

健康插件沿用现有配置，全新配置默认启用。关闭用 `sudo ./configure.sh --servo-health off --apply`，开启换为on。已配置时仅修改插件开关，必要时重启Tools，**不重启robotd**。不带开关重复配置不会重置Web保存值。退回官方后再配置可恢复校正模式，保留已有校正。

## Web怎么改、怎么生效

普通Tools是非root的localhost服务，适合查看、健康监测和相机设置保存。**IMU/关节写入使用临时维护Web**，不扩大常驻服务权限。

1. 已配置并启动校正模式后，菜单选 `4 → 1`，或运行 `sudo ./configure.sh web --apply`。暂停普通Tools，临时打开localhost维护Web，robotd保持运行。
2. 在Mac另开终端建立隧道，把地址换成本次确认的目标，然后浏览器打开 `http://127.0.0.1:8765`。隧道只转发端口，不安装程序。

```bash
ssh -N -L 8765:127.0.0.1:8765 USER@BOARD_IP
```

3. IMU页调整方向/比例、关节页调整映射，先预览再按页面提示确认保存。**保存只是写文件。** 完成后回维护终端按Ctrl-C，普通Tools自动恢复，不自动重启robotd。
4. 要加载IMU/关节保存值，再运行 `sudo ./services.sh reload --apply`。用status检查“运行时校正：已加载并一致”。无真实总线/IMU回执仍为待验证，不能用DEMO替代。

相机页保存0/90/180/270只保存插件设置。需要应用时，使用 `sudo ./configure.sh web --camera-apply --apply`，并在页面再次确认应用；会启动/重启mediad、中断视频。默认不允许通过页面启动相机。结束维护会复制设置给普通Tools；临时启动的mediad恢复到原先停止状态。相机生效看其实际进程和新图片方向，不能用IMU回执代替。

维护Web最长1小时，退出或启动失败后恢复原先运行的Tools。Linux设置父进程死亡信号，避免管理器被杀后遗留特权Web；内核行为仍待板端验证。中断后用recover，不另开第二个Web。维护健康日志放私有维护目录，与普通诊断分开。相机文件若被外部同时修改，保留原文件和维护副本、尝试恢复普通Tools，并报告冲突，不自动覆盖。

## 怎样读状态

下面是**合成示例**，不代表板端结果：

```text
当前：已配置，待启动
  [文件SHA已核对] 磁盘官方版本 — 0.18.3
  [已停止/未知] 机器人进程
  [已保存（仍需运行回执）] 校正文件/用户指针
  [待验证/未加载] 运行时校正
下一步：./services.sh start --apply
硬件状态：待现场验收
```

| 显示 | 代表什么 | 下一步 |
|---|---|---|
| 待备份 | 尚无可核验退路 | 镜像完成后选备份 |
| 备份未完成，原服务可能已停止 | 备份停止服务后未完成 | 继续备份；取消则recover后用session撤销 |
| 已备份，待安装 | 版本尚未换 | 安装 |
| 官方已安装，待配置校正 | 官方套件已换，未接校正 | 配置 |
| 已配置，待启动 | 文件和指针已保存 | 启动 |
| 进程已启动，等待真实回执 | 服务在跑，加载尚未证明 | 查实物；无舵机演练停在这里也正常 |
| 校正已加载；插件和硬件分别查看 | 官方完整性、PID/程序SHA、可信构建、用户层和Web回执一致 | 分别看插件/相机，物理验收另行安排 |
| 正在执行 | 另一个脚本仍持锁 | 等待原任务，看状态 |
| 中断/未完成 | 上次未结束，不代表升级已停 | 恢复；不重复安装 |
| 官方模式（未加载校正） | 同版回退完成 | 要恢复工具就配置再启动 |
| 备份介质离线 | 镜像当前不可访问 | 接回原介质；安装前可用backup重新绑定同一SHA的新路径 |

状态另显示镜像、插件“已保存/运行中”、失败步骤。busy/pending、版本未知或安装提交后仍为旧版时，会保留证据并停止，不自动重试；需要完整回退就用救援镜像。不要删除会话标记再安装。权限不足时用sudo查看，私有记录读不到不等于损坏。

## 文件位置与准备状态

| 路径 | 用途 |
|---|---|
| `/etc/robot/robotd.toml` | 官方基础配置，管理脚本不改 |
| `/var/lib/robot/robotd.toml` | 0.18.3用户层，指向校正文件 |
| `/var/lib/robot/calibration/microduck-0.18.3.toml` | IMU安装、比例、15关节零位/方向；保存后明确重载 |
| `/etc/microduck-tools/plugins.json` | 健康插件开关，Tools启动读取 |
| `/var/lib/microduck-tools/servo-health/` | 普通Tools规则、基线和诊断 |
| `/var/lib/microduck-tools/camera.json` | 相机保存值，不是生效证明 |
| `/etc/systemd/system/mediad.service.d/90-microduck-tools-camera.conf` | 相机明确应用后生成的mediad参数 |
| `/opt/robot/daemon/current` | 固定官方0.18.3套件 |
| `/opt/microduck-tools/native/current` | 自定义校正robotd/robotctl，与官方分开 |
| `/opt/microduck-tools/current` | Tools0.5.0源码运行包，Python执行，无需板端编译 |
| `/var/lib/microduck-tools-manager/` | root私有会话、锁、安装日志、维护副本；不要删掉绕过恢复 |
| 镜像旁 `microduck-backup-*` | 应用tar、原/迁移校正与备份登记；不替代整盘镜像 |

官方13个程序来自固定签名归档；两个native复用已交叉编译的GLIBC2.30目标产物，ELF最高需求2.29，本轮未重编。**产物齐全不代表该板能加载**，check及安装后检查不可省。只用签名离线更新器，不自动切latest/bootstrap强制安装。自动更新维持关闭，实机验收后另行决定恢复策略。

脚本只管理自己的覆盖和插件文件，不整体搬移系统目录。完整回旧系统必须恢复镜像，包含系统依赖及启动项。本地测试使用临时目录、假systemd/更新器；不证明真实安装、Linux父进程死亡处理、服务联动、断电持久性或磁盘写入已实测。
