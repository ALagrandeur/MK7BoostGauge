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

# Embedded default config — used if no file exists yet (e.g. dev on Windows)
DEFAULT_CONFIG: dict[str, Any] = {
    "_version": 1,
    "map_min_mbar": 300,
    "map_max_mbar": 2500,
    "temp_min_c": 50,
    "temp_max_c": 130,
    "scale": 1.0,
    "offset_c": 0,
    "tx_rate_hz": 25,
    "can": {
        "powertrain_iface": "can0",
        "cluster_iface": "can1",
        "bitrate": 500000,
        "map_source": "uds",
        "uds_did_map": 0x39C0,
        "uds_engine_req": 0x7E0,
        "uds_engine_resp": 0x7E8,
        "uds_query_rate_hz": 10,
    },
    "wifi": {
        "mode": "ap",
        "ap_ssid": "MK7-BoostGauge",
        "ap_password": "boostgauge",
        "ap_channel": 6,
    },
    "safety": {
        "forbidden_can_ids": [0x040, 0x572, 0x585],
    },
}


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
            # Migrate hex strings to ints if user typed "0x..." somewhere
            self._normalize_in_place(self.data)

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
        """Convert any '0x...' strings into int recursively (legacy CSV-style configs)."""
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
