# WaveGuard 共享前端基础

## 本轮范围
所有应用工作页：实时测量、参数扫描、LCR 测量、LCR 数据分析、单数据分析（含 EX）、参数数据分析、干扰实验。操作手册复用 Header、主题和基础字体，采用文档阅读布局。
所有工作发生在 Pico_4824A_btf。Pico_4824A 是只读参考；原始代码快照由 tests/source-readonly-manifest.json 校验。

## 三层结构

### 1. 共享基础：web/ui/workstation.js + workstation.css
唯一的 Header / 导航 / 设置模板；统一的 Shell、左侧上下文、Stage、右侧 Inspector、底部操作栏、状态消息、读数规范。
共用主题和强调色状态、本地存储、PicoScope A–H 色序、数学图调色板、Escape / 焦点 / 面板互斥 / 专注逻辑、响应式布局及 Reduced Motion。
ResizeObserver 与 requestAnimationFrame 调度器仅在基础层创建，页面注册当前视图的绘图回调。
基础层不调用 API、不计算算法、不启动采集、不修改源数据。

### 2. 页面组合：web/views/
- signal-analysis.js：实时测量和所有分析 Lens；文件抽屉、真实选区缩略图、游标联动。
- instruments.js：扫描、归档、LCR、文件夹分析；原始结果视图、模式挂载及安全控制的展示组合。
- experiment.js：干扰实验；原始会话、四个任务视图、接线与参数组合。
三者均调用同一 createStage / mountShell / Inspector 控制器，不各自创建基础界面模板。
功能 CSS 只保留数学/实验内容、表格、图形与特定参数组的表达。

### 3. 业务控制器
web/app.js、web/interference/app.js 继续持有原始 API、计算结果、设备状态、任务轮询和操作事件。算法及 Python 硬件控制未重构。
HTML 保留已有控件 ID；页面组合移动原始节点而非复制控件。LCR 模式仍由原始硬件 guard 允许后挂载。
pico4824a/web.py 仅增加共享 JS/CSS 的静态资源白名单，API 和采集调用时序未变。

## 扩展规则
- 修改基础间距/字体/颜色/主题/Inspector：改 web/ui/，所有页面同步。
- 增加工作页：使用 createStage 注册主工作区，使用 mountShell 标记已有控件容器，注册绘图回调。
- 任务差异放入 views；禁止重新实现 Header、主题存储、ResizeObserver 或单独的基础 Shell。
- 数据可见性由共享 activate 与对应 Lens/任务状态管理；不使用页面级 CSS 重新定义工作区尺寸。
- 输入通道色序不随界面强调色变化；真实数据不参与装饰动画。

## 验证
- Python unittest：87 项，包含采集/扫描/LCR/分析/干扰仿真 API、事件绑定和 SOURCE 哈希。
- tests/shared-ui-browser-regression.cjs：7 工作页 × 6 尺寸 × Light/Dark = 84 项；共享几何、唯一模板/ID、SPA 导航、参数输入稳定、Escape、LCR 模式、EX 标签、专注与 Reduced Motion。
- tests/shared-ui-loaded-regression.cjs：八种 Lens × 六尺寸 = 48 项真实文件加载态；通过可见开关启动八通道仿真采集并等待真实结果。
- tests/application-loaded-check.cjs：真实 A1.npz 全部 Lens、EX 实际计算结果、扫描归档及原始记录预览。
- tests/file_analysis_api_smoke.py compare：11 组输入/数值/导出签名与 baseline 一致。
- 检查尺寸：1366×768、1440×900、1600×900、1920×1080、2560×1440、1024×768。
- 测试日志、截图、缓存均在 TARGET 的 .test-tmp，未加入提交。

## 限制
当前主机未连接 PicoScope，实机报告 PICO_NOT_FOUND；本轮验证仿真和保存的真实文件，不能声称实机采集已验证。
本轮收敛的是前端基础布局与通用交互。既有算法、设备 API、业务控制器和特定绘图函数保持原实现，以避免改变数值与硬件行为。


## 交互精修 V2
- 共享 ui/file-picker.js 提供应用内文件/文件夹选择：路径、上级、数据快捷位置、搜索、选择、双击进入、取消。选择只回填原始路径控件，加载和算法仍由原始按钮执行。
- /api/files/list 仅列目录及文件元数据，不创建目录、不上传、不修改数据；允许 TARGET 工作目录与 SOURCE/data 只读参考目录，解析符号链接后仍检查根目录边界。
- ui/workstation.js 统一非阻塞 Web Animations 转场（页面/任务/Lens 约 320ms、Inspector 打开约 260ms），不会等待动画后才发送采集命令。
- Breathing 限于品牌材质和数据舞台边缘，不动画曲线、坐标或测量读数。动态切换 Reduced Motion 会取消已在运行的展示动画。
- 顶部导航采用对称布局居中；LCR 文件夹页加载/重算按钮统一 36px，并修复选择器和无历史 LCR 目录时的初始浏览位置。
- V2 测试：90 项 Python 测试；84 项 Light/Dark 布局；48 项真实文件加载态；文件/文件夹选择、导航居中、按钮对齐、Lens 转场与 Reduced Motion 专项；11 组算法和导出签名一致。没有验证实机硬件。

## 2026-10-09 审计修复
计算身份统一到 ui/analysis-state.js；时频及 EX 控制器拆入 web/analysis；持久化身份、历史导入与 ZIP 分别由 data_sessions.py、data_access.py 负责。详见 PROJECT_REPAIR_REPORT.md。
