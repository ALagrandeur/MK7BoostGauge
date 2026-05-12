#!/usr/bin/env bash
#
# MK7BoostGauge — configure Pi as WiFi AP "MK7-BoostGauge".
# Run AFTER setup.sh + after first home-WiFi install is no longer needed.
#
# Network: 192.168.4.1/24 (Pi), DHCP 192.168.4.10–50 for clients
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo)."
  exit 1
fi

SSID="MK7-BoostGauge"
PASS="boostgauge"
CHANNEL=6

echo "==> Stop existing wifi services"
systemctl stop wpa_supplicant 2>/dev/null || true
systemctl disable wpa_supplicant 2>/dev/null || true

echo "==> Configure static IP on wlan0"
cat > /etc/dhcpcd.conf.d/wlan0-ap.conf <<EOF
interface wlan0
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
EOF
# Some Pi OS releases don't support /etc/dhcpcd.conf.d — fallback append
if [[ ! -d /etc/dhcpcd.conf.d ]]; then
  if ! grep -q "# >>> MK7BoostGauge AP" /etc/dhcpcd.conf 2>/dev/null; then
    cat >> /etc/dhcpcd.conf <<EOF

# >>> MK7BoostGauge AP >>>
interface wlan0
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
# <<< MK7BoostGauge AP <<<
EOF
  fi
fi

echo "==> Configure dnsmasq (DHCP server for clients)"
cat > /etc/dnsmasq.d/boostgauge.conf <<EOF
interface=wlan0
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,24h
domain-needed
bogus-priv
# Captive portal — redirect all DNS to Pi (open page in any browser)
address=/#/192.168.4.1
EOF

echo "==> Configure hostapd (WiFi AP)"
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
sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

echo "==> Enable + start services"
systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq

echo ""
echo "Done. Reboot to activate."
echo "After reboot: connect to SSID '$SSID' password '$PASS', browse http://192.168.4.1"
