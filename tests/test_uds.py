"""UDS tests — DTC code decoding + DID payload decoders."""
import pytest

from app.uds import decode_did_coolant_real_c, decode_did_map_mbar, decode_dtc_to_pcode


# ---------------------------------------------------------------------------
# DTC code decoding (24-bit raw → 'P0123' string)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # ISO 14229 24-bit DTC: high byte 0xAB → type=(A>>6), digit1=(A>>4)&3, digit2=B
    (0x010123, "P0101"),       # 01 01 23 → type=0(P), d1=0, d2=1, d3=0, d4=1
    (0x123456, "P1234"),       # 12 34 56 → type=0(P), d1=1, d2=2, d3=3, d4=4
    (0x420100, "C0201"),       # 42 01 00 → type=1(C), d1=0, d2=2, d3=0, d4=1
    (0x804200, "B0042"),       # 80 42 00 → type=2(B), d1=0, d2=0, d3=4, d4=2
    (0xC10000, "U0100"),       # C1 00 00 → type=3(U), d1=0, d2=1, d3=0, d4=0
])
def test_decode_dtc_code_format(raw, expected):
    assert decode_dtc_to_pcode(raw) == expected


def test_decode_dtc_zero():
    """Zero DTC → P0000 (padding sentinel)."""
    assert decode_dtc_to_pcode(0x000000) == "P0000"


def test_decode_dtc_all_types():
    """All 4 type letters reachable."""
    assert decode_dtc_to_pcode(0x000000)[0] == "P"
    assert decode_dtc_to_pcode(0x400000)[0] == "C"
    assert decode_dtc_to_pcode(0x800000)[0] == "B"
    assert decode_dtc_to_pcode(0xC00000)[0] == "U"


# ---------------------------------------------------------------------------
# DID 0x39C0 — MAP (mbar) decoder
# ---------------------------------------------------------------------------

def test_decode_did_map_mbar():
    # 0x03B6 = 950 mbar
    assert decode_did_map_mbar(b"\x03\xB6") == 950.0
    # 0x0000 = 0 mbar
    assert decode_did_map_mbar(b"\x00\x00") == 0.0
    # 0x09C4 = 2500 mbar
    assert decode_did_map_mbar(b"\x09\xC4") == 2500.0


def test_decode_did_map_short_payload():
    assert decode_did_map_mbar(b"") is None
    assert decode_did_map_mbar(b"\x01") is None


# ---------------------------------------------------------------------------
# DID 0x202C — real coolant temp (0.1 °C) decoder
# ---------------------------------------------------------------------------

def test_decode_did_coolant_real_c():
    # 0x03B1 = 945 = 94.5 °C
    assert abs(decode_did_coolant_real_c(b"\x03\xB1") - 94.5) < 0.01
    # 0x0000 = 0.0 °C
    assert decode_did_coolant_real_c(b"\x00\x00") == 0.0
    # 0x07D0 = 2000 = 200.0 °C (impossible, but decoder doesn't care)
    assert decode_did_coolant_real_c(b"\x07\xD0") == 200.0


def test_decode_did_coolant_short_payload():
    assert decode_did_coolant_real_c(b"") is None
    assert decode_did_coolant_real_c(b"\x01") is None
