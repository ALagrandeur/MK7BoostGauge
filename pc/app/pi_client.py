"""PC-side client for talking to Pi daemon over HTTP."""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)


class PiClient:
    def __init__(self, host: str = "boostgauge.local", port: int = 8765,
                 timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def update_address(self, host: str, port: int = 8765) -> None:
        self.host = host
        self.port = port

    def ping(self) -> tuple[bool, str]:
        """Quick reachability check. Returns (ok, message_or_error)."""
        try:
            r = requests.get(f"{self.base_url}/ping", timeout=self.timeout)
            if r.status_code == 200:
                data = r.json()
                return True, f"OK - service={data.get('service')} version={data.get('version')}"
            return False, f"HTTP {r.status_code}"
        except requests.exceptions.ConnectTimeout:
            return False, "Timeout (Pi unreachable)"
        except requests.exceptions.ConnectionError as e:
            return False, f"Connection refused / DNS error: {e.__class__.__name__}"
        except Exception as e:
            return False, f"Error: {e}"

    def send_config(self, config: dict) -> tuple[bool, str, Optional[dict]]:
        """POST config to Pi. Returns (ok, message, pi_response_or_none)."""
        try:
            r = requests.post(
                f"{self.base_url}/config",
                json=config,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    return True, "Config envoyé et appliqué", data
                return False, f"Pi a refusé: {data.get('error', 'unknown')}", data
            return False, f"HTTP {r.status_code}", None
        except requests.exceptions.ConnectTimeout:
            return False, "Timeout (Pi pas joignable)", None
        except requests.exceptions.ConnectionError:
            return False, f"Pi pas joignable à {self.host}:{self.port}", None
        except Exception as e:
            return False, f"Erreur: {e}", None

    def get_status(self) -> tuple[bool, Optional[dict]]:
        """Get Pi current state (gear, mode, last byte, etc.)."""
        try:
            r = requests.get(f"{self.base_url}/status", timeout=self.timeout)
            if r.status_code == 200:
                return True, r.json()
            return False, None
        except Exception:
            return False, None
