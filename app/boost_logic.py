"""Boost gauge state machine — gear-conditional Motor_09 broadcast."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .can_manager import CanManager
from .config import Config
from .vw_signals import (
    BOOST_LEVERS, MOTOR_09_ID, build_motor_09, decode_lever, is_boost_mode,
    map_mbar_to_motor09_byte, motor09_byte_to_temp_c,
)

log = logging.getLogger(__name__)

# CAN IDs we care about
WBA_03_ID = 0x394   # gear lever from cluster CAN

# Powertrain CAN IDs to sniff for MAP (TBD — pending user's Powertrain capture)
# Likely candidates: Motor_05, Motor_06, Motor_07
POWERTRAIN_MAP_CANDIDATE_IDS = {0x130, 0x288, 0x640}

# UDS IDs for MAP query (engine ECU)
UDS_ENGINE_REQ = 0x7E0
UDS_ENGINE_RESP = 0x7E8
UDS_DID_MAP = 0x39C0  # Saugrohrdruck (mbar absolute)


@dataclass
class BoostState:
    """Live state — read by web UI and broadcast over websocket."""
    lever: Optional[str] = None         # 'P','R','N','D','S','M', or None
    mode: str = "WAITING"               # 'BOOST' / 'TEMP' / 'WAITING'
    map_mbar: float = 0.0               # last known MAP from engine ECU
    real_coolant_c: float = 0.0         # last known real coolant temp (for TEMP mode display only)
    last_motor09_byte: int = 0x80       # what we sent last
    tx_count: int = 0                   # frames sent total
    rx_powertrain_count: int = 0
    rx_cluster_count: int = 0
    map_last_seen_ts: float = 0.0
    lever_last_seen_ts: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "lever": self.lever,
                "mode": self.mode,
                "map_mbar": round(self.map_mbar, 1),
                "real_coolant_c": round(self.real_coolant_c, 1),
                "last_motor09_byte": self.last_motor09_byte,
                "last_motor09_temp_c": round(motor09_byte_to_temp_c(self.last_motor09_byte), 1),
                "tx_count": self.tx_count,
                "rx_powertrain_count": self.rx_powertrain_count,
                "rx_cluster_count": self.rx_cluster_count,
                "map_age_s": round(time.time() - self.map_last_seen_ts, 1) if self.map_last_seen_ts else None,
                "lever_age_s": round(time.time() - self.lever_last_seen_ts, 1) if self.lever_last_seen_ts else None,
            }


class BoostController:
    def __init__(self, config: Config, can_manager: CanManager) -> None:
        self.config = config
        self.can = can_manager
        self.state = BoostState()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ start

    def start(self) -> None:
        # Hook RX listeners
        self.can.add_listener("cluster", self._on_cluster_frame)
        self.can.add_listener("powertrain", self._on_powertrain_frame)

        # Spawn TX loop
        t_tx = threading.Thread(target=self._tx_loop, daemon=True, name="BoostTX")
        t_tx.start()
        self._threads.append(t_tx)

        # Spawn UDS query loop (only if configured for UDS source)
        if self.config["can"]["map_source"] == "uds":
            t_uds = threading.Thread(target=self._uds_query_loop, daemon=True, name="UdsMap")
            t_uds.start()
            self._threads.append(t_uds)

        log.info("BoostController started")

    def stop(self) -> None:
        self._stop.set()

    # ----------------------------------------------------------------- RX cluster

    def _on_cluster_frame(self, can_id: int, data: bytes, ts: float) -> None:
        with self.state.lock:
            self.state.rx_cluster_count += 1

        if can_id == WBA_03_ID:
            lever = decode_lever(data)
            if lever:
                with self.state.lock:
                    self.state.lever = lever
                    self.state.lever_last_seen_ts = ts
                    self.state.mode = "BOOST" if is_boost_mode(lever) else "TEMP"

    # ----------------------------------------------------------------- RX powertrain

    def _on_powertrain_frame(self, can_id: int, data: bytes, ts: float) -> None:
        with self.state.lock:
            self.state.rx_powertrain_count += 1

        # UDS response handler
        if can_id == self.config["can"]["uds_engine_resp"]:
            self._handle_uds_response(data, ts)
            return

        # If source is broadcast sniff, decode known IDs here.
        # TODO: identify exact ID/byte offset for MAP from user's Powertrain capture,
        # then add specific decoder here.
        if self.config["can"]["map_source"] == "broadcast":
            if can_id in POWERTRAIN_MAP_CANDIDATE_IDS:
                # Placeholder — actual byte offset TBD pending capture analysis
                pass

    def _handle_uds_response(self, data: bytes, ts: float) -> None:
        """Decode a UDS positive response on 0x7E8.

        Expected format for ReadDataByIdentifier(0x39C0):
            [LEN] 0x62 0x39 0xC0 <hi> <lo> ...
            len typically 5, value = (hi*256 + lo) mbar absolute
        """
        if len(data) < 6:
            return
        if data[1] != 0x62:        # not a positive ReadDataByIdentifier response
            return
        if data[2] != 0x39 or data[3] != 0xC0:
            return
        map_raw = (data[4] << 8) | data[5]
        with self.state.lock:
            self.state.map_mbar = float(map_raw)
            self.state.map_last_seen_ts = ts

    # ----------------------------------------------------------------- UDS query

    def _uds_query_loop(self) -> None:
        """Periodically poll engine ECU for MAP via UDS 0x22 0x39C0."""
        period = 1.0 / max(1, int(self.config["can"]["uds_query_rate_hz"]))
        req_id = self.config["can"]["uds_engine_req"]
        # ReadDataByIdentifier(0x39C0) single-frame: 03 22 39 C0 00 00 00 00
        payload = bytes([0x03, 0x22, 0x39, 0xC0, 0x00, 0x00, 0x00, 0x00])
        log.info("UDS MAP query loop started @ %.1f Hz", 1.0 / period)
        while not self._stop.is_set():
            self.can.send("powertrain", req_id, payload)
            time.sleep(period)

    # ----------------------------------------------------------------- TX loop

    def _tx_loop(self) -> None:
        """Broadcast Motor_09 (0x647) on cluster CAN at configured rate when in BOOST mode."""
        log.info("TX loop started")
        while not self._stop.is_set():
            cfg = self.config.data
            period = 1.0 / max(1, int(cfg.get("tx_rate_hz", 25)))

            with self.state.lock:
                lever = self.state.lever
                mode = self.state.mode

            # Mode (a): in non-BOOST levers, stay silent — let gateway forward real Motor_09
            if mode != "BOOST":
                time.sleep(period)
                continue

            # Compute byte 0 from MAP
            with self.state.lock:
                map_mbar = self.state.map_mbar
            byte0 = map_mbar_to_motor09_byte(
                map_mbar=map_mbar,
                map_min_mbar=cfg["map_min_mbar"],
                map_max_mbar=cfg["map_max_mbar"],
                temp_min_c=cfg["temp_min_c"],
                temp_max_c=cfg["temp_max_c"],
                scale=cfg.get("scale", 1.0),
                offset_c=cfg.get("offset_c", 0),
            )
            payload = build_motor_09(byte0)
            ok = self.can.send("cluster", MOTOR_09_ID, payload)
            if ok:
                with self.state.lock:
                    self.state.last_motor09_byte = byte0
                    self.state.tx_count += 1

            time.sleep(period)
