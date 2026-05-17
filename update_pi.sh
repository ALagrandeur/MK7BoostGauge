#!/usr/bin/env bash
#
# MK7BoostGauge — One-command update from PC to Pi via USB (Bash version).
#
# Same as update_pi.ps1 but for Linux/Mac PC.
#
# Usage (from project directory):
#   ./update_pi.sh
#
set -euo pipefail

PI_HOST="${PI_HOST:-pi@boostgauge.local}"
PI_HOST_ALT="pi@raspberrypi.local"
PI_PATH="/home/pi/MK7BoostGauge"
LOCAL_PATH="$(cd "$(dirname "$0")" && pwd)"
TARBALL="/tmp/mk7boost_update.tar.gz"

say()  { echo ""; echo -e "\033[1;36m==> $1\033[0m"; }
ok()   { echo -e "    \033[1;32m[OK]\033[0m $1"; }
fail() { echo -e "    \033[1;31m[FAIL]\033[0m $1"; }

# Pre-flight
for tool in git tar scp ssh; do
  command -v "$tool" >/dev/null 2>&1 || { fail "$tool missing"; exit 1; }
done
ok "Required tools present"

[[ -d "$LOCAL_PATH/.git" ]] || { fail "Not a git repo: $LOCAL_PATH"; exit 1; }
ok "Local repo: $LOCAL_PATH"

# Find reachable Pi
PI=""
for h in "$PI_HOST" "$PI_HOST_ALT"; do
  host="${h##*@}"
  if ping -c 1 -W 2 "$host" >/dev/null 2>&1; then
    PI="$h"
    break
  fi
done
[[ -n "$PI" ]] || { fail "Pi not reachable. Check USB cable + DATA port."; exit 1; }
ok "Pi reachable at: $PI"

# Step 1: pull
say "1/4  Pulling latest from GitHub..."
cd "$LOCAL_PATH" && git pull
ok "HEAD: $(git rev-parse --short HEAD)"

# Step 2: pack
say "2/4  Packing project files..."
[[ -f "$TARBALL" ]] && rm "$TARBALL"
tar -czf "$TARBALL" -C "$LOCAL_PATH" \
    app pi_setup tests requirements.txt config.example.json \
    preview_on_pc.py update_pi.ps1 update_pi.sh \
    README.md INSTALL.md PROCEDURE.txt 2>/dev/null
ok "Tarball: $(du -h "$TARBALL" | awk '{print $1}')"

# Step 3: push
say "3/4  Pushing to Pi via USB..."
scp "$TARBALL" "${PI}:/tmp/mk7boost_update.tar.gz"
ok "Tarball pushed"

# Step 4: extract + restart
say "4/4  Extracting + restarting service on Pi..."
ssh "$PI" bash <<'EOF'
set -e
cd /home/pi/MK7BoostGauge
tar -xzf /tmp/mk7boost_update.tar.gz
rm /tmp/mk7boost_update.tar.gz
echo "    [pip install]"
.venv/bin/pip install -q -r requirements.txt --upgrade 2>&1 | tail -3 || true
echo "    [restart service]"
sudo systemctl restart boostgauge
sleep 2
echo "    [service status]"
systemctl is-active boostgauge && echo "    >>> OK: service active" || echo "    >>> FAIL: service inactive"
EOF

rm "$TARBALL"
say "Update complete!"
echo ""
echo "    Phone (MK7-BoostGauge WiFi):   http://192.168.4.1"
echo "    PC browser (via USB):          http://boostgauge.local"
echo ""
