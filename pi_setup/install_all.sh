#!/usr/bin/env bash
#
# MK7BoostGauge — ONE-SHOT install. Does setup + AP + reboot.
#
# Usage (on a fresh Pi OS Lite 64-bit):
#   git clone https://github.com/ALagrandeur/MK7BoostGauge.git
#   cd MK7BoostGauge
#   sudo bash pi_setup/install_all.sh
#
# That's it. The Pi will:
#   1. Run setup.sh (system deps + CAN overlays + systemd services + autoupdate)
#   2. Run ap_setup.sh (creates 'MK7-BoostGauge' WiFi AP, password 'boost123')
#   3. Reboot automatically
#
# WARNING: ap_setup.sh switches the Pi to AP mode immediately.
# Your current SSH session over home WiFi WILL drop. After reboot, reconnect
# via the 'MK7-BoostGauge' WiFi from your phone (password 'boost123'),
# then http://192.168.4.1
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: must run as root (use sudo)."
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cat <<'BANNER'
================================================================
   MK7BoostGauge — One-shot installer
================================================================

This will:
  1. Install system + Python dependencies
  2. Configure CAN HAT (MCP2515 overlays)
  3. Create systemd service (auto-start on boot)
  4. Setup boot-time auto-updater (pulls GitHub on boot)
  5. Create WiFi AP 'MK7-BoostGauge' (password 'boost123')
  6. Reboot the Pi to activate everything

Total time: ~5-8 minutes.

After reboot:
  - Pi creates 'MK7-BoostGauge' WiFi (visible on phone)
  - Connect with password: boost123
  - Open browser: http://192.168.4.1

================================================================
BANNER

echo ""
read -p "Press ENTER to continue, or Ctrl+C to abort..." _

echo ""
echo "##############################################################"
echo "# STEP 1/3 — Running setup.sh                                #"
echo "##############################################################"
bash "$PROJECT_DIR/pi_setup/setup.sh"

echo ""
echo "##############################################################"
echo "# STEP 2/3 — Running ap_setup.sh                             #"
echo "##############################################################"
bash "$PROJECT_DIR/pi_setup/ap_setup.sh"

echo ""
echo "##############################################################"
echo "# STEP 3/3 — Rebooting in 10 seconds                         #"
echo "##############################################################"
echo ""
echo "After reboot:"
echo "  1. On your phone, connect to WiFi: MK7-BoostGauge"
echo "     Password: boost123"
echo "  2. Open browser: http://192.168.4.1"
echo ""
echo "If you need to SSH back in, the Pi will be at 192.168.4.1"
echo "(connect your phone or PC to MK7-BoostGauge first)."
echo ""
echo "Rebooting now..."
sleep 10
reboot
