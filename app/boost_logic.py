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
    BOOST_LEVERS, MOTOR_09_ID, build_motor_09, decode_lever_with_gear, is_boost_mode,
    map_mbar_to_motor09_byte, motor09_byte_to_temp_c,
    MAP_PCM_DECODER, REAL_COOLANT_PCM_DECODER, HALDEX_DEMAND_PCM_DECODER,
    decode_pcm_broadcast,
)

log = logging.getLogger(__name__)

# CAN IDs we care about
WBA_03_ID = 0x394   # gear lever, on cluster CAN

# PCM broadcast IDs/decoders are now defined in vw_signals.py
# (MAP_PCM_DECODER, REAL_COOLANT_PCM_DECODER, HALDEX_DEMAND_PCM_DECODER)

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
    # PCM live data (decoded from broadcasts when can1_mode='pcm')
    pcm_map_mbar: Optional[float] = None
    pcm_coolant_real_c: Optional[float] = None
    pcm_haldex_demand_pct: Optional[float] = None
    pcm_map_age_s: float = 0.0
    pcm_coolant_age_s: float = 0.0
    pcm_haldex_age_s: float = 0.0
    pcm_last_map_ts: float = 0.0
    pcm_last_coolant_ts: float = 0.0
    pcm_last_haldex_ts: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        now = time.time()
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
                "map_age_s": round(now - self.map_last_seen_ts, 1) if self.map_last_seen_ts else None,
                "lever_age_s": round(now - self.lever_last_seen_ts, 1) if self.lever_last_seen_ts else None,
                "can1_mode": self.can1_mode,
                "can1_listen_only": self.can1_listen_only,
                # PCM live data
                "pcm_map_mbar": round(self.pcm_map_mbar, 1) if self.pcm_map_mbar is not None else None,
                "pcm_coolant_real_c": round(self.pcm_coolant_real_c, 1) if self.pcm_coolant_real_c is not None else None,
                "pcm_haldex_demand_pct": round(self.pcm_haldex_demand_pct, 1) if self.pcm_haldex_demand_pct is not None else None,
                "pcm_map_age_s": round(now - self.pcm_last_map_ts, 1) if self.pcm_last_map_ts else None,
                "pcm_coolant_age_s": round(now - self.pcm_last_coolant_ts, 1) if self.pcm_last_coolant_ts else None,
                "pcm_haldex_age_s": round(now - self.pcm_last_haldex_ts, 1) if self.pcm_last_haldex_ts else None,
            }


class BoostController:
    def __init__(self, config: Config, can_manager: CanManager) -> None:
        self.config = config
        self.can = can_manager
        self.state = BoostState()
        self.state.can1_mode = config["can"].get("can1_mode", "pcm")
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        # OBD2 UDS client — lazily created (only used in Diagnostic mode)
        self._uds_client = None

    def get_uds_client(self):
        """Lazy-create a UdsClient (will be no-op if CAN1 is in PCM/listen-only)."""
        if self._uds_client is None:
            from .uds import UdsClient
            self._uds_client = UdsClient(
                self.can,
                channel=CH_CAN1,
                req_id=self.config["can"]["uds_engine_req"],
                resp_id=self.config["can"]["uds_engine_resp"],
            )
        return self._uds_client

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

        - PCM mode             → always 'broadcast' (PCM is RX-only, can't query)
        - Diagnostic + UDS     → 'uds'
        - Diagnostic + listen_only → 'broadcast' (no TX allowed)
        """
        cfg_src = self.config["can"].get("map_source", "auto")
        mode = self.config["can"].get("can1_mode", "pcm")
        listen_only = bool(self.config["can"].get("can1_listen_only", False))
        # Hot-update state mirrors + CanManager
        with self.state.lock:
            self.state.can1_mode = mode
            self.state.can1_listen_only = listen_only
        self.can.set_can1_mode(mode)
        self.can.set_can1_listen_only(listen_only)

        if mode == "pcm":
            return "broadcast"
        # Diagnostic mode below
        if listen_only:
            return "broadcast"
        if cfg_src == "broadcast":
            return "broadcast"
        if cfg_src == "uds":
            return "uds"
        return "uds"  # auto in Diagnostic → UDS

    # ------------------------------------------------------------ RX cluster

    def _on_cluster_frame(self, can_id: int, data: bytes, ts: float) -> None:
        with self.state.lock:
            self.state.rx_cluster_count += 1

        if can_id == WBA_03_ID:
            lever = decode_lever_with_gear(data)
            if lever:
                with self.state.lock:
                    self.state.lever = lever
                    self.state.lever_last_seen_ts = ts
                    self.state.mode = "BOOST" if is_boost_mode(lever) else "TEMP"

    # ------------------------------------------------------------ RX can1

    def _on_can1_frame(self, can_id: int, data: bytes, ts: float) -> None:
        with self.state.lock:
            self.state.rx_can1_count += 1

        # UDS responses are handled exclusively by UdsClient (lazy-created in
        # get_uds_client). We DO NOT double-process them here to avoid races
        # with the SID-filtered UdsClient listener.
        if can_id == self.config["can"]["uds_engine_resp"]:
            return

        # PCM mode broadcast decoders
        if self.state.can1_mode == "pcm":
            v = decode_pcm_broadcast(can_id, data, MAP_PCM_DECODER)
            if v is not None:
                with self.state.lock:
                    self.state.pcm_map_mbar = v
                    self.state.pcm_last_map_ts = ts
                    self.state.map_mbar = v       # also feed boost gauge
                    self.state.map_last_seen_ts = ts
                    self.state.map_source_active = "broadcast"

            v = decode_pcm_broadcast(can_id, data, REAL_COOLANT_PCM_DECODER)
            if v is not None:
                with self.state.lock:
                    self.state.pcm_coolant_real_c = v
                    self.state.pcm_last_coolant_ts = ts

            v = decode_pcm_broadcast(can_id, data, HALDEX_DEMAND_PCM_DECODER)
            if v is not None:
                with self.state.lock:
                    self.state.pcm_haldex_demand_pct = v
                    self.state.pcm_last_haldex_ts = ts

    # ------------------------------------------------------------ UDS query loop

    def _uds_query_loop(self) -> None:
        """Periodically poll engine ECU for MAP via UdsClient when effective source=uds.

        Goes through UdsClient (NOT direct can.send) to share the SID-filtered
        response machinery with OBD2 button endpoints — avoids races where
        button responses get clobbered by the periodic query.
        """
        from .uds import decode_did_map_mbar
        log.info("UDS MAP query loop started")
        while not self._stop.is_set():
            cfg = self.config["can"]
            period = 1.0 / max(1, int(cfg.get("uds_query_rate_hz", 10)))
            if self._effective_map_source() == "uds":
                client = self.get_uds_client()
                # 0.3s timeout < 1s/10Hz period (worst case 1 missed query)
                data = client.read_data_by_identifier(0x39C0, timeout_s=0.3)
                if data is not None:
                    map_mbar = decode_did_map_mbar(data)
                    if map_mbar is not None:
                        with self.state.lock:
                            self.state.map_mbar = map_mbar
                            self.state.map_last_seen_ts = time.time()
                            self.state.map_source_active = "uds"
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
                formula=cfg.get("formula", "linear"),
            )
            payload = build_motor_09(byte0)
            ok = self.can.send(CH_CLUSTER, MOTOR_09_ID, payload)
            if ok:
                with self.state.lock:
                    self.state.last_motor09_byte = byte0
                    self.state.tx_count += 1

            time.sleep(period)
