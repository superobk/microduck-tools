# 0.16.3 → 0.18.3：脚本升级路径

**使用Tools0.5.0完整交付包，在bundle目录运行 `sudo ./duck.sh`。** 菜单依次选择“检查、备份、安装、配置、启动”，每步显示当前状态、失败原因和下一步。完整说明见[简明指南](script-manager.zh-CN.md)。本页命令均为待执行清单，板端仍NOT_RUN，等另行通知。

## 先准备好，再进菜单

- 使用含官方0.18.3签名包、两个已编译校正程序和Tools的完整bundle。Git源码和轻量runtime包不能单独用于正式升级。
- 原系统须为0.16.3，原校正文件可迁移；安装方向和gyro比例保持原值，不猜测16.4或IMU轴向。
- 先从独立Linux救援系统启动，原盘未挂载，用 `sudo ./image.sh backup --apply` 创建外部系统镜像及 `.proof.json`，再启动原0.16.3系统。脚本询问设备/镜像路径并校验，不假设设备名。

完整bundle放在板端以下目录后，进入菜单：

```bash
cd /var/lib/robot/manual-upgrades/manager-0.5.0
sudo ./duck.sh
```

## 升级按这条路径走

菜单可完成全部步骤。偏好独立命令时，在同一bundle目录逐条执行，上一条成功再继续；不要整表作为批处理运行。

| 顺序 | 命令 | 做什么 / 怎样知道可以继续 |
|---|---|---|
| 1 检查 | `sudo ./check.sh` | 校验固定输入、动态库/ONNX和原校正；通过后再备份，不停止服务 |
| 2 备份 | `sudo ./backup.sh --apply` | 询问外部镜像位置，核验后停止服务，保存应用与配置；状态为“已备份，待安装” |
| 3 换官方套件 | `sudo ./install.sh --apply` | 用原可信更新器预演并安装官方0.18.3；状态为“官方已安装，待配置校正” |
| 4 接校正和Tools | `sudo ./configure.sh --apply` | 迁移真实校正、安装配套程序、设置持久用户层；状态为“已配置，待启动” |
| 5 启动 | `sudo ./services.sh start --apply` | 启动所选robotd和Tools；不执行init/enable/home或运动 |
| 6 验证 | `sudo ./status.sh` | 分别看官方文件SHA、实际进程、运行时校正与插件；持续观察加 `--watch` |

文件保存、进程启动都不等于校正已加载。必须看到“运行时校正：已加载并一致”；无真实舵机/IMU回执时保持待验证。硬件及运动验收仍需另行安排。执行确认前须稳固支撑、舵机动力物理断开；no-policy不能代替断动力。

## 配置调整与后续加载

| 目的 | 命令 | 意义 |
|---|---|---|
| 调整IMU/关节 | `sudo ./configure.sh web --apply` | 临时维护Web写配置；退出后恢复普通Tools，不重启robotd |
| 加载保存值 | `sudo ./services.sh reload --apply` | 明确停止再启动robotd/Tools，然后再次查看status |
| 健康插件关闭 | `sudo ./configure.sh --servo-health off --apply` | 已配置时只改插件开关，必要时重启Tools；开启将off换on |
| 相机允许应用 | `sudo ./configure.sh web --camera-apply --apply` | 另行允许启动mediad；页面确认应用后会重启mediad、中断视频 |
| 停止 | `sudo ./services.sh stop --apply` | 停相关机器人服务，保留文件和版本 |

Web隧道、页面操作、配置路径与生效字段见[简明指南](script-manager.zh-CN.md)。常驻Web非root，IMU/关节保存使用临时维护Web。

## 失败后用对应脚本，不重复安装

| 情况 | 命令 | 结果/边界 |
|---|---|---|
| 断线或操作未完成 | `sudo ./recover.sh --apply` | 核对实际版本与原事务，不再次提交安装；busy/pending时等待原事务 |
| 安装前取消维护 | `sudo ./rollback.sh --to session --apply` | 仅尚未提交安装且仍为原0.16.3时恢复原服务；备份失败先recover再撤销 |
| 退回同版官方 | `sudo ./rollback.sh --to official --apply` | 使用官方0.18.3 robotd并停止Tools，保留no-policy和原校正文件 |
| 完整回原0.16.3 | `sudo ./image.sh restore --apply` | 只在独立救援系统核验原盘/镜像后恢复整盘；不是在线应用回退 |

独立修改脚本不加 `--apply` 时只预览，执行还需终端确认。不要删会话标记、杀更新器或重复apply。每个会话继续使用同一完整bundle；不要替换其中tool目录。自动升级在维护配置中保持关闭，原updater配置保留。

## 程序是否准备齐全

官方13个ARM64程序、两个native校正程序均已备齐。native复用GLIBC2.30目标产物，ELF最高需求2.29；Tools为Python3.11+源码运行包，无需板端编译。产物齐全不代表该板运行库已兼容，check与安装后复查仍需执行。固定输入见[inputs.json](../deploy/manual-upgrade/inputs.json)，部署状态见[登记](deployment-register.zh-CN.md)。

旧[0.4.3手工流程](manual-upgrade-0183-0.4.3-history.zh-CN.md)仅供审阅历史。其session.env、RUN/BUNDLE变量与新管理会话不能混用；当前执行入口统一为本页脚本与简明指南。
