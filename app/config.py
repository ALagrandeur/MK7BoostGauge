"""Config persistence — JSON file at /var/lib/boostgauge/config.json."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)

CONFIG_PATH_ENV = "BOOSTGAUGE_CONFIG"
DEFAULT_CONFIG_PATH = Path("/var/lib/boostgauge/config.json")

# Embedded default config — used if no file exists yet (e.g. dev on Windows).
# Keep schema in sync with config.example.json (mirror).
DEFAULT_CONFIG: dict[str, Any] = {
    "_version": 2,
    "map_min_mbar": 300,
    "map_max_mbar": 2500,
    "temp_min_c": 50,
    "temp_max_c": 130,
    "scale": 1.0,
    "offset_c": 0,
    "tx_rate_hz": 25,
    "formula": "linear",   # "linear" | "exp" | "sqrt"
    # Cluster gauge dead zone skip (CRITICAL for boost gauge UX):
    # The MQB needle stays planted at center for ANY temp in [80°C, 110°C].
    # When True, the mapping splits MAP range over [temp_min, dead_low] +
    # [dead_high, temp_max] so the needle never freezes mid-MAP.
    "skip_dead_zone": True,
    "dead_zone_low_c":  80,
    "dead_zone_high_c": 110,
    "can": {
        "cluster_iface": "can0",
        "can1_iface": "can1",
        "can1_mode": "diagnostic",     # "pcm" or "diagnostic" — default = diagnostic (OBD-II tool)
        "can1_listen_only": False,     # SAFETY: when True, CAN1 is RX-only (no UDS query, no anything)
        "bitrate": 500000,
        "map_source": "auto",          # "auto" / "broadcast" / "uds"
        "uds_did_map": 0x39C0,
        "uds_engine_req": 0x7E0,
        "uds_engine_resp": 0x7E8,
        "uds_query_rate_hz": 10,
    },
    "wifi": {
        "mode": "ap",
        "ap_ssid": "MK7-BoostGauge",
        "ap_password": "boost123",
        "ap_channel": 6,
    },
    "safety": {
        "forbidden_can_ids": [0x040, 0x572, 0x585],
    },
}


# ---------------------------------------------------------------------------
# Schema migration — bump _version when changing structure
# ---------------------------------------------------------------------------

def _migrate_v1_to_v2(data: dict) -> dict:
    """v1 used 'powertrain_iface' — rename to 'can1_iface' + add can1_mode='pcm'."""
    can = data.get("can", {})
    if "powertrain_iface" in can and "can1_iface" not in can:
        can["can1_iface"] = can.pop("powertrain_iface")
        log.info("Migrated v1->v2: powertrain_iface -> can1_iface")
    if "can1_mode" not in can:
        can["can1_mode"] = "pcm"
        log.info("Migrated v1->v2: added can1_mode='pcm'")
    if "map_source" not in can:
        can["map_source"] = "auto"
    if "can1_listen_only" not in can:
        can["can1_listen_only"] = False
    data["_version"] = 2
    return data


def _migrate(data: dict) -> dict:
    v = data.get("_version", 1)
    if v < 2:
        data = _migrate_v1_to_v2(data)
    return data


# ---------------------------------------------------------------------------
# Config wrapper
# ---------------------------------------------------------------------------

class Config:
    """Thread-safe config wrapper. Reads/writes JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        env = os.environ.get(CONFIG_PATH_ENV)
        self.path = path or (Path(env) if env else DEFAULT_CONFIG_PATH)
        self._lock = Lock()
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    self.data = json.loads(self.path.read_text())
                    log.info("Loaded config from %s", self.path)
                except Exception as e:
                    log.error("Bad config %s — falling back to defaults: %s", self.path, e)
                    self.data = dict(DEFAULT_CONFIG)
            else:
                log.warning("No config file at %s — using defaults", self.path)
                self.data = dict(DEFAULT_CONFIG)
            self._normalize_in_place(self.data)
            self.data = _migrate(self.data)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2))
            log.info("Saved config to %s", self.path)

    def update(self, patch: dict[str, Any]) -> None:
        with self._lock:
            _deep_merge(self.data, patch)
        self.save()

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @staticmethod
    def _normalize_in_place(d: Any) -> None:
        """Convert any '0x...' strings into int recursively."""
        if isinstance(d, dict):
            for k, v in list(d.items()):
                if isinstance(v, str) and v.lower().startswith("0x"):
                    try:
                        d[k] = int(v, 16)
                    except ValueError:
                        pass
                elif isinstance(v, list):
                    d[k] = [int(x, 16) if isinstance(x, str) and x.lower().startswith("0x") else x
                            for x in v]
                elif isinstance(v, dict):
                    Config._normalize_in_place(v)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for k, v in patch.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
