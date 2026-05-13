"""UDS tests — DTC decoding + DID payload decoders + UdsClient race-safety."""
import threading
import time
import pytest

from app.uds import (
    decode_did_coolant_real_c, decode_did_map_mbar, decode_dtc_to_pcode,
    UdsClient, NEGATIVE_RESPONSE_SID, POSITIVE_RESPONSE_OFFSET,
)


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


# ---------------------------------------------------------------------------
# UdsClient race-condition / SID-filter / serialization tests
# ---------------------------------------------------------------------------
# These use a fake CanManager that records send() calls and lets us inject
# arbitrary RX frames via the registered listener.

class FakeCanManager:
    """Minimal CanManager stub: records sends, dispatches injected RX to listeners."""
    def __init__(self):
        self.sent = []
        self.listeners = {"can1": [], "cluster": []}

    def add_listener(self, channel, cb):
        self.listeners[channel].append(cb)

    def send(self, channel, can_id, data, extended=False):
        self.sent.append((channel, can_id, bytes(data)))
        return True

    def inject_rx(self, channel, can_id, data, ts=0.0):
        """Test helper: simulate a frame arriving on a channel."""
        for cb in self.listeners[channel]:
            cb(can_id, bytes(data), ts)


def _build_pos_resp(req_sid, payload_after_sid=b""):
    """Build a fake ISO-TP single-frame positive response."""
    body = bytes([req_sid + POSITIVE_RESPONSE_OFFSET]) + payload_after_sid
    frame = bytes([len(body)]) + body
    return frame.ljust(8, b"\x00")


def test_uds_filter_ignores_unrelated_response():
    """If a frame arrives with a different SID than what we requested, ignore it.

    Critical race: periodic MAP query (resp SID 0x62) must NOT be returned as
    the response to a DTC query (resp SID 0x59).
    """
    can = FakeCanManager()
    client = UdsClient(can, channel="can1", req_id=0x7E0, resp_id=0x7E8)

    # Inject an UNRELATED MAP response BEFORE we send anything → must be ignored
    can.inject_rx("can1", 0x7E8, _build_pos_resp(0x22, b"\x39\xC0\x03\xB6"))
    # No request in flight, _expected_resp_sid is None → drop

    # Now send a DTC request
    received = {}
    def caller():
        received["resp"] = client.read_dtc_information(timeout_s=0.5)

    th = threading.Thread(target=caller, daemon=True)
    th.start()
    time.sleep(0.05)  # let client send + start waiting

    # Inject the WRONG response (MAP) — must be ignored
    can.inject_rx("can1", 0x7E8, _build_pos_resp(0x22, b"\x39\xC0\x03\xB6"))
    time.sleep(0.05)

    # Inject the CORRECT response (DTC, SID 0x19+0x40 = 0x59)
    # Format: 0x59 0x02 0xFF + DTC_h DTC_m DTC_l status
    dtc_resp = bytes([0x59, 0x02, 0xFF, 0x01, 0x23, 0x45, 0xA0])
    can.inject_rx("can1", 0x7E8, bytes([len(dtc_resp)]) + dtc_resp + b"\x00")

    th.join(timeout=1.0)
    assert "resp" in received, "UdsClient never returned"
    assert received["resp"] is not None, "UdsClient timed out — filter rejected good frame"
    assert len(received["resp"]) == 1
    assert received["resp"][0].code.startswith("P")  # P-code from 0x012345


def test_uds_negative_response_for_our_service_accepted():
    """A negative response (NRC) for OUR service should be returned, not ignored."""
    can = FakeCanManager()
    client = UdsClient(can, channel="can1", req_id=0x7E0, resp_id=0x7E8)

    received = {}
    def caller():
        received["resp"] = client.read_data_by_identifier(0x39C0, timeout_s=0.5)

    th = threading.Thread(target=caller, daemon=True)
    th.start()
    time.sleep(0.05)

    # Inject NRC: 0x7F 0x22 0x31 (requestOutOfRange) for our SID 0x22
    nrc = bytes([0x7F, 0x22, 0x31])
    can.inject_rx("can1", 0x7E8, bytes([len(nrc)]) + nrc + b"\x00\x00\x00\x00")

    th.join(timeout=1.0)
    assert "resp" in received
    # NRC returns None from read_data_by_identifier (logged as warning)
    assert received["resp"] is None


def test_uds_negative_response_for_OTHER_service_ignored():
    """A NRC for a different service must be ignored (not consumed as our reply)."""
    can = FakeCanManager()
    client = UdsClient(can, channel="can1", req_id=0x7E0, resp_id=0x7E8)

    received = {}
    def caller():
        # We send DID query (SID 0x22)
        received["resp"] = client.read_data_by_identifier(0x39C0, timeout_s=0.3)

    th = threading.Thread(target=caller, daemon=True)
    th.start()
    time.sleep(0.05)

    # Inject NRC for SID 0x19 (DTC) — NOT our request → must be ignored
    nrc_other = bytes([0x7F, 0x19, 0x31])
    can.inject_rx("can1", 0x7E8, bytes([len(nrc_other)]) + nrc_other + b"\x00\x00\x00\x00")

    th.join(timeout=1.0)
    assert received["resp"] is None  # timed out (no real response came)


def test_uds_serialization_lock():
    """Two concurrent calls must serialize, not race on _pending."""
    can = FakeCanManager()
    client = UdsClient(can, channel="can1", req_id=0x7E0, resp_id=0x7E8)

    results = {}
    barrier = threading.Barrier(2)

    def caller_a():
        barrier.wait()
        results["a"] = client.read_data_by_identifier(0x39C0, timeout_s=0.5)

    def caller_b():
        barrier.wait()
        results["b"] = client.read_data_by_identifier(0x202C, timeout_s=0.5)

    th_a = threading.Thread(target=caller_a, daemon=True)
    th_b = threading.Thread(target=caller_b, daemon=True)
    th_a.start(); th_b.start()
    time.sleep(0.1)  # both threads start, one acquires lock

    # Inject responses: first for whoever sent first (we don't know order without
    # inspecting can.sent), so respond to BOTH DIDs.
    # The FakeCanManager records sends in order — use that to know which to respond first.
    time.sleep(0.05)  # let first request go out
    if can.sent:
        first_req = can.sent[0]
        # First request's payload[1:3] = SID + first byte of DID; we know it's 0x22 SID
        did_first = (first_req[2][2] << 8) | first_req[2][3]
        if did_first == 0x39C0:
            resp = _build_pos_resp(0x22, b"\x39\xC0\x03\xB6")
        else:
            resp = _build_pos_resp(0x22, b"\x20\x2C\x03\xE8")
        can.inject_rx("can1", 0x7E8, resp)

    time.sleep(0.1)  # let second request go out (after first releases lock)
    if len(can.sent) >= 2:
        second_req = can.sent[1]
        did_second = (second_req[2][2] << 8) | second_req[2][3]
        if did_second == 0x39C0:
            resp = _build_pos_resp(0x22, b"\x39\xC0\x03\xB6")
        else:
            resp = _build_pos_resp(0x22, b"\x20\x2C\x03\xE8")
        can.inject_rx("can1", 0x7E8, resp)

    th_a.join(timeout=1.5); th_b.join(timeout=1.5)
    # Both should have completed (not deadlocked, not None)
    assert "a" in results and "b" in results
    # Both calls should have produced sends (serialized, not dropped)
    assert len(can.sent) == 2
