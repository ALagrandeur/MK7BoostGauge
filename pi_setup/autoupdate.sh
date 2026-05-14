#!/usr/bin/env bash
#
# MK7BoostGauge — boot-time auto-updater.
#
# Behaviour at every boot:
#   1. If a home/STA WiFi is available and provides internet:
#        - git fetch + compare HEAD vs origin/master
#        - If different: git pull, run pip install if requirements.txt changed
#   2. If home WiFi NOT available: skip update silently
#   3. ALWAYS end by switching to AP mode (so phone can connect in the car)
#
# Disable mechanism: if /var/lib/boostgauge/disable_autoupdate exists, skip
# the whole flow (useful during dev / debug).
#
# Logs to /var/log/boostgauge-autoupdate.log (rotated by journald).

set -uo pipefail

PROJECT="/home/pi/MK7BoostGauge"
DISABLE_FLAG="/var/lib/boostgauge/disable_autoupdate"
LOG="/var/log/boostgauge-autoupdate.log"

# Append to log + stdout (systemd captures both)
exec > >(tee -a "$LOG") 2>&1
echo ""
echo "==========================================================="
echo "==> [$(date)] boostgauge-autoupdate boot run"
echo "==========================================================="

# -------------------------------------------------------- 0. disable flag
if [[ -f "$DISABLE_FLAG" ]]; then
  echo "Disable flag present at $DISABLE_FLAG — skipping autoupdate."
  echo "Bringing up AP and exiting."
  nmcli connection up "MK7BoostGauge-AP" 2>/dev/null || true
  exit 0
fi

# -------------------------------------------------------- 1. check git repo
if ! [[ -d "$PROJECT/.git" ]]; then
  echo "Not a git repo at $PROJECT — skipping."
  exit 0
fi

# -------------------------------------------------------- 2. find home/STA WiFi
# Any saved WiFi connection that is NOT our AP.
STA_CONN=$(nmcli -t -f NAME,TYPE connection show \
           | awk -F: '$2=="802-11-wireless" && $1!="MK7BoostGauge-AP"{print $1}' \
           | head -1)

if [[ -z "$STA_CONN" ]]; then
  echo "No STA WiFi connection saved. Skipping update."
  echo "Bringing up AP and exiting."
  nmcli connection up "MK7BoostGauge-AP" 2>/dev/null || true
  exit 0
fi
echo "Found STA WiFi: '$STA_CONN'"

# -------------------------------------------------------- 3. try connect (with timeout)
echo "Disabling AP first (mutual exclusion on wlan0)..."
nmcli connection down "MK7BoostGauge-AP" 2>/dev/null || true
sleep 1

echo "Attempting to connect to '$STA_CONN' (30s timeout)..."
HOME_OK=0
if timeout 30 nmcli connection up "$STA_CONN" 2>/dev/null; then
  # Wait for IP + internet (max 15s)
  for i in $(seq 1 15); do
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
  # Falls through to AP setup below
else
  # ------------------------------------------------------ 4. update if newer
  cd "$PROJECT"
  echo "Fetching latest from GitHub..."
  if ! sudo -u pi git fetch origin master 2>&1; then
    echo "git fetch FAILED — keeping current version, skipping update."
  else
    LOCAL=$(sudo -u pi git rev-parse HEAD)
    REMOTE=$(sudo -u pi git rev-parse origin/master)
    if [[ "$LOCAL" == "$REMOTE" ]]; then
      echo "Already up to date ($LOCAL)."
    else
      echo "New version available: $LOCAL -> $REMOTE"
      # Detect what changed BEFORE pulling (so we can adapt actions)
      CHANGED=$(sudo -u pi git diff --name-only "$LOCAL" "$REMOTE")
      echo "Changed files:"
      echo "$CHANGED" | sed 's/^/    /'

      echo "Running git pull..."
      if sudo -u pi git pull --ff-only; then
        echo "git pull OK."
        # Re-install Python deps if requirements changed
        if echo "$CHANGED" | grep -q "^requirements\.txt$"; then
          echo "requirements.txt changed — running pip install..."
          sudo -u pi "$PROJECT/.venv/bin/pip" install -r "$PROJECT/requirements.txt" --upgrade
        fi
        # If setup.sh changed, re-run it (overlays, systemd, capabilities)
        if echo "$CHANGED" | grep -q "^pi_setup/setup\.sh$"; then
          echo "setup.sh changed — re-running it..."
          bash "$PROJECT/pi_setup/setup.sh"
          # setup.sh might have added/changed services or overlays;
          # safest is to reboot. The reboot will trigger another autoupdate
          # which will detect no new commits → skip → AP mode → start.
          echo "Rebooting in 5s to apply setup.sh changes..."
          sync
          sleep 5
          systemctl reboot
          exit 0
        fi
        echo "Update applied successfully."
      else
        echo "git pull FAILED — keeping current version."
      fi
    fi
  fi
fi

# -------------------------------------------------------- 5. switch to AP (always)
echo "Switching to AP mode (MK7-BoostGauge)..."
nmcli connection down "$STA_CONN" 2>/dev/null || true
sleep 1
if nmcli connection up "MK7BoostGauge-AP" 2>/dev/null; then
  echo "AP up — Pi accessible at 192.168.4.1"
else
  echo "WARNING: AP failed to come up. Check ap_setup.sh output."
fi

echo "==> autoupdate complete."
exit 0
