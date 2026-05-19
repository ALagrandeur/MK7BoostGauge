#!/usr/bin/env python3
"""MK7 cluster bench test — standalone, no project deps beyond python-can.

Sends the FULL set of frames the cluster needs to:
  - Wake (Klemmen_Status_01 with MQB CRC + counter)
  - Accept engine context (Motor_Code_01 + system bundle: ESP_05/10/20, TSK_07, LH_EPS_01)
  - Display 1500 RPM tachometer (Motor_04)
  - Display 130 C coolant (Motor_09)

Based on the sister project (MK7 cluster bench controller) — same CAN IDs and same
MQB CRC algorithm (port of openpilot/opendbc mqbcan.py).

REQUIREMENTS (on the Pi):
  - python-can installed:  sudo apt -y install python3-can can-utils
  - can0 UP at 500 kbps:   sudo ip link set can0 up type can bitrate 500000 restart-ms 100
  - HAT wired to cluster:  CAN-H/CAN-L correct, termination present, cluster Kl.30+Kl.15 +12V

USAGE:
  sudo python3 bench_test_cluster.py             # full bench (wake + ctx + 1500 RPM + 130 C)
  sudo python3 bench_test_cluster.py --rpm 3000  # custom RPM
  sudo python3 bench_test_cluster.py --temp 50   # custom temp C
  sudo python3 bench_test_cluster.py --iface can1   # use can1 instead of can0

Ctrl+C to stop. Auto-prints stats every 2 sec. Auto-recovers can0 from bus-off.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time

try:
    import can
except ImportError:
    print("ERROR: python-can not installed. Run:")
    print("  sudo apt -y install python3-can")
    sys.exit(1)


# =====================================================================
# VW MQB CRC8H2F (port of openpilot/opendbc mqbcan.py)
# =====================================================================
def _gen_crc8h2f_table() -> list[int]:
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ 0x2F) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        t.append(c)
    return t

CRC8H2F = _gen_crc8h2f_table()

# Per-CAN-ID constants used by the MQB checksum.
# Subset relevant for bench test. Source: sister project webui/vw_mqb.py
MQB_CONST: dict[int, list[int]] = {
    0x040: [0x40] * 16,  # Airbag_01 (BENCH ONLY - never on real vehicle)
    0x3C0: [0xC3] * 16,  # Klemmen_Status_01 (wake)
    0x641: [0x47] * 16,  # Motor_Code_01    (engine code heartbeat)
    0x394: [0x47, 0x94, 0x92, 0x6A, 0x67, 0xB5, 0x0D, 0x38,
            0xE3, 0x8A, 0x5D, 0xB4, 0x54, 0xAB, 0xAE, 0x27],   # WBA_03 (gear)
    0x116: [0xAC] * 16,  # ESP_10
    0x31B: [0x67, 0x8A, 0xAE, 0x22, 0x4D, 0xD0, 0x51, 0x80,
            0x5C, 0xB9, 0xCE, 0x1E, 0xDF, 0x02, 0x2D, 0xD4],   # ESP_24
    0x31E: [0x78, 0x68, 0x3A, 0x31, 0x16, 0x08, 0x4F, 0xDE,
            0xF7, 0x35, 0x19, 0xE6, 0x28, 0x2F, 0x59, 0x82],   # TSK_07
    0x32A: [0x29] * 16,  # LH_EPS_01
    0x101: [0xAA] * 16,  # ESP_02
}

def mqb_crc(addr: int, data: bytes) -> int:
    """Compute the MQB custom checksum byte (goes at byte 0)."""
    crc = 0xFF
    for i in range(1, len(data)):
        crc ^= data[i]
        crc = CRC8H2F[crc]
    counter = data[1] & 0x0F
    if addr in MQB_CONST:
        crc ^= MQB_CONST[addr][counter]
        crc = CRC8H2F[crc]
    return crc ^ 0xFF

def apply_crc(addr: int, payload: bytes, counter: int) -> bytes:
    """Write counter into byte 1 low nibble, compute checksum into byte 0."""
    out = bytearray(payload)
    out[1] = (out[1] & 0xF0) | (counter & 0x0F)
    out[0] = 0
    out[0] = mqb_crc(addr, bytes(out))
    return bytes(out)


# =====================================================================
# Trame templates (from sister project webui/server.py + config.json)
# =====================================================================

# Wake — Klemmen_Status_01 (4 bytes). byte 2 = 0x03 (Kl.15 + Kl.S).
WAKE_ID       = 0x3C0
WAKE_TEMPLATE = bytes([0x00, 0x00, 0x03, 0x00])
WAKE_RATE_HZ  = 10

# Motor_Code_01 (0x641) — engine code heartbeat, REQUIRED alongside coolant
ENGINE_CODE_ID       = 0x641
ENGINE_CODE_TEMPLATE = bytes([0x00, 0x10, 0x00, 0xE8, 0x03, 0x00, 0x00, 0x00])
ENGINE_CODE_RATE_HZ  = 20

# Motor_04 (0x107) — RPM tachometer. NO CRC, NO counter.
# bytes 3-4 LE = MO_Anzeigedrehz: byte3 = (rpm/3) & 0xFF, byte4 = (rpm/3) >> 8
RPM_ID       = 0x107
RPM_RATE_HZ  = 20

# Motor_09 (0x647) — coolant override. NO CRC, NO counter.
# byte 0 = coolant byte (0x80=50C, 0xED=130C), bytes 1-7 = magic from r00li
COOLANT_ID            = 0x647
COOLANT_TAIL          = bytes([0xFD, 0xFF, 0x7F, 0x00, 0x00, 0x00, 0xC1])
COOLANT_RATE_HZ       = 20

# System Context Bundle — "alive ECUs" so cluster doesn't invalidate other signals
# Source: sister project UI shows bundle = Airbag_01 + ESP_05/10/20 + TSK_07 + LH_EPS_01
#
# IMPORTANT: Airbag_01 (0x040) included to match sister UI exactly.
# This is BENCH ONLY — never run with --bundle on a real vehicle (it would spoof
# the real airbag controller heartbeat, which is genuinely dangerous).
SYSTEM_CONTEXT = [
    {"id": 0x040, "name": "Airbag_01", "payload": bytes(8),                                        "crc": True},
    {"id": 0x106, "name": "ESP_05",    "payload": bytes(8),                                        "crc": False},
    {"id": 0x116, "name": "ESP_10",    "payload": bytes(8),                                        "crc": True},
    {"id": 0x65D, "name": "ESP_20",    "payload": bytes([0x00, 0x30, 0x2B, 0x12, 0x00, 0x00, 0xB4, 0x79]), "crc": False},
    {"id": 0x31E, "name": "TSK_07",    "payload": bytes([0xCA, 0xEF, 0x3F, 0x00, 0x00, 0x00, 0x00, 0x40]), "crc": True},
    {"id": 0x32A, "name": "LH_EPS_01", "payload": bytes([0x4B, 0x08, 0x00, 0x00, 0x02, 0x02, 0x00, 0x00]), "crc": True},
]
SYSTEM_CTX_RATE_HZ = 10

# Formula validated empirically: temp_C = byte * 0.7339 - 43.94
def temp_c_to_byte(t: float) -> int:
    raw = (t + 43.94) / 0.7339
    return max(0, min(255, int(round(raw))))


# =====================================================================
# Bus helpers
# =====================================================================

def try_recover_bus(iface: str) -> None:
    """Try to bring iface DOWN then UP — recovers from bus-off."""
    print(f"  [recover] bringing {iface} down/up at 500 kbps...")
    try:
        subprocess.run(["sudo", "ip", "link", "set", iface, "down"],
                       check=False, capture_output=True, timeout=5)
        time.sleep(0.2)
        subprocess.run(
            ["sudo", "ip", "link", "set", iface, "up", "type", "can",
             "bitrate", "500000", "restart-ms", "100"],
            check=True, capture_output=True, timeout=5,
        )
        print(f"  [recover] {iface} re-armed.")
    except Exception as e:
        print(f"  [recover] FAILED: {e}")


# =====================================================================
# Main
# =====================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="MK7 cluster bench test (standalone, no project deps).")
    ap.add_argument("--iface", default="can1", help="CAN interface (default: can1 - can0 IRQ broken on most HATs)")
    ap.add_argument("--rpm", type=int, default=4000, help="RPM to display (default: 4000)")
    ap.add_argument("--temp", type=float, default=130.0, help="Coolant temp C to display (default: 130)")
    ap.add_argument("--no-wake", action="store_true", help="Skip wake loop (if your gateway already broadcasts it)")
    ap.add_argument("--no-context", action="store_true", help="Skip system context bundle")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("WARNING: not running as root. ip link recovery may fail. Recommend: sudo python3 ...")
        print()

    rpm = max(0, min(8000, args.rpm))
    temp_c = max(50.0, min(130.0, args.temp))
    coolant_byte = temp_c_to_byte(temp_c)
    coolant_payload = bytes([coolant_byte]) + COOLANT_TAIL

    # RPM: bytes 3-4 LE = (rpm/3)
    rpm_raw = rpm // 3
    rpm_payload = bytearray(8)
    rpm_payload[3] = rpm_raw & 0xFF
    rpm_payload[4] = (rpm_raw >> 8) & 0xFF
    rpm_payload = bytes(rpm_payload)

    print("=" * 70)
    print("MK7 cluster bench test")
    print("=" * 70)
    print(f"  Interface     : {args.iface}")
    print(f"  Target RPM    : {rpm}     (Motor_04 byte 3-4 LE = {rpm_payload[3]:#04x} {rpm_payload[4]:#04x})")
    print(f"  Target temp   : {temp_c} C  (Motor_09 byte 0 = {coolant_byte:#04x})")
    print(f"  Wake loop     : {'OFF' if args.no_wake else 'ON'}")
    print(f"  System ctx    : {'OFF' if args.no_context else 'ON'}")
    print()
    print("Trame mix (per second):")
    print(f"  0x3C0 Wake               x{WAKE_RATE_HZ}   (+ MQB CRC)")
    print(f"  0x641 Motor_Code_01      x{ENGINE_CODE_RATE_HZ}   (+ MQB CRC)")
    print(f"  0x107 Motor_04 (RPM)     x{RPM_RATE_HZ}")
    print(f"  0x647 Motor_09 (coolant) x{COOLANT_RATE_HZ}")
    print(f"  System ctx (5 IDs)       x{SYSTEM_CTX_RATE_HZ} each")
    print()

    try:
        bus = can.interface.Bus(channel=args.iface, interface="socketcan", bitrate=500000)
    except Exception as e:
        print(f"ERROR: cannot open {args.iface}: {e}")
        print(f"  Check: ip -br link show {args.iface}")
        print(f"  Bring up manually: sudo ip link set {args.iface} up type can bitrate 500000 restart-ms 100")
        return 1

    stop = threading.Event()
    stats: dict[str, int] = {"wake": 0, "engine_code": 0, "rpm": 0, "coolant": 0,
                              "ctx": 0, "tx_fail": 0, "recover": 0}
    stats_lock = threading.Lock()

    def safe_send(msg: can.Message, name: str) -> None:
        try:
            bus.send(msg, timeout=0.05)
            with stats_lock:
                stats[name] += 1
        except can.CanOperationError as e:
            with stats_lock:
                stats["tx_fail"] += 1
            # If we accumulate many failures, try recovery
            if stats["tx_fail"] % 50 == 1:
                print(f"  [tx_fail] {name}: {e}")
                with stats_lock:
                    stats["recover"] += 1
                try_recover_bus(args.iface)
        except Exception as e:
            with stats_lock:
                stats["tx_fail"] += 1

    def tx_loop_crc(addr: int, template: bytes, rate_hz: float, name: str):
        period = 1.0 / rate_hz
        counter = 0
        while not stop.is_set():
            payload = apply_crc(addr, template, counter)
            msg = can.Message(arbitration_id=addr, data=payload, is_extended_id=False)
            safe_send(msg, name)
            counter = (counter + 1) & 0x0F
            stop.wait(period)

    def tx_loop_static(addr: int, payload: bytes, rate_hz: float, name: str):
        period = 1.0 / rate_hz
        msg = can.Message(arbitration_id=addr, data=payload, is_extended_id=False)
        while not stop.is_set():
            safe_send(msg, name)
            stop.wait(period)

    def system_ctx_loop():
        period = 1.0 / SYSTEM_CTX_RATE_HZ
        counters: dict[int, int] = {}
        while not stop.is_set():
            for entry in SYSTEM_CONTEXT:
                cid = entry["id"]
                p = entry["payload"]
                if entry["crc"]:
                    c = counters.get(cid, 0)
                    p = apply_crc(cid, p, c)
                    counters[cid] = (c + 1) & 0x0F
                msg = can.Message(arbitration_id=cid, data=p, is_extended_id=False)
                safe_send(msg, "ctx")
            stop.wait(period)

    threads: list[threading.Thread] = []

    if not args.no_wake:
        threads.append(threading.Thread(target=tx_loop_crc,
            args=(WAKE_ID, WAKE_TEMPLATE, WAKE_RATE_HZ, "wake"), daemon=True))

    threads.append(threading.Thread(target=tx_loop_crc,
        args=(ENGINE_CODE_ID, ENGINE_CODE_TEMPLATE, ENGINE_CODE_RATE_HZ, "engine_code"), daemon=True))

    threads.append(threading.Thread(target=tx_loop_static,
        args=(RPM_ID, rpm_payload, RPM_RATE_HZ, "rpm"), daemon=True))

    threads.append(threading.Thread(target=tx_loop_static,
        args=(COOLANT_ID, coolant_payload, COOLANT_RATE_HZ, "coolant"), daemon=True))

    if not args.no_context:
        threads.append(threading.Thread(target=system_ctx_loop, daemon=True))

    for t in threads:
        t.start()

    print("*** Cluster should react in 1-3 sec: RPM needle to 1500, coolant to 130 C ***")
    print("Ctrl+C to stop.")
    print()

    try:
        last = dict(stats)
        while True:
            time.sleep(2.0)
            with stats_lock:
                cur = dict(stats)
            d = {k: cur[k] - last.get(k, 0) for k in cur}
            last = cur
            print(f"  [/2s] wake={d['wake']:3d} engine={d['engine_code']:3d} "
                  f"rpm={d['rpm']:3d} coolant={d['coolant']:3d} ctx={d['ctx']:3d} "
                  f"tx_fail={d['tx_fail']:3d} recover={d['recover']}")
            # If everything failing, hint at root cause
            if d["tx_fail"] > 0 and (d["wake"] + d["engine_code"] + d["rpm"] + d["coolant"]) == 0:
                print("  [hint] All TX failing. Likely: bus muet (cluster pas alimente, "
                      "cablage CAN-H/L inverse, ou pas de terminaison 120 ohm).")
    except KeyboardInterrupt:
        stop.set()
        print()
        print("Stopping...")
        for t in threads:
            t.join(timeout=0.5)
        try:
            bus.shutdown()
        except Exception:
            pass
        print("Done.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
