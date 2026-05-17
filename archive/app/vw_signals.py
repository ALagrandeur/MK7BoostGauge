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


def decode_gear_digit(wba03_payload: bytes) -> Optional[int]:
    """Return engaged gear digit 1..6 from WBA_03 byte 3 low nibble, or None.

    Only meaningful in S/M/D mode. In P/R/N the digit is typically 0.
    """
    if not wba03_payload or len(wba03_payload) < 4:
        return None
    digit = wba03_payload[3] & 0x0F
    if 1 <= digit <= 6:
        return digit
    return None


def decode_lever_with_gear(wba03_payload: bytes) -> str | None:
    """Combined decode: returns 'S3', 'M5', 'D2', 'P', 'R', 'N' or None."""
    lever = decode_lever(wba03_payload)
    if lever is None:
        return None
    digit = decode_gear_digit(wba03_payload)
    if digit is not None and lever in ("S", "M", "D"):
        return f"{lever}{digit}"
    return lever


def is_boost_mode(lever: str | None) -> bool:
    """True if lever indicates boost gauge active. Accepts 'S', 'S3', 'M', 'M2', 'N', etc."""
    if not lever:
        return False
    return lever[0] in BOOST_LEVERS


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

# ---------------------------------------------------------------------------
# CRITICAL: Cluster dead zone (gauge damping)
# ---------------------------------------------------------------------------
# Empirically confirmed on 5G1 920 740B (Alltrack 2017): the MQB cluster applies
# a "neutral zone" on the temperature gauge between ~80°C and ~110°C — the
# needle stays planted at the center "90°C" mark for ANY real temperature in
# that range, regardless of the value sent.
#
# For the boost gauge use case, this means a naive linear MAP→°C mapping that
# spans the dead zone (e.g. 50→130°C) will cause the needle to FREEZE in the
# middle of the MAP range. Map MAP to (50→80°C) ∪ (110→130°C) instead, skipping
# the dead zone entirely → continuous needle motion across full MAP range.
#
# Reference: docs/mqb_can_ids.md "Zone neutre du cluster (gauge damping)"

CLUSTER_DEAD_ZONE_LOW_C = 80.0
CLUSTER_DEAD_ZONE_HIGH_C = 110.0


def map_mbar_to_motor09_byte(
    map_mbar: float,
    map_min_mbar: float,
    map_max_mbar: float,
    temp_min_c: float,
    temp_max_c: float,
    scale: float = 1.0,
    offset_c: float = 0.0,
    formula: str = "linear",
    skip_dead_zone: bool = True,
    dead_zone_low_c: float = CLUSTER_DEAD_ZONE_LOW_C,
    dead_zone_high_c: float = CLUSTER_DEAD_ZONE_HIGH_C,
) -> int:
    """
    Map a MAP pressure (mbar) into a Motor_09 byte 0 value via configurable bounds.

    Steps:
      1. Compute ratio (0..1) of MAP within [map_min, map_max], clamped.
      2. Apply formula curve (linear / exp / sqrt) to the ratio.
      3. If skip_dead_zone AND the [temp_min, temp_max] range spans the cluster
         dead zone [dead_zone_low_c, dead_zone_high_c], split the useful needle
         range into 2 segments [temp_min, dead_low] and [dead_high, temp_max],
         then map ratio across the combined useful length. The needle "jumps"
         past the dead zone without stalling.
      4. Apply scale + offset_c.
      5. Convert °C → byte via temp_c_to_motor09_byte (clamped 0..255).

    Formula choices:
      'linear' : straight line (default)
      'exp'    : ratio² → gauge ramps faster at high MAP (drama)
      'sqrt'   : √ratio → gauge moves more at low MAP (sensitivity)
    """
    # --- step 1+2: ratio in [0,1] with curve
    if map_max_mbar == map_min_mbar:
        ratio = 0.5
    else:
        ratio = (map_mbar - map_min_mbar) / (map_max_mbar - map_min_mbar)
        ratio = max(0.0, min(1.0, ratio))
        if formula == "exp":
            ratio = ratio * ratio
        elif formula == "sqrt":
            ratio = ratio ** 0.5

    # --- step 3: skip dead zone if enabled and applicable
    spans_dead_zone = (
        skip_dead_zone
        and temp_min_c < dead_zone_low_c
        and temp_max_c > dead_zone_high_c
    )

    if spans_dead_zone:
        # Useful length = bottom segment + top segment (skip dead zone middle).
        # SAFE_MARGIN: rounding from temp_C → byte → cluster_displayed_temp_C
        # has ~0.18°C error per byte (formula step ≈ 0.7339°C). Without margin,
        # boundary values land *inside* the dead zone by ~0.1°C → needle frozen.
        # 1°C margin shifts both segments safely outside the dead zone.
        SAFE_MARGIN = 1.0
        safe_low  = dead_zone_low_c  - SAFE_MARGIN   # max temp in bottom segment
        safe_high = dead_zone_high_c + SAFE_MARGIN   # min temp in top segment
        len_bottom = safe_low - temp_min_c
        len_top    = temp_max_c - safe_high
        total      = len_bottom + len_top
        if total <= 0:
            temp_c = (temp_min_c + temp_max_c) / 2
        else:
            useful_pos = ratio * total
            if useful_pos <= len_bottom:
                temp_c = temp_min_c + useful_pos
            else:
                temp_c = safe_high + (useful_pos - len_bottom)
    else:
        # Plain linear (or curved) mapping across full [temp_min, temp_max]
        temp_c = temp_min_c + ratio * (temp_max_c - temp_min_c)

    # --- step 4 + 5: scale, offset, convert
    temp_c = temp_c * scale + offset_c
    return temp_c_to_motor09_byte(temp_c)
