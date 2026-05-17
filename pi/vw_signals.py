"""VW MQB CAN signal helpers - lever decode, Motor_09 build, MAP->byte mapping with dead zone skip.

Used by Pi daemon + (mirrored on PC for UI preview).
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Gear lever decode (WBA_03 / 0x394 byte 1 high nibble)
# ---------------------------------------------------------------------------

LEVER_TABLE = {
    0x10: "P", 0x20: "R", 0x30: "N",
    0x40: "D", 0x50: "S", 0x60: "M",
}

# Boost mode triggers - gauge displays boost while lever is in any of these
BOOST_LEVERS = frozenset({"S", "M", "N"})


def decode_lever(wba03_payload: bytes) -> Optional[str]:
    if not wba03_payload or len(wba03_payload) < 2:
        return None
    return LEVER_TABLE.get(wba03_payload[1] & 0xF0)


def decode_gear_digit(wba03_payload: bytes) -> Optional[int]:
    if not wba03_payload or len(wba03_payload) < 4:
        return None
    digit = wba03_payload[3] & 0x0F
    return digit if 1 <= digit <= 6 else None


def decode_lever_with_gear(wba03_payload: bytes) -> Optional[str]:
    lever = decode_lever(wba03_payload)
    if lever is None:
        return None
    digit = decode_gear_digit(wba03_payload)
    if digit is not None and lever in ("S", "M", "D"):
        return f"{lever}{digit}"
    return lever


def is_boost_mode(lever: Optional[str]) -> bool:
    if not lever:
        return False
    return lever[0] in BOOST_LEVERS


# ---------------------------------------------------------------------------
# Motor_09 (0x647) - cluster coolant override
# ---------------------------------------------------------------------------
# Format confirmed on 5G1 920 740B + real-vehicle log:
#   byte 0   = coolant value (linear: 0x80=50C, 0xED=130C)
#   bytes 1-3 = magic 'FD FF 7F'
#   bytes 4-6 = 00
#   byte 7   = 0xC1 (static accepted by cluster)
# No CRC, no counter.

MOTOR_09_ID = 0x647
_MOTOR_09_TAIL = bytes([0xFD, 0xFF, 0x7F, 0x00, 0x00, 0x00, 0xC1])

# Formula validated empirically: temp_C = byte * 0.7339 - 43.94
def temp_c_to_motor09_byte(temp_c: float) -> int:
    raw = (temp_c + 43.94) / 0.7339
    return max(0, min(255, int(round(raw))))


def motor09_byte_to_temp_c(b: int) -> float:
    return b * 0.7339 - 43.94


def build_motor_09(coolant_byte: int) -> bytes:
    coolant_byte = max(0, min(255, int(coolant_byte)))
    return bytes([coolant_byte]) + _MOTOR_09_TAIL


# ---------------------------------------------------------------------------
# CRITICAL: Cluster dead zone (gauge damping)
# ---------------------------------------------------------------------------
# MK7 cluster needle stays planted at center for ANY real temp in [80, 110]C.
# Without skip, midpoint MAP -> ~90C -> needle freezes mid-range.
# With skip: mapping splits useful range into [50-80] + [110-130], jumping
# past the dead zone. SAFE_MARGIN=1.0 ensures round-trip rounding doesn't
# land us back in the dead zone.

CLUSTER_DEAD_ZONE_LOW_C = 80.0
CLUSTER_DEAD_ZONE_HIGH_C = 110.0
DEAD_ZONE_SAFE_MARGIN = 1.0


def map_mbar_to_motor09_byte(
    map_mbar: float,
    map_min_mbar: float,
    map_max_mbar: float,
    scale: float = 1.0,
    offset_c: float = 0.0,
    # Internal constants (not user-configurable in new architecture)
    temp_min_c: float = 50.0,
    temp_max_c: float = 130.0,
) -> int:
    """Map MAP pressure (mbar) -> Motor_09 byte 0.

    Always uses linear mapping + dead zone skip (no formula choice, no
    user-configurable dead zone bounds in this version - hardcoded internally
    based on empirical MK7 cluster characteristics).
    """
    if map_max_mbar == map_min_mbar:
        ratio = 0.5
    else:
        ratio = (map_mbar - map_min_mbar) / (map_max_mbar - map_min_mbar)
        ratio = max(0.0, min(1.0, ratio))

    # Dead zone skip with safe margin
    safe_low = CLUSTER_DEAD_ZONE_LOW_C - DEAD_ZONE_SAFE_MARGIN
    safe_high = CLUSTER_DEAD_ZONE_HIGH_C + DEAD_ZONE_SAFE_MARGIN
    len_bottom = safe_low - temp_min_c
    len_top = temp_max_c - safe_high
    total = len_bottom + len_top

    if total <= 0:
        # Degenerate config — fall back to mid
        temp_c = (temp_min_c + temp_max_c) / 2
    else:
        useful_pos = ratio * total
        if useful_pos <= len_bottom:
            temp_c = temp_min_c + useful_pos
        else:
            temp_c = safe_high + (useful_pos - len_bottom)

    temp_c = temp_c * scale + offset_c
    return temp_c_to_motor09_byte(temp_c)
