# MK7BoostGauge

Standalone in-car turbocharger boost gauge using the VW Golf MK7 instrument cluster
coolant temperature needle as a boost indicator.

> **Sister project**: [`mk7-cluster-bench-controller`](https://github.com/ALagrandeur/mk7-cluster-bench-controller)
> — bench-only PC-tethered controller used to reverse-engineer cluster behaviour.
> This project (`MK7BoostGauge`) is the **autonomous in-car** evolution.

> 📌 **Daily workflow**: see [`USB_WORKFLOW.md`](USB_WORKFLOW.md) —
> Pi accessed via USB cable from PC, WiFi 100% dedicated to phone AP.
> One-command update from PC: `.\update_pi.ps1`

---

## Concept

Replace the cluster's coolant temperature needle with a live **turbo boost gauge** while
the vehicle is in **Sport / Manual / Neutral** mode. Returns to the **real coolant temp**
when in **Drive / Park / Reverse**.

```
Vehicle Powertrain CAN  ─────►  Pi reads MAP (mbar) via UDS or sniff
                                      │
                                      ▼
                            MAP→°C mapping (configurable)
                                      │
                                      ▼
Vehicle Cluster CAN     ◄─────  Pi writes Motor_09 (0x647)
                                conditionally on gear lever
                                {S,M,N} → BOOST  /  {D,P,R} → silent (gateway forwards real)
```

## Hardware

| Item | Source | Approx CAD |
|---|---|---|
| Raspberry Pi Zero 2W | Pi Hut / Adafruit / Amazon CA | 20 $ |
| **WaveShare 2-CH CAN HAT** (MCP2515 ×2 + transceivers) | WaveShare / Amazon | 30 $ |
| MicroSD 16 GB Class 10 | any | 10 $ |
| Boîtier officiel Pi Zero | any | 10 $ |
| DC-DC 12 V → 5 V USB-C, 3 A | Amazon | 10 $ |
| Fusible auto 2 A + porte-fusible | any | 3 $ |
| Câble FLRY 0.5 mm² 2 m | any | 5 $ |
| **Total** | | **~88 $** |

## Architecture

- **OS**: **Raspberry Pi OS Lite 64-bit** (headless)
- **Boot mode**: Read-only root (overlayfs) → SD card survives abrupt power-cuts
- **CAN stack**: SocketCAN kernel driver
  - `can0` = **Cluster CAN** (always)
  - `can1` = **PCM (Powertrain)** OR **Diagnostic (OBD-II)** — togglable in UI without reboot
- **App**: Python 3 / Flask + Flask-SocketIO
- **WiFi**: AP mode by default, SSID `MK7-BoostGauge`, default IP `192.168.4.1`
- **Auto-start**: systemd service, launched at boot
- **Persistence**: `/var/lib/boostgauge/config.json` (writable partition only)

### CAN1 toggle behaviour

| Mode | Physical wiring | MAP source (default) | When to use |
|---|---|---|---|
| **PCM** | CAN1 tapped on Powertrain CAN at gateway Y-cable | Broadcast sniff (Motor_xx) — fast, low latency | Production install in vehicle |
| **Diagnostic** | CAN1 plugged into OBD-II J1962 (pin 6 = H, pin 14 = L) | UDS query DID 0x39C0 forced — broadcasts not visible through gateway | Dev / debug / temporary access |

The toggle is hot-applied — change it from the web UI and replug the cable, no reboot.

## 🛡️ Safety guarantees

The "🛡️ Sécurité" card in the web UI exposes two hard guarantees, both enforced
at the **lowest possible layer** (`CanManager.send`) — they cannot be bypassed
by any higher-level logic, config edit, or REST call:

### 1. Airbag blocklist (hardcoded)

```python
forbidden_ids = {0x040, 0x572, 0x585}  # Airbag_01, Airbag_02, Airbag_03
```

- Any TX attempt with an ID in this set is **rejected before reaching the bus**.
- Counter visible in UI : "TX bloqués (airbag)".
- The `/api/config` endpoint **refuses** any patch that tries to remove or shrink
  this set (HTTP 403). Tested in `tests/test_safety.py`.

### 2. CAN1 LISTEN-ONLY switch

A user-controllable safety arming switch :

- When **armed** (checkbox checked): `CanManager.send("can1", ...)` returns False
  for every call, regardless of ID. Includes the firmware's own UDS query.
- When **disarmed**: normal operation (UDS query active in Diagnostic mode, etc.)
- The CAN0 (cluster) bus is unaffected — Motor_09 keeps broadcasting.
- Hot-applied via REST `/api/config` with body `{"can": {"can1_listen_only": true}}`.
- Counter visible in UI : "TX bloqués (listen-only)".

Use this switch any time you connect to a vehicle for **read-only sniffing**,
or when you're unsure whether your CAN1 cable is plugged into the right bus
and want zero risk of injecting traffic on the wrong network.

## Config (settable in web UI, persisted)

| Param | Default | Range | Description |
|---|---|---|---|
| `map_min_mbar` | 300 | 0–4000 | MAP value mapped to `temp_min` |
| `map_max_mbar` | 2500 | 0–4000 | MAP value mapped to `temp_max` |
| `temp_min_c` | 50 | 0–150 | Cluster temp °C at MAP_min (default 50 = needle bottom) |
| `temp_max_c` | 130 | 0–150 | Cluster temp °C at MAP_max (default 130 = needle red zone) |
| `scale` | 1.0 | 0.1–3.0 | Multiplier on mapped value (fine tuning) |
| `offset_c` | 0 | -50 to +50 | °C added after mapping |
| `tx_rate_hz` | 25 | 5–50 | Frame rate of Motor_09 broadcast |

## Functional scope per channel (v0.2)

| Channel | Direction | Function |
|---|---|---|
| **CAN0 (Cluster)** | TX only | Drive cluster temperature needle via Motor_09 (0x647). Configurable: MAP min/max, Temp min/max, scale, offset, formula (linear/exp/sqrt), TX rate Hz |
| **CAN1 PCM mode** | **RX only** (hardcoded, RX-locked) | Decode broadcast: live MAP, real coolant temp, Haldex demand % |
| **CAN1 Diagnostic mode** | RX + TX | OBD2 tool: UDS query MAP (DID 0x39C0), real coolant (DID 0x202C), read DTCs (service 0x19), clear DTCs (service 0x14) |

## Conditional gear logic

| Lever position | Mode | Gauge displays | Pi action |
|---|---|---|---|
| S / S1-S6 | BOOST | Mapped MAP value | TX Motor_09 with mapped byte 0 |
| M / M1-M6 | BOOST | Mapped MAP value | TX Motor_09 with mapped byte 0 |
| N | BOOST | Mapped MAP value | TX Motor_09 with mapped byte 0 |
| D / D1-D6 | TEMP | Real coolant temp | **Silent** — gateway forwards real Motor_09 |
| P | TEMP | Real coolant temp | **Silent** — gateway forwards real Motor_09 |
| R | TEMP | Real coolant temp | **Silent** — gateway forwards real Motor_09 |

Lever position is read live from **WBA_03 (0x394) byte 1 high nibble**:
- 0x10 = P, 0x20 = R, 0x30 = N, 0x40 = D, 0x50 = S, 0x60 = M

## Project layout

```
MK7BoostGauge/
├── README.md            # this file
├── INSTALL.md           # step-by-step Pi flash + setup
├── requirements.txt     # Python deps
├── config.example.json  # default settings template
├── pi_setup/            # one-shot install scripts for fresh Pi
│   ├── setup.sh
│   ├── boot_config.txt  # /boot/config.txt additions
│   ├── ap_setup.sh      # hostapd + dnsmasq for WiFi AP
│   ├── overlay_setup.sh # read-only root protection
│   └── systemd/
│       └── boostgauge.service
├── app/
│   ├── main.py          # entry point
│   ├── can_manager.py   # python-can dual-channel wrapper
│   ├── boost_logic.py   # MAP→°C mapping + gear mode switch
│   ├── vw_signals.py    # WBA_03 decode, Motor_09 build, MQB CRC
│   ├── config.py        # JSON config persistence
│   ├── webserver.py     # Flask + SocketIO routes
│   └── static/
│       ├── index.html
│       ├── style.css
│       └── app.js
├── tests/
│   └── test_boost_logic.py
└── docs/
    ├── architecture.md
    ├── can_signals.md   # imported from sister project
    └── pinout.md        # gateway Y-cable pinout reference
```

## Status

🚧 **In development** — hardware on order, firmware skeleton being written.

## License

MIT (TBC).

## See also

- [`mk7-cluster-bench-controller`](https://github.com/ALagrandeur/mk7-cluster-bench-controller) — bench reverse-engineering app
- [r00li/CarCluster](https://github.com/r00li/CarCluster) — original Golf 7 MQB cluster control reference
- [openDBC vw_mqb_2010.dbc](https://github.com/commaai/opendbc/blob/master/opendbc/dbc/vw_mqb_2010.dbc) — MQB CAN definitions
