# 设计依据、实现语言与验证边界

## 实现语言

应用层使用 Python 3，原因是设备控制、数据处理、配置、自动测试和本地 Web 服务可以共用同一套代码。真正操作硬件的接口不是自定义协议，而是 PicoSDK 提供的 `ps4000a.dll` C API；Python 的 `ctypes` 包装层把参数按 C API 原型传入驱动。

这不是 Raspberry Pi Pico/RP2040 的程序。PicoScope 4824A 属于 USB PC 示波器，使用的是 PicoScope 4000 Series (A API)，驱动前缀为 `ps4000a`。

## 官方资料对应关系

| 程序功能 | 采用的官方接口/资料 |
|---|---|
| 打开与关闭设备 | `ps4000aOpenUnit`、`ps4000aCloseUnit` |
| 8 通道设置 | `ps4000aSetChannel`；通道枚举 A～H |
| 采样时间基准 | `ps4000aGetTimebase2`，不硬编码采样间隔 |
| 块采集 | `ps4000aRunBlock`、`ps4000aIsReady` |
| 数据缓存与读取 | `ps4000aSetDataBuffer`、`ps4000aGetValues` |
| ADC 量化值 | `ps4000aMaximumValue` |
| 简单硬件触发 | `ps4000aSetSimpleTrigger` |
| AWG 设备边界 | `ps4000aSigGenArbitraryMinMaxValues` |
| AWG DDS 相位 | `ps4000aSigGenFrequencyToPhase` |
| 任意波形配置 | `ps4000aSetSigGenArbitrary` |
| 软件启动 AWG | `ps4000aSigGenSoftwareControl` |

主要参考资料：

1. PicoScope 4000 Series (A API) Programmer's Guide  
   <https://www.picotech.com/helpfiles/4000a-api/index.html>
2. PicoScope 4000A Series 官方规格  
   <https://www.picotech.com/oscilloscope/4000/picoscope-4000-specifications>
3. Pico Technology 官方 Python 示例，特别是 `ps4824BlockExample.py` 和 `ps4000aSigGen.py`  
   <https://github.com/picotech/picosdk-python-wrappers/tree/master/ps4000aExamples>
4. PicoSDK Windows 64 位安装包  
   <https://www.picotech.com/downloads/_lightbox/pico-software-development-kit-64bit>

## 为什么采用“ADC 先武装、AWG 后触发”

`ps4000aSetSigGenArbitrary` 必须在采集开始前完成配置。程序先调用 `ps4000aRunBlock`，确保 ADC 已经等待触发，然后调用 `ps4000aSigGenSoftwareControl` 发出一次 AWG 脉冲。AWG 或功放低压监测输出回接 ADC 触发通道后，由示波器内部硬件触发器确定 `t=0`，避免使用 Windows 调度延迟作为同步基准。

如果不做物理回接而关闭 ADC 触发，八通道仍共用采样时钟、彼此同步，但 AWG 相对于采集窗口的位置会受到 USB 和操作系统延迟影响。

## 已完成的验证

- 配置边界测试：八通道 40 MS/s 上限、四通道 80 MS/s、AWG ±2 V 输出边界。
- AWG 数学测试：Hann 脉冲端点、周期与 DDS 重复频率、DAC 量化范围。
- 八通道仿真采集：点数、时间轴、通道结果与信号衰减。
- 假驱动完整流程：通道配置、触发、AWG、时间基准选择、缓存、采集、软件触发、ADC 转换。
- 本地 Web API：配置读取、仿真采集、状态轮询和结果读取。

## 目前不能声称的内容

在真实 PicoScope 4824A、实际 PicoSDK 版本、USB 端口、探头、功放和换能器组合上完成验收前，不能声称程序已经百分之百通过实机验证。驱动调用顺序和函数原型有官方文档及官方示例依据，但以下项目必须上机确认：

- PicoSDK/USB 驱动能否正确识别目标设备和序列号。
- 八通道实际可用时间基准与最大缓存点数。
- 所选触发阈值、回接极性和功放延迟。
- AWG 实际幅值、负载、失真和功放输入要求。
- 八通道同一输入信号下的相位一致性和通道间串扰。

建议按 README 中的实机验收顺序逐步测试，不要第一次运行就连接高压功放输出。

