#!/usr/bin/env bash
#
# MK7BoostGauge v3 - Pi setup script.
#
# Target: Raspberry Pi OS Lite 64-bit on Pi Zero 2W.
# Run ONCE after fresh flash + SSH access:
#   ssh pi@boostgauge.local
#   cd ~/MK7BoostGauge/pi
#   sudo bash setup_pi.sh
#
# Idempotent: safe to re-run.
#
# What it does:
#   1. Install apt deps (python venv, can-utils, git)
#   2. Enable SPI + MCP2515 overlays in /boot/firmware/config.txt
#      (SPI freq lowered to 1MHz to avoid kernel oops on Pi Zero 2W)
#   3. Create CAN bring-up systemd service (auto can0/can1 at 500kbps)
#   4. Create Python venv + install requirements
#   5. Create /var/lib/boostgauge/ writable dir
#   6. Install + enable boostgauge-daemon.service
#
# After this + reboot: Pi listens on http://boostgauge.local:8765 for
# config push from PC. No web UI, no GitHub, no autoupdate.
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: must run as root (sudo)."
  exit 1
fi

# Auto-detect user (fallback pi)
PI_USER="${SUDO_USER:-pi}"
[[ -z "$PI_USER" || "$PI_USER" == "root" ]] && PI_USER="pi"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PI_DIR="$PROJECT_DIR/pi"
VENV_DIR="$PI_DIR/.venv"
CONFIG_DIR="/var/lib/boostgauge"
DAEMON_SVC="/etc/systemd/system/boostgauge-daemon.service"
CAN_SVC="/etc/systemd/system/boostgauge-can-up.service"

echo "==> User: $PI_USER"
echo "==> Project: $PROJECT_DIR"

# ---------------------------------------------------------------- [1/6] apt
echo "==> [1/6] Installing apt deps"
apt -qq update
apt -qq -y install python3-pip python3-venv python3-dev can-utils git net-tools

# ---------------------------------------------------------------- [2/6] config.txt overlays
echo "==> [2/6] Enabling SPI + MCP2515 overlays"
CONFIG_TXT=""
for c in /boot/firmware/config.txt /boot/config.txt; do
  [[ -f "$c" ]] && CONFIG_TXT="$c" && break
done
[[ -n "$CONFIG_TXT" ]] || { echo "ERROR: config.txt not found"; exit 1; }
echo "    Using $CONFIG_TXT"

# Backup once
[[ -f "${CONFIG_TXT}.boostgauge.bak" ]] || cp "$CONFIG_TXT" "${CONFIG_TXT}.boostgauge.bak"

if ! grep -q "# >>> MK7BoostGauge v3 CAN HAT" "$CONFIG_TXT"; then
cat >> "$CONFIG_TXT" <<'EOF'

# >>> MK7BoostGauge v3 CAN HAT (WaveShare 2-CH MCP2515) >>>
dtparam=spi=on
# SPI freq lowered to 1MHz to avoid kernel oops on Pi Zero 2W + MCP2515
dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25,spimaxfrequency=1000000
dtoverlay=mcp2515-can1,oscillator=12000000,interrupt=24,spimaxfrequency=1000000
# <<< MK7BoostGauge v3 CAN HAT <<<
EOF
echo "    Overlays added"
else
echo "    Overlays already present, skipping"
fi

# ---------------------------------------------------------------- [3/6] CAN bring-up service
echo "==> [3/6] CAN bring-up systemd service"
cat > "$CAN_SVC" <<'EOF'
[Unit]
Description=MK7BoostGauge - bring up MCP2515 CAN interfaces at 500 kbps
DefaultDependencies=no
After=local-fs.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sleep 2
ExecStart=/bin/sh -c 'ip link set can0 up type can bitrate 500000 2>/dev/null || true'
ExecStart=/bin/sh -c 'ip link set can1 up type can bitrate 500000 2>/dev/null || true'
ExecStop=/bin/sh -c 'ip link set can0 down 2>/dev/null || true; ip link set can1 down 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable boostgauge-can-up.service

# ---------------------------------------------------------------- [4/6] venv
echo "==> [4/6] Creating Python venv + installing requirements"
sudo -u "$PI_USER" python3 -m venv "$VENV_DIR"
sudo -u "$PI_USER" "$VENV_DIR/bin/pip" install --upgrade pip wheel
sudo -u "$PI_USER" "$VENV_DIR/bin/pip" install -r "$PI_DIR/requirements.txt"

# ---------------------------------------------------------------- [5/6] config dir
echo "==> [5/6] Writable config dir"
mkdir -p "$CONFIG_DIR"
chown -R "$PI_USER:$PI_USER" "$CONFIG_DIR"
chmod 755 "$CONFIG_DIR"

# ---------------------------------------------------------------- [6/6] daemon service
echo "==> [6/6] Daemon systemd service"
cat > "$DAEMON_SVC" <<EOF
[Unit]
Description=MK7BoostGauge daemon (boost gauge + HTTP config listener)
After=network.target boostgauge-can-up.service
Wants=boostgauge-can-up.service

[Service]
Type=simple
User=$PI_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/python -m pi.daemon
Restart=on-failure
RestartSec=2

Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONDONTWRITEBYTECODE=1"

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
systemctl enable boostgauge-daemon.service

echo ""
echo "==> Setup complete!"
echo ""
echo "    NEXT STEPS:"
echo "    1. sudo reboot"
echo "    2. After reboot, verify:"
echo "         ip -br link show | grep can     # can0 + can1 should be UP"
echo "         systemctl status boostgauge-daemon   # active (running)"
echo "         curl http://localhost:8765/ping       # {\"ok\":true,...}"
echo ""
echo "    3. From PC: http://boostgauge.local:8765/ping should respond"
echo ""
echo "    Logs: journalctl -u boostgauge-daemon -f"
