# Tools 服务：保留原权限，明确配套 robotctl

> 当前Tools0.5.0用 `sudo ./configure.sh --apply` 接入校正/Tools，再用 `sudo ./services.sh start --apply` 启动；修改IMU/关节用临时维护Web。见[脚本升级路径](manual-upgrade-0183.zh-CN.md)。下文仅对应[0.4.3手工历史流程](manual-upgrade-0183-0.4.3-history.zh-CN.md)第5步，其变量不适用于新会话。

这是主指南第 5 步的补充，不是另一次 daemon 升级。本页板端 NOT_RUN。原机器人服务和 Tools 已停止、备份已完成；TOOL/RUN/BUNDLE 来自主指南 session.env。

**意义：确认已有服务是否可直接沿用。** 不要覆盖陌生服务。以下查询只读。

```bash
getent passwd microduck-tools
getent group robot
systemctl cat microduck-tools
```

用户/组不存在时先停止本次安装，补充专用账号和组权限部署审阅；不要把常驻用户改成 root。若首次部署服务，使用本包 deploy/microduck-tools.service；已有服务则 diff 比较并保留原有安全/权限设置。

**意义：只在服务缺失时安装，保留已有插件选择与状态。** 已存在插件文件不覆盖；新部署使用 schema=1、servo_health.enabled=true 的示例。该插件仅告警/记录，不写机器人控制参数。

```bash
if [ ! -e /etc/systemd/system/microduck-tools.service ]; then
  install -m 0644 "$TOOL/deploy/microduck-tools.service" /etc/systemd/system/microduck-tools.service
fi
install -d -m 0755 /etc/microduck-tools
install -d -m 0700 -o microduck-tools -g microduck-tools /var/lib/microduck-tools
if [ ! -e /etc/microduck-tools/plugins.json ]; then
  install -m 0644 "$TOOL/deploy/plugins.example.json" /etc/microduck-tools/plugins.json
fi
python3 -m json.tool /etc/microduck-tools/plugins.json
```

**意义：显式选择本次配套 robotctl。** 将下面内容先写到 RUN 工作区，再原子放进本次专属覆盖；不改变服务用户/沙箱。若曾有自定义 ExecStart，先审阅它的额外参数，不能照抄丢弃业务设置。

```bash
install -d -m 0755 /etc/systemd/system/microduck-tools.service.d
cat > "$RUN/tools.conf" <<'UNIT'
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /opt/microduck-tools/current/calibration/integration-0.16.3/web/calibration_web.py --target auto --listen 127.0.0.1 --port 8765 --plugin-config /etc/microduck-tools/plugins.json --servo-health-dir /var/lib/microduck-tools/servo-health --camera-settings /var/lib/microduck-tools/camera.json --robotctl /opt/microduck-tools/native/current/bin/robotctl
UNIT
install -m 0644 "$RUN/tools.conf" /etc/systemd/system/microduck-tools.service.d/zzzz-manual-0183.conf.new
mv -T /etc/systemd/system/microduck-tools.service.d/zzzz-manual-0183.conf.new /etc/systemd/system/microduck-tools.service.d/zzzz-manual-0183.conf
systemctl daemon-reload
systemctl show microduck-tools -p ExecStart -p User -p Group -p SupplementaryGroups
```

应是非 root 用户、robot 组权限、回环监听、配套 native robotctl。发现更后的覆盖、未知启动命令或不同权限需求时停止审阅。确认后回主指南第 6 步。

**意义：Mac 通过隧道访问，不把维护端口暴露给外部网络。** BOARD 使用第 2 步同一目标。

```bash
ssh -N -L 8765:127.0.0.1:8765 "$BOARD"
```

浏览器访问 http://127.0.0.1:8765，依次检查状态、IMU/关节运行值、舵机健康、相机。API /api/capabilities 的 servo_health.enabled 应与 plugins.json 一致；/api/servo-health/status 的数据年龄/缺失项按实际展示。非 root 服务不能写机器人配置是预期，主流程用 CLI 安装。

摄像头保存角度与应用分开：旧 media.camera_rotate 应在新版相机页保存成同角度；实际应用需另行批准短时维护模式并启用 --enable-camera-apply，只重启 mediad。相机未验收就登记 PENDING，不能用健康页面通过来代替。旧健康参考保留供审阅，不能直接当新版基线。
