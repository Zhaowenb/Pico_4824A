# 磁路诊断 · 第一版预览

入口：`/magnetic-diagnosis`。复用 `web/ui/workstation.js` 与 `web/ui/workstation.css` 的 Shell、主题、设置、Stage、Inspector、导航、专注与 Reduced Motion。

本版是交互与分析表达预览，所有曲线均为确定性仿真，未调用任何采集或电源 API。实机 Ramp 未实现，不可作为材料饱和测量结果。

观察视角：原始记录、磁通增量、增量响应、速度对照。模型：增量响应下降、磁通持续增长、电源跟踪不足。游标在时间与实际电流之间关联；默认位置为 10 A 附近。

仿真采样间隔 5 ms，非正式实机采样配置。校正前置基线后双向一阶低通、梯形积分；Ramp 起止和低 dI/dt 区间不参与增量响应。ΔB 为截面平均增量，不是绝对 B。

CSV 逐行标记 SIMULATED，包含配置、磁历史、原始电压、处理电压、磁通、ΔB、实际斜率、增量响应与有效标记。

检查：1366×768、1440×900、1600×900、1920×1080、2560×1440、1024×768、390×844；Light/Dark 和四个视角共 56 组布局通过。参数面板、Escape、生成、非法参数、CSV、专注、Reduced Motion 和导航检查通过；浏览器无 pageerror / 资源错误。记录位于 `.test-tmp/magnetic-preview-checks.json`。

新文件：web/magnetic-diagnosis.html、web/views/magnetic-diagnosis.js、web/views/magnetic-diagnosis.css、本说明。
最小修改：web/ui/workstation.js 导航；web/app.js 新页面普通导航；pico4824a/web.py 静态路由。

Pico_4824A 参考源未修改。未更改现有计算、硬件控制或安全规则。未更新 R76S / GitHub；本版先供本地评审。
