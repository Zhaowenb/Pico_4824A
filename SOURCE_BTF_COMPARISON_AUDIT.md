# Pico_4824A 与 Pico_4824A_btf 功能对照审查

审查日期：2026-10-09。

SOURCE：`F:\Project\01guided_waves\software\Pico_4824A`，只读。

TARGET：`F:\Project\01guided_waves\software\Pico_4824A_btf`。

## 结论

**未发现原项目主程序的 Python 功能函数或 Web API 被删除，但 BTF 还不能认定为完整的交互等价版本。**

当前缺口集中在：控件迁入 Inspector 后的显示与重绘、带通输入语义、时频参数缓存、历史数据访问，以及尚未完全统一的交互/存储底层。这些问题会出现“计算模块还在，用户却不能正确观察或操作”的情况。

本轮仅审查，未修改业务代码、未执行实机采集，也未改动 SOURCE。新增本报告；逐文件差异、控件对照和浏览器观察记录位于 TARGET `.test-tmp/comparison-audit`。

## 审查方法与范围

1. 比较原项目全部 17 个 Python 模块，使用 AST 对照各函数与方法，并阅读所有变化段。
2. 对照原 Web 页面、JavaScript、样式、CLI、桌面入口、依赖声明和启动脚本。
3. 对照 515 个原页面 ID：全部存在于 BTF 静态 HTML 或动态 HTML 代码中。
4. 对照 344 个带 ID 的原控件/画布的静态属性：仅默认分析路径改到 TARGET，以及带通启用复选框增加 `hidden`。其余记录的 type/value/min/max/step/checked/disabled/hidden 保持一致。
5. 追踪原服务器引用的外部 `Pico_4824A_error`：八个前端/说明文件均迁入 TARGET `web/interference`，实验指南 `guide.js` 相同。
6. 在 TARGET 的 4876 预览中，读取真实 `data/A1.npz`，观察 Lens 与参数状态、实际请求次数及 AWG 画布几何。未启动硬件采集或偏置输出。
7. 校验 SOURCE 既有清单中 622 个文件的 SHA256：全部不变。

没有用“文件存在”替代功能判断，也没有把上轮 112 项通过当作本轮发现问题不存在的证据；上述复现路径尚未纳入原回归覆盖。

## 原功能保留情况

| 功能 | 代码对比结果 | 需要注意 |
|---|---|---|
| Pico 驱动、同步采集、触发与 AWG 硬件时序 | `device.py` 完全相同 | 本轮未验证实机 |
| 采集配置、通道与输入量程 | `config.py` 完全相同 | 原配置写入边界仍较分散 |
| Hann、消振、爬升/保持等波形生成 | `waveforms.py` 完全相同 | 新 UI 的 AWG 预览退化 |
| 参数扫描、回波/尾振指标、推荐与归档重评 | 原函数保留；采集执行变化仅存储命名/目录 | 未发现计算算法删除 |
| 小信号 LCR、精准电阻复数校准 | 原函数保留；目录发现与命名变化 | 原 SOURCE 测量目录不能直接从 BTF 的 LCR 文件夹分析入口载入 |
| 大信号 LCR、Monitor 保护与处置 | 原函数保留；采集执行变化仅存储命名/目录 | 前端通过模式节点重挂载，仍保留原控制流程 |
| 线性度、THD、波形畸变与对齐 | 原函数保留；`big_signal_linearity.py` 完全相同 | 旧文件夹的访问范围需处理 |
| Raw / Filtered / FFT / STFT / WPD / CWT | 原后端计算保留；`time_frequency.py` 完全相同 | 带通控制、缓存与方法选择存在已复现问题 |
| 双通道 EX 实验分析 | `experimental_modes.py` 完全相同 | 共用带通状态缺乏可见说明 |
| 十二项干扰实验、会话、频谱、统计与证据 | 原后端分析保留，指南完整迁入 | 历史会话根目录改变，原两个会话尚不可直接恢复 |
| NPZ / CSV、元数据、原始时间轴和单位 | 原文件格式保留 | 命名及默认路径按用户要求改变，属于有意改动 |
| GUI、CLI、本机 Web、局域网启动 | 原入口均保留；启动脚本相同 | 桌面 GUI 未套用网页主题，这是范围差异 |

原服务器的所有 API 字面路由仍存在；BTF 额外增加偏置扫描、文件浏览和命名设置接口。

## 优先修复：已复现的交互退化

### P1-1 · 实时测量 AWG 预览消失

- 原样式为 `#awgPreviewCanvas` 提供 150px 高度。
- BTF 删除该专用高度，将画布移动到共享 Stage 的 Inspector；共享 `.wg-stage canvas` 使用 `height:100%`。
- 实际打开实时测量的参数设置后，画布 CSS 几何为 **263×0**，bitmap 为 **0×0**，元数据却显示“8,192 点 · 50.00 μs”。
- 原 `window.resize` 中的 `scheduleAwgPreview()` 也被移除；新 `signal-analysis.js` 的 measure painter 仅绘制 `drawScope()`。

需要同时补画布尺寸和可见时重绘。仅保留 ID、API、预览算法不足以恢复功能。

证据：`web/ui/workstation.css:47`、`web/views/signal-analysis.js:72`、`web/app.js:819`；原 `web/styles.css:108`。

### P1-2 · Raw 显示与数学视角的带通输入不一致

- 原复选框 `analysisFilterEnabled` 在 BTF 被永久隐藏。
- `setAnalysisLens()` 进入 Filtered/Mix 时将其置为 true，回到 Raw 时只改变显示系列，没有置回 false。
- 实际经过 **Filtered → Raw** 后：`analysisLens=raw`、`showRaw=true`、`showFiltered=false`，但 `analysisFilterPayload().enabled=true`。
- STFT/WPD/CWT 和 EX 都使用这个共用 payload；带通参数块又只在 Filtered/Mix/FFT 显示。

这不代表 Raw 曲线本身被替换；问题是后续数学分析可能使用隐含带通输入，用户没有明确的关闭入口和来源标识。

应把“原始/带通输入”与“Raw/Filtered/Mix 显示”分开建模，并在数学 Lens 中明确显示当前输入处理状态。

证据：`web/index.html:428`、`web/app.js:2592`、`web/app.js:4075`、`web/views/signal-analysis.js` 的 filterPanel 可见规则。

### P1-3 · 时频缓存没有完整参数身份

- `setAnalysisLens()` 主要通过 `timeFrequencyResult.method` 判断是否复用。
- 没有比较文件、通道、时间范围、带通、窗长/重叠率、WPD/CWT 参数的完整计算快照。
- 实际先计算 80µs STFT，再把控件改为 160µs，切到 FFT 后返回 STFT：控件为 **160**，结果的 `details.window_us` 仍为 **80.00000000000193**，状态仍为 **READY**。

这会误导观察者。应按完整计算参数生成缓存键，并明确区分 pending/processing/ready；显示结果必须携带其实际计算快照。

证据：`web/app.js:2600`，观察记录 `firstSTFT/reusedSTFT`。

### P1-4 · 参数面板切换方法出现假 Loading

- `timeFrequencyMethod` 的 change handler 调用 `setAnalysisLens(method, false)`。
- 该流程清空结果并显示“正在计算”，但 false 阻止了计算请求。
- 实际切换到 WPD 后：`result=null`，显示 **WPD 正在计算…**，观察窗口内新增计算请求为 **0**。

顶栏 Lens 和参数面板的方法选择应走同一个入口；自动计算与手动应用必须明确区分，不能显示尚未发生的计算。

证据：`web/app.js:4437` 与 `web/app.js:2600`。

## 兼容性及底层统一缺口

### P1 · 历史数据的读取与导入范围不一致

文件浏览器允许选择 SOURCE `data`，但 LCR 文件夹分析后端只接受 TARGET 对应目录。用户可能选到源目录，随后被后端拒绝。原数据格式没有丢失，但缺少一致的只读加载或复制导入流程。

原干扰会话保存在 `Pico_4824A_error/data`，BTF 改为 `data/interference`。原两个 UUID 会话目录在 TARGET 都不存在，BTF `_folder()` 只搜索自己的 root。旧格式兼容不等于旧数据已经可访问。

应提供统一“只读打开/导入到 TARGET”，对涉及重新分析落盘的功能明确在 TARGET 建立派生结果，不能写回 SOURCE。

证据：`pico4824a/file_browser.py:5`、`pico4824a/web.py:958`、`pico4824a/interference.py:20`。

### P2 · 写入边界尚未覆盖所有持久化函数

NPZ/CSV 已使用 `output_path()`，任务目录也已限制 TARGET。但 `AcquisitionConfig.save()` 和 `write_resistor_calibration()` 等仍自行 `Path(path)` 后写入。正常 Web 任务传入 TARGET，当前并未观察到 SOURCE 写入；然而底层还没有统一拒绝 SOURCE 路径，显式 CLI/模块调用可绕过捕获文件的保护。

这属于统一底层仍未完成，不能声称所有保存函数已经共享同一保护。

证据：`pico4824a/config.py:203`、`pico4824a/lcr.py:499`、`pico4824a/storage_naming.py:18`。

### P2 · 存储会话还不完全统一

实时测量的 NPZ/CSV 共用同一采集目录；单数据分析的 `export_filtered()` 每次调用都会新建目录，因此同一参数的 NPZ 与 CSV 导出仍分成两个任务文件夹。偏置扫描可导出整次 ZIP，其他任务尚没有共享的整任务打包入口。

现有名称已经比原版可读，但还缺统一 DataSession（任务身份、配置快照、数据索引、跨格式导出、完整性状态），不能把所有功能都视为同一套完整保存机制。

### P2 · 统一的是 Shell，状态与交互底层仍分散

- Stage、Inspector、主题、尺寸与部分动画已共享，这是已经完成的部分。
- 原业务 `app.js` 由 4208 行变为 4540 行，仍同时拥有配置、路由、请求、计算状态和绘图。
- 各页仍使用不同全局结果变量与可见控制，LCR 保留 `lcrModeRecords` 的 DOM detach/reattach。
- 干扰实验保留独立 app、独立初始化和会话恢复，导航进入它仍是完整页面加载。
- Canvas 注册流程遗漏 AWG，说明还缺对所有数据画布统一的生命周期管理。

后续应统一 RequestCoordinator / ResultStore / CanvasView / DataSession；业务算法可保持现状，不必为共享 UI 重写采集或分析。

## 有意变化、未迁移内容和新增功能边界

以下不应算成原主程序功能缺失：

- Light/Dark、主题强调色、官方通道身份色、Inspector、Lens、专注模式：按用户要求新增。
- 可读命名、分层偏置目录、派生导出移到 TARGET、原始保存拒绝 TARGET 外写入：按用户要求改变。
- SOURCE `analysis/` 中的单次历史窗口/相位分析脚本未迁入。这些是绑定历史采集路径的辅助脚本，不是 Web 主程序依赖；若要产品化，可列为独立“高级分析工具”工作。
- SOURCE `sites/n-over-f-sweep-dashboard` 未迁入。当前首页仅返回 `SkeletonPreview`，属于另一份前端起始工程，不是已实现的 Pico 功能。
- 偏置扫描是 BTF 新增功能，SOURCE 没有对照实现。当前真实 USB 驱动与仿真闭环在，但独立保护 `SafetyProtection.ready()` 默认 false，温度 Provider 仍是接口；实机扫描锁定是已确认的边界，不能为了“补齐”而取消。
- 桌面 GUI 保留原操作页面，仅保存默认机制改变；本轮网页主题并没有扩展到 Tk GUI。

## 建议执行顺序

1. 修复 AWG 画布尺寸与可见生命周期。
2. 明确所有 Lens 的数据输入与带通状态。
3. 统一数学视角入口、计算快照、缓存失效和请求状态，消除假 Loading。
4. 统一 SOURCE 历史数据的只读打开和 TARGET 导入/派生路径。
5. 统一持久化边界与 DataSession，补全多格式/整任务导出。
6. 再拆分全局 app 状态与绘图生命周期，保留现有算法和硬件时序。

优先补的是数据正确性与可操作性；这次审查没有发现需要复制另一套 FFT/STFT/CWT/WPD 算法的理由。

## 修复状态
本报告是修复前的审计记录。上述问题的实施与验证结果见 PROJECT_REPAIR_REPORT.md；请勿把此处的历史复现状态当作当前版本状态。
