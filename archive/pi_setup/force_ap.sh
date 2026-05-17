#!/usr/bin/env bash
#
# MK7BoostGauge - force AP up (emergency script).
#
# Use when:
#   - AP not active after reboot
#   - Pi shows IP 127.0.1.1 instead of 192.168.4.1
#   - Phone can't find 'MK7-BoostGauge' WiFi
#
# What it does:
#   1. Show current state
#   2. Disconnect any STA WiFi on wlan0
#   3. Bring up MK7BoostGauge-AP profile
#   4. Verify it stuck
#
# If profile doesn't exist, automatically runs ap_setup.sh first.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

echo "=== MK7BoostGauge force AP ==="
echo ""

# 1. Current state
echo "--- Current WiFi connections ---"
nmcli -t -f NAME,TYPE,AUTOCONNECT-PRIORITY connection show 2>/dev/null \
  | awk -F: '$2=="802-11-wireless"{printf "  %s (priority=%s)\n", $1, $3}'
echo ""
echo "--- Currently active on wlan0 ---"
nmcli -t -f NAME,DEVICE connection show --active | grep ":wlan0$" || echo "  (nothing)"
echo ""
echo "--- Current wlan0 IPs ---"
ip -br addr show wlan0 2>/dev/null || echo "  wlan0 not found"
echo ""

# 2. Check if AP profile exists
if ! nmcli -t -f NAME connection show 2>/dev/null | grep -q "^MK7BoostGauge-AP$"; then
  echo "AP profile MISSING. Running ap_setup.sh first..."
  bash "$SCRIPT_DIR/ap_setup.sh"
  exit $?
fi

# 3. Disconnect any STA on wlan0
echo "--- Disconnecting any STA WiFi on wlan0 ---"
while IFS= read -r conn; do
  [[ -z "$conn" ]] && continue
  if [[ "$conn" != "MK7BoostGauge-AP" ]]; then
    echo "  Bringing down '$conn'"
    nmcli connection down "$conn" 2>/dev/null || true
  fi
done < <(nmcli -t -f NAME,TYPE,DEVICE connection show --active \
          | awk -F: '$2=="802-11-wireless" && $3=="wlan0"{print $1}')

sleep 2

# 4. Bring up AP
echo ""
echo "--- Bringing up MK7BoostGauge-AP ---"
if nmcli connection up "MK7BoostGauge-AP"; then
  sleep 3
  echo ""
  echo "--- After: ---"
  nmcli -t -f NAME,DEVICE connection show --active | grep ":wlan0$" || true
  ip -br addr show wlan0
  echo ""
  echo "SUCCESS - phone should now see WiFi 'MK7-BoostGauge'"
  echo "Browse: http://192.168.4.1"
else
  echo ""
  echo "FAIL - check 'systemctl status NetworkManager' for errors"
  exit 1
fi
