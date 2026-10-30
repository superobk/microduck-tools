# daemon-v0.17.9 原生校正适配

上游固定为 `96b89d8613f06722c1d64ce75ab0d7124fed9d8f`，API 44 保持不变。

- `native-calibration-v3.patch`：六个 Rust 文件的完整补丁；支持校正路径、软件零位/方向、IMU 安装四元数和显式陀螺仪比例，并在完成总线验证后发布实际内存值。
- `native-build.json`：基线、补丁 SHA 与候选 revision；没有硬件验收声明。
- `raw16.4.example.toml`：合成格式示例。不要覆盖实际零位与安装姿态；使用迁移命令保留已有参数。

构建和生效路径见 [0.17.9 指南](../../docs/daemon-0.17.9-calibration.zh-CN.md)。统一 Web 仍位于 `calibration/integration-0.16.3/web/`，目录名为历史兼容路径。
