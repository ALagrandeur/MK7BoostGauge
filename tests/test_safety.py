"""Safety tests — airbag blocklist, CAN1 listen-only switch, config refusal."""
import pytest

from app.can_manager import CanManager


@pytest.fixture
def mgr():
    """A CanManager that doesn't actually open SocketCAN — for pure logic testing."""
    m = CanManager(
        cluster_iface="vcan0",
        can1_iface="vcan1",
        forbidden_ids={0x040, 0x572, 0x585},
        can1_listen_only=False,
    )
    # Don't open(); we test send() short-circuit before bus access
    return m


# ---------------------------------------------------------------------------
# Airbag blocklist (FORBIDDEN_IDS) — must block on EVERY channel
# ---------------------------------------------------------------------------

def test_airbag_blocked_on_cluster(mgr):
    ok = mgr.send("cluster", 0x040, b"\x00" * 8)
    assert ok is False
    assert mgr.blocked_forbidden_count == 1
    assert mgr.blocked_listen_only_count == 0


def test_airbag_blocked_on_can1(mgr):
    ok = mgr.send("can1", 0x572, b"\x00" * 8)
    assert ok is False
    assert mgr.blocked_forbidden_count == 1


def test_all_three_airbag_ids_blocked(mgr):
    for cid in (0x040, 0x572, 0x585):
        ok = mgr.send("cluster", cid, b"\x00" * 8)
        assert ok is False, f"Airbag id 0x{cid:X} not blocked!"
    assert mgr.blocked_forbidden_count == 3


def test_non_forbidden_id_not_blocked_by_airbag_gate(mgr):
    # 0x647 (Motor_09) is allowed — should not be airbag-blocked
    # (will fail because bus is None, but NOT for forbidden reason)
    mgr.send("cluster", 0x647, b"\x80\xFD\xFF\x7F\x00\x00\x00\xC1")
    assert mgr.blocked_forbidden_count == 0


# ---------------------------------------------------------------------------
# CAN1 listen-only switch — blocks ALL TX on can1 only, leaves cluster alone
# ---------------------------------------------------------------------------

def test_listen_only_blocks_can1_tx(mgr):
    mgr.set_can1_listen_only(True)
    ok = mgr.send("can1", 0x7E0, b"\x03\x22\x39\xC0\x00\x00\x00\x00")
    assert ok is False
    assert mgr.blocked_listen_only_count == 1


def test_listen_only_does_not_block_cluster_tx(mgr):
    mgr.set_can1_listen_only(True)
    # Try to TX Motor_09 on cluster — listen-only is can1-only, must NOT block
    mgr.send("cluster", 0x647, b"\x80\xFD\xFF\x7F\x00\x00\x00\xC1")
    assert mgr.blocked_listen_only_count == 0  # not counted


def test_listen_only_off_allows_can1(mgr):
    mgr.set_can1_listen_only(False)
    mgr.send("can1", 0x7E0, b"\x03\x22\x39\xC0\x00\x00\x00\x00")
    assert mgr.blocked_listen_only_count == 0


def test_listen_only_property_reflects_state(mgr):
    assert mgr.can1_listen_only is False
    mgr.set_can1_listen_only(True)
    assert mgr.can1_listen_only is True
    mgr.set_can1_listen_only(False)
    assert mgr.can1_listen_only is False


def test_listen_only_overrides_diagnostic_uds_attempt(mgr):
    """Even in 'diagnostic' (UDS-required) mode, listen-only must win."""
    mgr.set_can1_listen_only(True)
    # UDS query on can1 should be blocked
    blocked_before = mgr.blocked_listen_only_count
    mgr.send("can1", 0x7E0, b"\x03\x22\x39\xC0" + b"\x00" * 4)
    assert mgr.blocked_listen_only_count == blocked_before + 1


# ---------------------------------------------------------------------------
# Order of safety gates — airbag check before listen-only check
# ---------------------------------------------------------------------------

def test_airbag_id_on_can1_with_listen_only_counts_as_airbag(mgr):
    """If both gates would fire, airbag wins (more critical, counted first)."""
    mgr.set_can1_listen_only(True)
    ok = mgr.send("can1", 0x040, b"\x00" * 8)
    assert ok is False
    # Should be counted as forbidden, not as listen-only
    assert mgr.blocked_forbidden_count == 1
    assert mgr.blocked_listen_only_count == 0


# ---------------------------------------------------------------------------
# Total blocked counter is sum of both
# ---------------------------------------------------------------------------

def test_total_blocked_count(mgr):
    mgr.set_can1_listen_only(True)
    mgr.send("cluster", 0x040, b"\x00" * 8)   # airbag blocked
    mgr.send("can1",    0x572, b"\x00" * 8)   # airbag blocked
    mgr.send("can1",    0x7E0, b"\x00" * 8)   # listen-only blocked
    assert mgr.blocked_tx_count == 3
    assert mgr.blocked_forbidden_count == 2
    assert mgr.blocked_listen_only_count == 1
