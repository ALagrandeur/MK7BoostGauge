#!/usr/bin/env bash
#
# MK7BoostGauge — enable USB Ethernet gadget on Pi Zero 2W.
#
# After this, plugging a USB cable from the Pi's DATA port (NOT power port)
# to a PC creates a virtual ethernet connection. The PC sees a new "RNDIS
# Ethernet Gadget" adapter. SSH then works over USB:
#
#   ssh pi@boostgauge.local         (mDNS via USB)
#   or ssh pi@<IP-assigned-by-PC>
#
# Benefits:
#   - WiFi card stays permanently as AP for phone connection
#   - Update workflow via USB cable from PC (much more reliable than WiFi)
#
# Pi Zero 2W has TWO micro-USB ports:
#   - "PWR" port = power only
#   - "USB" port (the other one) = data + power (use THIS one for the cable)
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

CONFIG_TXT=""
for c in /boot/firmware/config.txt /boot/config.txt; do
  [[ -f "$c" ]] && CONFIG_TXT="$c" && break
done
CMDLINE_TXT="$(dirname "$CONFIG_TXT")/cmdline.txt"

if [[ -z "$CONFIG_TXT" ]] || [[ ! -f "$CMDLINE_TXT" ]]; then
  echo "ERROR: config.txt or cmdline.txt not found"
  exit 1
fi

echo "==> Using $CONFIG_TXT"
echo "==> Using $CMDLINE_TXT"

# 1. Add dtoverlay=dwc2 to config.txt
if ! grep -q "# >>> MK7BoostGauge USB gadget" "$CONFIG_TXT"; then
  echo "==> Adding dwc2 overlay to config.txt"
  cat >> "$CONFIG_TXT" <<'EOF'

# >>> MK7BoostGauge USB gadget mode >>>
dtoverlay=dwc2
# <<< MK7BoostGauge USB gadget mode <<<
EOF
else
  echo "==> dwc2 overlay already present in config.txt, skipping"
fi

# 2. Add modules-load=dwc2,g_ether to cmdline.txt (must be on the single line)
if ! grep -q "modules-load=dwc2,g_ether" "$CMDLINE_TXT"; then
  echo "==> Adding modules-load=dwc2,g_ether to cmdline.txt"
  # Insert before 'rootwait' (or at the end if rootwait not found)
  sed -i 's| rootwait| modules-load=dwc2,g_ether rootwait|' "$CMDLINE_TXT" || \
    sed -i 's|$| modules-load=dwc2,g_ether|' "$CMDLINE_TXT"
else
  echo "==> modules-load already present in cmdline.txt, skipping"
fi

# 3. Show resulting cmdline.txt
echo ""
echo "==> Resulting cmdline.txt:"
cat "$CMDLINE_TXT"
echo ""

echo "==> Done. REBOOT NOW: sudo reboot"
echo ""
echo "After reboot:"
echo "  1. Plug USB cable from Pi DATA port (NOT power port) to your PC"
echo "  2. On Windows: a 'USB Ethernet/RNDIS Gadget' adapter appears"
echo "     If Windows asks for driver, install via:"
echo "     Device Manager -> RNDIS adapter -> Update driver ->"
echo "     'Browse my computer' -> 'Let me pick' -> 'Microsoft' ->"
echo "     'Remote NDIS Compatible Device'"
echo "  3. From PC: ssh pi@boostgauge.local"
echo "     (or find IP via 'arp -a' if mDNS doesnt resolve)"
echo "  4. WiFi card remains free for AP mode (MK7-BoostGauge)"
