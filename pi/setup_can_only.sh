#!/usr/bin/env bash
#
# MK7BoostGauge - HARDWARE ONLY setup (no daemon, no project).
#
# Use this to verify the WaveShare 2-CH CAN HAT works BEFORE installing the
# full project. After this + reboot, you can run pi/bench_test_cluster.py
# to drive the cluster directly.
#
# Run:
#   sudo bash setup_can_only.sh
#   sudo reboot
#   # then verify:
#   ip -br link show | grep can
#   # then run bench:
#   sudo python3 pi/bench_test_cluster.py
#
# After bench is confirmed working, run the full setup:
#   sudo bash setup_pi.sh
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: must run as root (sudo)."
  exit 1
fi

echo "==> [1/3] Installing apt deps (python3-can, can-utils, git)"
apt -qq update
apt -qq -y install python3-can can-utils git

echo "==> [2/3] Enabling SPI + MCP2515 overlays"
CONFIG_TXT=""
for c in /boot/firmware/config.txt /boot/config.txt; do
  [[ -f "$c" ]] && CONFIG_TXT="$c" && break
done
[[ -n "$CONFIG_TXT" ]] || { echo "ERROR: config.txt not found"; exit 1; }
echo "    Using $CONFIG_TXT"

# Backup once
[[ -f "${CONFIG_TXT}.boostgauge.bak" ]] || cp "$CONFIG_TXT" "${CONFIG_TXT}.boostgauge.bak"

if ! grep -q "# >>> MK7BoostGauge CAN HAT" "$CONFIG_TXT"; then
cat >> "$CONFIG_TXT" <<'EOF'

# >>> MK7BoostGauge CAN HAT (WaveShare 2-CH MCP2515 + SIT65HVD230) >>>
dtparam=spi=on
# SPI freq lowered to 1MHz to avoid kernel oops on Pi Zero 2W + MCP2515
dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25,spimaxfrequency=1000000
dtoverlay=mcp2515-can1,oscillator=12000000,interrupt=24,spimaxfrequency=1000000
# <<< MK7BoostGauge CAN HAT <<<
EOF
echo "    Overlays added"
else
echo "    Overlays already present, skipping"
fi

echo "==> [3/3] CAN bring-up systemd service"
CAN_SVC="/etc/systemd/system/boostgauge-can-up.service"
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
# restart-ms 100 = auto-recover from bus-off after 100 ms
ExecStart=/bin/sh -c 'ip link set can0 up type can bitrate 500000 restart-ms 100 2>/dev/null || true'
ExecStart=/bin/sh -c 'ip link set can1 up type can bitrate 500000 restart-ms 100 2>/dev/null || true'
ExecStop=/bin/sh -c 'ip link set can0 down 2>/dev/null || true; ip link set can1 down 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable boostgauge-can-up.service

echo ""
echo "==> HW setup complete!"
echo ""
echo "    NEXT STEPS:"
echo "    1. sudo reboot"
echo ""
echo "    2. After reboot, verify HW:"
echo "         lsmod | grep mcp                 # mcp251x must be loaded"
echo "         ip -br link show | grep can     # can0 + can1 UP at 500 kbps"
echo "         ip -d link show can0             # check restart-ms = 100"
echo ""
echo "    3. Self-test (NO cluster, NO car) — checks driver only:"
echo "         sudo cansend can0 123#DEADBEEF"
echo "         # If exit code != 0 = bus has nothing on it = expected, that's OK"
echo "         # If exit code 0 = something acked = HAT loopback or other node alive"
echo ""
echo "    4. With cluster connected + powered (+12V Kl.30 + Kl.15):"
echo "         sudo python3 pi/bench_test_cluster.py"
echo "         # Cluster should react in 1-3 sec: RPM 1500, coolant 130C"
echo ""
echo "    5. If bench OK -> install full project:"
echo "         sudo bash setup_pi.sh"
echo ""
