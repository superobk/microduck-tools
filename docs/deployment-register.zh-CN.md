# 部署登记

| 工具版本 | 目标 | 准备和软件验证 | 板端状态 |
|---|---|---|---|
| 0.5.0 / 2026-10-31 | 固定0.16.3→0.18.3的菜单/任务管理 | 十个入口脚本、默认预览、分层运行状态、外部备份、独占更新锁、安装意图日志、中断恢复、同版官方/安装前撤销/救援镜像回退；236项Python通过无跳过，其中46项管理器测试；原生程序及核心Web/校正未改 | 板端安装、服务、总线、镜像与物理验收全部NOT_RUN |
| 0.4.3 / 2026-10-31 | 当前0.16.3直接到固定0.18.3 | 六步手工指南；故障/中断/镜像恢复；13+2程序固定输入；GLIBC2.30目标重建native，ELF最高要求2.29；190项Python通过无跳过，含26项本轮助手/文档测试和两处真实本地SIGKILL；原生补丁和核心校正/Web未改 | SSH、运行库加载、安装、服务、升级、回退、相机/IMU/性能均NOT_RUN，等另行通知 |

**当前入口：[0.16.3→0.18.3脚本升级路径](manual-upgrade-0183.zh-CN.md) / [菜单与任务脚本](script-manager.zh-CN.md)**，完整交付目录 `/Users/bou/Workspace/Duckduck/deliverables/microduck-tools-manager-20261031`，在bundle里运行 `sudo ./duck.sh`。独立check/backup/install/configure/status/services/rollback/recover/image脚本共用持久会话；旧手工包保留历史，不混用。普通Web非root；校正写入使用有时限的临时维护Web，退出恢复普通服务，robotd仅在明确reload时重启。

历史手工审阅入口：[0.4.3手工主流程](manual-upgrade-0183-0.4.3-history.zh-CN.md)、[回退与中断](manual-upgrade-recovery.zh-CN.md)、[镜像恢复](manual-upgrade-image.zh-CN.md)、[审查问题与修复](manual-upgrade-review.zh-CN.md)。固定程序SHA与源码身份在 [inputs.json](../deploy/manual-upgrade/inputs.json)。

程序准备：官方13个ARM64程序来自固定官方签名归档；两个自定义校正程序已交叉编译；Tools为Python/Web源码运行包，无需板端编译。上述软件可交付不等于该板已经可运行，前后动态库/ONNX和进程身份仍须实测。

当前完整安装包位于 `/Users/bou/Workspace/Duckduck/deliverables/microduck-tools-manager-20261031`；旧0.4.3手工包为 `/Users/bou/Workspace/Duckduck/deliverables/microduck-0183-manual-review-20261031`，仅留历史。按现有仓库规则只提交源码、手册、模板、输入摘要和测试，不提交真实配置/账号/系统镜像、巨大编译缓存或二进制；本地交付包含已编译候选、固定官方包和证据。旧0.4.2及第一版直升归档保留作历史，不再执行其中整体搬移系统目录的恢复循环。

2026-10-31 文档入口同步：README、0.18.3部署页与旧回退/镜像/Tools页统一指向简明指南的脚本命令；旧六步全文单列历史。脚本和固定程序未改，继续使用冻结的0.5.0完整bundle，不替换正在进行会话的tool目录。26项既有升级审查测试通过，新增文档链接/命令入口本地校验通过；指南增量、校验和源码快照留存在 `deliverables/microduck-tools-upgrade-path-20261031`；板端仍NOT_RUN。
