#!/usr/bin/env bash
#
# MK7BoostGauge — switch Pi to USB-primary mode.
#
# After this:
#   - USB gadget enabled (Pi reachable via USB cable from PC)
#   - WiFi card stays PERMANENTLY as AP 'MK7-BoostGauge'
#   - Boot-time WiFi auto-update is DISABLED (no STA switch at boot)
#   - Updates are done manually from PC via update_pi.ps1 over USB
#
# To revert later:
#   sudo systemctl enable boostgauge-autoupdate.service
#   # And edit /boot/firmware/config.txt to remove dwc2 + dwc2,g_ether
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

cat <<'BANNER'
================================================================
   MK7BoostGauge — Switch to USB-primary mode
================================================================

This will:
  1. Enable USB Ethernet gadget (dwc2 + g_ether modules)
  2. Disable boot-time WiFi auto-update
  3. Ensure WiFi AP 'MK7-BoostGauge' stays active
  4. Reboot

After reboot:
  - Phone connects to MK7-BoostGauge / boost123 -> http://192.168.4.1
  - PC connects via USB cable -> ssh pi@boostgauge.local
  - Updates done from PC: .\update_pi.ps1 (Windows) or ./update_pi.sh

The Pi will no longer try to fetch GitHub updates at boot.
You control when updates happen via the update_pi script.

================================================================
BANNER

read -p "Press ENTER to continue, or Ctrl+C to abort..." _

echo ""
echo "==> [1/4] Ensuring WiFi AP is configured (idempotent)"
# CRITICAL: ap_setup.sh must run so MK7BoostGauge-AP profile exists with
# autoconnect-priority=100. Without this, after reboot the Pi has no AP
# and the phone cannot connect.
bash "$SCRIPT_DIR/ap_setup.sh"

echo ""
echo "==> [2/4] Enabling USB Ethernet gadget"
bash "$SCRIPT_DIR/enable_usb_gadget.sh"

echo ""
echo "==> [3/4] Disabling boot-time WiFi auto-update"
bash "$SCRIPT_DIR/disable_autoupdate.sh"

echo ""
echo "==> [4/4] Final verification"
echo ""
echo "    Current WiFi connections (autoconnect priority):"
nmcli -t -f NAME,TYPE,AUTOCONNECT-PRIORITY connection show 2>/dev/null \
  | awk -F: '$2=="802-11-wireless"{printf "      %s (priority=%s)\n", $1, $3}'
echo ""
echo "    AP profile installed: $(nmcli -t -f NAME connection show | grep -c '^MK7BoostGauge-AP$')"
echo "    Autoupdate disabled : $(test -f /var/lib/boostgauge/disable_autoupdate && echo yes || echo no)"
echo "    USB gadget config   : $(grep -c '^dtoverlay=dwc2$' /boot/firmware/config.txt 2>/dev/null || echo 0)"
echo ""
echo "==> Rebooting in 15 sec to apply"
echo ""
echo "After reboot:"
echo "  - On phone: connect to MK7-BoostGauge WiFi (password boost123)"
echo "  - On PC: plug USB cable to Pi DATA port, then:"
echo "      ssh pi@boostgauge.local"
echo ""
sleep 15
reboot
