#!/usr/bin/env bash
#
# MK7BoostGauge — in-car update from GitHub.
#
# Use case: Pi is in the car, running in WiFi AP mode (no internet).
# This script temporarily switches the Pi to a known WiFi STA connection
# (e.g. your phone hotspot or home WiFi), pulls updates, then reboots
# back to AP mode automatically.
#
# Workflow:
#   1. Connect your phone to the Pi's WiFi 'MK7-BoostGauge' (password 'boost123')
#   2. SSH in: ssh pi@192.168.4.1
#   3. Run this script:  sudo bash pi_setup/update_in_car.sh "<SSID>" "<password>"
#   4. The script runs in background — your SSH session WILL drop, that's normal
#   5. Wait ~5 minutes
#   6. Reconnect phone to 'MK7-BoostGauge', SSH back in, check /tmp/update.log
#
# Examples:
#   sudo bash pi_setup/update_in_car.sh                       # uses any saved STA WiFi
#   sudo bash pi_setup/update_in_car.sh "MyPhone" "secret"    # connects fresh

set -uo pipefail

# Project + log
PROJECT_DIR="/home/pi/MK7BoostGauge"
LOG="/tmp/update_in_car.log"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

# Re-exec ourselves in background, detached, so SSH session can die safely
if [[ "${BG:-0}" != "1" ]]; then
  echo "Starting in-car update in background. SSH will drop in ~5 sec."
  echo "Reconnect to MK7-BoostGauge AP in 5 minutes, then check $LOG"
  echo ""
  BG=1 nohup sudo bash "$0" "$@" > "$LOG" 2>&1 &
  disown
  sleep 4
  exit 0
fi

# ======= Background path below =======
exec 2>&1
echo "==> [$(date)] in-car update started"

SSID="${1:-}"
PASS="${2:-}"

# 1. If SSID provided, register the connection (overwrite if exists)
if [[ -n "$SSID" ]]; then
  echo "==> Registering WiFi connection '$SSID'"
  nmcli connection delete "$SSID" 2>/dev/null || true
  nmcli device wifi connect "$SSID" password "$PASS" || {
    echo "ERROR: could not connect to '$SSID'"; exit 1; }
fi

# 2. Disable AP
echo "==> Disabling AP mode"
nmcli connection down "MK7BoostGauge-AP" || true
sleep 2

# 3. Find a saved STA WiFi and bring it up
STA_CONN=""
if [[ -n "$SSID" ]]; then
  STA_CONN="$SSID"
else
  STA_CONN=$(nmcli -t -f NAME,TYPE connection show \
              | awk -F: '$2=="802-11-wireless"{print $1}' \
              | grep -v "^MK7BoostGauge-AP$" | head -1)
fi
if [[ -z "$STA_CONN" ]]; then
  echo "ERROR: no STA WiFi connection found."
  echo "Run again with:  sudo bash $0 \"<SSID>\" \"<password>\""
  echo "Re-enabling AP and exiting."
  nmcli connection up "MK7BoostGauge-AP" || true
  exit 1
fi
echo "==> Connecting to STA WiFi '$STA_CONN'"
nmcli connection up "$STA_CONN" || {
  echo "ERROR: could not connect to '$STA_CONN'. Re-enabling AP."
  nmcli connection up "MK7BoostGauge-AP" || true
  exit 1
}

# 4. Wait for internet (max 60 sec)
echo "==> Waiting for internet"
for i in $(seq 1 30); do
  if ping -c 1 -W 2 1.1.1.1 &>/dev/null; then
    echo "    Internet OK after $((i*2)) sec"
    break
  fi
  sleep 2
done

# 5. Pull + install + setup
cd "$PROJECT_DIR" || { echo "ERROR: $PROJECT_DIR missing"; exit 1; }
echo "==> git pull"
sudo -u pi git pull
echo "==> pip install"
sudo -u pi .venv/bin/pip install -r requirements.txt --upgrade
echo "==> setup.sh"
bash pi_setup/setup.sh

# 6. Reboot back to AP
echo "==> Done. Rebooting in 5 sec — will come back in AP mode (MK7-BoostGauge)."
sync
sleep 5
reboot
