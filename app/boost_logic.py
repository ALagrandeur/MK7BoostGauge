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
WBA_03_ID = 0x394   # gear lever, on cluster CAN

# Powertrain CAN IDs to sniff for MAP (TBD — pending user's Powertrain capture).
# Likely candidates from openDBC vw_mqb_2010.dbc: Motor_05/06/07.
POWERTRAIN_MAP_CANDIDATE_IDS = {0x130, 0x288, 0x640}

# Channel name shortcuts (must match CanManager.CHANNELS)
CH_CLUSTER = "cluster"
CH_CAN1    = "can1"


@dataclass
class BoostState:
    """Live state — read by web UI and broadcast over websocket."""
    lever: Optional[str] = None
    mode: str = "WAITING"               # 'BOOST' / 'TEMP' / 'WAITING'
    map_mbar: float = 0.0
    map_source_active: str = "none"     # 'broadcast' / 'uds' / 'none'
    last_motor09_byte: int = 0x80
    tx_count: int = 0
    rx_cluster_count: int = 0
    rx_can1_count: int = 0
    map_last_seen_ts: float = 0.0
    lever_last_seen_ts: float = 0.0
    can1_mode: str = "pcm"
    can1_listen_only: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "lever": self.lever,
                "mode": self.mode,
                "map_mbar": round(self.map_mbar, 1),
                "map_source_active": self.map_source_active,
                "last_motor09_byte": self.last_motor09_byte,
                "last_motor09_temp_c": round(motor09_byte_to_temp_c(self.last_motor09_byte), 1),
                "tx_count": self.tx_count,
                "rx_cluster_count": self.rx_cluster_count,
                "rx_can1_count": self.rx_can1_count,
                "map_age_s": round(time.time() - self.map_last_seen_ts, 1) if self.map_last_seen_ts else None,
                "lever_age_s": round(time.time() - self.lever_last_seen_ts, 1) if self.lever_last_seen_ts else None,
                "can1_mode": self.can1_mode,
                "can1_listen_only": self.can1_listen_only,
            }


class BoostController:
    def __init__(self, config: Config, can_manager: CanManager) -> None:
        self.config = config
        self.can = can_manager
        self.state = BoostState()
        self.state.can1_mode = config["can"].get("can1_mode", "pcm")
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ start

    def start(self) -> None:
        # Hook RX listeners
        self.can.add_listener(CH_CLUSTER, self._on_cluster_frame)
        self.can.add_listener(CH_CAN1,    self._on_can1_frame)

        # Spawn TX loop
        t_tx = threading.Thread(target=self._tx_loop, daemon=True, name="BoostTX")
        t_tx.start()
        self._threads.append(t_tx)

        # Spawn UDS query loop — single thread, decides per-iteration whether to send
        t_uds = threading.Thread(target=self._uds_query_loop, daemon=True, name="UdsMap")
        t_uds.start()
        self._threads.append(t_uds)

        log.info("BoostController started (can1_mode=%s)", self.state.can1_mode)

    def stop(self) -> None:
        self._stop.set()

    # --------------------------------------------------------- helpers

    def _effective_map_source(self) -> str:
        """Decide actual MAP source given config + can1 mode + listen-only safety.

        - listen_only=True → 'broadcast' (no TX possible, so UDS impossible — only sniff)
        - source=broadcast → broadcast (only meaningful in PCM mode)
        - source=uds       → uds
        - source=auto      → broadcast in PCM mode, uds in Diagnostic mode
        """
        cfg_src = self.config["can"].get("map_source", "auto")
        mode = self.config["can"].get("can1_mode", "pcm")
        listen_only = bool(self.config["can"].get("can1_listen_only", False))
        # Hot-update state mirror
        with self.state.lock:
            self.state.can1_mode = mode
            self.state.can1_listen_only = listen_only

        if listen_only:
            # Can't query UDS → force broadcast attempt (will be no-op until ID identified)
            return "broadcast"
        if cfg_src == "broadcast":
            return "broadcast"
        if cfg_src == "uds":
            return "uds"
        return "broadcast" if mode == "pcm" else "uds"

    # ------------------------------------------------------------ RX cluster

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

    # ------------------------------------------------------------ RX can1

    def _on_can1_frame(self, can_id: int, data: bytes, ts: float) -> None:
        with self.state.lock:
            self.state.rx_can1_count += 1

        # UDS positive response handler — works on both PCM and Diagnostic
        if can_id == self.config["can"]["uds_engine_resp"]:
            self._handle_uds_response(data, ts)
            return

        # Broadcast sniff (only meaningful in PCM mode)
        if self._effective_map_source() == "broadcast" and can_id in POWERTRAIN_MAP_CANDIDATE_IDS:
            # TODO: identify exact byte offset for MAP from user's Powertrain capture.
            # Placeholder — currently does nothing until ID/byte known.
            pass

    def _handle_uds_response(self, data: bytes, ts: float) -> None:
        """Decode a UDS positive ReadDataByIdentifier response on 0x7E8.

        Expected: [LEN] 0x62 0x39 0xC0 <hi> <lo> ...
        Value = (hi*256 + lo) mbar absolute
        """
        if len(data) < 6:
            return
        if data[1] != 0x62:
            return
        if data[2] != 0x39 or data[3] != 0xC0:
            return
        map_raw = (data[4] << 8) | data[5]
        with self.state.lock:
            self.state.map_mbar = float(map_raw)
            self.state.map_last_seen_ts = ts
            self.state.map_source_active = "uds"

    # ------------------------------------------------------------ UDS query loop

    def _uds_query_loop(self) -> None:
        """Periodically poll engine ECU for MAP via UDS 0x22 0x39C0 if effective source=uds."""
        # ReadDataByIdentifier(0x39C0) single-frame: 03 22 39 C0 00 00 00 00
        payload = bytes([0x03, 0x22, 0x39, 0xC0, 0x00, 0x00, 0x00, 0x00])
        log.info("UDS MAP query loop started")
        while not self._stop.is_set():
            cfg = self.config["can"]
            period = 1.0 / max(1, int(cfg.get("uds_query_rate_hz", 10)))
            if self._effective_map_source() == "uds":
                req_id = cfg["uds_engine_req"]
                self.can.send(CH_CAN1, req_id, payload)
            time.sleep(period)

    # ------------------------------------------------------------ TX loop

    def _tx_loop(self) -> None:
        """Broadcast Motor_09 (0x647) on cluster CAN at configured rate when in BOOST mode."""
        log.info("TX loop started")
        while not self._stop.is_set():
            cfg = self.config.data
            period = 1.0 / max(1, int(cfg.get("tx_rate_hz", 25)))

            with self.state.lock:
                mode = self.state.mode
                map_mbar = self.state.map_mbar

            # Mode (a): in non-BOOST levers, stay silent — let gateway forward real Motor_09
            if mode != "BOOST":
                time.sleep(period)
                continue

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
            ok = self.can.send(CH_CLUSTER, MOTOR_09_ID, payload)
            if ok:
                with self.state.lock:
                    self.state.last_motor09_byte = byte0
                    self.state.tx_count += 1

            time.sleep(period)
