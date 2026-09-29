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

有意不复制 `cli.py`、`__main__.py`、`gui.py`、非分析页面之外的测试套件、整份采集数据目录和硬件缓存。`web.py` 直接导入的运行时模块已纳入上述闭包。

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
- Lens 行为：Raw / Filtered / FFT / STFT / WPD / CWT / Experimental 互斥显示；时域与时频视图共用时间范围；文件重载会清除旧派生结果；切换后重绘当前可见 Canvas；过期时频或实验响应不会覆盖当前文件。
- 视觉改造后 API 对照：11 组相同请求响应签名与 baseline 一致；另验证了 B/H 通道子集；Raw、Filtered、FFT、STFT、WPD、CWT、Experimental、NPZ/CSV Export 均通过。
- TARGET 静态检查：`node --check web/app.js`、`python -m compileall -q pico4824a tests`、`python tests/file_analysis_static_qa.py` 通过；HTML 无重复 ID，7 个 Lens ID 齐全，字面 DOM ID 引用均在 HTML 中，CSS 顶层解析通过。
- 指定尺寸：按 CSS 断点、容器与 `clamp()` 规则核算 `1366×768 / 1440×900 / 1600×900 / 1920×1080 / 2560×1440`；图表区域宽度约 `937 / 1005 / 1152 / 1460 / 1780 px`，高度约 `392 / 459 / 459 / 551 / 570 px`。这是静态尺寸核算，不是浏览器截图验证。
- HTTP 服务日志：页面与静态资源 GET、各分析 API 与导出请求均返回 HTTP 200，无服务端异常。运行环境的 NumPy 在首次计算时输出一条 `longdouble` 类型探测 `UserWarning`；分析接口及响应签名均通过，未改算法或抑制该警告。
- 浏览器控制台与实际截图：本轮未进行浏览器渲染/控制台实测；上述结论来自 HTTP、API 签名、JavaScript 语法、HTML/CSS 静态检查和尺寸核算。
- SOURCE_ROOT 保护复核：移植的 18 个后端/项目清单/数据夹具文件 SHA-256 与 SOURCE_ROOT 相同；本轮没有对 SOURCE_ROOT 执行写入命令。
