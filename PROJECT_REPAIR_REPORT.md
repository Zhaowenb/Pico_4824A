# WaveGuard 审计修复与工程整理

日期：2026-10-09。工作目录：Pico_4824A_btf；Pico_4824A 永远只读。

## 完成的修复

1. AWG Inspector 预览恢复固定 150px 高度，并接入共享尺寸观察与绘制；实测 bitmap 263×150。
2. Raw / Filtered / Mix 只决定显示序列；数学分析的带通输入拥有明确可见的独立开关。Filtered 仍计算真实带通波形，不改变原始数组。
3. 时频结果按文件、通道、选区、带通、频率范围、STFT/WPD/CWT 全部参数的快照判断有效性。参数修改立即标记待应用，旧请求不能提交到新快照。
4. Inspector 方法选择与 Lens 切换共用入口，WPD/STFT/CWT 自动发送真实计算请求，不显示未发生的 Loading。
5. EX 使用相同的计算身份与过期请求检查，改参数后不再把旧结果标记 READY。
6. LCR 原目录与旧干扰 UUID 会话先复制导入 TARGET，再执行可能落盘的分析。导入标记持久化，重复打开不重复复制；不写回 SOURCE。
7. 配置与电阻校准写入使用统一 output_path 边界，拒绝 TARGET 之外的路径。
8. 单数据分析同一输入/同一参数的 NPZ 和 CSV 共用持久化任务目录，不同参数新建目录。
9. 通用 archive_session 负责整任务 ZIP；偏置导出复用它。设置中可选择已保存任务导出完整 ZIP；仪器任务运行时禁止打包；拒绝打包 SOURCE。

## 结构整理

- `web/ui/analysis-state.js`：统一计算快照、结果身份和请求过期判定。
- `web/analysis/time-frequency.js`、`web/analysis/experimental.js`：从 app.js 拆出的计算控制与绘图；原 API 和算法不变。
- `pico4824a/data_access.py`：历史数据工作副本导入与只读来源标记。
- `pico4824a/data_sessions.py`：多格式任务身份和通用 ZIP。
- 早期独立 HTML 移至 `archive/design-prototypes/`；阶段记录移至 `archive/implementation-history/`。保留内容，不删除。
- 运行入口仍是 `web/index.html`、`pico4824a` 与原启动脚本，根目录旧设计 index 不属于服务入口。
- `tools/run_validation.ps1` 提供验证入口。缓存、日志、截图和输出位于 TARGET `.test-tmp` 与 `data`，不提交新测量结果。

## 验证

- Python 完整回归及新增边界/导入/跨格式/ZIP 测试；覆盖偏置13档×10次、5次模式、安全故障、停止、超时、温度、保存失败和输出关闭失败。
- 六尺寸：1366×768、1440×900、1600×900、1920×1080、2560×1440、1024×768。
- 全部8个工作页 × 双主题 × 六尺寸：96项布局、Inspector、Escape、导航和可见操作检查。
- 真实 A1 数据8个 Lens × 六尺寸：48项加载态布局；另验证八通道仿真采集。
- 审计专项：AWG尺寸、Filtered→Raw、STFT80→160μs、WPD方法选择、快速切换、独立带通与 Reduced Motion。
- EX双主题、参数待应用、通用 ZIP 下载、拒绝参考目录打包，控制台无新增错误/警告，SVG无NaN/undefined。
- 保存交互：12项双主题/尺寸按钮对齐 + 仿真扫描、可读名称、原始文件、ZIP、文件选择与干扰页，共13项。
- 原分析API：9组响应签名一致；Raw、Filtered、FFT、STFT、CWT、WPD、EX与导出兼容；真实 A1 未修改。
- 原项目纯算法只读执行，真实 A1 的 Raw/Filtered/FFT、STFT/WPD/CWT 与 BTF 返回值逐项一致。
- SOURCE只读清单622个文件的SHA256全部不变。
- 服务 stderr 无新增异常。Git diff --check 无空白错误。

## 验证边界

本轮验证仿真、真实已保存数据和软件安全故障。没有启动真实 PicoScope/电源扫描，没有将仿真当作实机验收。偏置实机所需限值、感性关断与独立保护仍待确认，锁定保持。

共享基础现覆盖布局、主题、面板、重绘、数学计算身份和保存/打包；app.js仍保留现有仪器业务编排，干扰实验仍是独立业务应用。没有为形式上的统一重写原算法或设备控制时序。

历史导入是工作副本，不实时同步 SOURCE 的后续变化；`.importing` 未完成目录会明确拒绝复用，需要检查后处理。

## 发布

发布目标：`https://github.com/Zhaowenb/Pico_4824A.git` 的 `btf` 分支。保留本地修复前版本 `0f4374e`，并保留已有远端历史；不强推、不改主分支。

## 2026-10-09 页面叠加：运行中的旧后端混用新前端

复现于 4824：旧 Python 服务运行中更新文件，HTML/app.js 被实时读取为新版本，但旧路由缺少 /ui/storage-naming.js、/ui/analysis-state.js、/analysis/time-frequency.js、/analysis/experimental.js，返回 404，继而出现 WaveGuardAnalysisState 等 ReferenceError，路由/通道初始化中断，造成多页内容叠加。4877 新服务同页无误。

已确认原服务停止且偏置输出 off，经 /api/admin/shutdown 安全退出，再从 TARGET 启动当前代码于原 4824 地址。静态资源改为每次启动的一致快照，包括干扰页；运行中更新文件不会再混入新前端，更新后必须重启服务。README 补充更新步骤。

159 项 Python 回归通过，新增运行期间修改磁盘文件不会混用版本的 HTTP 回归。全站 8 页 × 6 尺寸 × Light/Dark 共 96 项布局/导航/参数面板检查通过；4824 的 LCR 页面只有一个工作区，所有依赖 200，无页面脚本错误。SOURCE 只读校验通过。仅仿真验证，无实机通电。
