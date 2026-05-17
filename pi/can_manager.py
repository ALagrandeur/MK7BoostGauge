"""Dual CAN HAT manager (cluster + can1).

Simplified vs old version:
  - No PCM/Diagnostic mode toggle (CAN1 unused in this version)
  - No frame log buffer
  - No OBD2/UDS logic
  - Just: open buses, listen cluster for WBA_03, TX Motor_09

Channels:
  'cluster' = CAN0 = wired to MK7 cluster bus
  'can1'    = CAN1 = wired but unused (kept for future expansion)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

try:
    import can
    HAVE_PYTHON_CAN = True
except ImportError:
    HAVE_PYTHON_CAN = False

log = logging.getLogger(__name__)

CHANNELS = ("cluster", "can1")

# Hardcoded safety blocklist - airbag IDs never transmitted, EVER.
FORBIDDEN_IDS = frozenset({0x040, 0x572, 0x585})


class CanManager:
    def __init__(self, cluster_iface: str = "can0", can1_iface: str = "can1",
                 bitrate: int = 500_000) -> None:
        self.iface = {"cluster": cluster_iface, "can1": can1_iface}
        self.bitrate = bitrate
        self.bus: dict[str, Optional["can.BusABC"]] = {ch: None for ch in CHANNELS}
        self._listeners: dict[str, list[Callable]] = {ch: [] for ch in CHANNELS}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        # Counters for debug
        self.tx_count = 0
        self.rx_cluster_count = 0
        self.rx_can1_count = 0
        self.blocked_airbag_count = 0

    def open(self) -> None:
        if not HAVE_PYTHON_CAN:
            log.warning("python-can not installed - stub mode (no real CAN)")
            return
        for ch in CHANNELS:
            try:
                self.bus[ch] = can.interface.Bus(
                    channel=self.iface[ch], interface="socketcan", bitrate=self.bitrate
                )
                log.info("Opened %s CAN on %s", ch, self.iface[ch])
            except Exception as e:
                log.error("Failed to open %s CAN %s: %s", ch, self.iface[ch], e)
                self.bus[ch] = None

        for ch in CHANNELS:
            if self.bus[ch] is not None:
                t = threading.Thread(
                    target=self._rx_loop, args=(ch, self.bus[ch]),
                    daemon=True, name=f"CanRx-{ch}",
                )
                t.start()
                self._threads.append(t)

    def close(self) -> None:
        self._stop.set()
        for ch in CHANNELS:
            if self.bus[ch] is not None:
                try:
                    self.bus[ch].shutdown()
                except Exception:
                    pass

    def send(self, channel: str, can_id: int, data: bytes) -> bool:
        # SAFETY: airbag IDs never leave the Pi
        if can_id in FORBIDDEN_IDS:
            self.blocked_airbag_count += 1
            log.warning("BLOCKED forbidden TX id 0x%X on %s", can_id, channel)
            return False

        bus = self.bus.get(channel)
        if bus is None or not HAVE_PYTHON_CAN:
            return False
        try:
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
            bus.send(msg, timeout=0.05)
            self.tx_count += 1
            return True
        except Exception as e:
            log.error("TX fail on %s id 0x%X: %s", channel, can_id, e)
            return False

    def add_listener(self, channel: str, cb: Callable) -> None:
        if channel in self._listeners:
            self._listeners[channel].append(cb)

    def _rx_loop(self, channel: str, bus: "can.BusABC") -> None:
        log.info("RX thread started on %s", channel)
        while not self._stop.is_set():
            try:
                msg = bus.recv(timeout=0.2)
            except Exception as e:
                log.warning("recv error on %s: %s", channel, e)
                time.sleep(0.1)
                continue
            if msg is None:
                continue
            if channel == "cluster":
                self.rx_cluster_count += 1
            else:
                self.rx_can1_count += 1
            data = bytes(msg.data) if msg.data is not None else b""
            for cb in self._listeners[channel]:
                try:
                    cb(msg.arbitration_id, data, msg.timestamp)
                except Exception:
                    log.exception("Listener exception on %s", channel)
