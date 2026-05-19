#!/usr/bin/env bash
#
# MK7BoostGauge - Ultra-simple bench launch.
#
# Brings up can1 in NORMAL mode with all the tricks (txqueuelen, restart-ms)
# then runs the bench script with defaults (RPM=4000, TEMP=130).
#
# Usage:
#   sudo bash ~/MK7BoostGauge/pi/bench_simple.sh           # default 4000 RPM, 130C
#   sudo bash ~/MK7BoostGauge/pi/bench_simple.sh 3000      # 3000 RPM, 130C
#   sudo bash ~/MK7BoostGauge/pi/bench_simple.sh 3000 90   # 3000 RPM, 90C
#   sudo bash ~/MK7BoostGauge/pi/bench_simple.sh 3000 90 loopback   # in loopback (no cluster needed)
#
RPM="${1:-4000}"
TEMP="${2:-130}"
MODE="${3:-normal}"   # normal or loopback

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_SCRIPT="$SCRIPT_DIR/bench_test_cluster.py"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: must run as root (sudo)."
  exit 1
fi

echo "=============================================="
echo "  MK7 cluster bench launcher"
echo "  RPM=$RPM  TEMP=${TEMP}C  MODE=$MODE"
echo "=============================================="

# Step 1: Make sure MCP2515 driver is loaded fresh
echo ""
echo "[1/5] Resetting mcp251x driver..."
modprobe -r mcp251x 2>/dev/null
sleep 2
modprobe mcp251x
sleep 3
dmesg | grep mcp | tail -2

# Step 2: Bring up can1
echo ""
echo "[2/5] Bringing up can1..."
ip link set can1 down 2>/dev/null
sleep 0.5

if [[ "$MODE" == "loopback" ]]; then
  ip link set can1 type can bitrate 500000 loopback on
else
  ip link set can1 type can bitrate 500000 restart-ms 100
fi
ip link set can1 up

# Step 3: Big TX queue (avoids ENOBUFS)
echo ""
echo "[3/5] Setting txqueuelen 65535..."
ifconfig can1 txqueuelen 65535

# Step 4: Verify
echo ""
echo "[4/5] can1 status:"
ip -br link show can1
if ! ip -br link show can1 | grep -q UP; then
  echo "ERROR: can1 is not UP. Try power cycling the Pi (unplug 30s)."
  exit 1
fi

# Step 5: Launch bench
echo ""
echo "[5/5] Launching bench (Ctrl+C to stop)..."
echo ""
exec python3 "$PY_SCRIPT" --iface can1 --rpm "$RPM" --temp "$TEMP"
