#!/usr/bin/env bash
#
# MK7BoostGauge — one-shot install script.
# Target: Raspberry Pi OS Lite 64-bit on Pi Zero 2W.
# Hardware: WaveShare 2-CH CAN HAT (MCP2515 + MCP2562).
#
# Idempotent: safe to re-run.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
CONFIG_DIR="/var/lib/boostgauge"
SERVICE_FILE="/etc/systemd/system/boostgauge.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: must run as root (use sudo)."
  exit 1
fi

# ---------------------------------------------------------------- detect user
# Prefer SUDO_USER, fallback to 'pi' (Pi OS default).
PI_USER="${SUDO_USER:-}"
if [[ -z "$PI_USER" || "$PI_USER" == "root" ]]; then
  if id pi &>/dev/null; then
    PI_USER="pi"
  else
    PI_USER="$(getent passwd 1000 | cut -d: -f1)"
    [[ -z "$PI_USER" ]] && PI_USER="root"
  fi
fi
echo "==> Installing for user: $PI_USER"

echo "==> [1/8] APT install dependencies"
apt -qq update
apt -qq -y install python3-pip python3-venv python3-dev can-utils \
                   hostapd dnsmasq net-tools git

echo "==> [2/8] Enable SPI + add MCP2515 device tree overlays in config.txt"
# Config path:
#   - Pi OS Lite Bookworm (current default) → /boot/firmware/config.txt
#   - Pi OS Lite Bullseye (older)           → /boot/config.txt
CONFIG_TXT=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "$candidate" ]]; then
    CONFIG_TXT="$candidate"
    break
  fi
done
if [[ -z "$CONFIG_TXT" ]]; then
  echo "ERROR: no config.txt found in /boot/firmware/ or /boot/"
  exit 1
fi
echo "    Using $CONFIG_TXT"

# Backup once
[[ -f "${CONFIG_TXT}.boostgauge.bak" ]] || cp "$CONFIG_TXT" "${CONFIG_TXT}.boostgauge.bak"

# Append our block (idempotent — guarded by marker)
if ! grep -q "# >>> MK7BoostGauge" "$CONFIG_TXT"; then
cat >> "$CONFIG_TXT" <<'EOF'

# >>> MK7BoostGauge CAN HAT (WaveShare 2-CH MCP2515) >>>
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25
dtoverlay=mcp2515-can1,oscillator=12000000,interrupt=24
dtoverlay=spi-bcm2835-overlay
# <<< MK7BoostGauge CAN HAT <<<
EOF
echo "    Added overlays (will activate after reboot)."
else
echo "    Overlays already present, skipping."
fi

echo "==> [3/8] Create CAN bring-up systemd service"
# Dedicated oneshot service rather than systemd-networkd, because:
#   - Pi OS Bookworm uses NetworkManager for eth0/wlan0 — enabling
#     systemd-networkd here would HIJACK the WiFi (you'd lose AP/STA).
#   - This service ONLY touches can0/can1, never WiFi/Ethernet.
cat > /etc/systemd/system/can-up.service <<'EOF'
[Unit]
Description=Bring up MCP2515 CAN interfaces (can0, can1) at 500 kbps
DefaultDependencies=no
After=local-fs.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
# Wait briefly for kernel to enumerate the SPI MCP2515 controllers
ExecStartPre=/bin/sleep 2
# Bring up each interface; '|| true' so service stays "active" even if a
# controller is missing (e.g. only one CAN HAT chip wired).
ExecStart=/bin/sh -c 'ip link set can0 up type can bitrate 500000 2>/dev/null || true'
ExecStart=/bin/sh -c 'ip link set can1 up type can bitrate 500000 2>/dev/null || true'
ExecStop=/bin/sh -c 'ip link set can0 down 2>/dev/null || true; ip link set can1 down 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable can-up.service

echo "==> [4/8] Create writable config dir at $CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
  cp "$PROJECT_DIR/config.example.json" "$CONFIG_DIR/config.json"
  chown -R "$PI_USER:$PI_USER" "$CONFIG_DIR"
  echo "    Initial config copied."
else
  echo "    Config already exists, preserved."
fi

echo "==> [5/8] Create Python venv + install requirements"
sudo -u "$PI_USER" python3 -m venv "$VENV_DIR"
sudo -u "$PI_USER" "$VENV_DIR/bin/pip" install --upgrade pip wheel
sudo -u "$PI_USER" "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "==> [6/8] Install systemd service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=MK7BoostGauge standalone in-car boost gauge
After=network.target can-up.service
Wants=can-up.service

[Service]
Type=simple
User=$PI_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/python -m app.main
Restart=on-failure
RestartSec=2

# Don't write .pyc cache files — venv lives under /home which is ProtectHome=read-only.
Environment="PYTHONDONTWRITEBYTECODE=1"
Environment="PYTHONUNBUFFERED=1"

# Allow binding port 80 as non-root user (needed for our HTTP server).
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE CAP_NET_RAW

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$CONFIG_DIR /var/log
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable boostgauge.service

echo "==> [7/8] (Optional) WiFi AP setup — uncomment block in this script if you want it"
# bash "$PROJECT_DIR/pi_setup/ap_setup.sh"

echo "==> [8/8] Done!"
echo ""
echo "    NEXT STEPS:"
echo "    1. REBOOT the Pi: sudo reboot"
echo "    2. After reboot, verify CAN: ip -br link show | grep can"
echo "    3. Service should be running: systemctl status boostgauge"
echo "    4. Logs: journalctl -u boostgauge -f"
echo ""
