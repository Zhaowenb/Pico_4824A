# WaveGuard · PicoScope 4824A 导波工作站

**部署分支：`btf`。** 支持 Windows 本机／局域网 Web 工作站及 Linux ARM64 容器。PicoScope 和 IT6524D 连接到**运行 Python 服务的电脑**，其他电脑的浏览器只访问界面。

> **在另一台 Windows 从零部署，按 1 → 6 执行。** 不需要原电脑的 F 盘路径，不依赖 `Pico_4824A` 参考工程。不要复制旧电脑的 `.venv`，每台电脑单独创建环境。

## 目录

- [1. 安装前准备](#1-安装前准备)
- [2. 拉取 btf 分支](#2-拉取-btf-分支)
- [3. 安装 Python 依赖](#3-安装-python-依赖)
- [4. 无硬件验证与网页启动](#4-无硬件验证与网页启动)
- [5. 连接 PicoScope 与 IT6524D](#5-连接-picoscope-与-it6524d)
- [6. 日常启动、停止与局域网访问](#6-日常启动停止与局域网访问)
- [页面功能](#页面功能)
- [偏置与幅值扫描首次配置](#偏置与幅值扫描首次配置)
- [数据保存与换机迁移](#数据保存与换机迁移)
- [更新版本](#更新版本)
- [常见问题](#常见问题)
- [Linux 与详细文档](#linux-与详细文档)

## 1. 安装前准备

推荐 **Windows 10/11 x64 + 标准 CPython 3.12 x64**。项目要求 Python ≥3.10；不要使用 32 位 Python 或 Git/MSYS 自带的 Python。

| 软件 | 仿真／离线分析 | Pico 实机 | IT6524D 偏置扫描 | 获取方式 |
|---|---|---|---|---|
| Git | 拉取和更新需要 | 同左 | 同左 | [Git for Windows 官方安装](https://git-scm.com/install/windows)，确保终端能执行 `git` |
| Python x64 | 必需 | 必需 | 必需 | [Python Windows 官方下载](https://www.python.org/downloads/windows/)，安装时启用 PATH |
| NumPy、picosdk Python 包 | 必需 | 必需 | 必需 | 第 3 步 `setup.ps1` 自动安装 |
| Pico 官方 x64 驱动／PicoSDK | 不需要 | 必需 | 必需 | [PicoSDK 64 位官方下载](https://www.picotech.com/downloads/_lightbox/pico-software-development-kit-64bit) |
| PyVISA Python 包 | 不需要 | 单独 Pico 不需要 | 必需 | 第 3 步另外安装 |
| 系统 VISA USB 驱动 | 不需要 | 单独 Pico 不需要 | 必需 | Windows 使用 [NI-VISA 官方下载](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html)，确认版本支持你的 Windows |

**不要混淆 Python 包与仪器驱动：**

- `pip install picosdk` 是 Python 封装，不安装 Pico 的 Windows DLL／USB 驱动。
- `pip install pyvisa` 是 Python 接口，不安装 NI-VISA USB 驱动。[PyVISA 安装说明](https://pyvisa.readthedocs.io/en/stable/introduction/getting.html)
- 使用浏览器界面不需要 Node、npm 或 Playwright；它们仅用于开发时的浏览器测试。

只做分析或仿真时，可以先跳过仪器驱动。驱动安装可能需要管理员权限或重启 Windows；项目日常启动通常不需要管理员权限。安装 Git／Python 后重新打开 PowerShell，确保 PATH 生效。

Python 下载时选择 **Windows installer (64-bit)**；不要下载源代码压缩包、embeddable 包或 ARM64 安装包。若使用新的 Python 安装管理器，也须先安装可供终端调用的标准 x64 Python 运行时，再执行本项目 setup。

## 2. 拉取 btf 分支

打开 **PowerShell**。示例目录为 `D:\Pico\Pico_4824A_btf`；没有 D 盘，可把 `D:\Pico` 改为 `C:\Pico` 或其他可写目录。

```powershell
New-Item -ItemType Directory -Force D:\Pico | Out-Null
Set-Location D:\Pico
git clone --branch btf --single-branch https://github.com/Zhaowenb/Pico_4824A.git Pico_4824A_btf
Set-Location .\Pico_4824A_btf
git branch --show-current
```

最后一行应输出 **`btf`**。目录中应有 `README.md`、`setup.ps1`、`start_web.ps1`、`pico4824a/` 和 `web/`。

- 已有同名目录时，先确认内容，不要覆盖。已有 Git 副本按后面的“更新版本”操作。
- GitHub ZIP 必须选 `btf` 分支。ZIP 没有 `.git`，一键更新不可用；需要一键更新请用 `git clone`。
- 克隆显示权限错误时，检查 GitHub 账号是否有仓库访问权；它与仪器驱动无关。

## 3. 安装 Python 依赖

**以下命令都在项目根目录执行。** 即使终端前面显示 `(base)`，仍使用本项目 `.venv\Scripts\python.exe`，避免依赖装到其他环境。

### 基础安装：所有用户执行

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

脚本会寻找 Windows x64 Python、创建项目内 `.venv`，安装 `requirements.txt` 中的 NumPy 和 picosdk。`ExecutionPolicy Bypass` 只作用于这次 PowerShell 进程，不需要永久改变系统策略。

确认环境：

```powershell
.\.venv\Scripts\python.exe -c "import sys,struct,numpy; print(sys.executable); print(sys.version); print('Python bits:',struct.calcsize('P')*8); print('NumPy:',numpy.__version__)"
```

应显示当前项目的 `.venv\Scripts\python.exe` 和 **64** 位。无需激活虚拟环境。

### 使用 IT6524D：额外执行

先安装第 1 节的 NI-VISA，再安装 Python 包：

```powershell
.\.venv\Scripts\python.exe -m pip install "pyvisa>=1.14"
```

`setup.ps1` **不会安装 PyVISA，也不会安装系统 VISA 驱动**。这两项都要单独完成。

本指南的 Windows 路径使用系统 NI-VISA；Linux 容器使用 PyVISA-py／PyUSB。Windows 只安装 `pyvisa-py` 并不等于 USB 驱动已经齐全，不要随意替换 Pico 的 USB 驱动。[PyVISA-py USB 依赖说明](https://github.com/pyvisa/pyvisa-py/blob/main/docs/source/installation.rst)

## 4. 无硬件验证与网页启动

先运行一次仿真采集：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_simulation.ps1
```

应看到 `Capture complete` 和保存路径，示例 NPZ 在 `data/captures/` 任务目录。该脚本使用模拟设备，不发送真实激励。

启动 Web：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_web.ps1
```

浏览器打开 **[http://127.0.0.1:4824/measure](http://127.0.0.1:4824/measure)**，保留启动终端。

没有硬件／驱动时，“设备预连接失败”不一定阻止网页和仿真使用。实时测量／扫描前确认对应的“仿真”选项已开启；离线分析直接加载文件。

**这一步验证软件环境，不代表真实设备或带电扫描已验证。**

## 5. 连接 PicoScope 与 IT6524D

### PicoScope 4824A

1. 安装官方 x64 SDK／驱动，USB 连接设备，等待 Windows 完成设备安装。
2. 可先用 PicoScope 官方 App 验证识别，随后**退出官方 App**再启动 WaveGuard，避免占用同一设备。
3. 涉及功放时先关闭功放输出／置于待机，再启动服务。服务连接 Pico 后将 AWG 归零；确认低压接线和触发后再采集。
4. 实时测量页取消仿真，配置通道量程、采样、AWG 和触发。H 只是默认触发通道，不是固定接线要求。

仅加载 DLL 的诊断，不打开示波器或发送激励：

```powershell
.\.venv\Scripts\python.exe -c "from pico4824a.device import Pico4824A; s=Pico4824A(); s._load_sdk(); print('ps4000a driver loaded')"
```

程序自动检查 `C:\Program Files\Pico Technology\SDK\lib`、`PicoSDK\lib` 和部分 PicoScope 7 目录。安装在其他位置时，指定**实际包含 `ps4000a.dll` 的目录**，再从同一终端启动：

```powershell
# 路径示例，按实际安装位置替换
$env:PICO_SDK_PATH = 'C:\Program Files\Pico Technology\SDK\lib'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_web.ps1
```

不要只复制一个 DLL：它还依赖配套库及 USB 驱动，使用官方安装程序。[Pico 驱动依赖说明](https://www.picotech.com/helpfiles/4000a-api/driver.html)

### IT6524D 电源

确认 NI-VISA 和 PyVISA 均已安装，连接电源的 USB **数据接口**。先关闭电源输出，再检查资源：

```powershell
.\.venv\Scripts\python.exe -c "import pyvisa; rm=pyvisa.ResourceManager(); print('VISA backend:',rm); print('Resources:',rm.list_resources()); rm.close()"
```

应出现 `USB0::...::INSTR`。`ASRL1::INSTR` 是串口，不能作为本页的 USB 电源选择。

打开 [偏置电流扫描](http://127.0.0.1:4824/bias-scan)：**查找 USB → 选择资源 → 连接并断电**。程序核对 IT6524D 型号并确认输出关闭；连接动作不开启电源。

- 关闭其他电源控制软件，不同时运行第二个后端。
- 出现“输出状态未知”时，使用 **确认断电并恢复**；不会自动恢复旧扫描。
- 使用 `rm=...; ...; rm.close()`，不使用旧的 `with pyvisa.ResourceManager()` 写法。
- 设备未发现时检查 NI MAX、设备管理器、USB 数据线和占用程序。[NI USB 通信排查](https://knowledge.ni.com/KnowledgeArticleDetails?id=kA03q000000x1qzCAA)

两台仪器需要同一服务电脑的两个 USB 连接。接口不足可使用独立供电、支持 USB 3.0 数据传输的 Hub，Pico 使用合适的数据线；仅供电 USB 线无法通信。

## 6. 日常启动、停止与局域网访问

| 用途 | 启动方式 | 地址／说明 |
|---|---|---|
| 本机 Web（推荐） | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_web.ps1` | `http://127.0.0.1:4824` |
| 手机／另一台电脑访问 | 双击 `start_web_lan.cmd` | 使用启动器显示的服务电脑 LAN 地址 |
| Tk 桌面界面 | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_gui.ps1` | 旧桌面入口；新工作站及偏置扫描使用 Web |
| 自定义端口 | `.\.venv\Scripts\python.exe -B -m pico4824a web --host 127.0.0.1 --port 4825` | 浏览器端口也改为 4825 |

退出前先停止任务并确认偏置输出关闭，再在终端按 `Ctrl+C`。正常退出会执行设备收尾；强制结束进程不能保证这一点。

局域网模式：

- 可信网络应在 Windows 中设为“专用”。启动器可申请建立仅专用网络、TCP 4824、本地子网来源的防火墙规则。
- 防火墙／网络条件未满足时可能退回仅本机监听，以启动器实际显示为准。
- 字母后按 Enter：`i` 状态与地址、`o` 网页、`s` 停止测量、`q` 安全关闭、`r` 安全重启、`l` 日志、`h` 帮助。
- 启动器日志在 `data/launcher_logs/`。客户端的 `127.0.0.1` 指客户端自己，应使用**服务电脑 IP**。
- 当前没有登录鉴权，仅用于可信实验室网络，不做公网端口映射。

## 页面功能

所有页面共用一个后端和设置，无需分别启动。

| 页面 | 路径 | 功能 |
|---|---|---|
| 实时测量 | `/measure` | ADC、AWG、触发、采集／停止／保存 |
| 参数扫描 | `/sweep` | 频率、周期、二维扫描及评价 |
| LCR 测量 | `/lcr` | 小／大信号阻抗和线性度采集 |
| LCR 数据分析 | `/lcr-linearity` | LCR、线性度／畸变文件夹分析 |
| 单数据分析 | `/file-analysis` | Raw／Filtered／Mix／FFT／STFT／CWT／WPD／EX |
| 参数数据分析 | `/sweep-analysis` | 归档热图、逐点波形、重新评价 |
| 偏置电流扫描 | `/bias-scan` | IT6524D + Pico 偏置／幅值扫描 |
| 磁路诊断 | `/magnetic-diagnosis` | 当前为诊断思路仿真预览，不执行实机诊断 |
| 干扰实验 | `/interference` | 实验配置、采集与会话 |

顶部齿轮：Light/Dark、界面强调色、保存命名、任务 ZIP 导出、软件更新。通道颜色保持 PicoScope 色序，不随界面强调色改变。

## 偏置与幅值扫描首次配置

1. 在实时测量配置采样、启用通道、AWG 和触发；偏置任务启动时冻结快照，默认记录窗口 2000 μs。
2. 默认 0–6 A、0.5 A 步长，每幅值重复 5–10 次。软件范围最高 21.65 A，**不代表线圈安全工作电流**；不会自动提高实际电流保护限值。
3. 设置 PZT 通道、直达波窗口及带通。Vpp 默认评价滤波后的直达波，原始 NPZ 保持不变；激励电压／电流通道和换算系数分别配置。
4. 填写电压合规上限、每电流档总通电时间、稳定超时、实际电流上限及冷却参数。实机限值留空会拒绝启动，不照搬另一台机器的实验限值。
5. 固定偏置扫幅值：同一电流档通电／稳定一次，连续采完全部幅值。完成／超时／异常后先断电，再保存、分析、冷却一次；切换幅值／量程重采不重置截止时间。
6. 波形保护是可选开关，开启才检查形变、削顶、跳闸和可选 H2/H1。无论此开关是否开启，偏置通电限时、过流／过压／功率异常与停止关断均有效。
7. 没有独立适配器可选“仅软件保护”；独立模式要求真正接入适配器。软件不能保证强杀、USB 断开或主机掉电后断电；输出关闭不等于线圈储能释放。

**触发阈值使用 Pico 输入端的原始 V，不是探头换算后的电压。** 阈值高于实际信号会等待自动触发，增加通电耗时。50 次采集按 100 ms 开始间隔，仅开始时刻跨度就至少 4.9 s，3 s 上限无法采完。

详见 [偏置扫描指南](BIAS_SCAN_GUIDE.md)。CLI 仿真：

```powershell
.\.venv\Scripts\python.exe -B -m pico4824a bias-scan --config configs/bias-scan.example.json --simulate
```

## 数据保存与换机迁移

| 数据 | 项目内默认位置 |
|---|---|
| 实时／CLI 采集 | `data/captures/` |
| 参数扫描 | `data/sweeps/` |
| 小信号 LCR／校准 | `data/lcr/` |
| 大信号 LCR | `data/lcr_big/` |
| LCR 线性度 | `data/lcr_linearity/` |
| 干扰实验 | `data/interference/` |
| 偏置扫描 | `data/bias_scans/<扫描任务>/电流…A/<幅值点>/` |
| 带通派生文件 | `data/analysis_exports/` |

扫描任务自动保存；“导出”用于下载 CSV／JSON 或完整任务 ZIP。实时测量使用“保存”。可读命名与目录详见 [数据保存指南](DATA_STORAGE_GUIDE.md)。

换电脑时：

- Git 不包含旧电脑实测数据、`.venv`、本机配置和系统驱动。需要的数据／校准文件另行复制或 ZIP 迁移，新电脑重新安装环境。
- 按需复制 `configs/storage-naming.json`、`configs/software-updates.json` 和自己的 CLI 配置；检查本机路径。
- 浏览器采集／显示设置不随 Git 自动迁移，新电脑重新核查通道、探头系数、触发、AWG 和安全限值。
- 文件选择器浏览的是**服务电脑**磁盘，不是手机／客户端磁盘。
- 程序保存结果限制在本项目目录。`Pico_4824A` 参考工程不需要复制，不能作为写入／运行目录。

## 更新版本

已有新版本：**顶部设置 → 软件更新 → 检查更新 → 更新并重启**。

- 间隔可设 1 分钟～1 年（365 天），默认 1 小时；只提示，不自动安装。
- 需要 Git、`btf`、正确 origin 和干净工作目录。
- 仪器运行、输出未知或待保存数据时阻止安装；依赖／驱动变更提示先处理环境。
- 更新后后端重启、浏览器重连。只刷新网页不会升级旧后端。

首次启用、ZIP 版迁移或手动更新：停止任务并确认断电，在启动终端 `Ctrl+C`，然后执行：

```powershell
git status --short
git branch --show-current
git pull --ff-only origin btf
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_web.ps1
```

`git status --short` 有输出时先保存自己的修改，不使用强制 reset／clean。ZIP 版改用 Git 副本。详见 [软件更新说明](SOFTWARE_UPDATE_GUIDE.md)。

## 常见问题

| 现象 | 处理 |
|---|---|
| `git`／`python` 无法识别 | 安装并加入 PATH，重开终端；Python 使用标准 x64 CPython |
| GitHub 克隆／更新超时或 SSL 错误 | 检查 GitHub 访问、代理、系统时间与证书；这是网络问题，不反复安装仪器驱动，也不关闭 TLS 验证 |
| 找不到 Python 3.10+／生成 `.venv/bin` | 使用 Windows x64 Python，重新运行 setup；不复制 Linux `.venv` |
| 禁止运行 PowerShell 脚本 | 使用本文带 `-ExecutionPolicy Bypass -File` 的命令 |
| `No module named numpy / picosdk / pyvisa` | 使用本项目 `.venv\Scripts\python.exe -m pip ...`，不要安装到 `(base)` |
| 不能加载 ps4000a DLL | 官方 x64 SDK、Python 位数、真实 DLL 目录及 `PICO_SDK_PATH` |
| Pico 找不到／忙 | 检查 USB，关闭官方 App／第二个后端；Web 占用时不再开实机 CLI／GUI |
| `Could not locate a VISA implementation` | 安装系统 NI-VISA，再重启服务；只有 PyVISA 不够 |
| VISA 资源为空 | 检查 NI MAX、设备管理器、数据线和设备占用 |
| ResourceManager 不支持 context manager | 用本文 `rm=...; ...; rm.close()`，更新 btf，弃用旧 `with` 写法 |
| 输出状态未知 | 核实输出，使用“确认断电并恢复”；刷新／消除弹窗不代表确认断电 |
| 采集慢／第一组通电超限 | 检查原始触发阈值、自动触发等待和真实耗时；上限涵盖整个电流档 |
| 实机限值待定／未接入独立适配器 | 填实验限值；无适配器选仅软件保护，不伪造独立确认 |
| 网页打不开／4824 被占用 | 查看终端日志，避免重复启动；需要时改 4825；LAN 启动器用 `i`／`l` |
| 手机访问不了 | 服务电脑 LAN IP、同一可信网络、专用网络、防火墙、客户端隔离 |
| 一键更新禁用 | 查看设置中的 Git、分支、本地修改、网络、仪器状态或环境变更原因 |

网络安装失败可重新运行 `setup.ps1`；脚本提供镜像与官方 PyPI。自行指定官方源：

```powershell
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r requirements.txt
# 使用 IT6524D 时另装
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple "pyvisa>=1.14"
```

## Linux 与详细文档

Linux／R76S ARM64 按 [Linux 部署指南](deploy/linux/README.md) 操作。Windows `.venv` 与 DLL 不能搬到 Linux；旧容器首次启用一键更新需重建含 Git 的镜像。本文的 PowerShell 命令不适用于 Linux shell。

| 文档 | 内容 |
|---|---|
| [配置与使用说明](配置与使用说明.md) | 采集字段、操作细节、LAN 管理 |
| [测量与分析参考](docs/MEASUREMENT_REFERENCE.md) | 原 README 的 LCR 原理、N/f、滤波与时频、AWG |
| [偏置扫描指南](BIAS_SCAN_GUIDE.md) | 偏置／幅值扫描、保护、统计 |
| [数据保存指南](DATA_STORAGE_GUIDE.md) | 命名、目录、ZIP |
| [软件更新说明](SOFTWARE_UPDATE_GUIDE.md) | 自动检查、一键更新和镜像要求 |
| [磁路诊断预览](MAGNETIC_DIAGNOSIS_PREVIEW.md) | 预览范围与当前限制 |
| [前端架构](FRONTEND_ARCHITECTURE.md) | 共享布局及交互 |

目录：`pico4824a/` 后端；`web/` 前端；`configs/` 示例；`data/` 结果；`tests/` 回归；`archive/` 历史试稿，不参与应用运行。

开发者回归，不属于首次安装必做步骤：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\run_validation.ps1
```

最低部署检查点：**btf 正确 → 本项目 x64 Python 可用 → 仿真保存成功 → 网页可打开 → Pico 实机识别 → 使用电源时 USB 身份及断电确认成功**。完整带电实验需在新电脑上验证真实接线、触发和允许限值。
