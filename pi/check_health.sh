#!/usr/bin/env bash
#
# MK7BoostGauge v3 - self-test / health check.
#
# Run on the Pi to verify everything is OK after setup_pi.sh + reboot:
#   bash ~/MK7BoostGauge/pi/check_health.sh
#
# Exit code: 0 = all OK, non-zero = at least one issue
#
# Checks:
#   1. MCP2515 kernel module loaded (no kernel oops)
#   2. Both CAN interfaces UP at 500 kbps
#   3. systemd services enabled + active
#   4. HTTP daemon responds (ping + status + config)
#   5. /var/lib/boostgauge/config.json present + valid
#   6. CAN traffic count (RX > 0 means cluster wiring OK if Pi connected)
#

OK="\033[1;32m[OK]\033[0m"
FAIL="\033[1;31m[FAIL]\033[0m"
WARN="\033[1;33m[WARN]\033[0m"
INFO="\033[1;36m[INFO]\033[0m"

ISSUES=0
fail() { echo -e "$FAIL $1"; ISSUES=$((ISSUES + 1)); }
ok()   { echo -e "$OK $1"; }
warn() { echo -e "$WARN $1"; }
info() { echo -e "$INFO $1"; }

echo "==================================================================="
echo "  MK7BoostGauge v3 health check  ($(date))"
echo "==================================================================="
echo ""

# -------------------------------------------------------- 1. MCP2515 module
info "1) MCP2515 kernel driver"
if lsmod 2>/dev/null | grep -q mcp251x; then
  ok "  mcp251x module loaded"
else
  fail "  mcp251x NOT loaded — check overlays in /boot/firmware/config.txt"
  info "       cat /boot/firmware/config.txt | grep mcp2515"
  info "       Then: sudo reboot"
fi

# Check for kernel oops in last boot
if dmesg 2>/dev/null | grep -qi "oops\|null pointer\|kernel panic"; then
  warn "  Kernel oops/panic detected in dmesg!"
  info "       dmesg | grep -i 'oops\\|panic' | tail -5"
fi
echo ""

# -------------------------------------------------------- 2. CAN interfaces
info "2) CAN interfaces"
for ch in can0 can1; do
  if ! ip link show "$ch" &>/dev/null; then
    fail "  $ch: device does not exist (MCP2515 overlay missing/failed)"
    continue
  fi
  state=$(ip -br link show "$ch" 2>/dev/null | awk '{print $2}')
  bitrate=$(ip -d link show "$ch" 2>/dev/null | grep -oP 'bitrate \K[0-9]+' || echo "?")
  if [[ "$state" == "UP" ]]; then
    ok "  $ch: UP at ${bitrate} bps"
  else
    fail "  $ch: state=$state (expected UP)"
    info "       sudo systemctl restart boostgauge-can-up"
  fi
done
echo ""

# -------------------------------------------------------- 3. Systemd services
info "3) Systemd services"
for svc in boostgauge-can-up.service boostgauge-daemon.service; do
  if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
    ok "  $svc: enabled"
  else
    fail "  $svc: NOT enabled — run sudo bash pi/setup_pi.sh"
  fi
done

if systemctl is-active --quiet boostgauge-daemon.service 2>/dev/null; then
  ok "  boostgauge-daemon.service: active (running)"
else
  fail "  boostgauge-daemon.service: NOT active"
  info "       journalctl -u boostgauge-daemon -n 30 --no-pager"
fi
echo ""

# -------------------------------------------------------- 4. HTTP daemon
info "4) HTTP daemon"
PING=$(curl -s -m 3 http://localhost:8765/ping 2>/dev/null)
if [[ -n "$PING" ]] && echo "$PING" | grep -q '"ok": true'; then
  ok "  GET /ping -> $(echo "$PING" | head -c 80)"
else
  fail "  GET /ping FAILED — daemon not listening?"
  info "       systemctl status boostgauge-daemon"
fi

STATUS=$(curl -s -m 3 http://localhost:8765/status 2>/dev/null)
if [[ -n "$STATUS" ]] && echo "$STATUS" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
  ok "  GET /status -> valid JSON"
  # Quick parse of key fields
  echo "$STATUS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('       lever:', d.get('lever') or '(none)')
print('       mode:', d.get('mode'))
print('       MAP:', d.get('map_mbar'), 'mbar (age:', d.get('map_age_s'), 's)')
print('       coolant real:', d.get('coolant_real_c'), 'C (age:', d.get('coolant_age_s'), 's)')
can = d.get('can', {})
print('       CAN tx:', can.get('tx_count'), 'rx_cluster:', can.get('rx_cluster_count'),
      'rx_can1:', can.get('rx_can1_count'), 'blocked_airbag:', can.get('blocked_airbag'))
" 2>/dev/null
else
  fail "  GET /status FAILED"
fi
echo ""

# -------------------------------------------------------- 5. Config file
info "5) Config file"
CFG="/var/lib/boostgauge/config.json"
if [[ -f "$CFG" ]]; then
  ok "  Config exists: $CFG"
  owner=$(stat -c '%U:%G' "$CFG" 2>/dev/null)
  if [[ "$owner" == "pi:pi" ]]; then
    ok "  Owner pi:pi"
  else
    fail "  Owner $owner (should be pi:pi) — chown -R pi:pi /var/lib/boostgauge"
  fi
  if python3 -c "import json; json.load(open('$CFG'))" 2>/dev/null; then
    ok "  Valid JSON"
    python3 -c "
import json
d = json.load(open('$CFG'))
keys = ['map_source', 'map_min_mbar', 'map_max_mbar', 'cluster_motor09_id_hex']
for k in keys:
    print(f'       {k} =', d.get(k))
" 2>/dev/null
  else
    fail "  Invalid JSON"
  fi
else
  info "  Not yet created (will be on first config push from PC)"
fi
echo ""

# -------------------------------------------------------- 6. Quick CAN sniff (optional)
info "6) Quick CAN sniff (3 sec on can0)"
if command -v timeout &>/dev/null && command -v candump &>/dev/null; then
  COUNT=$(timeout 3 candump can0 2>/dev/null | wc -l)
  if [[ "$COUNT" -gt 0 ]]; then
    ok "  can0: $COUNT frames in 3 sec — cluster CAN wired and active"
  else
    warn "  can0: no frames seen — check wiring or cluster powered ON"
    info "       (normal if Pi is on bench without cluster connected)"
  fi
else
  info "  can-utils not available, skipping"
fi
echo ""

# -------------------------------------------------------- summary
echo "==================================================================="
if [[ $ISSUES -eq 0 ]]; then
  echo -e "$OK Health check PASSED — system operational"
  echo ""
  echo "    PC can now connect: http://boostgauge.local:8765/ping"
  exit 0
else
  echo -e "$FAIL Health check found $ISSUES issue(s) — see details above"
  exit 1
fi
