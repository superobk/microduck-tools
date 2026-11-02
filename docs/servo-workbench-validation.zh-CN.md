# 工作台0.1.0：验证、交付和现场边界

2026-11-02；功能分支 `feat/servo-workbench-v1`。Tools主版本0.6.0，工作台独立0.1.0。上一轮PR #6已核实合入main；本轮不合并新功能PR。

## 本地通过

| 检查 | 结果 / 范围 |
|---|---|
| 新工作台Python | 82项通过：双协议编解码、官方SDK交叉对照、15只逐次上下电、整组掉电/参数丢失、USB消失改名/歧义、读写中断、Reset Mid无效、有限动作取消、参数回退、修订/代次/幂等、两浏览器、权威重启、模式隔离、EOF冻结、旧SSH reader、代理与安装白名单、有界诊断导出 |
| 既有校正/升级Python | 236项通过；包含原Web/插件/相机、三版兼容、升级/回退/中断恢复 |
| 既有安装向导Python | 37项通过 |
| Studio Python | 41项通过（40项原功能+历史报告导入隔离）；报告导入不调用helper或IPC |
| Node | 11项通过，原输入/反馈功能保持 |
| 浏览器 | 实际演练创建、稳定识别、备份预览、单只确认、换机自动预览、刷新与后台重启恢复1/15；USB拔出禁用旧确认、改名后只读恢复，数量不变 |
| 响应式 | Playwright CLI0.1.23 + 本地Chromium缓存，1280/768/390宽度无横向溢出，三张最终截图；桌面、平板及手机布局已人工查看 |
| 静态 | JS语法、shell语法、Git diff空白和manifest核对，交付逐文件哈希及解包验证另存本地交付记录 |

共396项Python与11项Node，无测试跳过。实际本机为macOS/Python3.14.7；最低声明Python3.11，另提供macOS/Windows/Linux + Python3.11 CI，托管CI以PR实际结果为准，不能把本机结果称作Windows通过。

首次托管Windows CI发现安装测试夹具的write_text被系统换行转换为CRLF，造成预期哈希与文件字节不一致；已改为固定UTF-8字节写入，保留生产校验强度。Linux工作台82项通过，macOS测试步骤通过但被矩阵fail-fast取消；后续设置三个平台独立完成，最终状态与运行链接登记在交付release-record，初次失败不覆盖。

完整原始输出位于工作区中央审计session `20261102T075521Z-100f26a21344/commands/`，关联运行证据 `logs/servo-workbench-20261102T075521Z/`。本轮早期Plan只读核查与压缩恢复时README直读缺口已如实note，未补造工具收据。测试中预期错误HTTP响应与ResourceWarning不等于产品失败；初期loopback sandbox权限、SDK stuffing参考差异、状态落盘失败及浏览器截图超时均保留原记录，修复/替代后重新验证。

写中断覆盖：写前intent、发包后、应答缺失、读回后、完成提交前；本地子进程 `os._exit(19)` 后SQLite integrity_check为ok，重开只读，不重发。该实验不是物理断电、不证明存储硬件掉电保障。

自动发现模拟测试验证三次有效回应跨度≥500ms，并通过本机HTTP/browser观察稳定后进入待确认。已知设备约3秒是适配器待测目标；不提前宣称USB/板端达标。

## 实机仍待验收

以下均为 **NOT_RUN**：真实USB适配器及驱动、Windows电脑运行、实体型号/固件、双协议真实包时序/半双工、Linux root全FD独占和systemd真实状态、SSH重启重连、整组电源过程、EEPROM持续性、保护阈值、硬件Reset Mid、扭矩/动作轨迹、真实速度/电流/资源性能、Studio板端部署/联动。真实执行继续等待另行通知。

所有状态和报告保留证据来源；“规则未发现异常”与“配置读回一致”都不能证明额定扭矩、寿命、机械装配或机器人已可运动。模拟运行不会打开串口/SSH，HD1910维护支持不代表原daemon新增Feetech驱动。

## 交付与兼容

独立ZIP/tar.gz包含源码、标准库网页/SQLite向导、通用pyserial3.5离线wheel、固定SHA、受限板端代理及安装预览、指南与恢复说明。Python解释器及USB驱动需预装；无需编译daemon。测试依赖dynamixel-sdk3.8.4只用于开发对照，不进入运行依赖。

电脑入口 `workbench.sh` / `workbench.cmd`，板端安装默认预览且不创建自启动服务。Studio报告入口新增于运行源码；既有冻结Studio/升级归档不会自动增加新报告页，更新该页须后续明确部署当前源码/运行包。原核心校正、daemon候选及原生补丁、冻结0.5.0升级包保持原样；本轮没有重新编译机器人程序。

本地正式交付目录：`/Users/bou/Workspace/Duckduck/deliverables/microduck-servo-workbench-20261102/`，以其中manifest、release-record和逐文件SHA为准。源码提交与PR链接完成推送后登记在交付release-record；中央审计不自动公开上传。
