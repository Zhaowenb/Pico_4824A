# R76S / ARM64 Linux 部署

- 主机 Ubuntu 20.04 保持不变。应用使用 ARM64 Python 3.12 / Debian 12 容器，满足当前 Pico `libps4000a` 的 libc ≥2.34 / libstdc++ ≥12 依赖。
- Pico 4824A 使用官方免费 ARM64 ps4000a 驱动；ITECH 使用 PyVISA-py / PyUSB / 主机 libusb 内核 USB 接口。不是全开源驱动栈。
- 主机部署目录：`/home/pi/Pico_4824A_btf`；代码在 `app`，数据在 `app/data`，容器删除后仍保留。
- 容器名：`waveguard-r76s`；系统服务：`waveguard-r76s.service`；仅发布到指定 LAN 地址 `192.168.10.26:4824`。
- USB 使用 `/dev/bus/usb` 热插拔挂载与 USB 字符设备规则，不使用 `--privileged`。主机 udev 仅为 Pico VID `0ce9`、ITECH VID `2ec7` 设置 pi 组读写权限。
- 正常 SIGTERM / systemctl stop / docker stop 均进入服务器优先断电收尾。主机掉电、强杀或 USB 失联仍不能保证软件断电。

在线构建（网络可用时，从项目目录）：

```bash
sudo docker build -t waveguard-r76s:local -f deploy/linux/Dockerfile .
```

离线 Dockerfile 的 build context 需包含经过校验的 `debs/` 和 ARM64 `wheels/`；本次在开发电脑从官方来源下载、校验并传输，二进制依赖不提交 Git。

运行管理：

```bash
sudo systemctl status waveguard-r76s
sudo systemctl restart waveguard-r76s
sudo systemctl stop waveguard-r76s
sudo docker logs --tail 80 waveguard-r76s
```

USB 诊断（不通电）：

```bash
sudo docker exec waveguard-r76s python -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources('USB?*::INSTR'))"
sudo docker exec waveguard-r76s python -c "from picosdk.ps4000a import ps4000a; print('Pico driver loaded')"
```

网页“查找 USB”和重新连接会刷新 Linux PyUSB 的设备发现上下文，避免容器在拔插后继续返回旧设备列表；无需为此重启服务。刷新不会重置 USB、关闭既有采集句柄或开启电源。此兼容处理验证于 PyUSB 1.3.1。设备拔插前仍须停止任务并关闭输出；拔插中的通信失败会保留“输出状态未知”，重新连接并确认断电后才能恢复。

连接两台设备请使用有独立供电的 USB 3.0 Hub，Pico 使用原装 USB 3.0 数据线。设备未接入时，网页及仿真可用；不能将仿真当成实机验收。

更新代码前先停止扫描、确认偏置输出关闭；`git pull origin btf` 后重启服务即可加载前后端。依赖或驱动版本变更还需重新构建镜像。


## 主机出网失败时离线更新

开发电脑在项目目录创建 bundle 并复制到 R76S：

```powershell
git bundle create .test-tmp/waveguard-update.bundle btf
scp .test-tmp/waveguard-update.bundle pi@192.168.10.26:/home/pi/Pico_4824A_btf/deployment/
```

R76S 上先停止正在运行的扫描并确认输出关闭，然后执行：

```bash
sudo systemctl stop waveguard-r76s
cd /home/pi/Pico_4824A_btf/app
git fetch ../deployment/waveguard-update.bundle btf
git merge --ff-only FETCH_HEAD
sudo systemctl start waveguard-r76s
```

更新失败时先保留原始文件和 Git 状态，不执行 hard reset 或 clean。服务读取 bind mount 的代码，因此纯代码更新无需重新构建镜像。
