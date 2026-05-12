"""Unit tests — boost mapping + lever decode + Motor_09 build."""
import pytest

from app.vw_signals import (
    BOOST_LEVERS, build_motor_09, decode_lever, is_boost_mode,
    map_mbar_to_motor09_byte, motor09_byte_to_temp_c, temp_c_to_motor09_byte,
)


# ---------------- Lever decode ----------------

@pytest.mark.parametrize("byte1,expected", [
    (0x10, "P"), (0x20, "R"), (0x30, "N"),
    (0x40, "D"), (0x50, "S"), (0x60, "M"),
    (0x14, "P"),  # high nibble only matters
    (0x55, "S"),
    (0x70, None), (0x00, None),
])
def test_decode_lever(byte1, expected):
    payload = bytes([0x00, byte1, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    assert decode_lever(payload) == expected


def test_decode_lever_short_payload():
    assert decode_lever(b"") is None
    assert decode_lever(b"\x00") is None


def test_boost_mode_set():
    assert BOOST_LEVERS == frozenset({"S", "M", "N"})
    for x in "SMN":
        assert is_boost_mode(x)
    for x in ("D", "P", "R", None):
        assert not is_boost_mode(x)


# ---------------- Coolant byte conversion ----------------

def test_temp_byte_round_trip():
    """Confirmed mapping: 0x80 ≈ 50°C, 0xED ≈ 130°C."""
    assert abs(motor09_byte_to_temp_c(0x80) - 50) < 1.5
    assert abs(motor09_byte_to_temp_c(0xED) - 130) < 1.5
    # Round-trip
    for c in (50, 75, 90, 110, 130):
        b = temp_c_to_motor09_byte(c)
        c2 = motor09_byte_to_temp_c(b)
        assert abs(c - c2) < 1.0


def test_temp_clamp():
    assert temp_c_to_motor09_byte(-1000) == 0
    assert temp_c_to_motor09_byte(10_000) == 255


# ---------------- Motor_09 frame build ----------------

def test_build_motor_09_format():
    f = build_motor_09(0xB6)
    assert f == bytes([0xB6, 0xFD, 0xFF, 0x7F, 0x00, 0x00, 0x00, 0xC1])
    assert len(f) == 8


def test_build_motor_09_clamp():
    assert build_motor_09(-5)[0] == 0
    assert build_motor_09(999)[0] == 255


# ---------------- MAP → byte mapping ----------------

def test_map_mapping_low():
    """At MAP_min, output should equal byte for temp_min."""
    b = map_mbar_to_motor09_byte(
        map_mbar=300, map_min_mbar=300, map_max_mbar=2500,
        temp_min_c=50, temp_max_c=130,
    )
    assert b == temp_c_to_motor09_byte(50)


def test_map_mapping_high():
    """At MAP_max, output should equal byte for temp_max."""
    b = map_mbar_to_motor09_byte(
        map_mbar=2500, map_min_mbar=300, map_max_mbar=2500,
        temp_min_c=50, temp_max_c=130,
    )
    assert b == temp_c_to_motor09_byte(130)


def test_map_mapping_mid():
    """At midpoint MAP, output should equal byte for midpoint temp."""
    b = map_mbar_to_motor09_byte(
        map_mbar=1400, map_min_mbar=300, map_max_mbar=2500,
        temp_min_c=50, temp_max_c=130,
    )
    expected = temp_c_to_motor09_byte(90)
    assert abs(b - expected) <= 1


def test_map_mapping_clamp_below():
    """MAP below min should clamp to temp_min."""
    b = map_mbar_to_motor09_byte(
        map_mbar=0, map_min_mbar=300, map_max_mbar=2500,
        temp_min_c=50, temp_max_c=130,
    )
    assert b == temp_c_to_motor09_byte(50)


def test_map_mapping_clamp_above():
    """MAP above max should clamp to temp_max."""
    b = map_mbar_to_motor09_byte(
        map_mbar=4000, map_min_mbar=300, map_max_mbar=2500,
        temp_min_c=50, temp_max_c=130,
    )
    assert b == temp_c_to_motor09_byte(130)


def test_map_mapping_offset():
    base = map_mbar_to_motor09_byte(
        map_mbar=1400, map_min_mbar=300, map_max_mbar=2500,
        temp_min_c=50, temp_max_c=130, offset_c=0,
    )
    plus10 = map_mbar_to_motor09_byte(
        map_mbar=1400, map_min_mbar=300, map_max_mbar=2500,
        temp_min_c=50, temp_max_c=130, offset_c=10,
    )
    # +10°C offset → byte should increase by ~14 (1°C ≈ 1.36 byte step)
    assert 12 <= (plus10 - base) <= 16


def test_map_mapping_div_zero_safe():
    b = map_mbar_to_motor09_byte(
        map_mbar=1000, map_min_mbar=500, map_max_mbar=500,
        temp_min_c=50, temp_max_c=130,
    )
    assert b == temp_c_to_motor09_byte(90)
