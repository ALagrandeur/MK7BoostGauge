"""Dual-channel CAN manager — wraps python-can SocketCAN with safety blocklist."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

try:
    import can  # python-can
    HAVE_PYTHON_CAN = True
except ImportError:
    HAVE_PYTHON_CAN = False

log = logging.getLogger(__name__)


class CanManager:
    """
    Manages two SocketCAN interfaces (powertrain + cluster), with:
    - Hard safety blocklist on TX (forbidden_ids never leave the device)
    - Per-channel listener registration
    - Stub mode if python-can not installed (dev on Windows)
    """

    def __init__(
        self,
        powertrain_iface: str,
        cluster_iface: str,
        bitrate: int = 500_000,
        forbidden_ids: Optional[set[int]] = None,
    ) -> None:
        self.powertrain_iface = powertrain_iface
        self.cluster_iface = cluster_iface
        self.bitrate = bitrate
        self.forbidden_ids = forbidden_ids or set()

        self.bus_powertrain: Optional["can.BusABC"] = None
        self.bus_cluster: Optional["can.BusABC"] = None

        self._listeners: dict[str, list[Callable]] = {"powertrain": [], "cluster": []}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ open

    def open(self) -> None:
        if not HAVE_PYTHON_CAN:
            log.warning("python-can not installed — CanManager running in STUB mode")
            return
        try:
            self.bus_powertrain = can.interface.Bus(
                channel=self.powertrain_iface, interface="socketcan", bitrate=self.bitrate
            )
            log.info("Opened Powertrain CAN on %s", self.powertrain_iface)
        except Exception as e:
            log.error("Failed to open powertrain CAN %s: %s", self.powertrain_iface, e)
            self.bus_powertrain = None
        try:
            self.bus_cluster = can.interface.Bus(
                channel=self.cluster_iface, interface="socketcan", bitrate=self.bitrate
            )
            log.info("Opened Cluster CAN on %s", self.cluster_iface)
        except Exception as e:
            log.error("Failed to open cluster CAN %s: %s", self.cluster_iface, e)
            self.bus_cluster = None

        # Spawn RX threads
        if self.bus_powertrain:
            t = threading.Thread(
                target=self._rx_loop, args=("powertrain", self.bus_powertrain), daemon=True
            )
            t.start()
            self._threads.append(t)
        if self.bus_cluster:
            t = threading.Thread(
                target=self._rx_loop, args=("cluster", self.bus_cluster), daemon=True
            )
            t.start()
            self._threads.append(t)

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        self._stop.set()
        for bus in (self.bus_powertrain, self.bus_cluster):
            if bus:
                try:
                    bus.shutdown()
                except Exception:
                    pass

    # ------------------------------------------------------------------ TX

    def send(self, channel: str, can_id: int, data: bytes, extended: bool = False) -> bool:
        """Send a frame on a named channel ('powertrain' or 'cluster').

        Returns True on success, False if blocked by safety or send failed.
        """
        if can_id in self.forbidden_ids:
            log.warning("BLOCKED forbidden TX id 0x%X on %s", can_id, channel)
            return False

        bus = self.bus_powertrain if channel == "powertrain" else self.bus_cluster
        if bus is None:
            return False

        if not HAVE_PYTHON_CAN:
            return False

        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=extended)
        try:
            bus.send(msg, timeout=0.05)
            return True
        except Exception as e:
            log.error("TX fail on %s id 0x%X: %s", channel, can_id, e)
            return False

    # ------------------------------------------------------------------ RX

    def add_listener(self, channel: str, callback: Callable) -> None:
        """Register a callback called for every received frame on a channel.

        Callback signature: (can_id: int, data: bytes, timestamp: float) -> None
        """
        self._listeners[channel].append(callback)

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
            data = bytes(msg.data) if msg.data is not None else b""
            for cb in self._listeners[channel]:
                try:
                    cb(msg.arbitration_id, data, msg.timestamp)
                except Exception as e:
                    log.exception("Listener exception on %s: %s", channel, e)
        log.info("RX thread stopped on %s", channel)
