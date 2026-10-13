# WaveGuard 全应用结构统一 — 2026-10-08

## 工作范围
仅修改 TARGET `F:\Project\01guided_waves\software\Pico_4824A_btf`。
SOURCE `Pico_4824A` 保持只读。原有源码哈希保护测试通过。未修改 Python、算法、API、设备驱动及采集时序。

## 设计与结构
- 实时测量和单数据分析保留已认可的设计；统一其余正式工作页的空间组织。
- 参数扫描、扫描归档、LCR 测量、LCR 文件夹分析采用 64px 顶栏、264px 共性栏、一体数据舞台、300px 任务 Inspector、固定操作条。
- LCR 结果按观察视图显示原有曲线／波形／明细，避免全部图表纵向堆叠。大信号阻抗与线性度作为两个任务视图。
- 小信号校准、开始、停止等原按钮直接迁移到底部。LCR 模式节点及其占位符共同迁移，继续由原硬件状态检查决定切换。
- 归档参数地图与采集记录分开观察；顶部记录选择器转发到原预览按钮事件，无重复 API 实现。
- 干扰实验统一全局导航、步骤上下文、数据／条件／证据视图、会话参数 Inspector 和固定采集动作；保留接线核对、确认弹窗及原采集逻辑。
- 操作手册改为工作文档构图，统一顶栏和目录侧栏；完整章节及打印功能保留。
- Light／Dark 共用布局，界面强调色独立于 PicoScope 通道色。
- 仅使用短容器过渡；Reduced Motion 直接切换；真实波形不播放装饰性动画。

## 修改文件
`web/app.js`：新增展示层节点迁移、视图状态、Inspector、记录选择与原绘图器的调度。文件夹原始波形添加展示缓存；`renderLcrFolder` 增加仅重绘选项，避免布局变化重复请求预览。
`web/styles.css`：扩展已认可空间规则并添加各任务数据舞台／Inspector 样式。
`web/index.html`：更新资源版本。
`web/interference/index.html`、`app.js`、`styles.css`：实验工作区结构及原控件迁移。
`web/interference/manual.html`、`manual.css`：工作文档布局和全局导航。
`tests/application-browser-regression.cjs`：42 组布局及交互检查。
`tests/application-loaded-check.cjs`：TARGET 已有 NPZ／扫描归档加载态检查。

## 已执行检查
- 原有 unittest：86 项通过，含仿真采集、扫描、大／小 LCR、线性度、干扰实验、API 与源文件保护。
- 真实文件分析 API 签名对比：11 组保持一致，覆盖 Raw／Filtered／FFT／STFT／CWT／WPD／EX／导出；TARGET A1.npz 未改变。
- 独立无头 Edge：七个工作页 × 1366×768、1440×900、1600×900、1920×1080、2560×1440、1024×768，共 42 组；无横向越界，数据舞台和底部操作在视口内。
- Inspector 保持展开、LCR 大／小反复切换、阻抗／线性度切换、文件夹三模式及观察视图、主题、Reduced Motion 和专注模式通过。
- TARGET A1.npz 实际加载并逐项查看八种 Lens，运行 EX 原计算按钮；读取既有扫描归档并选择原始采集波形；无新增 pageerror / API 失败。
- 首轮浏览器显示 favicon.ico 404，为现有图标资源缺失；后续检查排除该请求，页面脚本及 API 无新增错误。
- 截图／浏览器报告及缓存保存在 `.test-tmp`，不提交生成图。

## 限制与运行
未连接实机执行新的采集；设备操作的验证使用原测试中的仿真流程。实机接线和安全限值仍按原界面确认。
浏览器测试使用现有 Playwright 与本机 Edge，未安装新依赖。运行测试需已有本机服务（默认 4824）、Playwright 可解析或设置 `WAVEGUARD_PLAYWRIGHT_MODULE`；可用 `WAVEGUARD_BASE_URL`、`WAVEGUARD_BROWSER_CHANNEL` 覆盖。
所有生产后端／算法文件未修改。所有文件操作、运行及测试均以 TARGET 为工作目录。
