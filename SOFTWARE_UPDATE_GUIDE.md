# 设置 · 软件更新

所有工作页共用顶部齿轮设置中的“软件更新”。默认在打开页面和每小时检查 `origin/btf`，仅提示新版本；可关闭自动检查，或点击“检查更新”。“更新并重启”只在检查成功、发现版本差异且符合更新条件时可用。

更新流程：阻止新仪器任务 → 确认偏置输出关闭、Pico 安全收尾 → Git 快进到已检查的提交 → 重启同一 Python Web 服务 → 浏览器自动重新连接并刷新。Windows 与 Linux 使用同一更新代码；Linux 容器在原进程中 exec 新服务，不需要 Docker socket、sudo 或特权容器。服务不自动恢复旧扫描。

必须在独立 Git 工作目录的 `btf` 分支，origin 指向 `Zhaowenb/Pico_4824A`。本地修改、分支分叉、正在采集、未知偏置输出、尚未退出的安全线程或待保存波形都会阻止安装。只检查更新时允许本地修改。不会强制 reset、clean、切换分支、自动 stash 或安装依赖。SOURCE 目录 `Pico_4824A` 不允许使用更新器。

原始数据、忽略的本地配置和命名设置保留。自动检查开关保存在 `configs/software-updates.json`；更新前后提交记录在 `data/software_updates/last-update.json`，均不纳入 Git。检查失败显示真实错误，不将网络失败显示为“已是最新”。

## 首次启用

- Windows：需安装 Git 且 `git --version` 可用。先拉取本功能并重启 Web 后端，此后可使用设置更新。
- Linux 原生 Python：同样需要 Git、可写项目 Git 目录及原有 Pico/VISA 运行环境。
- Linux Docker：新版 `deploy/linux/Dockerfile` 已安装 Git。旧容器镜像没有 Git，需要先构建新版镜像并在确认断电、停止服务后按原 USB/目录挂载配置更换容器；`app/data` 不在镜像内，应保留现有 app 绑定目录。首次切换镜像需手动完成，代码更新不替代环境部署。
- 离线 Docker：`debs/` 必须包含 ARM64 Git、git-man 及所需依赖；缺 Git 时离线镜像构建会明确失败。

```bash
docker build -t waveguard-r76s:local -f deploy/linux/Dockerfile .
```

安装脚本默认发布全部接口，可用 `WG_BIND_ADDRESS` 指定已有 LAN 地址；将地址放在环境配置中，无需修改跟踪的脚本，否则本地修改会阻止自动安装。

依赖清单、Python 项目元数据、安装脚本或 Docker 驱动配置变化时，一键安装会停止并提示先更新运行环境；不会在连接仪器的进程中偷偷安装或升级设备驱动。GitHub 不可达时仍可使用已有仪器功能，待网络恢复再检查更新。

本地完整回归、Git 仓库更新与前端状态流程用于验证此功能；实机服务器升级必须先确认无正在运行的实验。
