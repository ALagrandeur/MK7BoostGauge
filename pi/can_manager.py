"""Dual CAN HAT manager (cluster + can1).

Simplified vs old version:
  - No PCM/Diagnostic mode toggle (CAN1 unused in this version)
  - No frame log buffer
  - No OBD2/UDS logic (handled by daemon)
  - Just: open buses, listen cluster for WBA_03, TX Motor_09

v3.x: throttled error logging + per-channel TX success/failure counters.
  Designed for the vehicle case where CAN1 may have no ACK (ENOBUFS) and
  was previously spamming logs.

Channels:
  'cluster' = CAN0 = wired to MK7 cluster bus
  'can1'    = CAN1 = OBD-II / PCM (MAP source)
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

# Throttle TX error logging: max one log line per channel per N seconds.
TX_ERROR_LOG_INTERVAL_S = 10.0


class CanManager:
    def __init__(self, cluster_iface: str = "can0", can1_iface: str = "can1",
                 bitrate: int = 500_000) -> None:
        self.iface = {"cluster": cluster_iface, "can1": can1_iface}
        self.bitrate = bitrate
        self.bus: dict[str, Optional["can.BusABC"]] = {ch: None for ch in CHANNELS}
        self._listeners: dict[str, list[Callable]] = {ch: [] for ch in CHANNELS}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        # Counters for debug (aggregate, kept for back-compat)
        self.tx_count = 0
        self.rx_cluster_count = 0
        self.rx_can1_count = 0
        self.blocked_airbag_count = 0

        # Per-channel counters
        self.tx_ok: dict[str, int] = {ch: 0 for ch in CHANNELS}
        self.tx_fail: dict[str, int] = {ch: 0 for ch in CHANNELS}
        self.last_tx_ok_ts: dict[str, float] = {ch: 0.0 for ch in CHANNELS}
        self.last_tx_fail_ts: dict[str, float] = {ch: 0.0 for ch in CHANNELS}
        self.last_tx_error: dict[str, str] = {ch: "" for ch in CHANNELS}

        # Log throttling state per channel
        self._last_err_log_ts: dict[str, float] = {ch: 0.0 for ch in CHANNELS}
        self._suppressed_since_last_log: dict[str, int] = {ch: 0 for ch in CHANNELS}

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

    # -------------------------------------------------------------------- send
    def send(self, channel: str, can_id: int, data: bytes) -> bool:
        # SAFETY: airbag IDs never leave the Pi
        if can_id in FORBIDDEN_IDS:
            self.blocked_airbag_count += 1
            log.warning("BLOCKED forbidden TX id 0x%X on %s", can_id, channel)
            return False

        bus = self.bus.get(channel)
        if bus is None or not HAVE_PYTHON_CAN:
            self.tx_fail[channel] = self.tx_fail.get(channel, 0) + 1
            return False

        try:
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
            bus.send(msg, timeout=0.05)
            self.tx_count += 1
            self.tx_ok[channel] = self.tx_ok.get(channel, 0) + 1
            self.last_tx_ok_ts[channel] = time.time()
            return True
        except Exception as e:
            self.tx_fail[channel] = self.tx_fail.get(channel, 0) + 1
            now = time.time()
            self.last_tx_fail_ts[channel] = now
            err_str = f"{type(e).__name__}: {e}"
            self.last_tx_error[channel] = err_str
            # Throttle the log: at most once per TX_ERROR_LOG_INTERVAL_S per channel
            last_log = self._last_err_log_ts.get(channel, 0.0)
            if now - last_log >= TX_ERROR_LOG_INTERVAL_S:
                suppressed = self._suppressed_since_last_log.get(channel, 0)
                if suppressed > 0:
                    log.error("TX fail on %s id 0x%X: %s (suppressed %d similar errors in last %.0fs)",
                              channel, can_id, err_str, suppressed, TX_ERROR_LOG_INTERVAL_S)
                else:
                    log.error("TX fail on %s id 0x%X: %s", channel, can_id, err_str)
                self._last_err_log_ts[channel] = now
                self._suppressed_since_last_log[channel] = 0
            else:
                self._suppressed_since_last_log[channel] = (
                    self._suppressed_since_last_log.get(channel, 0) + 1
                )
            return False

    def add_listener(self, channel: str, cb: Callable) -> None:
        if channel in self._listeners:
            self._listeners[channel].append(cb)

    # ---------------------------------------------------------------- bus diag
    def bus_health(self, channel: str) -> dict:
        """Compact health snapshot for /status endpoint."""
        ok = self.tx_ok.get(channel, 0)
        fail = self.tx_fail.get(channel, 0)
        total = ok + fail
        ratio = (ok / total) if total > 0 else None
        now = time.time()
        last_ok = self.last_tx_ok_ts.get(channel, 0.0)
        last_fail = self.last_tx_fail_ts.get(channel, 0.0)
        return {
            "tx_ok": ok,
            "tx_fail": fail,
            "tx_ok_ratio": round(ratio, 3) if ratio is not None else None,
            "last_tx_ok_age_s": round(now - last_ok, 1) if last_ok else None,
            "last_tx_fail_age_s": round(now - last_fail, 1) if last_fail else None,
            "last_tx_error": self.last_tx_error.get(channel, ""),
        }

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
