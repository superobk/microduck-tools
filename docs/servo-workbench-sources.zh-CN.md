# 预设来源、型号和单位边界

引用为本轮固定源码契约，不自动追踪上游HEAD；实现是本项目独立代码，不复制XGO运行器。

| 来源 | 固定证据 | 用途 |
|---|---|---|
| [XGO Duck Arduino runtime](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/tree/b401d85d20bb5bfd0237242a8a5ab6847fc962a2) | b401d85d20bb5bfd0237242a8a5ab6847fc962a2 | HD1910、XGO左右腿布局、模式4、中位0x0B；可选永久KP5/KD10 |
| [Feetech官方Python SDK](https://github.com/ftservo/FTServo_Python/tree/9e6e342f6011dc727ba7144fc835bca5357b3ca9/scservo_sdk) | 9e6e342f6011dc727ba7144fc835bca5357b3ca9，hls.py及协议编解码 | HLS/SCS地址、小端、符号位、读写包与Reset Mid指令 |
| [ROBOTIS XL330-M288手册](https://emanual.robotis.com/docs/en/dxl/x/xl330-m288/) | 型号1200、Protocol2、3.7–6.0V、电流1mA/count | 型号门控、寄存器/保护意义；电流不是准确输出扭矩 |
| 三版现有Microduck固定model.rs | 0.16.3 / 0.17.9 / 0.18.3；新版基线身份见 `calibration/daemon-v0.17.9/native-build.json` 和 `calibration/daemon-v0.18.3/native-build.json` | ID布局、1Mbps、return delay、PWM slope、膝关节−512；新工作台测试逐版核对预设 |
| [pySerial端口识别](https://pyserial.readthedocs.io/en/latest/tools.html#serial.tools.list_ports.ListPortInfo) | pyserial3.5 | VID/PID、序列号、location，不固定COM3/ttyUSB0 |

XL330写入仅开放1200，未知型号不套用寄存器。默认保留Shutdown现状，52的电压保护关闭位必须明确选；没有关闭扭矩不代表供电合规。

HD1910-C001按已审阅厂商资料使用4.0–8.4V、4096刻度、中位2048、默认模式4。协议读取不能可靠证明唯一具体型号，要求实物确认；固件3.43+与小端契约外拒绝配置。SDK电流6.5mA/count、速度50step/s/count作为参考解码，尚未经实机标定；不宣称额定连续电流、准确扭矩或寿命。

HD1910地址：ID5、baud6、副ID7、保护19、永久P/D/I21/22/23、保护电流28、偏移31、模式33、扭矩40、加速度41、目标42、目标电流44、速度46、扭矩限制48、临时P/D/I50/51/52、EPROM锁55、反馈位置56、速度58、负载60、电压62、温度63、故障65、电流69。写白名单只开放当前类型化任务所需字段。

XGO旧本地源码与本轮固定上游不同：新版保留腿/头永久增益，嘴部只用临时KP10/KD20，UI的KP5/KD10是可选永久设置。不能把“XGO初始化”理解成全部舵机统一改增益；不拷贝他人实测ZERO_POS/全部负方向。运行角度中心2047与硬件Reset Mid2048分别处理。

## 离线依赖

运行网页/SQLite/模拟使用Python标准库；串口仅依赖pyserial3.5，wheel SHA256：

`c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0`

测试额外使用dynamixel-sdk3.8.4与已有Studio依赖，不进入工作台运行依赖。对照测试覆盖普通Protocol2发送包及SDK返回的stuffing结果；SDK3.8.4的txPacket忽略addStuffing扩容返回值，特殊字节不能直接以其实际TX为正确参考，本项目保留正确stuffing并独立验证CRC。未修改外部SDK。
