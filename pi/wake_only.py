#!/usr/bin/env python3
"""Standalone wake-only loop for MK7 cluster.

Sends Klemmen_Status_01 (0x3C0) at 10 Hz with proper MQB CRC + rolling counter.
Required because cansend cannot compute the CRC (which changes per frame).

Use this in one SSH terminal while you spam RPM/coolant via cansend in others.

USAGE:
    sudo python3 wake_only.py            # default can0
    sudo python3 wake_only.py --iface can1
    sudo python3 wake_only.py --hz 5     # slower if you want
"""
import argparse
import sys
import time

try:
    import can
except ImportError:
    print("ERROR: sudo apt -y install python3-can")
    sys.exit(1)

# CRC8H2F table (port openpilot/opendbc)
T = [0] * 256
for i in range(256):
    c = i
    for _ in range(8):
        c = ((c << 1) ^ 0x2F) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    T[i] = c

# Klemmen_Status_01 constant = 0xC3 for all 16 counters
KS01_CONST = 0xC3

ap = argparse.ArgumentParser()
ap.add_argument("--iface", default="can0")
ap.add_argument("--hz", type=float, default=10.0)
args = ap.parse_args()

bus = can.interface.Bus(channel=args.iface, interface="socketcan", bitrate=500000)
period = 1.0 / args.hz
ctr = 0
n_ok = 0
n_fail = 0
t0 = time.time()

print(f"Sending Klemmen_Status_01 (0x3C0) on {args.iface} at {args.hz} Hz")
print("Payload: byte0=CRC byte1=counter byte2=0x03 (Kl.15+Kl.S) byte3=0x00")
print("Ctrl+C to stop.")
print()

try:
    while True:
        # Build payload
        p = bytearray([0x00, ctr & 0x0F, 0x03, 0x00])
        # Compute MQB CRC
        crc = 0xFF
        for b in p[1:]:
            crc ^= b
            crc = T[crc]
        crc ^= KS01_CONST
        crc = T[crc]
        p[0] = crc ^ 0xFF
        # Send
        try:
            bus.send(can.Message(arbitration_id=0x3C0, data=bytes(p),
                                  is_extended_id=False), timeout=0.05)
            n_ok += 1
        except Exception:
            n_fail += 1
        # Rotate counter
        ctr = (ctr + 1) & 0x0F
        time.sleep(period)
        # Stats every 2s
        if time.time() - t0 >= 2.0:
            print(f"  [/2s] ok={n_ok}  fail={n_fail}")
            n_ok = n_fail = 0
            t0 = time.time()
except KeyboardInterrupt:
    print("\nStopped.")
    bus.shutdown()
