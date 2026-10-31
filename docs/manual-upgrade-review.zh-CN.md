# 0.16.3 直升 0.18.3：第二轮审查与修复

本页为 Tools 0.4.3 的本地软件/手册审查。板端升级、加载、回退及硬件均 NOT_RUN。旧直升归档保留作历史，不再作为推荐执行入口；新入口为[六步手工指南](manual-upgrade-0183.zh-CN.md)。

| 问题 | 为什么不可靠 | 本次处理 |
|---|---|---|
| PATH 中 robotctl 可能是旧 native 或别名 | 同一命令可能连接不同版本接口 | 备份固定官方旧 robotctl；升级后使用绝对路径和实际进程身份 |
| native 构建要求 GLIBC2.35，官方较低 | 官方能启动不表示校正候选可加载 | 相同补丁按 GLIBC2.30 目标重建；读取 ELF DT_VERNEED 表，不再用字符串扫描当 ABI 证据 |
| ldd 没检查 ONNX 的 dlopen 依赖 | 文件名/软链接可能伪报版本或无法加载 | 有超时的子进程读取真实 OrtGetApiBase、GetVersionString 和 GetApi(23)；升级前后都检查 |
| 检查全量程序前后不一致 | 钩子可改运行库，复制也可能不完整 | 固定 13+2 个程序清单/SHA，拒绝空/未知清单；板端前后各检查一次 |
| 直接 stop updaterd 可能截断正在升级的事务 | 状态查询与停止之间仍有竞争 | 先读状态，再用同一个 update.lock 持锁执行 stop；忙则拒绝，不能杀进程或删锁 |
| 启动旧 updater 会恢复事务并可能自动更新 | 维护流程可能被后台强制更新打断 | 先检查无进行中事务；生成仅 auto_apply=off 的维护配置和持久服务覆盖，原配置不改 |
| 停止判断只看 is-active | Job、控制进程、子 cgroup 可能尚未结束 | 独立只读 stopped 检查；区分未安装/查询失败，timer不强求service专属PID字段 |
| 更新完成后还有延迟重启 timer | 单看目标 unit 的 Job 看不到将来的重启命令 | 查 run-* transient timer/service 的实际 ExecStart；自然完成后才停止服务/改配置，不取消未知 timer |
| 原校正放在正式升级后才迁移 | 比例或源文件问题会让流程卡在半升级 | 在 apply 前生成和核对迁移文件，不改真实配置；非默认比例仍需真实契约确认 |
| 整体移动 /etc/systemd/system、/var/lib/robot 做回退 | 涉及无关服务，中断后目录归属不明确 | 撤下该恢复循环；同版撤候选只改本次覆盖，完整版本恢复使用已核验系统镜像 |
| 靠 apply.started 不存在判断没升级 | 断电可丢标记，current 仍旧也可能跑过钩子 | 标记仅提示；按链接、PID/事务/钩子共同判断；助手不建议自动重试 |
| 固定 current.new 阻塞重试 | 链接切换前后中断不易区分 | 工具链接用随机暂存、原子 rename、旧值守卫；已指新目标时幂等返回 |
| unit 直接 cat 写入 | 中断可能留下半份正式覆盖 | 工作区完整文件 → .new → rename；恢复先比较模板 |
| 镜像 guard 依赖 lsblk/grep -q 管道 | pipefail 下 SIGPIPE 可能误判；还未核验同盘存储 | 只读 disk-check 检查 lsblk JSON、挂载/子设备、镜像 st_dev、容量；写盘仍由人执行 |

## 边界

这不是新的自动升级器。upgrade-review.py 不 apply 发布、不启动/停止服务、不操作总线、不自动恢复；只有 manual-config、remember-links 写指定审阅输出，tool-link 明确操作两个工具 current。它拒绝官方 daemon/current。

官方套件仍由可信更新器验签/执行。bootstrap 仅留在故障附录，并明确没有本次自动健康回滚。OS镜像、依赖准备、真实板端身份/方向/比例与运动验收未执行。不能把本地 mock 或合成迁移叫作板端中断恢复通过。

现有 IMU、关节、相机及健康插件核心源码不变；0.4.3 增加审阅助手、手册、输入清单与测试。新 native 的源码基线和校正补丁不变，ABI 编译选项、文件摘要和目录单独登记。
