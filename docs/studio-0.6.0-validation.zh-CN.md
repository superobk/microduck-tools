# Studio 0.6.0 验收与执行登记

2026-11-01；源码基线main `9e0df24e25a75ec77b8085db0deb43ea77bfa6f3`，候选分支 `feat/mobile-studio-0.6.0`。源码SHA、归档和逐文件清单以对应deliverables的最终MANIFEST为准。**本地实现完成，板端全部NOT_RUN。**

## 已执行的软件验证

| 项目 | 证据与结果 |
|---|---|
| 原功能回归 | 236项Python通过、无跳过；原IMU/16.4/关节/Camera/健康插件、三版本dry-run及0.5.0管理器逻辑未改 |
| Studio后端 | 39项通过、无跳过：时间戳重复/回退、数据缺失、超时/归中/切源、身份/健康门槛、原生RPC白名单、HTTP/WS认证/Origin/CSRF、双端上限、演练/日志/导出隔离、最新值邮箱停车不回放旧输入、原Web代理CSRF/移动资源注入、配置漂移与中断恢复 |
| 真正的本地中断 | 两项SIGKILL：安装文件已写但未完成登记、手柄暂停标记已落盘但停止未完成。均在临时目录和假systemd/设备中验证；中断不会自动恢复手柄，明确恢复保留原来源 |
| 客户端逻辑 | 11项Node测试通过：坐标转换、ToF状态/地面/新鲜度/配对/迟滞、模拟来源、质量门槛、输入归中/失焦重复键/非有限轴 |
| 浏览器 | macOS本机Edge独立自动化会话：七主题、1440/768/390三尺寸×五页=15组合无横向溢出；原校正七页×三尺寸=21组合；旧页面仅注入同一CSS/显示标签，不提交表单 |
| 3D几何 | 七场景24次绘制；庭院/码头/森林/小镇/雪原/工坊7808三角形，太空9680，低于45/15000门槛。主题变更不重置模拟位姿；输入产生模拟反馈而非实机证据 |
| 输入演练 | 明确注入的KeyboardEvent和Gamepad API样本通过；自动化CLI的keydown未提供code，未将其冒充真实键盘/手柄验收。触控/手机姿态逻辑与失效门槛有单元覆盖，实际设备未验收 |
| 模型 | 固定q8模型和本地WASM实际加载、六张独立公共场景图在独立Worker真实推理；见下表。没有取真实相机/云端推理 |
| 冻结包 | 旧0.5.0归档 SHA256 `d17bc4444d8c3fd817e2ed212bdb7001330680ad0fbe97fe30ec5417135d8790`复核一致；原生程序、补丁、核心校正Web与管理器无差异 |

证据：`deliverables/microduck-tools-studio-20261101/evidence/` 中的 `studio-tests.log`、`legacy-regression.log`、`js-tests.log`、`browser/browser-qa.json`、`browser/legacy-mobile-qa.json`、`semantic-browser.json`及截图。软件测试不能替代真实板端加载或物理停车测试。

## 六张语义小样本

标注来自独立公共照片内容/描述，每类仅一张；并非训练集独立性证明，也不构成统计准确率。图片原页、作者、许可、下载URL和SHA保存在 `semantic-samples/samples.json`，交付资产 `assets/validation` 也保留这些元数据。无图像上传云端。

| 粗标注 | 最高分标签 | 支持度 | 与次选差 | 自动主题资格（还须两帧及30秒保持） |
|---|---|---:|---:|---|
| workspace | workspace | 0.636 | 0.307 | 未达0.65，保持 |
| home | home | 0.507 | 0.040 | 支持/差距不足，保持 |
| greenery | greenery | 0.968 | 0.950 | 候选合格 |
| urban | urban | 0.876 | 0.793 | 候选合格 |
| waterside | waterside | 0.769 | 0.582 | 候选合格 |
| snow | snow | 0.865 | 0.750 | 候选合格 |

本机约114–163ms/图；这不代表手机速度/耗电，不以六张最高标签相符宣传实机场景准确率。质量检查阈值、标签文本及0.65/0.15均是工程初值。遮挡/纯色/过暗/过曝单元案例拒绝自动主题；真实运动模糊、夜间、镜头朝地、人群和混合场景仍需扩充独立样本。**默认手动主题，自动主题需要用户明确勾选。**

## 资源结论的界限

- 本机Headless Edge触发过15FPS低功耗降级；各轮约13–24FPS，不宣称手机已达到30FPS或稳定15FPS。几何预算通过；真实手机帧率、GPU内存/温升/电量均NOT_RUN。
- 没有板端CPU/RSS结果。20% CPU / 64MiB高水位 / 96MiB硬上限是部署模板配置，不代表在目标systemd/cgroup已生效。普通服务、受限助手合计RSS≤64MiB，常态CPU≤一核5%，3D相对同数据量二维增量≤一核2%/8MiB均等待实测。
- Camera会增加mediad取帧/PNG编码开销，不能只看Studio进程。现场同工况依次测：无Studio、二维且相机关闭、3D且相机关闭、开启低频相机、双显示端、首次模型加载。每阶段核对robotd achieved_hz/missed/last_tick_age、slice CPU/MemoryCurrent和mediad CPU/RSS，记录时间窗口/温度/网络。
- 超预算先停语义/相机，再降前端像素比或二维；不能自动放宽安全超时或把模型迁回板端。

## 板端最小待执行清单

所有项目等待明确现场通知；[操作指南](studio-0.6.0-guide.zh-CN.md)给出每条命令意义与路径。

1. 确认目标身份、稳定支撑/动力条件、升级到固定0.18.3的原方案已验收；保留旧0.5.0完整包与回滚证据。没有舵机只做演练/已有dry-run，不打开真实控制。
2. 解包后核对便携归档SHA、MANIFEST、Python3.11 ARM64、运行库、venv/systemd/openssl和robot组；先setup预览，再明确安装。此阶段不启动原生服务。
3. status核对安装阶段/源资产哈希；start只启动Studio/helper；核对两个PID、普通用户、slice CPUQuota/MemoryHigh/MemoryMax实际值。新CA经指纹核对后信任，登录每台显示端。
4. 控制关闭、Camera关闭，先验证真实反馈/IMU/关节/ToF/identity与原校正回执；旧版本保留只读/原校正入口。页面标记live不等于identity校验通过。
5. 现场明确授权后才configure打开所需开关并重新start。先在演练检查本机手柄标准/非标准映射、手机授权/竖横屏、触屏/键盘；再分别验证板端手柄与浏览器接管。手柄服务交接须包含停止失败、断网、进程退出/重启和明确交还流程。
6. 小幅、限速、有支撑地对照requested/applied/实际反馈，测试释放/失焦/后台/设备断开/断流停车。检查无其他直接写入客户端；daemon本身没有跨所有程序的独占租约。全系统故障不由网页代替物理停止措施。
7. 检查七主题位姿/轨迹参考系不重置；独立断开ToF、IMU、Camera的“数据模拟回放”先验证降级，实际硬件断接需另行现场方案，不在线拔动力接头。扩充相机独立标注样本，再决定是否启用自动主题。
8. 完成上述资源阶段对比；性能/设备结果单独登记。先stop，再在全部输入归中后明确hand-back；无待交接和外部漂移才rollback。安装中断使用recover，不删除pending保护，不整体搬移robot系统目录。

## 可重复的软件命令

仓库根目录，带aiohttp/dbus-next的隔离Python；原SDK测试另需固定dynamixel-sdk3.8.4。测试不会访问真实设备：

```sh
python -m unittest discover -s studio/tests -v
python -m unittest discover -s calibration/tests -v
node --test studio/tests/*.test.js
```

浏览器测试需先启动并登录本地DEMO，然后运行（session对应你自己的独立自动化浏览器）：

```sh
python3 tools/studio-browser-qa.py --session studioedge --out /绝对路径/新证据目录
agent-browser --session studioedge eval --stdin < tools/studio-semantic-qa.js
```

语义样本测试需便携包内 `assets/validation`，生产Worker本身不依赖样本。原Web移动检查用 `tools/studio-legacy-mobile-qa.py`，会先拒绝非loopback或非DEMO，再注入显示CSS/标签，全程不提交表单。实际设备与板端命令绝不由这些测试自动启动。
