"""VW MQB CAN signal helpers — decode WBA_03 gear, build Motor_09, MQB CRC."""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Gear lever decode (WBA_03 / 0x394 byte 1 high nibble)
# ---------------------------------------------------------------------------
# Confirmed on 5G1 920 740B (Alltrack 2017) — high nibble of byte 1:
#   0x10 = P, 0x20 = R, 0x30 = N, 0x40 = D, 0x50 = S, 0x60 = M (manual/tip)
#
# In M mode the engaged gear digit is in byte 3 (low nibble): 1..6.
# We only need the LEVER position for our gear-mode logic, the gear digit is
# irrelevant.

LEVER_TABLE = {
    0x10: "P",
    0x20: "R",
    0x30: "N",
    0x40: "D",
    0x50: "S",
    0x60: "M",
}

# Boost mode triggers — gauge displays boost while lever is in any of these
BOOST_LEVERS = frozenset({"S", "M", "N"})


def decode_lever(wba03_payload: bytes) -> str | None:
    """Return 'P'/'R'/'N'/'D'/'S'/'M' from a WBA_03 (0x394) payload, or None if unknown."""
    if not wba03_payload or len(wba03_payload) < 2:
        return None
    high_nibble = wba03_payload[1] & 0xF0
    return LEVER_TABLE.get(high_nibble)


def is_boost_mode(lever: str | None) -> bool:
    return lever in BOOST_LEVERS


# ---------------------------------------------------------------------------
# Motor_09 (0x647) — coolant override frame
# ---------------------------------------------------------------------------
# Format confirmed on 5G1 920 740B AND validated against real vehicle log:
#   byte 0 = coolant value (linear: 0x80 = 50°C, 0xED = 130°C)
#   bytes 1-3 = magic 'FD FF 7F'
#   bytes 4-6 = 00
#   byte 7   = cycles 0x13 / 0x18 / 0xC1 / 0x12 on real ECU; static 0xC1 accepted by cluster
#
# No CRC, no counter on this frame. Cluster picks the most recent value at its
# poll rate. Real engine ECU only broadcasts at ~2 Hz, so spoofing at 25-50 Hz
# wins arbitration ~95% of the time.

MOTOR_09_ID = 0x647
_MOTOR_09_TAIL = bytes([0xFD, 0xFF, 0x7F, 0x00, 0x00, 0x00, 0xC1])

# Coolant byte 0 mapping bounds (from r00li, confirmed empirically):
COOLANT_BYTE_AT_50C = 0x80   # 128
COOLANT_BYTE_AT_130C = 0xED  # 237

# Linear formula for °C ↔ byte (validated against real warmup curve 0x86→0xB3):
#   temp_C = byte * 0.7339 - 43.94
#   byte   = (temp_C + 43.94) / 0.7339


def temp_c_to_motor09_byte(temp_c: float) -> int:
    raw = (temp_c + 43.94) / 0.7339
    return max(0, min(255, int(round(raw))))


def motor09_byte_to_temp_c(b: int) -> float:
    return b * 0.7339 - 43.94


def build_motor_09(coolant_byte: int) -> bytes:
    """Build the 8-byte Motor_09 (0x647) payload for the given coolant byte (0..255)."""
    coolant_byte = max(0, min(255, int(coolant_byte)))
    return bytes([coolant_byte]) + _MOTOR_09_TAIL


# ---------------------------------------------------------------------------
# Boost mapping — MAP (mbar) → coolant byte
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Powertrain CAN broadcast decoders (PCM listen mode)
# ---------------------------------------------------------------------------
# These IDs and offsets are PLACEHOLDERS. Pending user's Powertrain capture
# (10 sec moteur tournant + idle + accélération) to identify exact:
#   - MAP_PCM_BROADCAST_ID + byte offset + scaling
#   - REAL_COOLANT_PCM_BROADCAST_ID + byte offset + scaling
#   - HALDEX_DEMAND_PCM_BROADCAST_ID + byte offset + scaling
#
# Likely candidates from openDBC vw_mqb_2010.dbc:
#   Motor_05 (0x130) — engine status, possibly MAP
#   Motor_06 (0x288) — common MAP location
#   Motor_07 (0x640) — engine temperatures (intake, oil, coolant real)
#   Haldex_01 / AWD_01 — Haldex demand %
#
# Format below: (can_id, byte_offset, scale, offset, valid_range_min, valid_range_max)
# Set CAN_ID to None to mark as TBD — decoder returns None until configured.

MAP_PCM_DECODER = {
    "can_id": None,        # TBD — pending capture
    "byte": 0,
    "scale": 1.0,
    "offset": 0.0,
    "valid_min": 0,
    "valid_max": 4000,
    "unit": "mbar",
}

REAL_COOLANT_PCM_DECODER = {
    "can_id": None,        # TBD — pending capture (likely Motor_07 or Motor_xx byte for true coolant)
    "byte": 0,
    "scale": 0.75,
    "offset": -48.0,
    "valid_min": -40,
    "valid_max": 150,
    "unit": "°C",
}

HALDEX_DEMAND_PCM_DECODER = {
    "can_id": None,        # TBD — pending capture (likely Haldex_01 or AWD_xx)
    "byte": 0,
    "scale": 0.4,          # typical: byte 0..255 → 0..100%
    "offset": 0.0,
    "valid_min": 0,
    "valid_max": 100,
    "unit": "%",
}


def decode_pcm_broadcast(can_id: int, data: bytes, decoder: dict) -> Optional[float]:
    """Generic decoder for a PCM broadcast signal using a decoder dict."""
    target_id = decoder.get("can_id")
    if target_id is None or can_id != target_id:
        return None
    byte = decoder.get("byte", 0)
    if byte >= len(data):
        return None
    raw = data[byte]
    val = raw * decoder.get("scale", 1.0) + decoder.get("offset", 0.0)
    if val < decoder.get("valid_min", -1e9) or val > decoder.get("valid_max", 1e9):
        return None
    return val


# ---------------------------------------------------------------------------
# Mapping function (CAN0 cluster Motor_09)
# ---------------------------------------------------------------------------

def map_mbar_to_motor09_byte(
    map_mbar: float,
    map_min_mbar: float,
    map_max_mbar: float,
    temp_min_c: float,
    temp_max_c: float,
    scale: float = 1.0,
    offset_c: float = 0.0,
    formula: str = "linear",
) -> int:
    """
    Map a MAP pressure (mbar) into a Motor_09 byte 0 value via configurable bounds.

    Interpolation between (map_min → temp_min) and (map_max → temp_max),
    then apply scale & offset, then convert °C to byte.

    Formula choices:
      'linear'      : straight line interpolation (default)
      'exp'         : exponential — gauge ramps faster as MAP goes up (drama)
      'sqrt'        : square-root — gauge moves more at low MAP (sensitivity)

    Clamped to byte range 0..255.
    """
    if map_max_mbar == map_min_mbar:
        temp_c = (temp_min_c + temp_max_c) / 2
    else:
        ratio = (map_mbar - map_min_mbar) / (map_max_mbar - map_min_mbar)
        ratio = max(0.0, min(1.0, ratio))
        if formula == "exp":
            ratio = ratio * ratio          # 0..1 → curved more at top
        elif formula == "sqrt":
            ratio = ratio ** 0.5           # 0..1 → curved more at bottom
        # else "linear" (and unknown values fall back to linear)
        temp_c = temp_min_c + ratio * (temp_max_c - temp_min_c)

    temp_c = temp_c * scale + offset_c
    return temp_c_to_motor09_byte(temp_c)
