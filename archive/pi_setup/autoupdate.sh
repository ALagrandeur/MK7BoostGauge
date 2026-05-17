#!/usr/bin/env bash
#
# MK7BoostGauge — boot-time auto-updater.
#
# Behaviour at every boot:
#   1. If a STA WiFi is in range and provides internet:
#        - git fetch + compare HEAD vs origin/master
#        - If different: git pull, run pip install if requirements.txt changed,
#          re-run setup.sh + reboot if it changed
#   2. If STA not in range OR no internet: skip update silently
#   3. ALWAYS end by ensuring AP is up (so phone can connect in the car)
#
# Disable mechanism: if /var/lib/boostgauge/disable_autoupdate exists, skip
# the whole flow (useful during dev / debug).
#
# Hard guarantees:
#   - AP comes up at end via trap (even if script fails midway)
#   - STA attempt has SHORT timeout (10s) to minimize AP downtime in car
#   - Any errors are logged, no error exit (always exit 0 so systemd unit OK)

set -uo pipefail

PROJECT="/home/pi/MK7BoostGauge"
DISABLE_FLAG="/var/lib/boostgauge/disable_autoupdate"
LOG="/var/log/boostgauge-autoupdate.log"
STA_CONN=""

# Append to log + stdout (systemd captures both)
exec > >(tee -a "$LOG") 2>&1
echo ""
echo "==========================================================="
echo "==> [$(date)] boostgauge-autoupdate boot run"
echo "==========================================================="

# -------------------------------------------------------- HARD GUARANTEE
# Whatever happens below, ensure AP is up at exit (if it exists).
ensure_ap_up() {
  echo "==> Ensuring AP is up (cleanup trap)"
  if nmcli -t -f NAME connection show 2>/dev/null | grep -q "^MK7BoostGauge-AP$"; then
    # Bring down any STA we might have brought up
    if [[ -n "$STA_CONN" ]]; then
      nmcli connection down "$STA_CONN" 2>/dev/null || true
    fi
    sleep 1
    if nmcli connection up "MK7BoostGauge-AP" 2>/dev/null; then
      echo "    AP up — Pi at 192.168.4.1"
    else
      echo "    WARNING: AP failed to come up. Re-trying in 3 sec..."
      sleep 3
      nmcli connection up "MK7BoostGauge-AP" 2>/dev/null || \
        echo "    FATAL: AP refuses to come up. SSH via STA may be your only option."
    fi
  else
    echo "    AP profile 'MK7BoostGauge-AP' NOT installed."
    echo "    Run: sudo bash $PROJECT/pi_setup/ap_setup.sh"
    echo "    For now, leaving STA up so you can SSH in to fix."
  fi
}
trap ensure_ap_up EXIT

# -------------------------------------------------------- 0. disable flag
if [[ -f "$DISABLE_FLAG" ]]; then
  echo "Disable flag present at $DISABLE_FLAG — skipping update."
  exit 0
fi

# -------------------------------------------------------- 1. check git repo
if ! [[ -d "$PROJECT/.git" ]]; then
  echo "Not a git repo at $PROJECT — skipping."
  exit 0
fi

# -------------------------------------------------------- 2. find saved STA WiFi
STA_CONN=$(nmcli -t -f NAME,TYPE connection show \
           | awk -F: '$2=="802-11-wireless" && $1!="MK7BoostGauge-AP"{print $1}' \
           | head -1)

if [[ -z "$STA_CONN" ]]; then
  echo "No STA WiFi profile saved. Skipping update (AP only)."
  exit 0
fi
echo "Found STA WiFi profile: '$STA_CONN'"

# -------------------------------------------------------- 3. attempt STA (short timeout)
echo "Disabling AP briefly (mutual exclusion on wlan0)..."
nmcli connection down "MK7BoostGauge-AP" 2>/dev/null || true
sleep 1

echo "Trying to connect to '$STA_CONN' (10s timeout — short to limit AP downtime)..."
HOME_OK=0
if timeout 10 nmcli connection up "$STA_CONN" 2>/dev/null; then
  echo "STA connected. Waiting for internet (5s max)..."
  for i in $(seq 1 5); do
    if ping -c 1 -W 2 1.1.1.1 &>/dev/null; then
      HOME_OK=1
      echo "Internet OK after $i sec"
      break
    fi
    sleep 1
  done
fi

if [[ $HOME_OK -ne 1 ]]; then
  echo "Home WiFi unreachable or no internet — skipping update."
  exit 0   # trap restores AP
fi

# -------------------------------------------------------- 4. update if newer
cd "$PROJECT" || exit 0
echo "Fetching latest from GitHub..."
if ! sudo -u pi git fetch origin master 2>&1; then
  echo "git fetch FAILED — keeping current version, skipping update."
  exit 0
fi

LOCAL=$(sudo -u pi git rev-parse HEAD 2>/dev/null || echo "unknown")
REMOTE=$(sudo -u pi git rev-parse origin/master 2>/dev/null || echo "unknown")
if [[ "$LOCAL" == "$REMOTE" ]]; then
  echo "Already up to date ($LOCAL)."
  exit 0
fi

echo "New version available: $LOCAL -> $REMOTE"
CHANGED=$(sudo -u pi git diff --name-only "$LOCAL" "$REMOTE" 2>/dev/null)
echo "Changed files:"
echo "$CHANGED" | sed 's/^/    /'

echo "Running git pull --ff-only..."
if ! sudo -u pi git pull --ff-only; then
  echo "git pull FAILED — keeping current version."
  exit 0
fi
echo "git pull OK."

# Re-install Python deps if requirements changed
if echo "$CHANGED" | grep -q "^requirements\.txt$"; then
  echo "requirements.txt changed — running pip install..."
  sudo -u pi "$PROJECT/.venv/bin/pip" install -r "$PROJECT/requirements.txt" --upgrade \
    || echo "pip install FAILED — service may not start cleanly."
fi

# If setup.sh changed, re-run it (overlays, systemd, capabilities)
if echo "$CHANGED" | grep -q "^pi_setup/setup\.sh$"; then
  echo "setup.sh changed — re-running it..."
  bash "$PROJECT/pi_setup/setup.sh" || \
    echo "setup.sh re-run FAILED — system may need manual fix."
  echo "Setup re-run done. Rebooting to apply..."
  # Disarm trap (we WANT the reboot to happen even if AP setup races)
  trap - EXIT
  sync
  sleep 5
  systemctl reboot
  exit 0
fi

# If ap_setup.sh changed, re-run it (might need new AP config)
if echo "$CHANGED" | grep -q "^pi_setup/ap_setup\.sh$"; then
  echo "ap_setup.sh changed — re-running it..."
  bash "$PROJECT/pi_setup/ap_setup.sh" || \
    echo "ap_setup.sh re-run FAILED — falling through to standard AP up."
fi

echo "Update applied successfully ($LOCAL -> $REMOTE)."
exit 0  # trap restores AP
