# Microduck Tools

Microduck 校准与配置工具。当前基线为 **daemon-v0.16.3**，已包含 IMU 角速度换算、安装方向、15 舵机软件映射、摄像头四向配置和黑橙统一 Web。

这是软件准备和审阅版本，尚未执行实机回滚、部署或物理验收。Web 不自动重启 robotd；摄像头应用需单独启用并明确确认，只重启 mediad。校正及摄像头扩展依赖配套原生补丁，不能仅复制页面便声称官方原版已支持这些配置。

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

本地功能分支加入 `--enable-servo-health`，复用原有5Hz遥测和只读健康缓存，提供15关节电流、跟随误差、速度曲线、60秒人工参考、持续异常告警、JSON/CSV导出及历史维护报告导入。默认关闭，不修改robotd、不增加总线读取，不自动卸力或控制。规则初值未做实机标定，未发现规则异常不代表硬件完好。

```bash
python3 calibration/integration-0.16.3/web/calibration_web.py \
  --target daemon-v0.16.3 --demo --no-telemetry --enable-servo-health \
  --config calibration/integration-0.16.3/evidence/demo/robotd.toml \
  --dest /tmp/microduck-tools-demo-calibration.toml \
  --servo-health-dir /tmp/microduck-tools-demo-health --port 8765
```

普通DEMO没有真实遥测。需要合成曲线时运行 `python3 calibration/integration-0.16.3/evidence/servo-demo.py`。独立维护CLI为 `calibration/integration-0.16.3/servo-maintenance.py`，只支持Linux root、人工先停robotd及串口独占后的PING/READ；网页不能执行采集。其可选依赖锁定在同目录 `requirements-maintenance.txt`，Web不依赖SDK。

已推送的 `main` 基线为 `2b93cb3`，新增插件在本地 `feat/servo-health-plugin` 分支单独保留，尚未推送。完整部署/维护指南与验证记录保留本地交付目录，实际设备操作仍待用户另行通知。
