# 0.4.2 dry run可靠性审查与修复

2026-10-31。审查基线0.4.1 / `d72e3df`。本轮直接修复脚本、补充测试；不连接板端、不改变正式机器人核心功能或原生候选。

| 发现的问题 | 0.4.2处理 | 对应验证 |
|---|---|---|
| 固定 `.state.json.tmp` / `.current-next` 在中断后可能阻塞下一次保存或回退 | 唯一临时文件/链接、原子替换、父目录fsync；遗留文件不被复用或误删 | 两个真实本地SIGKILL子进程留下残留，再恢复成功；写入错误保持旧JSON |
| systemctl返回成功不等于脚本已核验停止；客户端超时后后台Job或cgroup任务可能仍未结束 | 停止后验证MainPID/ControlPID为0、inactive/failed、Job结束；cgroup尚存在时要求TasksCurrent为0，无法核实时拒绝；所有start/stop用job-mode=fail | 停止不生效、主PID已死但cgroup仍有任务、停止/启动超时、待执行任务及手动恢复 |
| unit文件和drop-in检查不能排除旧的管理器缓存、服务别名或其他unit配置的依赖联动 | init前确认名称未被使用；每次服务操作前重新核验LoadState/Names/NeedDaemonReload/沙箱，拒绝正反向Requires/Conflicts等非预期关系 | 别名、待重载、外部关系、root用户、错误KillMode等逐项拒绝 |
| 回退可停止一个身份未经检查的进程 | 停止前要求可执行文件属于测试版本目录、完整参数为固定fake命令 | 外部命令/PID身份不符时不发送stop |
| socket路径不保证响应来自刚验证的进程，部分行可长期拖延读取 | Linux SO_PEERCRED绑定MainPID、拒绝软链接/被替换socket；整个IPC交换有绝对期限 | 错误对端未收到任何RPC、软链接拒绝、慢速碎片超时、只读RPC集合 |
| 通用robotctl包装入口可被用于访问默认正式updater接口 | 包装入口只接受Web精确的只读monitor参数，其他命令/附加参数退出2 | apply、robot init、health及socket注入均拒绝；允许调用保持测试socket |
| 恢复状态结构校验不足；日志写失败可能丢失已执行结果 | 校验pending.from与current一致，恢复拒绝事务外链接；报告execution_ok/evidence_error，不因日志失败隐式回滚 | 非法状态、非事务版本、日志磁盘失败、循环停止、中断退出130 |
| umask可能令目录不可读或配置可被其他用户修改 | 受管路径核验所有者/禁止组与全局写，目录/配置显式设置权限 | 限制性umask仍可遍历、可写配置/根目录在服务操作前拒绝 |

切换中断矩阵在记录pending、停止、切链接、启动、验证、提交状态的前/后12个位置中断；恢复矩阵在停止、切链接、启动、验证、清pending的前/后10个位置中断。未提交时显式rollback恢复原已通过版本；提交已完成时保留并验证已提交版本。恢复本身再次中断可重试，previous历史保留。SIGINT/SIGTERM按中断处理返回130，不自动执行额外恢复；SIGKILL依靠已持久化的事务状态。

恢复时先确认停止失败实例，再校验/选择回退文件；若文件在切换过程中变坏，保留pending且不使用坏文件。正常新版失败但旧版恢复成功仍返回非零；若失败发生在审阅/前置校验，不启动/停止其他服务。初始化中断则保留部分环境并拒绝再次覆盖，需人工审阅，不自动删除目录。

**隔离结论有明确条件：** 脚本只向固定测试unit发送start/stop，拒绝别名、配置漂移及可引起对外联动的已加载关系，不调用正式服务的控制/更新接口。unit或关系不可核实时默认拒绝。它不防御同时操作systemd/修改文件的其他root管理员，也不承诺CPU/内存/控制时序开销为零。Linux板端真实systemd属性、任务行为、沙箱与运行库尚未执行；本地mock及macOS进程不能替代这层验收。

实现依据采用官方[systemd 249 Unit接口](https://www.freedesktop.org/software/systemd/man/249/org.freedesktop.systemd1.html)、[依赖关系说明](https://www.freedesktop.org/software/systemd/man/249/systemd.unit.html)及[Linux UNIX socket凭据](https://man7.org/linux/man-pages/man7/unix.7.html)。核心属性缺失时拒绝，不自动删除隔离检查来兼容；新版本若提供Upholds/UpheldBy也检查为空。

使用方式见[部署和演练指南](dryrun.zh-CN.md)。完整本地日志、源差异、归档与哈希按项目规则保留在本地deliverables；**板端仍NOT_RUN，继续等待用户另行通知。**
