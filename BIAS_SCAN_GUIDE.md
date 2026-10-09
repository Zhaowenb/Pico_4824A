# 偏置电流扫描 · 运行与安全接口

开发与运行目录：`F:\Project\01guided_waves\software\Pico_4824A_btf`。
`Pico_4824A` 仅作为只读参考。本功能没有修改采集、滤波或时频算法。

## 网页

```powershell
Set-Location F:\Project\01guided_waves\software\Pico_4824A_btf
.\.venv\Scripts\python.exe -B -m pico4824a web --host 127.0.0.1 --port 4824
```

打开 `http://127.0.0.1:4824/bias-scan`。请先退出旧服务，避免 Windows 的端口复用让旧服务继续接收请求。独立验收服务使用 `4876`，可访问 `http://127.0.0.1:4876/bias-scan`。

1. 在实时测量页配置采样、通道、AWG 与触发；无需执行采集。
2. 打开“偏置电流扫描”。默认勾选仿真；默认 0–6 A / 0.5 A / 10 次 / 2000 μs / 100 ms。
3. 配置启用的 PZT 通道、完整位于记录内的直达波窗口，以及带通频段。默认开启滤波评价，示例频段 60–90 kHz、过渡带 5 kHz；请按实际信号频率调整。
4. “参数设置”打开共享右侧 Inspector；不会增加页面高度。
5. 启动时重新复制实时测量设置，服务器冻结快照，覆盖记录长度。进行中的输入不可编辑。
6. 每档断电后保存、计算并更新曲线；选择档位与重复查看评价实际使用的 PZT 波形，原始 NPZ 保持不变。误差棒为样本标准差。
7. “停止并断电”优先关闭偏置输出，再停止 Pico；导出逐档/逐次 CSV、JSON 和配置快照。

通道沿用共享官方通道色；响应曲线沿用用户选择的工作站强调色。Light/Dark、设置、专注、Inspector、导航与 Reduced Motion 均使用现有底层。

## CLI 仿真

```powershell
.\.venv\Scripts\python.exe -B -m pico4824a bias-scan --config configs/bias-scan.example.json --simulate
```

示例安全限值为 `null`。仅仿真采用有效限值 40 V / 最大通电 3 s / 稳定超时 1 s / 冷却 0.1 s，并在配置快照中标明。实机没有这些回退值。

仿真直接复用现有 Pico 模拟采集，不额外编造“电流影响 Vpp”的物理模型。各档模拟波形相同、Vpp 均值并列时会选最低电流。这只检验闭环、保存与统计，**不能用于判断电磁铁的最佳偏置**。

## 实机门槛与可选依赖

```powershell
# 只安装到 TARGET 的虚拟环境；不自动安装系统 VISA 驱动
.\.venv\Scripts\python.exe -B -m pip install -e ".[bias]"
```

PyVISA 延迟导入；缺包或缺 VISA 后端时页面给出明确消息，不影响仿真和其他页面。用户必须明确选择 USB VISA 资源。连接核对 `*IDN?` 的 ITECH / IT6524D 型号，随后发送关闭命令并通过 `OUTP?` 确认。不提供网页直接开启电源的入口。

驱动核对型号后发送 `SYST:REM` 进入远程控制，再立即关闭并确认输出；使用 `VOLT`、`CURR`、`VOLT?`、`CURR?`、`OUTP ON/OFF`、`OUTP?`、`MEAS:CURR?`、`MEAS:VOLT?`。厂家参考：[IT6500C/D 编程指南](https://oss.itechate.com/uploadfiles/2019/01/201901251035103510.pdf)，已核对厂家 V2.1 手册的[经销商镜像](https://www.calpower.it/gallery/cpit6500cd-programming-guide-en2020.pdf)（印刷页 17、26–29、33、44–45）。指令通过模拟 VISA 测试；实际 IT6524D USB 通信、固件响应与查询耗时尚未验证。

实机启动须同时满足：

- 明确填写电压合规上限、每档最大通电、稳定超时、冷却基准；稳定超时必须小于最大通电并大于稳定保持时间。
- 目标、实际电流安全上限均不超过 6 A；采集次数为 5–10。
- 参数设置 → 关断保护模式，网页默认“仅软件保护”，不要求独立适配器或硬件确认框。最大通电截止线程、过流/通信/采集异常断电、停止与退出断电仍保留。
- 如果选择“软件 + 独立超时断电适配器”，才要求确认续流/钳位、记录保护配置并接入真实适配器；勾选确认框不代表硬件已接入。
- 电源身份、关闭确认、PZT 通道、时间窗、内存与磁盘预算均通过预检。

独立保护接口位于 `pico4824a/bias_scan/safety.py`：实现 `SafetyProtection.ready / arm / trip / disarm`，把实际适配器赋给 `WebControlState.bias_protection`；模块方式也可传给 `BiasScanController(protection=...)`。适配器应调用独立于当前 Python 进程的物理超时断电装置，回调应有明确短超时；不可用空实现把 `ready()` 改为 true。示例 JSON 使用 `protection_mode: "software"`。CLI 填写限值后可使用软件模式；独立模式仍需在模块入口注入实际适配器。旧配置未指定模式时保持 independent，避免静默改变原保护要求。

温度输入实现 `TemperatureProvider.read()`，返回 `TemperatureReading(celsius, monotonic_time)`，赋给 `WebControlState.bias_temperature` 或传入控制器。温度缺失、非有限、过期、超限和不能恢复都会中止。没有传感器时默认使用电流相关冷却，也保留固定冷却选项。

软件的关闭确认仅表示电源返回输出关闭；不表示线圈储能已释放。进程强杀、主机断电、USB 断开等情况不能靠 `finally` 保证发送关闭命令，软件模式不提供这些故障的硬件保护；感性负载的续流/钳位属于外部电气保护，软件输出关闭不能代替它。本轮没有实机通电试验。

## 电流相关冷却

网页及示例配置默认 `cooling_mode: "current"`。`cooldown_s` 为 6 A 档的冷却基准，`cooldown_min_s` 为最短冷却，两者需由用户明确填写（不代填安全值）。每档等待 `max(cooldown_min_s, cooldown_s × (I / 6 A)²)`；I 取目标与通电期间实测峰值的较大值，0 A 断电基线为 0 秒。

这是依据电阻发热 I² 趋势的可调调度规则，不是热模型或温度测量，不自动宣称线圈已经冷却。实际通电时长也会影响发热，仍应按线圈热表现校准基准时间；有温度输入时可选 `current_temperature`，还需满足恢复阈值。旧配置 `fixed` / `temperature` 保持兼容。

每档 `summary.csv` / `summary.json` 保存 `cooling_mode`、`cooldown_current_a`、`cooldown_s`，网页逐档统计可查看冷却时长。冷却从确认 OFF 开始，与保存分析重叠。

## 输出未知时恢复

第一档完成后若出现输出未知，电源区会保留具体指令与通信错误。点击左侧“确认断电并恢复”：后台仅发送 OFF、查询 OUTP?，必要时重新打开同一 USB 资源并复核型号；确认输出关闭后解除启动锁定，无需刷新网页，不自动续跑旧扫描。恢复失败仍保持未知与锁定。扫描未退出或安全线程仍运行时不允许解除占用。

关闭确认采用最多三次 OFF-only 确认，处理短暂 ON 回读和查询超时；查询超时后清理 USB 通信，避免旧测量回复被误当作 OUTP?。识别/初始关断使用 2000 ms 启动超时，通电扫描保持原 200 ms 通信超时和通电截止机制。

原任务失败状态保留，`off_error` 记录具体关断错误，`recovery` 与 `manual_off_recovery` 事件另行记录确认断电。软件关断和恢复未做实际电源验收。

## 时序与结果

`断电预检 → 设置 → 通电 → 稳定 → 连续采集 → 优先断电 → 保存/分析/冷却 → 下一档`。

- 0 A 始终断电采集。其他档的通电计时从发送 ON 前开始，包含稳定、配置、触发和传输。
- 开始间隔为 start-to-start；超时不追赶，不加额外补睡眠。
- 通电期间只留内存中的原始结果、遥测与事件；不压缩或写波形文件、不计算统计、不刷新大图。
- 独立截止线程不等待采集返回；遥测线程检查过流、超电压和温度。VISA 查询有有限超时，OFF 请求锁定普通通信。
- 最后一次 capture 返回后优先发送 OFF；异常同样先 OFF，再停止 Pico。每档、整个任务、停止与服务退出都有关断收尾。
- OFF 失败或不能确认时保留“输出状态未知”，触发保护接口并锁住所有仪器任务。安全线程未返回时同样保持占用锁；不得自动重试、恢复或继续下一档。
- 冷却从首次 OFF 确认开始，可与保存分析重叠。实际批次耗时单独记录；`capture_batch_target_met` 表示是否达到 ≤1.5 s，不能把 2000 μs 当成整档耗时。

结果目录为 `data/bias_scans/<名称__日期__仿真或实机__范围步长次数>`：

| 文件 | 内容 |
|---|---|
| `config.json` | 冻结的采集/扫描配置、身份、仿真标志、有效限值 |
| `00_电流0.000A_断电基线/重复01__PZT-A__原始波形.npz` 等分档文件 | 复用原 `save_npz()`，可直接在单数据分析页打开 |
| `runs.csv` | 目标/实际电流、遥测 wall/monotonic 时间、采集开始/结束、Vpp、有效性、原始文件 |
| `summary.csv` | 次数、实际电流统计、Vpp 均值/样本 SD、资格、实际批次与通电耗时 |
| `result.json` | 完成/停止/错误、原因、最佳已测点与并列、关闭状态、安全事件及所有结果 |

CSV 采集起止为单调时钟秒，可计算间隔；遥测同时含 UTC Unix wall time。Vpp 默认先使用现有零相位 FFT 带通算法处理整段原始 PZT 波形，再对直达波窗口求 `max-min`。关闭“带通滤波后 Vpp”则评价原始信号。滤波不修改原始 NPZ；配置与逐次 CSV 记录评价方式、频段、过渡带，预览与统计使用同一份评价信号。标准差 `ddof=1`。输入溢出、非有限 PZT、非法采样元数据、缺窗/样本不足或不完整档不能参与最佳评选。均值严格相等时选最低电流并保留所有并列值；不插值、不声称连续范围全局最优。保存失败会中止并记录原因；磁盘故障时不能承诺写出尚在内存中的数据。

每次扫描使用可读任务目录，每档电流单独建目录，保存该档逐次 NPZ、`runs.csv` 和 `summary.json`。顶部齿轮设置默认名称，本页左侧可填写本次扫描名称。导出默认下载整个扫描的 ZIP；自动保存无需点击导出。完整目录、全站命名与兼容性说明见 [DATA_STORAGE_GUIDE.md](DATA_STORAGE_GUIDE.md)。

## 测试与回退

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP=Join-Path (Get-Location) '.test-tmp'
$env:TMP=$env:TEMP
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_*.py
# 浏览器测试需要环境已有 Playwright 与 Edge；不新增项目生产依赖
# WAVEGUARD_PLAYWRIGHT_MODULE 指向已有 Playwright，WAVEGUARD_URL 指向测试服务
node tests/bias-scan-browser.cjs
```

`tests/source-readonly-manifest.json` 的 SOURCE 文件 SHA256 校验纳入回归。所有输出、缓存与测试临时目录都位于 TARGET。改造前本地 Git 标签：`bias-scan-before`（`ddea1d0`）；本轮代码提交保存在当前本地分支，不自动推送。

实机验收顺序仍为：断电通信预检 → 保护验证 → 低电流单档 → 完整 0–6 A。电压、通电、稳定与冷却限值尚未确认，实际电源/Pico 通信与完整实机扫描仍为 **未验证**。软件模式已通过模拟设备的实机代码路径与故障注入测试；这不替代实机验收。
