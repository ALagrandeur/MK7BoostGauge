"""Unit tests — boost mapping + lever decode + Motor_09 build."""
import pytest

from app.vw_signals import (
    BOOST_LEVERS, build_motor_09, decode_lever, decode_gear_digit,
    decode_lever_with_gear, is_boost_mode,
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


# ---------------- Gear digit decode + combined lever+gear ----------------

@pytest.mark.parametrize("byte3_low_nibble,expected", [
    (0x01, 1), (0x02, 2), (0x03, 3), (0x04, 4), (0x05, 5), (0x06, 6),
    (0x00, None), (0x07, None), (0x0F, None),
])
def test_decode_gear_digit(byte3_low_nibble, expected):
    payload = bytes([0, 0x50, 0, byte3_low_nibble, 0, 0, 0, 0])
    assert decode_gear_digit(payload) == expected


def test_decode_gear_digit_short_payload():
    assert decode_gear_digit(b"") is None
    assert decode_gear_digit(b"\x00\x00\x00") is None  # < 4 bytes


@pytest.mark.parametrize("byte1,byte3,expected", [
    (0x10, 0x00, "P"),     # P → no digit appended
    (0x20, 0x00, "R"),
    (0x30, 0x00, "N"),
    (0x40, 0x01, "D1"),    # D + gear digit
    (0x40, 0x06, "D6"),
    (0x50, 0x03, "S3"),    # S + gear digit
    (0x50, 0x05, "S5"),
    (0x60, 0x02, "M2"),    # M + gear digit
    (0x60, 0x06, "M6"),
    (0x50, 0x00, "S"),     # S without digit (when invalid digit)
    (0x60, 0x07, "M"),     # M with invalid digit → just lever
])
def test_decode_lever_with_gear(byte1, byte3, expected):
    payload = bytes([0, byte1, 0, byte3, 0, 0, 0, 0])
    assert decode_lever_with_gear(payload) == expected


def test_decode_lever_with_gear_unknown_lever():
    payload = bytes([0, 0x70, 0, 0x01, 0, 0, 0, 0])  # 0x70 = unknown
    assert decode_lever_with_gear(payload) is None


def test_is_boost_mode_with_combined_strings():
    """is_boost_mode should accept combined lever+gear strings like 'S3', 'M5'."""
    # All BOOST cases — N, S1-S6, M1-M6
    for x in ("N", "S", "S1", "S2", "S3", "S4", "S5", "S6",
              "M", "M1", "M2", "M3", "M4", "M5", "M6"):
        assert is_boost_mode(x), f"{x} should be BOOST"
    # All TEMP cases — P, R, D1-D6
    for x in ("P", "R", "D", "D1", "D2", "D3", "D4", "D5", "D6"):
        assert not is_boost_mode(x), f"{x} should NOT be BOOST"
    # None
    assert not is_boost_mode(None)
    assert not is_boost_mode("")


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


# ---------------- Formula choice ----------------

def test_formula_linear_default_matches_explicit():
    args = dict(map_mbar=1400, map_min_mbar=300, map_max_mbar=2500,
                temp_min_c=50, temp_max_c=130)
    b_default = map_mbar_to_motor09_byte(**args)
    b_linear  = map_mbar_to_motor09_byte(**args, formula="linear")
    assert b_default == b_linear


def test_formula_exp_at_top_matches_linear_at_top():
    """At MAP_max, all formulas should converge to temp_max byte."""
    args = dict(map_mbar=2500, map_min_mbar=300, map_max_mbar=2500,
                temp_min_c=50, temp_max_c=130)
    b_lin = map_mbar_to_motor09_byte(**args, formula="linear")
    b_exp = map_mbar_to_motor09_byte(**args, formula="exp")
    b_sqrt = map_mbar_to_motor09_byte(**args, formula="sqrt")
    assert b_lin == b_exp == b_sqrt


def test_formula_exp_lower_than_linear_at_midpoint():
    """Exp curve: at MAP midpoint, output should be LOWER than linear (still ramping)."""
    args = dict(map_mbar=1400, map_min_mbar=300, map_max_mbar=2500,
                temp_min_c=50, temp_max_c=130)
    b_lin = map_mbar_to_motor09_byte(**args, formula="linear")
    b_exp = map_mbar_to_motor09_byte(**args, formula="exp")
    assert b_exp < b_lin


def test_formula_sqrt_higher_than_linear_at_midpoint():
    """Sqrt curve: at MAP midpoint, output should be HIGHER than linear."""
    args = dict(map_mbar=1400, map_min_mbar=300, map_max_mbar=2500,
                temp_min_c=50, temp_max_c=130)
    b_lin = map_mbar_to_motor09_byte(**args, formula="linear")
    b_sqrt = map_mbar_to_motor09_byte(**args, formula="sqrt")
    assert b_sqrt > b_lin


def test_unknown_formula_falls_back_to_linear():
    args = dict(map_mbar=1400, map_min_mbar=300, map_max_mbar=2500,
                temp_min_c=50, temp_max_c=130)
    b_lin = map_mbar_to_motor09_byte(**args, formula="linear")
    b_garbage = map_mbar_to_motor09_byte(**args, formula="nonexistent")
    assert b_lin == b_garbage
