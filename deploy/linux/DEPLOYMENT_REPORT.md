# R76S 部署验收记录

日期：2026-10-10（Asia/Shanghai）

## 已完成

- 主机：192.168.10.26，用户 pi；Ubuntu 20.04 / aarch64 / 内核 6.1.141，主机 Python 3.8 保持不变。
- 运行环境：非特权 ARM64 Docker，Python 3.12.15 / Debian 12；USER 1000:1000、只读容器根目录、仅 LAN 地址发布 4824、USB 热插拔挂载。
- 官方 Pico 驱动：libps4000a 2.2.265-2r7906，官方 Release 签名和 Packages/驱动 SHA256 校验通过。官方基础镜像 ARM64 digest：sha256:739ba32ae445e8d58f3d90feb85f83bebc8346f8dd280fa1eb5848f4ff1ed163。
- ITECH 后端：PyVISA 1.16.2 / PyVISA-py 0.8.1 / PyUSB 1.3.1 / libusb；驱动导入成功。
- 服务：waveguard-r76s.service，已 active / enabled。正常 SIGTERM 收尾经真实 Linux 子进程测试，服务重启已验证。
- 本地时间：Asia/Shanghai；永久数据目录：/home/pi/Pico_4824A_btf/app/data。
- 源码目录：/home/pi/Pico_4824A_btf/app，btf 分支，Git origin 指向原 GitHub 仓库。

## 测试

- Linux 回归：171 项，170 通过；1 项只读参考源码校验因板子没有 SOURCE 仓库而跳过。
- 浏览器：所有 8 个工作页 HTTP 200；96 项尺寸 / 主题 / 导航 / Inspector 检查，无错误。
- 扫描 API：13 档 × 10 次 = 130 次，明确 SIMULATED，完成并确认模拟输出 OFF；逐档 CSV 14 行，导出成功。
- 仿真任务原始 NPZ、逐次/逐档统计与任务快照已保存在 app/data/bias_scans；服务重启不删除文件。

## 未验证与限制

- USB 当前仅检测到主机控制器，没有连接 Pico4824A 或 IT6524D；未做实际 USB *IDN?、实机采集、输出关闭确认或任何通电扫描。
- 使用有独立供电的 USB 3.0 Hub 连接两台设备，Pico 用原装 USB 3.0 线。插入后需检查枚举、探头系数和保护阈值，再完成实机验收。
- PyVISA-py 会提示缺少可选 psutil/zeroconf，影响 TCPIP 发现范围，USB 后端本身已加载；本次不把它描述为 USB 实机已通过。
- 主机直连国外下载站不稳定，本次离线下载经验证的镜像 / 驱动 / ARM64 wheels 再经 SSH 传输。未来 git pull 需要可用出网；也可用 Git bundle 离线更新。
- 不提供主机掉电、强杀或 USB 断开后的软件断电保证；正常停止路径会先执行既有断电收尾。
- 本地 SOURCE_ROOT 未修改。密码不写入部署文件、服务或 Git。
