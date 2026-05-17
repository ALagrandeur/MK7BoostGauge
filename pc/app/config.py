"""PC-side config persistence - JSON file in project dir."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Default config (matches Pi daemon defaults)
DEFAULT_CONFIG: dict[str, Any] = {
    # Cluster mapping (sliders)
    "map_min_mbar": 300,
    "map_max_mbar": 2500,
    "scale": 1.0,
    "offset_c": 0,
    "tx_rate_hz": 25,

    # MAP source selector: "obd2_diagnostic" or "pcm_broadcast"
    "map_source": "obd2_diagnostic",

    # OBD2 (UDS) settings — used when map_source = obd2_diagnostic
    # CAN1 must be wired to OBD-II port (or PCM bus with UDS forwarded)
    "obd2_req_id_hex": "0x7E0",       # Engine ECU diagnostic request ID
    "obd2_resp_id_hex": "0x7E8",      # Engine ECU diagnostic response ID
    "obd2_did_map_hex": "0x39C0",     # Saugrohrdruck (MAP) DID
    "obd2_query_rate_hz": 5,          # UDS query rate

    # PCM broadcast settings — used when map_source = pcm_broadcast
    # CAN1 wired directly to Powertrain CAN (listen-only sniff)
    "pcm_map_can_id_hex": "0x0",      # CAN ID broadcasting MAP (TBD by user)
    "pcm_map_byte_offset": 0,         # Byte index of MAP within the frame
    "pcm_map_scale": 1.0,             # Raw byte * scale
    "pcm_map_offset": 0.0,            # + offset = mbar

    # Pi connection
    "pi_host": "boostgauge.local",
    "pi_port": 8765,
}

# Storage path: project root / pc_config.json
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "pc_config.json"


class PcConfig:
    """Thread-safe PC config wrapper. Persists to JSON file in project root."""

    def __init__(self, path: Path = None) -> None:
        self.path = path if path is not None else CONFIG_PATH
        self._lock = threading.Lock()
        self.data: dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                disk = json.loads(self.path.read_text(encoding="utf-8"))
                with self._lock:
                    self.data = dict(DEFAULT_CONFIG)
                    self.data.update(disk)
                log.info("Loaded config from %s", self.path)
            except Exception as e:
                log.error("Bad config %s - using defaults: %s", self.path, e)
                with self._lock:
                    self.data = dict(DEFAULT_CONFIG)
        else:
            log.info("No config at %s - using defaults", self.path)
            self.save()

    def save(self) -> tuple[bool, str]:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
                return True, ""
            except Exception as e:
                log.error("Failed to save config: %s", e)
                return False, str(e)

    def update(self, patch: dict[str, Any]) -> tuple[bool, str]:
        with self._lock:
            self.data.update(patch)
        return self.save()

    def get_pi_config_only(self) -> dict:
        """Return only the keys that should be sent to the Pi daemon."""
        pi_keys = {
            "map_min_mbar", "map_max_mbar", "scale", "offset_c", "tx_rate_hz",
            "map_source",
            "obd2_req_id_hex", "obd2_resp_id_hex", "obd2_did_map_hex", "obd2_query_rate_hz",
            "pcm_map_can_id_hex", "pcm_map_byte_offset", "pcm_map_scale", "pcm_map_offset",
        }
        with self._lock:
            return {k: v for k, v in self.data.items() if k in pi_keys}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.data)
