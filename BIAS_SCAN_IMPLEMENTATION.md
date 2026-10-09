# 偏置扫描交付记录

## 范围

唯一开发目录为 `F:\Project\01guided_waves\software\Pico_4824A_btf`。
新增 `/bias-scan`。没有修改 SOURCE，也没有改动 TARGET 的 `device.py`、`storage.py`、滤波、FFT、STFT、CWT、WPD 或实验分析算法。

## 新增文件

| 文件 | 用途 |
|---|---|
| `pico4824a/bias_scan/__init__.py` | 独立模块入口 |
| `pico4824a/bias_scan/config.py` | 冻结配置、0–6 A、时间窗、预算、安全门槛 |
| `pico4824a/bias_scan/power.py` | IT6524D USB VISA SCPI 与明确的仿真电源 |
| `pico4824a/bias_scan/adapter.py` | 调用原 Pico capture；复用设备、限制等待时间 |
| `pico4824a/bias_scan/controller.py` | 状态机、独立截止/遥测线程、OFF 优先、保存/冷却 |
| `pico4824a/bias_scan/analyzer.py` | 原始 Vpp、ddof=1、资格及并列最优规则 |
| `pico4824a/bias_scan/safety.py` | 温度与实际独立断电保护接口，默认实机不可启动 |
| `pico4824a/bias_scan/web_extension.py` | 共用仪器任务互斥、预检/结果/预览/导出 |
| `configs/bias-scan.example.json` | 可直接执行的仿真配置；实机安全限值留空 |
| `web/views/bias-scan.js` | 使用 WaveGuardUI Shell/Stage/Inspector、Canvas 与任务状态 |
| `web/views/bias-scan.css` | 新功能局部几何；沿用共享颜色、主题、字体、导航、操作条 |
| `tests/test_bias_scan.py` | 状态机、VISA、统计、故障与晚返回监控测试 |
| `tests/test_bias_scan_http.py` | HTTP 闭环、互斥、实机锁定、退出关断 |
| `tests/bias-scan-browser.cjs` | 六尺寸双主题、全扫描、选点/重复、表格、导出、停止、专注 |
| `BIAS_SCAN_GUIDE.md` | 配置、运行、接口集成与实机边界 |
| `BIAS_SCAN_IMPLEMENTATION.md` | 本记录 |

## 修改文件

- `pico4824a/web.py`：注册路由/API；最小接入 mixin；所有仪器启动入口检查偏置未知/清理未完成锁；停止/退出先关闭偏置电源。
- `pico4824a/cli.py`：新增 `bias-scan --config ... --simulate`。实机 CLI 明确拒绝未接入的保护适配器；Ctrl+C 清理后返回 130。
- `pyproject.toml`：可选 `bias` 依赖，仅 PyVISA；延迟导入。
- `web/index.html`：加载新功能组合与样式。
- `web/ui/workstation.js`：共享导航新增“偏置电流扫描”。
- `web/app.js`：仅新增路由标题、路由识别和既有轮询的 `waveguard-status` 展示事件；没有重写原 API、计算或采集逻辑。
- `.gitignore`：忽略运行结果 `data/bias_scans/`。
- `tests/shared-ui-browser-regression.cjs`：加入新页，测试地址可从环境指定。
- `tests/shared-ui-loaded-regression.cjs`、`tests/file_analysis_api_smoke.py`：允许指定隔离 TARGET 测试服务地址；原数值基线文件保持不变。

## API

既有 API 保留。新增：

- `GET /api/bias-scan/resources`：可选 USB VISA 资源列表。
- `POST /api/bias-scan/connect`：明确选择、核对型号、远程控制、OFF 与确认。
- `POST /api/bias-scan/preflight`、`POST /api/bias-scan/start`。
- `GET /api/bias-scan/result`、`GET /api/bias-scan/preview?point=0&repeat=1`。
- `GET /api/bias-scan/export?name=summary.csv`（只允许指定的四个结果文件）。

停止复用 `POST /api/stop`。没有新增“直接电源 ON”的公开接口。波形预览只读取本任务已经保存的原始文件；路径不能由用户任意注入。

## 验证结果

| 验证 | 结果 |
|---|---|
| 全量 Python 回归 | 108 项通过，其中新增 18 项 |
| 默认完整闭环 | 13 档 × 10 次，130 个兼容原始 NPZ；最终 OFF |
| 5 次配置 | HTTP 完整 13 档 × 5 次，65 次 |
| CLI 计划命令 | 成功；13 档/130 次/输出关闭确认 |
| CLI 仿真批次耗时 | 约 0.985–1.032 s，不含断电后的保存分析；不代表实机 |
| 故障 | 稳定失败、过流、USB 超时、Pico 异常、停止、KeyboardInterrupt、服务退出、OFF 失败、温度过期/过热、保存失败均覆盖 |
| 顺序断言 | 最后采集返回后的关断优先；分析/保存均在 OFF 后；阻塞采集期间截止线程仍触发 |
| 部分档 | 已取得原始数据保存，不完整档不参与最佳值 |
| 晚返回监控 | 不进入下一档；仪器占用保持锁定 |
| 网页新页 | 六尺寸 × Light/Dark 共 12 个布局，另加完整扫描/锁定/停止；无测试发现的脚本错误或越界 |
| 全站共享布局 | 8 页 × 6 尺寸 × 2 主题，96 项通过；导航、Inspector、Escape、专注正常 |
| 已加载分析布局 | 8 Lens × 6 尺寸，48 项通过；仿真实时采集正常 |
| 原真实分析数据 | `A1.npz` 未变；Raw/Filtered/FFT/STFT/CWT/WPD/Experimental/导出共 11 项 API 签名严格匹配原基线 |
| SOURCE | 原只读清单的 622 个文件 SHA256 校验全部通过；本轮未向 SOURCE 写入或在 SOURCE 运行测试 |

尺寸：1366×768、1440×900、1600×900、1920×1080、2560×1440、1024×768。测试输出位于 TARGET 的 `.test-tmp/`；扫描数据位于 TARGET 的 `data/bias_scans/`。

已目视检查空态和加载态。正常网页/服务流程没有发现新增代码异常；没有连接 Pico 时既有启动提示 `PICO_NOT_FOUND` 属于预期。故意提交非法实机配置会返回 400/409，属于安全预检拒绝。

## 未验证与限制

1. **没有实机通电验收**：USB 实际驱动、固件响应、低电流稳定、Pico 实机批次耗时均未验证。
2. 电压、通电、稳定超时、冷却限值仍待用户确认；独立物理超时与感性保护适配器未接入。实机扫描保持锁定，确认框不能绕过。
3. 200 ms VISA 超时和 50 ms 目标查询周期须实机验证；不自动放宽限值或重试电流档。
4. 电源 OFF 确认不等于储能释放。强杀、USB 断开或断电后的安全必须由外部装置保证。
5. 模拟 Pico 没有新增偏置物理模型，各档波形一致，最低并列电流不代表实机最佳值。
6. 磁盘写入失败时优先断电并中止；无法保证保存磁盘无法接收的内存数据。

## 本地回退

已保留改造前标签 `bias-scan-before` → `ddea1d0`；实现完成后另存本地提交。未自动推送。运行方法见 `BIAS_SCAN_GUIDE.md`。
