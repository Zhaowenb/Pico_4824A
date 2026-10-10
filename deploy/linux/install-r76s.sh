#!/usr/bin/env bash
set -euo pipefail
WG_DEPLOY_ROOT=/home/pi/Pico_4824A_btf
WG_IMAGE=${1:-waveguard-r76s:143942a}
WG_FILES=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [ "$(id -u)" -ne 0 ]; then echo 'Run with sudo on the R76S host.' >&2; exit 1; fi
if [ ! -f "$WG_DEPLOY_ROOT/app/pico4824a/web.py" ]; then echo 'Project checkout missing.' >&2; exit 1; fi
if docker container inspect waveguard-r76s >/dev/null 2>&1; then echo 'Container already exists; stop safely and review before replacing it.' >&2; exit 1; fi
if [ -e /etc/systemd/system/waveguard-r76s.service ] || [ -e /etc/udev/rules.d/99-waveguard-usb.rules ]; then echo 'Deployment configuration already exists; refusing to overwrite.' >&2; exit 1; fi
getent group pi >/dev/null
test -d /usr/share/zoneinfo
install -m 0644 "$WG_FILES/99-waveguard-usb.rules" /etc/udev/rules.d/99-waveguard-usb.rules
install -m 0644 "$WG_FILES/waveguard-r76s.service" /etc/systemd/system/waveguard-r76s.service
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=0ce9
udevadm trigger --subsystem-match=usb --attr-match=idVendor=2ec7
docker create --name waveguard-r76s --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --stop-signal=SIGTERM --stop-timeout=30 --pids-limit=512 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  --device-cgroup-rule='c 189:* rmw' \
  --mount type=bind,src=/dev/bus/usb,dst=/dev/bus/usb \
  --mount type=bind,src="$WG_DEPLOY_ROOT/app",dst=/app \
  --mount type=bind,src=/usr/share/zoneinfo,dst=/usr/share/zoneinfo,readonly \
  -e TZ=Asia/Shanghai -e OPENBLAS_NUM_THREADS=4 -e OMP_NUM_THREADS=4 \
  -p 192.168.10.26:4824:4824 \
  --log-opt max-size=10m --log-opt max-file=3 "$WG_IMAGE"
systemctl daemon-reload
systemctl enable --now waveguard-r76s.service
systemctl --no-pager status waveguard-r76s.service
