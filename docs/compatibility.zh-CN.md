# 兼容性与部署：Microduck Tools 0.4.0

本版本完成软件缺陷修复与新版适配实现。所有硬件采集、部署、回滚及性能验收仍需独立执行与登记；本仓库的软件测试不代表实机已验证。

## 支持范围

| 入口/能力 | 软件支持 | 边界 |
|---|---|---|
| `--target daemon-v0.16.3` | 保留既有校正、相机、监测流程 | 写入要求匹配的定制daemon生效快照；原有确认与备份保留 |
| `--target daemon-v0.17.8` | 保留原有精确版本路径 | 此入口不等于官方原版支持校正；原生补丁仍单独验收 |
| `--target daemon-v0.17.9` | 官方监测/相机；配套 calv3 支持 IMU/关节校正 | 官方原版禁写；原生写入需匹配回执、文件权限及确认 |
| `--target daemon-v0.18.3` | API47、分层配置、官方相机CLI与配套原生校正 | 用户层保存；拒绝旧/错误原生身份；官方未补丁版仍禁写校正 |
| `--target auto` | 实际hello/model能力探测、可信原生目标解析 | 未知API、拓扑或目标不匹配时关闭规则分析，保留原始遥测及原因 |
| API 41/44/45/47 | 已评审的监测字段契约 | 45依据固定main快照，不代表所有后续版本；均待板端验收 |

固定上游依据：0.17.9为`96b89d8613f06722c1d64ce75ab0d7124fed9d8f`；main为`5def2f3e66501f0f365d4fb1183020b8ee875443`。0.17.9 原生补丁已单独扩展，保持 API44。详见[原生校正部署指南](daemon-0.17.9-calibration.zh-CN.md)；官方未打补丁的 0.17.9 仍无此契约。

插件运行后端须位于机器人本机，`t_ns`与本机monotonic属于相同时钟域。远程浏览器通过SSH隧道访问，不把机器人时间戳直接与另一台电脑时钟比较。

## 插件配置与扩展

使用`--plugin-config`读取独立JSON：

```json
{"schema":1,"servo_health":{"enabled":true}}
```

未知schema或插件键拒绝加载，原文件保持不动；宿主会停用该可选监测并显示原因，原校正流程继续可用。命令行`--enable-servo-health`可显式启用；不提供配置且不传该参数时保持关闭，关闭不启动观察/RPC/日志任务。规则、基线及维护报告保持独立版本；本轮不会自动改写旧参考。

后续增加适配器时，在`daemon_adapter.register_profile()`注册经过源码和契约测试的API；单位/字段语义变化时实现新的解析适配器，不仅扩大版本列表。RPC边界只允许hello、robot.model和robot.health。新的功能插件通过明确内置模块注册及host schema升级加入；不支持从网页安装或执行任意Python文件。

基线绑定设备、daemon身份、模型/关节顺序及配置/校正摘要。生产模式缺少可信身份或旧参考无身份时，不启用参考电流规则，需重新确认正常工况并采集。设备默认使用本机machine-id的摘要，不导出原文；特殊环境可在servo_health配置中显式设置`device_id`，其身份为操作者声明。`calibration_hash`覆盖项也由操作者负责维护，通常应使用自动文件摘要。

汇总温度/电压没有源采样时间，较低缓存不能解除既有高值告警。界面分别显示最近读数和等待新鲜证据状态；温压告警并非硬件故障定论。电流与卡滞规则仍按新样本、持续时间及恢复窗口处理。

曲线使用字符串纳秒游标和stream epoch增量读取；断流、身份变化、队列溢出时重置连续判定。维护报告按版本按需加载，刷新保留详情展开和焦点。完整JSON/CSV导出仍可用。

## 官方相机适配

0.17.9使用独立`camera.json`保存方向，绝不向官方robotd.toml写入`media.camera_rotate`。Web预览给出对应systemd片段；应用仍需要`--enable-camera-apply`、明确确认及写入系统覆盖文件/重启媒体服务的权限。默认非root服务仅保存工具设置，应用保持禁用。

写入只接受已核验的mediad版本、当前PID、受评审的命令形式、标准vendor ExecStart，以及工具自身的唯一drop-in；其他启动参数或覆盖文件会拒绝替换，需要单独审阅。该限制防止丢失现场参数。不要为绕过限制删除其他维护人员的覆盖文件。

工具拥有的覆盖文件为`/etc/systemd/system/mediad.service.d/90-microduck-tools-camera.conf`。应用依次写入/备份该文件、daemon-reload、仅重启mediad；不会重启robotd。重启失败会返回错误，保留文件与备份供审阅恢复，不会声称成功。

官方`/frame`返回按元数据方向校正的PNG，本页不再旋转。必须核对新PID、角度及实际画面；身份/CLI核验不能证明物理安装方向正确。旧定制0.16.3继续使用原有生效回执和配置路径。

## 独立安装布局与待执行步骤

以下仅为工具宿主的独立安装；原生候选另按 0.17.9 指南分层安装。本版本不自动执行设备部署。

```text
/opt/microduck-tools/releases/0.4.0/
/opt/microduck-tools/current -> releases/0.4.0
/etc/microduck-tools/plugins.json
/var/lib/microduck-tools/
```

1. 构建运行包：`python3 tools/build-release.py --out <新归档路径>`，核验归档SHA和RUNTIME-MANIFEST后再传输。
2. 现场核对Python>=3.11、robot组、robotd版本、robotctl路径及socket权限；解包到新的工具版本目录。原生daemon/current不变。
3. 创建工具专用用户，将其加入robot组；安装plugins.json及专用service模板。模板只以After排序，不启动/重启/停止robotd；相机应用默认未启用。
4. 检查systemd沙箱、状态目录和有效ExecStart，审阅后只切换工具current并启用工具服务。经SSH转发8765到本地浏览器核验。
5. 记录实际hello/revision、数据年龄、基线可比性和部署哈希。未获匹配原生支持的校正写入保持禁用。

robot组可访问整个robotd控制socket，非root身份不提供方法级只读隔离。工具客户端固定RPC白名单，HTTP不转发任意RPC；需要更强隔离时再增加专用只读代理。

相机应用需要额外的明确维护流程：先审阅Web生成的drop-in与已有unit；可由有权限的维护入口应用，或在临时、受控的Web进程上启用原有apply参数。不要给常驻服务任意sudo/systemctl权限。具体实机命令、用户和现场条件应在执行通知后核对。

## 回滚和并发保存

工具升级前记录旧current目标并备份状态目录。回滚只停止工具、还原旧工具current和与旧schema兼容的状态副本，再启动工具；不执行官方daemon回滚。相机回滚只恢复/删除工具自己的drop-in并在独立批准窗口重载及重启mediad，保留其他覆盖。

相机配置采用文件锁、预览版本、备份前后冲突检查、持久化原子替换和回读核验。外部写者不遵循共同锁时仍不能保证绝对串行；避免维护期间多个入口同时修改同一文件。冲突返回错误，不覆盖已经检测到的外部修改。

## 验证

运行`python3 -m unittest discover -s calibration/tests -v`。完整可选SDK测试应在单独venv安装固定requirements-maintenance.txt后运行；该测试拦截实际SDK内存数据包，不打开串口。浏览器需检查真实Blob图片加载、七页、三视口、表单草稿、维护详情保持及官方新版禁写、匹配 0.17.9 原生回执下的保存和待重启状态。

板端CPU/RSS、控制环频率/抖动、媒体方向和真实采样验收尚未执行。插件异常、磁盘失败或浏览器断开不能改变原有控制路径。

0.18.3用户层与持久校正默认路径、降级拒绝、配置移除遮蔽及回滚详见[专用指南](daemon-0.18.3-calibration.zh-CN.md)。仅扩大API白名单不足以支持新版配置；本轮已按固定源码处理分层差异。[上游功能对比](daemon-0.18.3-comparison.zh-CN.md)区分工具已接入的能力与板端OS前提。
