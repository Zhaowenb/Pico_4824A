# `/file-analysis` 移植与重构记录

## 路径边界

- SOURCE_ROOT（只读）：`F:\Project\01guided_waves\software\Pico_4824A`
- TARGET_ROOT（唯一工作目录）：`F:\Project\01guided_waves\software\Pico_4824A_btf`
- 移植前 TARGET_ROOT 已存在 7 个独立 HTML 视觉稿；全部保留，未覆盖或删除。

## 复制清单

| SOURCE | TARGET | 原因 |
|---|---|---|
| `web/index.html` | `web/index.html` | 保留单数据分析全部控件、结果画布及全局 DOM ID。 |
| `web/app.js` | `web/app.js` | 页面与请求逻辑为共享单体脚本；保留原载入、处理、绘图、交互和导出实现。 |
| `web/styles.css` | `web/styles.css` | 原全局样式与分析页样式；作为 baseline 后续只在 TARGET 重构。 |
| `pico4824a/__init__.py` | `pico4824a/__init__.py` | Python 包入口，依赖 config/device。 |
| `pico4824a/web.py` | `pico4824a/web.py` | 本机 HTTP 服务、`/file-analysis` 页面路由及全部既有分析 API。 |
| `pico4824a/config.py` | `pico4824a/config.py` | 设备配置与通道名称，被公共模块导入。 |
| `pico4824a/device.py` | `pico4824a/device.py` | `CaptureResult` 等共享类型；SDK 仅在真实设备连接时延迟加载。 |
| `pico4824a/waveforms.py` | `pico4824a/waveforms.py` | device/config 的采集信号工具依赖。 |
| `pico4824a/analysis.py` | `pico4824a/analysis.py` | NPZ/CSV 加载、滤波、FFT、处理与导出算法。 |
| `pico4824a/storage.py` | `pico4824a/storage.py` | analysis 与公共路由使用的存储格式实现。 |
| `pico4824a/sweep.py` | `pico4824a/sweep.py` | `analysis.py` 导入的扫描公共指标/类型。 |
| `pico4824a/time_frequency.py` | `pico4824a/time_frequency.py` | 原始 STFT/WPD/CWT 计算实现。 |
| `pico4824a/experimental_modes.py` | `pico4824a/experimental_modes.py` | 原始双通道实验分析实现。 |
| `pico4824a/lcr.py` | `pico4824a/lcr.py` | `web.py` 顶层导入的公共路由依赖。 |
| `pico4824a/big_signal_lcr.py` | `pico4824a/big_signal_lcr.py` | `web.py`/LCR 线性分析的顶层依赖。 |
| `pico4824a/big_signal_linearity.py` | `pico4824a/big_signal_linearity.py` | `web.py` 与 LCR 线性分析的顶层依赖。 |
| `pico4824a/lcr_linearity.py` | `pico4824a/lcr_linearity.py` | `web.py` 顶层导入的公共路由依赖。 |
| `pico4824a/interference.py` | `pico4824a/interference.py` | `web.py` 顶层导入；分析页启动时必须可导入。 |
| `requirements.txt` | `requirements.txt` | 记录原运行时依赖（NumPy、PicoSDK）。 |
| `pyproject.toml` | `pyproject.toml` | 保留原 Python 项目元数据与包定义。 |
| `data/A1.npz` | `data/A1.npz` | 一份真实 40,000 点、A/B/C/H 四通道采集，作为 TARGET 测试夹具；API 导出写入其 TARGET 副本旁的 `processed/`。 |
| `../Pico_4824A_error/{index,app,styles,manual,guide}` | `web/interference/` | 原 `/interference` 路由实际依赖的独立前端资源；复制后 TARGET 不再从工作目录外读取页面。 |

初始 `/file-analysis` 阶段未复制 `cli.py`、`__main__.py`、`gui.py` 与其它功能测试；后续整站阶段已把这些启动入口和完整测试套件复制到 TARGET，使 TARGET 可作为独立的 WaveGuard 工作目录运行。整份历史采集目录和硬件缓存仍未复制。

## `/file-analysis` 原接口

保持原路径、请求和响应实现：

- `POST /api/analysis/browse`：JSON `{path}`；返回 `kind/root/files/count/truncated`。
- `POST /api/analysis/process`：JSON `{path, channels, filter, show_raw, show_filtered, spectrum, time_start_us, time_end_us}`；返回 waveform、spectra、metadata 及频率/时间信息。
- `POST /api/analysis/run-preview`：JSON `{path, max_points}`；返回抽样 time/channel 数组、metadata、display_step。
- `POST /api/analysis/time-frequency`：JSON `{path, channel, method, start_us, end_us, frequency_min_hz, frequency_max_hz, floor_db, ...method parameters, filter}`；返回 method、time/frequency 轴、values_db、details。
- `POST /api/analysis/experimental-modes`：JSON `{path, reference_channel, comparison_channel, filter, ...analysis windows}`；返回实验状态、对齐/对称性/相似度数组及 metrics。
- `POST /api/analysis/export`：JSON `{path, format, filter}`；返回 `{path}`；原始数据保持不变，输出写入输入文件同目录下 `processed/`。

## 实施状态

- 只读依赖追踪与复制清单：完成。
- TARGET 文件复制：完成；21 个复制文件逐一通过 SHA-256 字节级比对。
- baseline API/功能检查：完成；先于任何视觉修改执行。TARGET Python 包 compileall 与 `pico4824a.web` 导入通过，原 `web/app.js` 语法检查通过。
- baseline HTTP 检查：`/file-analysis`、`/app.js`、`/styles.css` 返回正常；浏览、首次加载、Raw、Filtered、FFT、STFT、WPD、CWT、Experimental、NPZ/CSV 导出通过。
- baseline 数据保护：使用复制到 TARGET 的真实 `A1.npz`（40,000 点；A/B/C/H）；导出进入 TARGET `data/processed/`；输入 NPZ SHA-256 前后相同。
- baseline API 响应：12 组签名保存在 `tests/file-analysis-baseline-signatures.json`，用于视觉重构后做逐项相同输入对照。
- TARGET 视觉改造：完成。`web/index.html` 增加 WaveGuard Signal Lab 标题、中文优先的 Lens 导航与共享数据 Inspector；`web/styles.css` 仅在 `/file-analysis` 页面建立 Porcelain shell、Midnight data stage、钴蓝信号层级和响应式布局；`web/app.js` 仅加入 Lens/状态/时间窗同步、过期请求保护、旧数据清空和画布显示协调，并调整分析画布的展示配色。原计算函数、请求路径和请求参数结构保持不变。
- Lens 行为：Raw / Filtered / Mix / FFT / STFT / WPD / CWT / Experimental 互斥显示；时域与时频视图共用时间范围；文件重载会清除旧派生结果；切换后重绘当前可见 Canvas；过期时频或实验响应不会覆盖当前文件。
- 视觉改造后 API 对照：11 组相同请求响应签名与 baseline 一致；另验证了 B/H 通道子集；Raw、Filtered、FFT、STFT、WPD、CWT、Experimental、NPZ/CSV Export 均通过。
- TARGET 静态检查：`node --check web/app.js`、`python -m compileall -q pico4824a tests`、`python tests/file_analysis_static_qa.py` 通过；HTML 无重复 ID，8 个 Lens ID 齐全，字面 DOM ID 引用均在 HTML 中，CSS 顶层解析通过。
- 指定尺寸：按 CSS 断点、容器与 `clamp()` 规则核算 `1366×768 / 1440×900 / 1600×900 / 1920×1080 / 2560×1440`；图表区域宽度约 `937 / 1005 / 1152 / 1460 / 1780 px`，高度约 `392 / 459 / 459 / 551 / 570 px`。这是静态尺寸核算，不是浏览器截图验证。
- HTTP 服务日志：页面与静态资源 GET、各分析 API 与导出请求均返回 HTTP 200，无服务端异常。运行环境的 NumPy 在首次计算时输出一条 `longdouble` 类型探测 `UserWarning`；分析接口及响应签名均通过，未改算法或抑制该警告。
- 浏览器控制台与实际截图：后续整站阶段已在本地浏览器实测，见下方“整站 UI 阶段”。
- SOURCE_ROOT 保护复核：移植的 18 个后端/项目清单/数据夹具文件 SHA-256 与 SOURCE_ROOT 相同；本轮没有对 SOURCE_ROOT 执行写入命令。

### 视觉微调：Lens 参数位置

- Git 基线提交 `e1543f9` 保存了调整前的可运行版本。
- FFT 与 STFT / WPD / CWT 参数区通过 `web/app.js` 移入 Lens 导航栏的“参数设置”浮层，原 DOM ID 与事件绑定保留；原侧栏不再堆叠这些参数区。
- 浮层锚定在 Lens 导航栏右侧并覆盖内容层，不进入页面文档流，因此打开设置不会增加页面高度；Raw、Filtered、Experimental 下隐藏该入口。
- 本次仅调整 TARGET 的 `web/index.html`、`web/styles.css`、`web/app.js` 与本说明；数据计算、API 请求和 SOURCE_ROOT 均未改动。

## 整站 UI 阶段（2026-09-29）

- 将相同的 **Porcelain Instrument + Midnight Data Stage + Cobalt Signal + Copper Physics** 设计系统扩展到 `/measure`、`/sweep`、`/lcr`、`/lcr-linearity`、`/file-analysis`、`/sweep-analysis`、`/interference` 及其完整操作手册。
- Light / Dark 共用完全相同的布局、DOM 与信息层级；主题切换位于全局顶栏并跨页面记忆。
- 左侧统一为持续可见的任务上下文与控制轨；右侧统一为标题状态、指标带和深色数据舞台。参数扫描与扫描归档在无数据时显示明确的空数据舞台，加载真实结果后原位替换。
- 单数据分析保留共性上下文，新增 Mix；FFT 直接呈现；STFT / CWT / WPD 切换后自动计算；参数设置使用不进入文档流的浮层，并验证弹层内操作不会关闭。
- 颜色语义统一：蓝色用于 Signal / Measurement / Data，铜色用于物理交互与次级比较，绿色用于 Complete / Healthy，红色只用于过量程、错误与真实警报。旧热图的青绿色阶已替换为钴蓝测量梯度。
- 干扰实验资源从只读参考位置复制到 TARGET 的 `web/interference/`，服务端资源根改为该目录；默认会话输出改为 TARGET 的 `data/interference/`。已通过浏览器实际建立会话，确认 `session.json` 只写入 TARGET。
- 浏览器实测：Light / Dark、1024×768 与 1440×900；实时测量完成一次 8 通道仿真采集（20,000 点/通道）；参数扫描完成 1 参数点 / 1 次原始采集；真实 `A1.npz` 的 Raw、FFT、STFT、CWT、WPD 即时切换通过；扫描归档读取和热图通过；干扰实验会话创建及手册跳转通过；控制台 `error/warn` 为 0。
- 自动测试：视觉修改完成后，在 TARGET 运行 `python -m unittest discover -s tests -p 'test_*.py' -v`，82 项全部通过；另有 `node --check web/app.js` 与 `python tests/file_analysis_static_qa.py` 通过。
- 运行环境未安装 PicoSDK，因此浏览器实机预连接按既有逻辑给出明确提示；仿真、离线分析、HTTP API 和完整测试均正常。真实硬件行为由未改动的设备/API 逻辑及相应测试覆盖。
- SOURCE_ROOT 最终只读复核：其 `git status --short` 与任务开始时完全一致；本轮所有运行输出、临时目录、仿真扫描和日志均位于 TARGET。
