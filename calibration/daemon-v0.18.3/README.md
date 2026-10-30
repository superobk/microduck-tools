# daemon-v0.18.3 原生校正

固定基线 `b2b0d213a402caeac492dd662ea891faa17f67c0`，API47。配套候选 `b2b0d213-calv3-30452a086d1e`。

完整补丁支持15关节软件映射、IMU安装四元数及明确的raw/16.4；读取上游合并后的参数，发布实际内存值、用户层位置及其拒绝状态。缺少校正时保留官方默认行为。官方未打补丁的daemon没有此契约。

`raw16.4.example.toml` 为合成示例，部署前迁移实际校正并人工核实固件比例。[部署及分层回滚](../../docs/daemon-0.18.3-calibration.zh-CN.md) / [功能对比](../../docs/daemon-0.18.3-comparison.zh-CN.md)。

运行入口保持 `calibration/integration-0.16.3/web/calibration_web.py` 历史目录；支持显式 `--target daemon-v0.18.3` 和auto。板端验收未执行。
