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
from collections import deque
from typing import Callable, Optional

try:
    import can  # python-can
    HAVE_PYTHON_CAN = True
except ImportError:
    HAVE_PYTHON_CAN = False

log = logging.getLogger(__name__)

CHANNELS = ("cluster", "can1")

# Frame log: ring buffer per channel of recent CAN frames (for web UI Frame Log card)
FRAME_LOG_SIZE = 100   # last N frames per channel


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
        can1_mode: str = "pcm",
    ) -> None:
        self.iface = {"cluster": cluster_iface, "can1": can1_iface}
        self.bitrate = bitrate
        self.forbidden_ids = forbidden_ids or set()
        # SAFETY: TX on can1 is blocked when ANY of these is true:
        #   - mode == "pcm"          (hardcoded — PCM is always RX-only by design)
        #   - listen_only == True    (user-armed safety in Diagnostic mode)
        self._can1_listen_only = bool(can1_listen_only)
        self._can1_mode = can1_mode

        self.bus: dict[str, Optional["can.BusABC"]] = {"cluster": None, "can1": None}
        self._listeners: dict[str, list[Callable]] = {ch: [] for ch in CHANNELS}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        # Counters for blocked TX events (visible in UI for trust/debug)
        self.blocked_tx_count = 0
        self.blocked_listen_only_count = 0
        self.blocked_forbidden_count = 0
        self.blocked_pcm_mode_count = 0
        # Frame Log: ring buffer of recent frames per channel (for UI display)
        self.frame_log: dict[str, deque] = {
            ch: deque(maxlen=FRAME_LOG_SIZE) for ch in CHANNELS
        }
        self._frame_log_lock = threading.Lock()
        self._frame_log_paused = False
        # Aggregated by ID: {channel: {can_id: {count, last_data, last_ts}}}
        self.frame_agg: dict[str, dict[int, dict]] = {ch: {} for ch in CHANNELS}

    # ------------------------------------------------------------------ frame log API

    def get_frame_log(self, channel: str, since_ts: float = 0.0) -> list[dict]:
        """Return the recent frames for a channel, optionally only those after since_ts.

        Returns list of {"id": int, "data": "AABB..", "ts": float, "dir": "rx"/"tx"}.
        """
        if channel not in CHANNELS:
            return []
        with self._frame_log_lock:
            return [f for f in self.frame_log[channel] if f["ts"] > since_ts]

    def get_frame_aggregate(self, channel: str) -> list[dict]:
        """Return per-ID summary: count, last_data, age_s."""
        if channel not in CHANNELS:
            return []
        now = time.time()
        with self._frame_log_lock:
            return [
                {
                    "id": cid,
                    "id_hex": f"0x{cid:03X}",
                    "count": info["count"],
                    "last_data": info["last_data"],
                    "age_s": round(now - info["last_ts"], 2),
                    "dir": info.get("dir", "rx"),
                }
                for cid, info in sorted(self.frame_agg[channel].items())
            ]

    def set_frame_log_paused(self, paused: bool) -> None:
        with self._frame_log_lock:
            self._frame_log_paused = bool(paused)

    def clear_frame_log(self) -> None:
        with self._frame_log_lock:
            for ch in CHANNELS:
                self.frame_log[ch].clear()
                self.frame_agg[ch].clear()

    def _record_frame(self, channel: str, can_id: int, data: bytes, direction: str = "rx") -> None:
        if self._frame_log_paused:
            return
        ts = time.time()
        data_hex = data.hex(" ").upper() if data else ""
        with self._frame_log_lock:
            self.frame_log[channel].append({
                "id": can_id,
                "id_hex": f"0x{can_id:03X}",
                "data": data_hex,
                "ts": ts,
                "dir": direction,
            })
            agg = self.frame_agg[channel].setdefault(can_id, {"count": 0})
            agg["count"] += 1
            agg["last_data"] = data_hex
            agg["last_ts"] = ts
            agg["dir"] = direction

    def set_can1_listen_only(self, on: bool) -> None:
        if on != self._can1_listen_only:
            log.warning("CAN1 listen-only changed: %s -> %s", self._can1_listen_only, on)
        self._can1_listen_only = bool(on)

    def set_can1_mode(self, mode: str) -> None:
        if mode != self._can1_mode:
            log.warning("CAN1 mode changed: %s -> %s", self._can1_mode, mode)
        self._can1_mode = mode

    @property
    def can1_listen_only(self) -> bool:
        return self._can1_listen_only

    @property
    def can1_mode(self) -> str:
        return self._can1_mode

    def is_can1_tx_blocked(self) -> tuple[bool, str]:
        """Single source of truth for whether CAN1 TX is allowed.

        Returns (blocked, reason) so caller / counters / UI can explain why.
        Order: PCM mode (hardcoded by design) wins over user listen-only switch.
        """
        if self._can1_mode == "pcm":
            return True, "pcm_mode"
        if self._can1_listen_only:
            return True, "listen_only"
        return False, ""

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

        # SAFETY GATE 2: CAN1 TX blocked (PCM mode hardcoded OR user listen-only)
        if channel == "can1":
            blocked, reason = self.is_can1_tx_blocked()
            if blocked:
                self.blocked_tx_count += 1
                if reason == "pcm_mode":
                    self.blocked_pcm_mode_count += 1
                    log.info("BLOCKED TX id 0x%X on can1 (PCM mode = RX-only by design)", can_id)
                else:
                    self.blocked_listen_only_count += 1
                    log.info("BLOCKED TX id 0x%X on can1 (listen-only armed)", can_id)
                return False

        bus = self.bus[channel]
        if bus is None or not HAVE_PYTHON_CAN:
            return False

        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=extended)
        try:
            bus.send(msg, timeout=0.05)
            self._record_frame(channel, can_id, data, direction="tx")
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
            self._record_frame(channel, msg.arbitration_id, data, direction="rx")
            for cb in self._listeners[channel]:
                try:
                    cb(msg.arbitration_id, data, msg.timestamp)
                except Exception as e:
                    log.exception("Listener exception on %s: %s", channel, e)
        log.info("RX thread stopped on %s", channel)
