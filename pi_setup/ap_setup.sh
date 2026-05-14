#!/usr/bin/env bash
#
# MK7BoostGauge — configure Pi as WiFi AP "MK7-BoostGauge".
# Detects the Pi OS network manager (NetworkManager / dhcpcd) and adapts.
#
# Compatible:
#   - Raspberry Pi OS Lite Bookworm (NetworkManager) — default since Oct 2023
#   - Raspberry Pi OS Lite Bullseye (dhcpcd) — older release
#
# Network: 192.168.4.1/24 (Pi), DHCP 192.168.4.10–50 for clients
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

SSID="MK7-BoostGauge"
PASS="boost123"
CHANNEL=6

# ---------------------------------------------------------------- detect network stack
NET_STACK="unknown"
if command -v nmcli &>/dev/null && systemctl is-active --quiet NetworkManager 2>/dev/null; then
  NET_STACK="networkmanager"
elif [[ -f /etc/dhcpcd.conf ]] && systemctl is-enabled --quiet dhcpcd 2>/dev/null; then
  NET_STACK="dhcpcd"
fi
echo "==> Detected network stack: $NET_STACK"

if [[ "$NET_STACK" == "unknown" ]]; then
  echo "ERROR: could not detect NetworkManager or dhcpcd."
  echo "       Are you sure you're on Raspberry Pi OS Lite ?"
  exit 1
fi

# ---------------------------------------------------------------- NetworkManager path (Bookworm)
if [[ "$NET_STACK" == "networkmanager" ]]; then
  echo "==> Configuring WiFi AP via NetworkManager (Pi OS Bookworm)"
  # NetworkManager handles AP entirely with nmcli. No hostapd/dnsmasq needed.
  nmcli connection delete "MK7BoostGauge-AP" 2>/dev/null || true
  nmcli connection add type wifi ifname wlan0 con-name "MK7BoostGauge-AP" \
        autoconnect yes ssid "$SSID"
  nmcli connection modify "MK7BoostGauge-AP" \
        802-11-wireless.mode ap \
        802-11-wireless.band bg \
        802-11-wireless.channel $CHANNEL \
        ipv4.method shared \
        ipv4.addresses 192.168.4.1/24 \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "$PASS" \
        connection.autoconnect-priority 100

  # ---- CRITICAL: prevent NetworkManager ping-pong between AP and STA ----
  # Without explicit priorities, NM alternates wlan0 between any saved STA
  # WiFi (autoconnect=yes by default) and our AP — Pi IP flips between
  # 192.168.4.1 and 127.0.1.1 every 10 sec, UI unreachable.
  # Solution: AP priority=100 (above), all OTHER WiFi profiles priority=10.
  echo "==> Setting STA WiFi connections to lower autoconnect priority"
  STA_LIST=$(nmcli -t -f NAME,TYPE connection show \
              | awk -F: '$2=="802-11-wireless" && $1!="MK7BoostGauge-AP"{print $1}')
  if [[ -n "$STA_LIST" ]]; then
    while IFS= read -r conn; do
      [[ -z "$conn" ]] && continue
      echo "    '$conn' -> autoconnect-priority=10 (AP wins)"
      nmcli connection modify "$conn" connection.autoconnect-priority 10
      # ALSO bring it down to release wlan0 immediately
      nmcli connection down "$conn" 2>/dev/null || true
    done <<< "$STA_LIST"
  else
    echo "    (no STA WiFi connections found — only the AP will exist)"
  fi

  nmcli connection up "MK7BoostGauge-AP"
  echo ""
  echo "==> Done. SSID '$SSID' password '$PASS' active."
  echo "    Browse: http://192.168.4.1"
  echo "    Note: any saved STA WiFi remains for autoupdate use, but AP wins"
  echo "          autoconnect priority. autoupdate.sh manually brings up STA"
  echo "          briefly when needed (boot-time check)."
  exit 0
fi

# ---------------------------------------------------------------- dhcpcd path (Bullseye)
echo "==> Installing hostapd + dnsmasq (Pi OS Bullseye / dhcpcd path)"
apt -qq update
apt -qq -y install hostapd dnsmasq

echo "==> Stopping wpa_supplicant"
systemctl stop wpa_supplicant 2>/dev/null || true

echo "==> Configuring static IP on wlan0 via dhcpcd"
mkdir -p /etc/dhcpcd.conf.d
cat > /etc/dhcpcd.conf.d/wlan0-ap.conf <<EOF
interface wlan0
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
EOF
# Older dhcpcd doesn't support .conf.d → also append in main file as fallback
if ! grep -q "# >>> MK7BoostGauge AP" /etc/dhcpcd.conf 2>/dev/null; then
  cat >> /etc/dhcpcd.conf <<EOF

# >>> MK7BoostGauge AP >>>
interface wlan0
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
# <<< MK7BoostGauge AP <<<
EOF
fi

echo "==> Configure dnsmasq (DHCP server for clients)"
mkdir -p /etc/dnsmasq.d
cat > /etc/dnsmasq.d/boostgauge.conf <<EOF
interface=wlan0
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,24h
domain-needed
bogus-priv
# Captive portal — redirect all DNS to Pi
address=/#/192.168.4.1
EOF

echo "==> Configure hostapd (WiFi AP)"
mkdir -p /etc/hostapd
cat > /etc/hostapd/hostapd.conf <<EOF
interface=wlan0
driver=nl80211
ssid=$SSID
hw_mode=g
channel=$CHANNEL
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=$PASS
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
EOF
if [[ -f /etc/default/hostapd ]]; then
  sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
fi

echo "==> Enable + start services"
systemctl unmask hostapd 2>/dev/null || true
systemctl enable hostapd
systemctl enable dnsmasq

echo ""
echo "Done. Reboot to activate."
echo "After reboot: connect to SSID '$SSID' password '$PASS', browse http://192.168.4.1"
