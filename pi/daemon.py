"""MK7BoostGauge Pi daemon v3.1.

Refactor: no test mode. MAP source = OBD2 (UDS query on CAN1) or PCM broadcast (sniff CAN1).
Real coolant temp sniffed from CAN0 (Motor_09 byte 0).

Endpoints:
  GET  /ping       - discovery
  GET  /status     - lever, mode, MAP real, coolant real (live)
  GET  /config     - current config
  POST /config     - hot apply new config from PC
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request

from .can_manager import CanManager
from .vw_signals import (
    MOTOR_09_ID, build_motor_09, decode_lever_with_gear, is_boost_mode,
    map_mbar_to_motor09_byte, motor09_byte_to_temp_c,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("MK7Daemon")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_PATH = Path("/var/lib/boostgauge/config.json")

DEFAULT_CONFIG = {
    "map_min_mbar": 300,
    "map_max_mbar": 2500,
    "scale": 1.0,
    "offset_c": 0,
    "tx_rate_hz": 25,
    "map_source": "obd2_diagnostic",
    "obd2_req_id_hex": "0x7E0",
    "obd2_resp_id_hex": "0x7E8",
    "obd2_did_map_hex": "0x39C0",
    "obd2_query_rate_hz": 5,
    "pcm_map_can_id_hex": "0x0",
    "pcm_map_byte_offset": 0,
    "pcm_map_scale": 1.0,
    "pcm_map_offset": 0.0,
    # Cluster CAN0 addresses (modifiable for flexibility / different cluster variants)
    "cluster_motor09_id_hex": "0x647",   # Motor_09: where we TX coolant + sniff real
    "cluster_wba03_id_hex": "0x394",     # WBA_03: gear lever (RX only)
}


def _hex_to_int(v) -> int:
    """Accept either int or '0x...' string. Returns int (0 on failure)."""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v, 16) if v.lower().startswith("0x") else int(v)
        except ValueError:
            return 0
    return 0


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class DaemonState:
    config: dict = field(default_factory=lambda: dict(DEFAULT_CONFIG))
    # Lever / mode
    lever: Optional[str] = None
    mode: str = "WAITING"               # BOOST / TEMP / WAITING
    last_motor09_byte: int = 0x80
    last_wba03_ts: float = 0.0
    # MAP from CAN1 (OBD2 query OR PCM broadcast)
    last_map_mbar: Optional[float] = None
    last_map_ts: float = 0.0
    # Real coolant sniffed from CAN0 (Motor_09 byte 0)
    # NOTE: when our daemon is TXing Motor_09 in BOOST, this reflects OUR override value.
    #       When silent (TEMP/WAITING), this is the gateway-forwarded REAL temp.
    coolant_real_c: Optional[float] = None
    last_coolant_ts: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            return {
                "config": dict(self.config),
                "lever": self.lever,
                "mode": self.mode,
                "last_motor09_byte": self.last_motor09_byte,
                "last_motor09_temp_c": round(motor09_byte_to_temp_c(self.last_motor09_byte), 1),
                "lever_age_s": round(now - self.last_wba03_ts, 1) if self.last_wba03_ts else None,
                "map_mbar": round(self.last_map_mbar, 1) if self.last_map_mbar is not None else None,
                "map_age_s": round(now - self.last_map_ts, 1) if self.last_map_ts else None,
                "coolant_real_c": round(self.coolant_real_c, 1) if self.coolant_real_c is not None else None,
                "coolant_age_s": round(now - self.last_coolant_ts, 1) if self.last_coolant_ts else None,
            }


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

def load_config_from_disk() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            merged = dict(DEFAULT_CONFIG)
            merged.update(data)
            log.info("Loaded config from %s", CONFIG_PATH)
            return merged
        except Exception as e:
            log.error("Bad config file: %s - using defaults", e)
    return dict(DEFAULT_CONFIG)


def save_config_to_disk(config: dict) -> bool:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2))
        return True
    except Exception as e:
        log.error("Failed to save config: %s", e)
        return False


# ---------------------------------------------------------------------------
# Boost controller
# ---------------------------------------------------------------------------

class BoostController:
    def __init__(self, state: DaemonState, can_mgr: CanManager) -> None:
        self.state = state
        self.can = can_mgr
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self.can.add_listener("cluster", self._on_cluster_frame)
        self.can.add_listener("can1", self._on_can1_frame)

        for fn, name in [(self._tx_loop, "BoostTX"),
                          (self._uds_query_loop, "UdsQuery")]:
            t = threading.Thread(target=fn, daemon=True, name=name)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    # ---------------------------------------------------- CAN0 (cluster) RX
    def _on_cluster_frame(self, can_id: int, data: bytes, ts: float) -> None:
        cfg = self.state.config
        wba03_id = _hex_to_int(cfg.get("cluster_wba03_id_hex", "0x394"))
        motor09_id = _hex_to_int(cfg.get("cluster_motor09_id_hex", "0x647"))

        # Lever from WBA_03 (or whatever ID is configured)
        if can_id == wba03_id:
            lever = decode_lever_with_gear(data)
            if lever:
                with self.state.lock:
                    self.state.lever = lever
                    self.state.last_wba03_ts = ts
                    self.state.mode = "BOOST" if is_boost_mode(lever) else "TEMP"
            return

        # Real coolant: sniff Motor_09 byte 0 (what cluster currently displays)
        if can_id == motor09_id and len(data) >= 1:
            with self.state.lock:
                self.state.coolant_real_c = motor09_byte_to_temp_c(data[0])
                self.state.last_coolant_ts = ts
            return

    # ---------------------------------------------------- CAN1 RX
    def _on_can1_frame(self, can_id: int, data: bytes, ts: float) -> None:
        cfg = self.state.config

        # OBD2 mode: parse UDS response
        if cfg.get("map_source") == "obd2_diagnostic":
            resp_id = _hex_to_int(cfg.get("obd2_resp_id_hex", "0x7E8"))
            did = _hex_to_int(cfg.get("obd2_did_map_hex", "0x39C0"))
            if can_id == resp_id and len(data) >= 6:
                # Expected: [LEN] 0x62 [did_hi] [did_lo] [MSB] [LSB] ...
                if (data[1] == 0x62
                        and data[2] == ((did >> 8) & 0xFF)
                        and data[3] == (did & 0xFF)):
                    map_raw = (data[4] << 8) | data[5]
                    with self.state.lock:
                        self.state.last_map_mbar = float(map_raw)
                        self.state.last_map_ts = ts

        # PCM broadcast mode: sniff configured CAN ID
        elif cfg.get("map_source") == "pcm_broadcast":
            target = _hex_to_int(cfg.get("pcm_map_can_id_hex", "0x0"))
            if target > 0 and can_id == target:
                byte_idx = int(cfg.get("pcm_map_byte_offset", 0))
                if byte_idx < len(data):
                    raw = data[byte_idx]
                    sc = float(cfg.get("pcm_map_scale", 1.0))
                    of = float(cfg.get("pcm_map_offset", 0.0))
                    val = raw * sc + of
                    with self.state.lock:
                        self.state.last_map_mbar = val
                        self.state.last_map_ts = ts

    # ---------------------------------------------------- UDS query loop
    def _uds_query_loop(self) -> None:
        log.info("UDS query loop started")
        while not self._stop.is_set():
            cfg = dict(self.state.config)
            if cfg.get("map_source") != "obd2_diagnostic":
                time.sleep(1.0)
                continue

            req_id = _hex_to_int(cfg.get("obd2_req_id_hex", "0x7E0"))
            did = _hex_to_int(cfg.get("obd2_did_map_hex", "0x39C0"))
            if req_id == 0 or did == 0:
                time.sleep(1.0)
                continue

            # ISO-TP single frame: [03 22 DID_H DID_L 00 00 00 00]
            payload = bytes([0x03, 0x22, (did >> 8) & 0xFF, did & 0xFF,
                              0x00, 0x00, 0x00, 0x00])
            self.can.send("can1", req_id, payload)
            rate = int(cfg.get("obd2_query_rate_hz", 5))
            time.sleep(1.0 / max(1, rate))

    # ---------------------------------------------------- TX loop (cluster)
    def _tx_loop(self) -> None:
        log.info("TX loop started")
        while not self._stop.is_set():
            with self.state.lock:
                cfg = dict(self.state.config)
                mode = self.state.mode
                lever = self.state.lever
                map_mbar = self.state.last_map_mbar
                map_ts = self.state.last_map_ts

            period = 1.0 / max(1, int(cfg.get("tx_rate_hz", 25)))

            # Re-evaluate mode from lever each iteration
            if lever:
                new_mode = "BOOST" if is_boost_mode(lever) else "TEMP"
                if new_mode != mode:
                    with self.state.lock:
                        self.state.mode = new_mode
                    mode = new_mode

            if mode == "BOOST":
                # Use real MAP if recent (<3s old), else fall back to map_min
                map_to_use = map_mbar
                if map_to_use is None or (time.time() - map_ts > 3.0):
                    map_to_use = float(cfg["map_min_mbar"])

                byte0 = map_mbar_to_motor09_byte(
                    map_mbar=map_to_use,
                    map_min_mbar=cfg["map_min_mbar"],
                    map_max_mbar=cfg["map_max_mbar"],
                    scale=cfg.get("scale", 1.0),
                    offset_c=cfg.get("offset_c", 0),
                )
                payload = build_motor_09(byte0)
                motor09_id = _hex_to_int(cfg.get("cluster_motor09_id_hex", "0x647"))
                with self.state.lock:
                    self.state.last_motor09_byte = byte0
                self.can.send("cluster", motor09_id, payload)
            # TEMP / WAITING: silent

            time.sleep(period)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app(state: DaemonState, controller: BoostController) -> Flask:
    app = Flask(__name__)

    @app.route("/ping", methods=["GET"])
    def ping():
        return jsonify({"ok": True, "service": "MK7BoostGauge", "version": "v3.1"})

    @app.route("/status", methods=["GET"])
    def status():
        snap = state.snapshot()
        snap["can"] = {
            "tx_count": controller.can.tx_count,
            "rx_cluster_count": controller.can.rx_cluster_count,
            "rx_can1_count": controller.can.rx_can1_count,
            "blocked_airbag": controller.can.blocked_airbag_count,
        }
        response = jsonify(snap)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/config", methods=["POST"])
    def post_config():
        try:
            patch = request.get_json(force=True)
            if not isinstance(patch, dict):
                return jsonify({"ok": False, "error": "expected JSON object"}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": f"bad JSON: {e}"}), 400

        allowed = set(DEFAULT_CONFIG.keys())
        clean = {k: v for k, v in patch.items() if k in allowed}

        with state.lock:
            state.config.update(clean)
            new_config = dict(state.config)

        save_ok = save_config_to_disk(new_config)
        log.info("Config applied (hot): %s | disk_save=%s",
                 list(clean.keys()), save_ok)
        return jsonify({
            "ok": True,
            "applied": clean,
            "config": new_config,
            "disk_save": save_ok,
        })

    @app.route("/config", methods=["GET"])
    def get_config():
        return jsonify(state.snapshot()["config"])

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    log.info("MK7BoostGauge daemon v3.1 starting")
    state = DaemonState()
    state.config = load_config_from_disk()

    can_mgr = CanManager()
    can_mgr.open()

    controller = BoostController(state, can_mgr)
    controller.start()

    app = create_app(state, controller)

    def _shutdown(*_):
        log.info("Shutdown requested")
        controller.stop()
        can_mgr.close()
        sys.exit(0)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    port = int(os.environ.get("MK7_DAEMON_PORT", 8765))
    log.info("HTTP API on http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
