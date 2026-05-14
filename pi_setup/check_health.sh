#!/usr/bin/env bash
#
# MK7BoostGauge — self-test / health check.
# Run on the Pi to verify install is correct and ready.
#
# Usage:  bash pi_setup/check_health.sh
# Exit code 0 = all OK, 1 = at least one issue
#
# Checks:
#   - Both CAN interfaces up at 500 kbps
#   - boostgauge.service running
#   - boostgauge-autoupdate.service enabled
#   - can-up.service enabled
#   - AP NetworkManager profile exists with correct priority
#   - Web UI responds on 192.168.4.1:80 (or localhost)
#   - Config file present and valid JSON
#   - Forbidden CAN IDs (airbag) blocklist intact

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
echo "  MK7BoostGauge health check  ($(date))"
echo "==================================================================="
echo ""

# -------------------------------------------------------- 1. CAN interfaces
info "1) CAN interfaces"
for ch in can0 can1; do
  if ! ip link show "$ch" &>/dev/null; then
    fail "  $ch: device does not exist (MCP2515 overlay missing or not loaded?)"
    info "       Check: cat /boot/firmware/config.txt | grep mcp2515"
    info "       Reboot might be needed if config was just changed"
    continue
  fi
  state=$(ip -br link show "$ch" 2>/dev/null | awk '{print $2}')
  bitrate=$(ip -d link show "$ch" 2>/dev/null | grep -oP 'bitrate \K[0-9]+' || echo "?")
  if [[ "$state" == "UP" ]]; then
    ok "  $ch: UP at ${bitrate} bps"
  else
    fail "  $ch: state=$state (should be UP)"
    info "       Try: sudo systemctl restart can-up.service"
  fi
done
echo ""

# -------------------------------------------------------- 2. systemd services
info "2) Systemd services"
for svc in can-up.service boostgauge-autoupdate.service boostgauge.service; do
  if systemctl is-enabled --quiet "$svc"; then
    ok "  $svc: enabled"
  else
    fail "  $svc: NOT enabled"
    info "       Try: sudo systemctl enable $svc"
  fi
done

# Main service active state
if systemctl is-active --quiet boostgauge.service; then
  ok "  boostgauge.service: active (running)"
else
  fail "  boostgauge.service: NOT active"
  info "       Logs: journalctl -u boostgauge -n 30 --no-pager"
fi
echo ""

# -------------------------------------------------------- 3. WiFi AP
info "3) WiFi Access Point"
if ! command -v nmcli &>/dev/null; then
  fail "  nmcli missing (NetworkManager not installed?)"
else
  if nmcli -t -f NAME connection show 2>/dev/null | grep -q "^MK7BoostGauge-AP$"; then
    ok "  AP profile 'MK7BoostGauge-AP' exists"
    prio=$(nmcli -g connection.autoconnect-priority connection show "MK7BoostGauge-AP" 2>/dev/null)
    if [[ "$prio" -ge 100 ]]; then
      ok "  AP priority: $prio (high — wins over STA)"
    else
      warn "  AP priority: $prio (should be >= 100 to prevent STA ping-pong)"
      info "       Fix: sudo nmcli connection modify 'MK7BoostGauge-AP' connection.autoconnect-priority 100"
    fi
    # Check STA priorities lower
    while IFS= read -r conn; do
      [[ -z "$conn" ]] && continue
      sta_prio=$(nmcli -g connection.autoconnect-priority connection show "$conn" 2>/dev/null)
      if [[ "$sta_prio" -ge 100 ]]; then
        warn "  STA '$conn' priority $sta_prio (should be < 100 to let AP win)"
        info "       Fix: sudo nmcli connection modify '$conn' connection.autoconnect-priority 10"
      else
        ok "  STA '$conn' priority: ${sta_prio:-0} (lower than AP — good)"
      fi
    done < <(nmcli -t -f NAME,TYPE connection show \
              | awk -F: '$2=="802-11-wireless" && $1!="MK7BoostGauge-AP"{print $1}')
    # Is AP currently active?
    if nmcli -t -f NAME,DEVICE connection show --active | grep -q "^MK7BoostGauge-AP:"; then
      ok "  AP currently ACTIVE"
    else
      warn "  AP profile exists but NOT active right now"
      info "       Bring up: sudo nmcli connection up 'MK7BoostGauge-AP'"
    fi
  else
    fail "  AP profile 'MK7BoostGauge-AP' MISSING"
    info "       Run: sudo bash $(dirname "$0")/ap_setup.sh"
  fi
fi
echo ""

# -------------------------------------------------------- 4. Web UI
info "4) Web UI"
for url in "http://localhost/api/state" "http://192.168.4.1/api/state"; do
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [[ "$http_code" == "200" ]]; then
    ok "  $url -> HTTP 200"
  else
    warn "  $url -> HTTP $http_code (may be normal if AP not active yet)"
  fi
done
echo ""

# -------------------------------------------------------- 5. Config file
info "5) Config file"
CFG="/var/lib/boostgauge/config.json"
if [[ -f "$CFG" ]]; then
  ok "  Config exists: $CFG"
  if python3 -c "import json; json.load(open('$CFG'))" 2>/dev/null; then
    ok "  Config is valid JSON"
    forbidden=$(python3 -c "
import json
d = json.load(open('$CFG'))
ids = d.get('safety',{}).get('forbidden_can_ids',[])
ids = [int(x,16) if isinstance(x,str) else int(x) for x in ids]
required = {0x040, 0x572, 0x585}
missing = required - set(ids)
print(','.join(f'0x{m:03X}' for m in missing) if missing else 'OK')
" 2>/dev/null)
    if [[ "$forbidden" == "OK" ]]; then
      ok "  Airbag IDs blocklist intact (0x040, 0x572, 0x585)"
    else
      fail "  AIRBAG IDs MISSING from forbidden_can_ids: $forbidden"
      info "       SAFETY ISSUE — restore via web UI or reset config.json"
    fi
  else
    fail "  Config is INVALID JSON"
  fi
else
  warn "  Config not yet created at $CFG"
  info "       Will be created on first service start from config.example.json"
fi
echo ""

# -------------------------------------------------------- 6. Last autoupdate log
info "6) Last autoupdate log (tail)"
LOG="/var/log/boostgauge-autoupdate.log"
if [[ -f "$LOG" ]]; then
  echo "--- last 20 lines of $LOG ---"
  tail -20 "$LOG"
  echo "--- end ---"
else
  warn "  No autoupdate log yet (service has not run since boot)"
fi
echo ""

# -------------------------------------------------------- summary
echo "==================================================================="
if [[ $ISSUES -eq 0 ]]; then
  echo -e "$OK Health check PASSED — system operational"
  exit 0
else
  echo -e "$FAIL Health check found $ISSUES issue(s) — see details above"
  exit 1
fi
