# Microduck Tools

Microduck 校准与配置工具 **0.2.0**。保留 daemon-v0.16.3 的 IMU、15 舵机软件映射、摄像头四向配置和黑橙统一 Web；增加官方新版只读能力适配、独立插件配置与相机 CLI 适配。

这是软件准备和审阅版本，尚未执行实机回滚、部署或物理验收。Web 不自动重启 robotd；摄像头应用需单独启用并明确确认，只重启 mediad。旧版校正依赖配套原生补丁，新版监测不需要补丁；官方新版 IMU/关节校正写入保持禁用。

## 本地预览

Python 3.11+，从仓库根目录运行：

```bash
python3 calibration/integration-0.16.3/web/calibration_web.py \
  --target daemon-v0.16.3 --demo --no-telemetry \
  --config calibration/integration-0.16.3/evidence/demo/robotd.toml \
  --dest /tmp/microduck-tools-demo-calibration.toml --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。DEMO 不连接机器人，保存和相机应用不可用。真实使用保持 localhost 监听并通过 SSH 隧道访问。

```bash
python3 -m unittest discover -s calibration/tests -v
```

## 当前入口

- [统一 Web](calibration/integration-0.16.3/web/calibration_web.py) / [前端](calibration/integration-0.16.3/web/index.html)
- [摄像头配置模块](calibration/integration-0.16.3/web/camera_config.py)
- [校准 CLI](calibration/common/duck_calibrate.py)
- [0.16.3 完整源码补丁](calibration/integration-0.16.3/evidence/calibration-camera-v2-0.16.3.patch)
- [同步代码哈希](CODE-MANIFEST.json)

`calibration/web/`、`daemon-v0.16.3/`、`daemon-v0.17.8/` 保留原始输入包，当前统一入口为 `calibration/integration-0.16.3/web/`。0.17.8 原始文件不代表本次已适配或验证该版本。

本仓库只同步源码、源码补丁和合成测试数据。既有部署文档、截图、完整构建日志、候选程序、原始 ZIP 和历史归档保留在本地交付目录，不在本次公开代码同步范围内。真实部署前需另行核对完整操作指南与目标环境。

## 可选只读舵机健康插件

`--enable-servo-health` 复用原有5Hz遥测和只读健康缓存，提供15关节电流、跟随误差、速度曲线、60秒人工参考、持续异常告警、JSON/CSV导出及历史维护报告导入。默认关闭，不修改robotd、不增加总线读取，不自动卸力或控制。规则初值未做实机标定，未发现规则异常不代表硬件完好。

```bash
python3 calibration/integration-0.16.3/web/calibration_web.py \
  --target daemon-v0.16.3 --demo --no-telemetry --enable-servo-health \
  --config calibration/integration-0.16.3/evidence/demo/robotd.toml \
  --dest /tmp/microduck-tools-demo-calibration.toml \
  --servo-health-dir /tmp/microduck-tools-demo-health --port 8765
```

普通DEMO没有真实遥测。需要合成曲线时运行 `python3 calibration/integration-0.16.3/evidence/servo-demo.py`。独立维护CLI为 `calibration/integration-0.16.3/servo-maintenance.py`，只支持Linux root、人工先停robotd及串口独占后的PING/READ；网页不能执行采集。其可选依赖锁定在同目录 `requirements-maintenance.txt`，Web不依赖SDK。

## 新版适配与独立部署

实际使用新增 `--target auto` 或 `--target daemon-v0.17.9`；通过 hello/model 验证 API、实际版本和关节拓扑，再启用规则。当前评审契约为 API41/44/45；未知契约降级并显示原因。`--plugin-config` 接收独立schema，不改robotd.toml。相机适配官方0.17.9的`--rotate`，要求已评审的revision、命令和服务覆盖层。

修复Blob预览CSP、相机保存冲突和旧温压缓存错误解除；增加绑定身份的基线、增量曲线、维护详情保持和SIGTERM收尾。原有校正目标/确认/备份与安全边界保留。

- [兼容范围、部署与回滚说明](docs/compatibility.zh-CN.md)
- [非root服务模板](deploy/microduck-tools.service) / [插件配置示例](deploy/plugins.example.json)
- 运行包构建：`python3 tools/build-release.py --out /tmp/microduck-tools-0.2.0.tar.gz`

软件回放、假daemon和本机浏览器验证不代表板端运行或硬件验收。完整现场部署审阅与验证记录保留本地deliverables，实际设备操作仍待用户另行通知。
