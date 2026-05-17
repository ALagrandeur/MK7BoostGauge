"""MK7BoostGauge Pi daemon.

Architecture v3: minimal autonomous service.
  - HTTP listener on port 8765 for config push from PC (POST /config)
  - HTTP GET /status for live monitoring (gear, mode, last byte)
  - HTTP GET /ping for discovery
  - Reads WBA_03 from cluster CAN (gear lever)
  - TX Motor_09 conditional on gear (BOOST = S/M/N, silent in D/P/R)
  - Hot apply: config received over HTTP applied immediately, no restart
  - Persists last received config to /var/lib/boostgauge/config.json
  - Boots autonomously with last config even if PC unreachable

Config schema (JSON):
{
  "map_min_mbar": 300,
  "map_max_mbar": 2500,
  "scale": 1.0,
  "offset_c": 0,
  "tx_rate_hz": 25,
  "test_mode_active": false,
  "test_mode_temp_c": 90.0
}
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
    map_mbar_to_motor09_byte, motor09_byte_to_temp_c, temp_c_to_motor09_byte,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("MK7Daemon")

# ---------------------------------------------------------------------------
# Config (persisted on Pi, overwritten on every PC push)
# ---------------------------------------------------------------------------

CONFIG_PATH = Path("/var/lib/boostgauge/config.json")

DEFAULT_CONFIG = {
    "map_min_mbar": 300,
    "map_max_mbar": 2500,
    "scale": 1.0,
    "offset_c": 0,
    "tx_rate_hz": 25,
    "test_mode_active": False,
    "test_mode_temp_c": 90.0,
}


# WBA_03 on cluster CAN
WBA_03_ID = 0x394


@dataclass
class DaemonState:
    config: dict = field(default_factory=lambda: dict(DEFAULT_CONFIG))
    lever: Optional[str] = None       # 'P', 'R', 'N', 'D1'..D6, 'S1'..S6, 'M1'..M6
    mode: str = "WAITING"             # WAITING / BOOST / TEMP / TEST
    last_motor09_byte: int = 0x80
    last_wba03_ts: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "config": dict(self.config),
                "lever": self.lever,
                "mode": self.mode,
                "last_motor09_byte": self.last_motor09_byte,
                "last_motor09_temp_c": round(motor09_byte_to_temp_c(self.last_motor09_byte), 2),
                "lever_age_s": round(time.time() - self.last_wba03_ts, 1) if self.last_wba03_ts else None,
            }


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

def load_config_from_disk() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            # Merge with defaults to handle missing fields
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
        log.info("Saved config to %s", CONFIG_PATH)
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
        t = threading.Thread(target=self._tx_loop, daemon=True, name="BoostTX")
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    def _on_cluster_frame(self, can_id: int, data: bytes, ts: float) -> None:
        if can_id != WBA_03_ID:
            return
        lever = decode_lever_with_gear(data)
        if lever:
            with self.state.lock:
                self.state.lever = lever
                self.state.last_wba03_ts = ts
                # Don't change mode if test_mode_active (TX loop handles it)
                if not self.state.config.get("test_mode_active"):
                    self.state.mode = "BOOST" if is_boost_mode(lever) else "TEMP"

    def _tx_loop(self) -> None:
        log.info("TX loop started")
        while not self._stop.is_set():
            with self.state.lock:
                cfg = dict(self.state.config)
                mode = self.state.mode
                lever = self.state.lever

            period = 1.0 / max(1, int(cfg.get("tx_rate_hz", 25)))

            # ---- TEST MODE (highest priority) ----
            if cfg.get("test_mode_active"):
                test_temp = float(cfg.get("test_mode_temp_c", 90.0))
                byte0 = temp_c_to_motor09_byte(test_temp)
                payload = build_motor_09(byte0)
                with self.state.lock:
                    self.state.mode = "TEST"
                    self.state.last_motor09_byte = byte0
                self.can.send("cluster", MOTOR_09_ID, payload)
                time.sleep(period)
                continue

            # ---- Exit TEST mode ----
            if mode == "TEST":
                with self.state.lock:
                    self.state.mode = "WAITING"
                    mode = "WAITING"

            # ---- Re-evaluate mode from lever (after test exit) ----
            if lever:
                with self.state.lock:
                    self.state.mode = "BOOST" if is_boost_mode(lever) else "TEMP"
                    mode = self.state.mode

            # ---- BOOST mode TX ----
            if mode == "BOOST":
                # No MAP source in v3 - we use a SIMULATED MAP value for now
                # (in future: read from another source, or accept MAP from PC)
                # For now, default to mid-range (gauge stays at center boost equivalent)
                # TODO: hook MAP source when CAN1 / OBD-II integration ready
                map_mbar = (cfg["map_min_mbar"] + cfg["map_max_mbar"]) / 2
                byte0 = map_mbar_to_motor09_byte(
                    map_mbar=map_mbar,
                    map_min_mbar=cfg["map_min_mbar"],
                    map_max_mbar=cfg["map_max_mbar"],
                    scale=cfg.get("scale", 1.0),
                    offset_c=cfg.get("offset_c", 0),
                )
                payload = build_motor_09(byte0)
                with self.state.lock:
                    self.state.last_motor09_byte = byte0
                self.can.send("cluster", MOTOR_09_ID, payload)

            # In TEMP / WAITING: silent (let gateway forward real Motor_09)
            time.sleep(period)


# ---------------------------------------------------------------------------
# Flask app (HTTP API for PC communication)
# ---------------------------------------------------------------------------

def create_app(state: DaemonState, controller: BoostController) -> Flask:
    app = Flask(__name__)

    @app.route("/ping", methods=["GET"])
    def ping():
        # Discovery endpoint — PC checks if boostgauge.local is reachable
        return jsonify({"ok": True, "service": "MK7BoostGauge", "version": "v3"})

    @app.route("/status", methods=["GET"])
    def status():
        snap = state.snapshot()
        snap["can"] = {
            "tx_count": controller.can.tx_count,
            "rx_cluster_count": controller.can.rx_cluster_count,
            "blocked_airbag": controller.can.blocked_airbag_count,
        }
        response = jsonify(snap)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/config", methods=["POST"])
    def post_config():
        """Receive new config from PC and apply HOT (no restart).

        Body: JSON with any subset of DEFAULT_CONFIG keys.
        Missing keys retain current value (merge semantics, but PC always
        sends FULL config per spec).
        """
        try:
            patch = request.get_json(force=True)
            if not isinstance(patch, dict):
                return jsonify({"ok": False, "error": "expected JSON object"}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": f"bad JSON: {e}"}), 400

        # Whitelist of allowed keys (defensive)
        allowed = set(DEFAULT_CONFIG.keys())
        clean = {k: v for k, v in patch.items() if k in allowed}

        # Hot apply
        with state.lock:
            state.config.update(clean)
            new_config = dict(state.config)

        # Persist (best-effort - we don't fail if disk write fails)
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
    log.info("MK7BoostGauge daemon v3 starting")

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
    # Use threaded mode so multiple PC requests don't block
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
