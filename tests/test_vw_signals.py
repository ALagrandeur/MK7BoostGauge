"""Unit tests for pi/vw_signals.py (also used by PC if needed)."""
import sys
from pathlib import Path

# Add project root so we can import pi.vw_signals
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from pi.vw_signals import (
    BOOST_LEVERS, build_motor_09, decode_lever, decode_gear_digit,
    decode_lever_with_gear, is_boost_mode,
    map_mbar_to_motor09_byte, motor09_byte_to_temp_c, temp_c_to_motor09_byte,
    CLUSTER_DEAD_ZONE_LOW_C, CLUSTER_DEAD_ZONE_HIGH_C,
)


# ---------------- Lever decode ----------------

@pytest.mark.parametrize("byte1,expected", [
    (0x10, "P"), (0x20, "R"), (0x30, "N"),
    (0x40, "D"), (0x50, "S"), (0x60, "M"),
    (0x70, None), (0x00, None),
])
def test_decode_lever_high_nibble(byte1, expected):
    payload = bytes([0, byte1, 0, 0, 0, 0, 0, 0])
    assert decode_lever(payload) == expected


def test_decode_lever_short_payload():
    assert decode_lever(b"") is None
    assert decode_lever(b"\x00") is None


@pytest.mark.parametrize("byte1,byte3,expected", [
    (0x10, 0x00, "P"),     # P/R/N — no gear digit appended
    (0x20, 0x00, "R"),
    (0x30, 0x00, "N"),
    (0x40, 0x01, "D1"),    # D + gear digit
    (0x40, 0x06, "D6"),
    (0x50, 0x03, "S3"),    # S + gear digit (BOOST mode)
    (0x60, 0x02, "M2"),    # M + gear digit (BOOST mode)
    (0x50, 0x00, "S"),     # S with invalid digit -> just lever
    (0x60, 0x07, "M"),
])
def test_decode_lever_with_gear(byte1, byte3, expected):
    payload = bytes([0, byte1, 0, byte3, 0, 0, 0, 0])
    assert decode_lever_with_gear(payload) == expected


def test_is_boost_mode_accepts_combined_strings():
    for x in ("N", "S", "S1", "S2", "S6", "M", "M1", "M6"):
        assert is_boost_mode(x), f"{x} should be BOOST"
    for x in ("D", "D1", "D6", "P", "R", None, ""):
        assert not is_boost_mode(x), f"{x} should NOT be BOOST"


# ---------------- Motor_09 frame ----------------

def test_motor09_byte_roundtrip():
    """0x80 -> ~50C, 0xED -> ~130C."""
    assert abs(motor09_byte_to_temp_c(0x80) - 50) < 1.5
    assert abs(motor09_byte_to_temp_c(0xED) - 130) < 1.5
    for c in (50, 75, 90, 110, 130):
        b = temp_c_to_motor09_byte(c)
        c2 = motor09_byte_to_temp_c(b)
        assert abs(c - c2) < 1.0


def test_build_motor_09_format():
    f = build_motor_09(0xB6)
    assert f == bytes([0xB6, 0xFD, 0xFF, 0x7F, 0x00, 0x00, 0x00, 0xC1])
    assert len(f) == 8


# ---------------- MAP mapping with dead zone skip ----------------

def test_map_min_gives_temp_min():
    b = map_mbar_to_motor09_byte(300, 300, 2500)
    assert abs(motor09_byte_to_temp_c(b) - 50) < 1


def test_map_max_gives_temp_max():
    b = map_mbar_to_motor09_byte(2500, 300, 2500)
    assert abs(motor09_byte_to_temp_c(b) - 130) < 1


def test_dead_zone_NEVER_freezes_needle():
    """CRITICAL: sweep MAP, ensure NO byte produces cluster temp in dead zone."""
    bad = []
    for m in range(300, 2501, 5):
        b = map_mbar_to_motor09_byte(m, 300, 2500)
        t = motor09_byte_to_temp_c(b)
        if CLUSTER_DEAD_ZONE_LOW_C <= t <= CLUSTER_DEAD_ZONE_HIGH_C:
            bad.append((m, b, t))
    assert not bad, f"Dead zone hits: {bad[:3]}"


def test_dead_zone_jumps_cleanly():
    """At midpoint boundary, expect jump from below 80 to above 110."""
    # ratio=0.5, useful_pos=24, < bottom_len 29 -> temp ~74 (in safe zone below)
    b_below = map_mbar_to_motor09_byte(1500, 300, 2500)
    t_below = motor09_byte_to_temp_c(b_below)
    assert t_below < 80, f"At MAP=1500 expect below dead zone, got {t_below}"

    # Just above boundary -> jump above dead zone
    b_above = map_mbar_to_motor09_byte(1700, 300, 2500)
    t_above = motor09_byte_to_temp_c(b_above)
    assert t_above > 110, f"At MAP=1700 expect above dead zone, got {t_above}"
    # And the jump should be visible
    assert (t_above - t_below) > 25


def test_scale_offset_apply():
    """Scale * temp + offset should change the output predictably."""
    b1 = map_mbar_to_motor09_byte(300, 300, 2500, scale=1.0, offset_c=0)
    b2 = map_mbar_to_motor09_byte(300, 300, 2500, scale=1.0, offset_c=10)
    # +10C offset -> byte should increase by ~14 (1C = ~1.36 bytes)
    assert b2 > b1
    assert 12 <= (b2 - b1) <= 16
