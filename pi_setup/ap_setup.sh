#!/usr/bin/env bash
#
# MK7BoostGauge — configure Pi as WiFi AP "MK7-BoostGauge".
# Detects the network manager (ifupdown / dhcpcd / NetworkManager) and adapts.
#
# Compatible:
#   - DietPi (ifupdown)
#   - Raspberry Pi OS Lite Bullseye (dhcpcd)
#   - Raspberry Pi OS Lite Bookworm (NetworkManager)
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

# ---------------------------------------------------------------- detect network stack
NET_STACK="unknown"
if command -v nmcli &>/dev/null && systemctl is-active --quiet NetworkManager 2>/dev/null; then
  NET_STACK="networkmanager"
elif [[ -f /etc/dhcpcd.conf ]] && systemctl is-enabled --quiet dhcpcd 2>/dev/null; then
  NET_STACK="dhcpcd"
elif [[ -d /etc/network/interfaces.d ]] || [[ -f /etc/network/interfaces ]]; then
  NET_STACK="ifupdown"
fi
echo "==> Detected network stack: $NET_STACK"

if [[ "$NET_STACK" == "unknown" ]]; then
  echo "ERROR: could not detect network manager. Please configure WiFi AP manually."
  exit 1
fi

# ---------------------------------------------------------------- install deps
echo "==> Installing hostapd + dnsmasq if missing"
apt -qq update
apt -qq -y install hostapd dnsmasq

# ---------------------------------------------------------------- stop conflicting services
echo "==> Stop conflicting services"
systemctl stop wpa_supplicant 2>/dev/null || true
# Don't disable wpa_supplicant fully — hostapd uses its own WPA implementation
# but on some systems wpa_supplicant manages other interfaces.

# ---------------------------------------------------------------- per-stack static IP
case "$NET_STACK" in
  ifupdown)
    echo "==> Configuring static IP on wlan0 via ifupdown"
    mkdir -p /etc/network/interfaces.d
    cat > /etc/network/interfaces.d/wlan0-ap <<EOF
allow-hotplug wlan0
iface wlan0 inet static
    address 192.168.4.1
    netmask 255.255.255.0
EOF
    ;;
  dhcpcd)
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
    ;;
  networkmanager)
    echo "==> Configuring WiFi AP via NetworkManager (preferred on Pi OS Bookworm)"
    # NetworkManager replaces hostapd entirely on Bookworm. Configure with nmcli.
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
          wifi-sec.psk "$PASS"
    nmcli connection up "MK7BoostGauge-AP"
    echo ""
    echo "==> Done (NetworkManager mode). No hostapd/dnsmasq needed."
    echo "    SSID '$SSID' password '$PASS' active. Browse http://192.168.4.1"
    exit 0  # NetworkManager handles everything — skip hostapd/dnsmasq config below
    ;;
esac

# ---------------------------------------------------------------- dnsmasq (DHCP for clients)
echo "==> Configure dnsmasq (DHCP server for clients)"
mkdir -p /etc/dnsmasq.d
cat > /etc/dnsmasq.d/boostgauge.conf <<EOF
interface=wlan0
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,24h
domain-needed
bogus-priv
# Captive portal — redirect all DNS to Pi (open page in any browser)
address=/#/192.168.4.1
EOF

# ---------------------------------------------------------------- hostapd (AP itself)
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
# Make hostapd read our config
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
