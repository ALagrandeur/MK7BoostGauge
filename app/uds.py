"""Minimal UDS (ISO 14229) helpers for OBD2 tool functionality.

Implements:
  - 0x22 ReadDataByIdentifier (single-frame request, single-frame response)
  - 0x19 ReadDTCInformation   (sub-function 0x02 = reportDTCByStatusMask)
  - 0x14 ClearDiagnosticInformation

Limitations:
  - Single-frame ISO-TP only on RX. If DTC list exceeds ~6 codes, response will
    be multi-frame (FirstFrame + ConsecutiveFrame) which this minimal implementation
    does NOT yet handle. TODO: add python-can-isotp dep when needed.
  - Synchronous request/response with timeout — caller blocks for ≤ timeout_s.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UDS service constants
# ---------------------------------------------------------------------------

SID_READ_DATA_BY_ID         = 0x22
SID_READ_DTC_INFORMATION    = 0x19
SID_CLEAR_DTC               = 0x14
SID_DIAGNOSTIC_SESSION      = 0x10

POSITIVE_RESPONSE_OFFSET    = 0x40   # response SID = request SID + 0x40
NEGATIVE_RESPONSE_SID       = 0x7F

# DTC status masks (ISO 14229-1)
DTC_STATUS_MASK_ALL         = 0xFF

# Engine ECU diagnostic IDs (Powertrain CAN AND Diagnostic CAN — gateway forwards)
ENGINE_REQ_ID  = 0x7E0
ENGINE_RESP_ID = 0x7E8

# Cluster diagnostic IDs (for completeness, not used by this tool currently)
CLUSTER_REQ_ID  = 0x714
CLUSTER_RESP_ID = 0x77E

# OBD-II functional broadcast (all ECUs may respond)
OBD2_BROADCAST  = 0x7DF


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DTC:
    """Diagnostic Trouble Code."""
    raw: int                      # 24-bit DTC code (high<<16 | mid<<8 | low)
    status: int                   # 8-bit status byte
    code: str                     # Human-readable ('P0123', 'B0420', etc.)

    def to_dict(self) -> dict:
        return {"raw": self.raw, "status": self.status,
                "status_hex": f"0x{self.status:02X}", "code": self.code}


def decode_dtc_to_pcode(raw_24: int) -> str:
    """Convert 24-bit DTC raw code to standard OBD-II format ('P0123', etc.).

    ISO 14229 / SAE J2012 24-bit DTC layout:
      byte 0 (high) : TYPE[2 bits] | DIGIT_1[2 bits] | DIGIT_2[4 bits]
      byte 1 (mid)  : DIGIT_3[4 bits] | DIGIT_4[4 bits]
      byte 2 (low)  : FAILURE_TYPE[8 bits]      (ignored in returned code string)

    Returns e.g. 'P0123', 'C1234', 'B0420', 'U0100' — the user-facing 4-digit code.
    """
    high = (raw_24 >> 16) & 0xFF
    mid  = (raw_24 >> 8) & 0xFF
    type_bits = (high >> 6) & 0x03
    type_char = "PCBU"[type_bits]
    digit1 = (high >> 4) & 0x03
    digit2 = high & 0x0F
    digit3 = (mid >> 4) & 0x0F
    digit4 = mid & 0x0F
    return f"{type_char}{digit1}{digit2:X}{digit3:X}{digit4:X}"


# ---------------------------------------------------------------------------
# UDS client (sync request/response)
# ---------------------------------------------------------------------------

class UdsClient:
    """Synchronous UDS client over a CanManager channel.

    Usage:
        client = UdsClient(can_manager, channel="can1",
                           req_id=0x7E0, resp_id=0x7E8)
        resp = client.read_data_by_identifier(0x39C0, timeout_s=0.5)
    """

    def __init__(self, can_manager, channel: str = "can1",
                 req_id: int = ENGINE_REQ_ID,
                 resp_id: int = ENGINE_RESP_ID) -> None:
        self.can = can_manager
        self.channel = channel
        self.req_id = req_id
        self.resp_id = resp_id

        # State machine for sync request/response
        self._pending: Optional[bytes] = None
        self._expected_resp_sid: Optional[int] = None  # filter so periodic queries don't clobber
        self._pending_event = threading.Event()
        self._lock = threading.Lock()
        # Serializes ALL requests so concurrent OBD2 buttons don't race
        self._request_lock = threading.Lock()

        # Register listener
        self.can.add_listener(channel, self._on_frame)

    # ------------------------------------------------------------------ rx hook

    def _on_frame(self, can_id: int, data: bytes, ts: float) -> None:
        """Receive any can1 frame. Filter to only the response for the in-flight request.

        Critical: this prevents the periodic UDS MAP query (response SID 0x62) from
        being received as the response to an OBD2 button click (e.g. ReadDTC SID 0x59).
        """
        if can_id != self.resp_id:
            return
        if len(data) < 2:
            return
        sid = data[1]
        with self._lock:
            expected = self._expected_resp_sid
            if expected is None:
                return  # no request in flight — ignore
            # Accept either: positive response SID match, OR negative response (0x7F)
            # (negative responses include the original SID at byte[2], we'll filter below)
            if sid != expected and sid != NEGATIVE_RESPONSE_SID:
                return  # someone else's response — ignore
            if sid == NEGATIVE_RESPONSE_SID:
                # Verify the NRC is for OUR service (data[2] = original SID)
                if len(data) < 3 or data[2] != (expected - POSITIVE_RESPONSE_OFFSET):
                    return
            self._pending = bytes(data)
            self._pending_event.set()

    # ------------------------------------------------------------------ low-level send/wait

    def _send_and_wait(self, payload: bytes, timeout_s: float = 0.5) -> Optional[bytes]:
        """Send a single-frame ISO-TP request and wait for the matching response.

        Serialized via _request_lock so concurrent UDS calls don't race.
        Filters responses by SID via _expected_resp_sid.
        """
        if len(payload) > 7:
            raise ValueError("payload too long for single-frame ISO-TP (max 7 bytes)")
        if len(payload) < 1:
            raise ValueError("empty payload")
        # ISO-TP single frame: byte[0] = length nibble, then payload, padded to 8
        frame = bytes([len(payload)]) + payload + bytes(7 - len(payload))
        # Expected positive response SID = request SID + 0x40
        expected_resp_sid = (payload[0] + POSITIVE_RESPONSE_OFFSET) & 0xFF

        # Serialize all UDS interactions on this client
        with self._request_lock:
            with self._lock:
                self._pending = None
                self._pending_event.clear()
                self._expected_resp_sid = expected_resp_sid

            if not self.can.send(self.channel, self.req_id, frame):
                log.warning("UDS send blocked (channel=%s, req_id=0x%X)", self.channel, self.req_id)
                with self._lock:
                    self._expected_resp_sid = None
                return None

            ok = self._pending_event.wait(timeout=timeout_s)
            with self._lock:
                self._expected_resp_sid = None
                if not ok:
                    log.warning("UDS timeout (req_id=0x%X, payload=%s)",
                                self.req_id, payload.hex())
                    return None
                return self._pending

    # ------------------------------------------------------------------ services

    def read_data_by_identifier(self, did: int, timeout_s: float = 0.5) -> Optional[bytes]:
        """0x22 ReadDataByIdentifier. Returns the data payload (after 0x62 + DID echo) or None."""
        req = bytes([SID_READ_DATA_BY_ID, (did >> 8) & 0xFF, did & 0xFF])
        resp = self._send_and_wait(req, timeout_s)
        if resp is None:
            return None
        # Response format: [len, 0x62, did_hi, did_lo, ...data, padding]
        if len(resp) < 4:
            return None
        if resp[1] == NEGATIVE_RESPONSE_SID:
            log.warning("UDS NRC for DID 0x%04X: 0x%02X", did, resp[3] if len(resp) > 3 else 0)
            return None
        if resp[1] != SID_READ_DATA_BY_ID + POSITIVE_RESPONSE_OFFSET:
            return None
        if (resp[2] << 8 | resp[3]) != did:
            return None
        length = resp[0]
        # data starts at index 4, length given by SF length nibble - 3 (SID + DID)
        data_len = max(0, length - 3)
        return resp[4:4 + data_len]

    def read_dtc_information(self, status_mask: int = DTC_STATUS_MASK_ALL,
                              timeout_s: float = 1.0) -> Optional[list[DTC]]:
        """0x19 ReadDTCInformation, sub-function 0x02 (reportDTCByStatusMask).

        Returns list of DTC objects, or None on timeout / NRC.
        WARNING: only handles single-frame responses (≤2 DTCs typically).
        """
        req = bytes([SID_READ_DTC_INFORMATION, 0x02, status_mask])
        resp = self._send_and_wait(req, timeout_s)
        if resp is None:
            return None
        if len(resp) < 4:
            return []
        if resp[1] == NEGATIVE_RESPONSE_SID:
            log.warning("UDS NRC for ReadDTC: 0x%02X", resp[3] if len(resp) > 3 else 0)
            return None
        if resp[1] != SID_READ_DTC_INFORMATION + POSITIVE_RESPONSE_OFFSET:
            return None
        # Format: [len, 0x59, 0x02, availability_mask, DTC_hi, DTC_mid, DTC_lo, status, ...]
        # Each DTC = 4 bytes (3 raw + 1 status)
        length = resp[0]
        body = resp[1:1 + length]   # full payload
        # Skip 0x59, 0x02, availability_mask = 3 bytes
        dtc_bytes = body[3:]
        dtcs: list[DTC] = []
        for i in range(0, len(dtc_bytes) - 3, 4):
            raw = (dtc_bytes[i] << 16) | (dtc_bytes[i + 1] << 8) | dtc_bytes[i + 2]
            if raw == 0:
                break  # padding zero
            status = dtc_bytes[i + 3]
            dtcs.append(DTC(raw=raw, status=status, code=decode_dtc_to_pcode(raw)))
        return dtcs

    def clear_diagnostic_information(self, group: int = 0xFFFFFF,
                                      timeout_s: float = 2.0) -> bool:
        """0x14 ClearDiagnosticInformation. group=0xFFFFFF = all DTCs."""
        req = bytes([SID_CLEAR_DTC,
                     (group >> 16) & 0xFF, (group >> 8) & 0xFF, group & 0xFF])
        resp = self._send_and_wait(req, timeout_s)
        if resp is None:
            return False
        if resp[1] == NEGATIVE_RESPONSE_SID:
            log.warning("UDS NRC for ClearDTC: 0x%02X", resp[3] if len(resp) > 3 else 0)
            return False
        return resp[1] == SID_CLEAR_DTC + POSITIVE_RESPONSE_OFFSET


# ---------------------------------------------------------------------------
# Convenience decoders for known VW DIDs
# ---------------------------------------------------------------------------

def decode_did_map_mbar(data: bytes) -> Optional[float]:
    """DID 0x39C0 — Saugrohrdruck (MAP) absolute, 2 bytes BE in mbar."""
    if len(data) < 2:
        return None
    return float((data[0] << 8) | data[1])


def decode_did_coolant_real_c(data: bytes) -> Optional[float]:
    """DID 0x202C — Kühlmitteltemperatur (real coolant), 2 bytes BE in 0.1°C."""
    if len(data) < 2:
        return None
    raw = (data[0] << 8) | data[1]
    return raw * 0.1
