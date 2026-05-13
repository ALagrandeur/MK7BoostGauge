"""Dual-channel CAN manager — wraps python-can SocketCAN with safety blocklist.

Channels:
  'cluster' = always wired to Cluster CAN bus (CAN0 by physical convention)
  'can1'    = wired to either PCM (Powertrain) or Diagnostic (OBD-II) bus,
              depending on user toggle in config.
"""
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

CHANNELS = ("cluster", "can1")


class CanManager:
    """
    Manages two SocketCAN interfaces, with:
    - Hard safety blocklist on TX (forbidden_ids never leave the device)
    - Per-channel listener registration
    - Stub mode if python-can not installed (dev on Windows)
    """

    def __init__(
        self,
        cluster_iface: str,
        can1_iface: str,
        bitrate: int = 500_000,
        forbidden_ids: Optional[set[int]] = None,
        can1_listen_only: bool = False,
    ) -> None:
        self.iface = {"cluster": cluster_iface, "can1": can1_iface}
        self.bitrate = bitrate
        self.forbidden_ids = forbidden_ids or set()
        # SAFETY: when True, every TX on can1 is hard-blocked at this layer
        # (independent of any logic in boost_logic / webserver above).
        self._can1_listen_only = bool(can1_listen_only)

        self.bus: dict[str, Optional["can.BusABC"]] = {"cluster": None, "can1": None}
        self._listeners: dict[str, list[Callable]] = {ch: [] for ch in CHANNELS}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        # Counter for blocked TX events (visible in UI for trust/debug)
        self.blocked_tx_count = 0
        self.blocked_listen_only_count = 0
        self.blocked_forbidden_count = 0

    def set_can1_listen_only(self, on: bool) -> None:
        """Hot-update the listen-only flag (called by webserver on toggle change)."""
        if on != self._can1_listen_only:
            log.warning("CAN1 listen-only changed: %s -> %s", self._can1_listen_only, on)
        self._can1_listen_only = bool(on)

    @property
    def can1_listen_only(self) -> bool:
        return self._can1_listen_only

    # ------------------------------------------------------------------ open

    def open(self) -> None:
        if not HAVE_PYTHON_CAN:
            log.warning("python-can not installed — CanManager running in STUB mode")
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
                    target=self._rx_loop, args=(ch, self.bus[ch]), daemon=True,
                    name=f"CanRx-{ch}",
                )
                t.start()
                self._threads.append(t)

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        self._stop.set()
        for ch in CHANNELS:
            if self.bus[ch] is not None:
                try:
                    self.bus[ch].shutdown()
                except Exception:
                    pass

    # ------------------------------------------------------------------ TX

    def send(self, channel: str, can_id: int, data: bytes, extended: bool = False) -> bool:
        """Send a frame on a named channel ('cluster' or 'can1').

        Returns True on success, False if blocked by safety or send failed.
        """
        if channel not in CHANNELS:
            log.error("Unknown channel '%s'", channel)
            return False

        # SAFETY GATE 1: airbag / forbidden IDs — hard-block on ANY channel
        if can_id in self.forbidden_ids:
            self.blocked_tx_count += 1
            self.blocked_forbidden_count += 1
            log.warning("BLOCKED forbidden TX id 0x%X on %s (FORBIDDEN_IDS)", can_id, channel)
            return False

        # SAFETY GATE 2: CAN1 listen-only — hard-block ALL TX on can1 when armed
        if channel == "can1" and self._can1_listen_only:
            self.blocked_tx_count += 1
            self.blocked_listen_only_count += 1
            log.info("BLOCKED TX id 0x%X on can1 (listen-only mode armed)", can_id)
            return False

        bus = self.bus[channel]
        if bus is None or not HAVE_PYTHON_CAN:
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
        if channel not in CHANNELS:
            log.error("Unknown channel '%s' for listener", channel)
            return
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
