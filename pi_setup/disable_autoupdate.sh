#!/usr/bin/env bash
#
# MK7BoostGauge — permanently disable boot-time WiFi auto-update.
#
# After this:
#   - WiFi card stays as AP only at boot (no STA switch)
#   - boostgauge.service starts immediately (no autoupdate wait)
#   - Update workflow becomes manual: SSH in via USB, git pull, restart
#
# To re-enable later: sudo bash pi_setup/enable_autoupdate.sh
# (or just: sudo systemctl enable boostgauge-autoupdate.service)
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

echo "==> Stopping + disabling boostgauge-autoupdate.service"
systemctl stop boostgauge-autoupdate.service 2>/dev/null || true
systemctl disable boostgauge-autoupdate.service 2>/dev/null || true

echo "==> Updating boostgauge.service to remove dependency on autoupdate"
# Edit the unit file in-place: remove autoupdate from After= and Wants=
SVC_FILE="/etc/systemd/system/boostgauge.service"
if [[ -f "$SVC_FILE" ]]; then
  # Backup once
  [[ -f "${SVC_FILE}.preautoremove.bak" ]] || cp "$SVC_FILE" "${SVC_FILE}.preautoremove.bak"
  sed -i 's| boostgauge-autoupdate.service||g' "$SVC_FILE"
  systemctl daemon-reload
fi

echo "==> Creating /var/lib/boostgauge/disable_autoupdate flag"
# This flag is also checked by autoupdate.sh in case the service ever runs.
mkdir -p /var/lib/boostgauge
touch /var/lib/boostgauge/disable_autoupdate

echo ""
echo "==> Done."
echo ""
echo "WiFi auto-update at boot is now DISABLED."
echo "Pi will boot directly into AP mode."
echo ""
echo "To manually update from PC via USB:"
echo "  ssh pi@boostgauge.local"
echo "  cd ~/MK7BoostGauge && git pull"
echo "  sudo systemctl restart boostgauge"
echo ""
echo "To re-enable autoupdate later:"
echo "  sudo rm /var/lib/boostgauge/disable_autoupdate"
echo "  sudo systemctl enable --now boostgauge-autoupdate.service"
echo "  # And re-add 'boostgauge-autoupdate.service' to After/Wants in"
echo "  # /etc/systemd/system/boostgauge.service (or re-run setup.sh)"
