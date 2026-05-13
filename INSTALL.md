# MK7BoostGauge — Pi Zero 2W install guide

> Step-by-step from bare SD card to operational boost gauge.
> Time estimate: **30 min** flash + **10 min** wiring once HAT is in hand.
>
> **OS supported**: DietPi 64-bit (recommended) **or** Raspberry Pi OS Lite 64-bit.
> The install script auto-detects which one you're on.

---

## 1. Flash the SD card — DietPi (recommended)

DietPi advantages on Pi Zero 2W: 2× faster boot (~15 s), 5× smaller footprint
(~400 MB), logs in RAM (less SD wear), optimized for SBC.

1. Download DietPi image: https://dietpi.com/#download → Raspberry Pi → "ARMv8 64-bit"
2. Extract the `.img` from the `.7z` (use [7-Zip](https://www.7-zip.org/) or similar).
3. Flash with **Raspberry Pi Imager**: choose "Use custom" → select the `.img`,
   pick your SD card, write. (When asked about OS settings, click **NO** — DietPi
   has its own pre-config system.)
4. **DO NOT eject yet**. In Windows Explorer, the SD card shows as `boot` partition.
   Edit two files with Notepad:

   `boot/dietpi.txt` — set these keys:
   ```
   AUTO_SETUP_NET_HOSTNAME=boostgauge
   AUTO_SETUP_LOCALE=en_US.UTF-8
   AUTO_SETUP_TIMEZONE=America/Toronto
   AUTO_SETUP_NET_WIFI_ENABLED=1
   AUTO_SETUP_NET_WIFI_COUNTRY_CODE=CA
   AUTO_SETUP_AUTOMATED=1
   AUTO_SETUP_GLOBAL_PASSWORD=BoostGauge2026
   SURVEY_OPTED_IN=0
   AUTO_SETUP_SSH_SERVER_INDEX=-1
   ```

   `boot/dietpi-wifi.txt` — fill credentials:
   ```
   aWIFI_SSID[0]='YourHomeWiFi'
   aWIFI_KEY[0]='YourWiFiPassword'
   aWIFI_KEYMGR[0]='WPA-PSK'
   ```
5. Save both, eject, insert in Pi Zero 2W.

### 1-bis. Alternative — Raspberry Pi OS Lite 64-bit

If you prefer Pi OS Lite:
1. Use Raspberry Pi Imager → Pi Zero 2W → Pi OS Lite (64-bit).
2. ⚙️ settings: hostname `boostgauge`, user `pi`, password, SSH enabled, WiFi configured.
3. Write, eject, insert.

## 2. First boot + SSH

**DietPi**: first boot is **8–15 min** (runs dietpi-update + initial setup). Subsequent
boots are ~15 sec. Be patient on the first one.

**Pi OS Lite**: first boot ~60 sec.

```bash
# DietPi
ssh dietpi@boostgauge.local

# Pi OS Lite
ssh pi@boostgauge.local
```

If `.local` doesn't resolve, find the IP via your router admin page and use it.

## 3. Install MK7BoostGauge

```bash
# On the Pi, after SSH:
sudo apt update && sudo apt -y full-upgrade
sudo apt -y install git python3-pip python3-venv hostapd dnsmasq

git clone https://github.com/ALagrandeur/MK7BoostGauge.git
cd MK7BoostGauge
sudo bash pi_setup/setup.sh
```

The `setup.sh` script will:
- Enable SPI in `/boot/config.txt`
- Add MCP2515 device tree overlays for `can0` and `can1` (8 MHz crystal, WaveShare HAT)
- Bring up CAN interfaces at 500 kbps
- Install Python deps in venv
- Configure systemd service `boostgauge.service`
- (Optional) Configure WiFi AP mode `MK7-BoostGauge`
- (Optional) Setup overlayfs read-only root for SD safety

## 4. Test CAN HAT (without vehicle, on bench)

```bash
# Should list can0 and can1 as UP
ip -br link show | grep can

# Send a test frame on can0 (need a CAN load + termination, or a loopback to can1)
cansend can0 123#DEADBEEF

# Listen on can1
candump can1
```

If you see your test frame → HAT is working.

## 5. Vehicle wiring

> ⚠️ **Power MUST come from a switched +12V** (ignition-on, key-off = power off). Never tap to permanent battery — Pi will drain it overnight.

| Pi / HAT pin | Vehicle wire |
|---|---|
| DC-DC 12V in (+) | Ignition-switched fuse (e.g. accessory) via 2 A inline fuse |
| DC-DC GND | Vehicle chassis GND |
| **HAT CAN0 H** | **Cluster CAN-H** (gateway Y-cable) |
| **HAT CAN0 L** | **Cluster CAN-L** (gateway Y-cable) |
| **HAT CAN1 H** | **PCM CAN-H** (gateway Y-cable) **OR** OBD-II pin 6 |
| **HAT CAN1 L** | **PCM CAN-L** (gateway Y-cable) **OR** OBD-II pin 14 |

> 💡 **CAN1 dual-purpose** — switch between PCM tap and OBD-II diagnostic port at any time. Physically replug the wires, then change `CAN1 mode` in the web UI to match. No reboot needed.

**⚠️ Termination jumpers** : the WaveShare 2-CH CAN HAT has 120 Ω termination jumpers (`R-CAN0` and `R-CAN1` on the silkscreen). **REMOVE them** before installing in vehicle (the vehicle bus already has terminators — adding more = bus impedance broken = errors).

## 6. First connect

After installation in vehicle, key on:

1. Wait ~30 sec for Pi boot
2. On phone, scan WiFi → connect to **`MK7-BoostGauge`** (password: `boostgauge`)
3. Open browser → http://192.168.4.1
4. Adjust sliders, save, observe live cluster gauge response

## 7. Calibration procedure

With engine running, in a safe stationary location:

1. Set lever to **D** → gauge should show real coolant temp (idle ~85-90 °C)
2. Set lever to **S** → gauge enters BOOST mode, idle MAP ~300 mbar should map to needle near bottom
3. Briefly blip throttle in **S** (engine in neutral or wheels off ground!) → gauge should swing toward red zone proportional to boost peak
4. Adjust sliders in web UI:
   - If needle doesn't reach red on hard boost → lower `map_max_mbar` or raise `temp_max_c`
   - If needle is too sensitive at idle → raise `map_min_mbar`
   - If response is laggy → raise `tx_rate_hz`
5. Save config

## 8. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `ip link` doesn't show `can0/can1` | SPI not enabled, or wrong overlay. Check `/boot/config.txt`, reboot. |
| CAN errors in `dmesg` | Termination, wiring, or wrong bitrate. Confirm 500 kbps + remove termination jumpers. |
| Web UI not reachable | Check `systemctl status boostgauge`. Connect to Pi via USB-OTG ethernet gadget if WiFi AP fails. |
| Gauge frozen | Verify `candump can1 \| grep 0394` shows WBA_03 (gear). If empty, gateway isn't forwarding → check CAN1 wiring. |
| Pi reboots randomly | Power supply too weak. DC-DC must be 3 A capable, 5.0 V exact. |

## 9. Updates

```bash
cd ~/MK7BoostGauge
git pull
sudo systemctl restart boostgauge
```
