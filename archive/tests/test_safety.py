"""Safety tests — airbag blocklist, CAN1 mode-based TX blocking, listen-only switch."""
import pytest

from app.can_manager import CanManager


@pytest.fixture
def mgr_diag():
    """CanManager in Diagnostic mode (TX allowed unless listen-only)."""
    m = CanManager(
        cluster_iface="vcan0",
        can1_iface="vcan1",
        forbidden_ids={0x040, 0x572, 0x585},
        can1_listen_only=False,
        can1_mode="diagnostic",
    )
    return m


@pytest.fixture
def mgr_pcm():
    """CanManager in PCM mode (TX always blocked on can1 by design)."""
    m = CanManager(
        cluster_iface="vcan0",
        can1_iface="vcan1",
        forbidden_ids={0x040, 0x572, 0x585},
        can1_listen_only=False,
        can1_mode="pcm",
    )
    return m


# ---------------------------------------------------------------------------
# Airbag blocklist (forbidden IDs) — must block on EVERY channel, EVERY mode
# ---------------------------------------------------------------------------

def test_airbag_blocked_on_cluster_diag(mgr_diag):
    assert mgr_diag.send("cluster", 0x040, b"\x00" * 8) is False
    assert mgr_diag.blocked_forbidden_count == 1


def test_airbag_blocked_on_cluster_pcm(mgr_pcm):
    assert mgr_pcm.send("cluster", 0x040, b"\x00" * 8) is False
    assert mgr_pcm.blocked_forbidden_count == 1


def test_all_airbag_ids_blocked(mgr_diag):
    for cid in (0x040, 0x572, 0x585):
        assert mgr_diag.send("cluster", cid, b"\x00" * 8) is False
    assert mgr_diag.blocked_forbidden_count == 3


# ---------------------------------------------------------------------------
# CAN1 mode = PCM → ALL TX blocked on can1, regardless of listen-only
# ---------------------------------------------------------------------------

def test_pcm_mode_blocks_all_can1_tx(mgr_pcm):
    blocked, reason = mgr_pcm.is_can1_tx_blocked()
    assert blocked is True
    assert reason == "pcm_mode"
    assert mgr_pcm.send("can1", 0x7E0, b"\x00" * 8) is False
    assert mgr_pcm.blocked_pcm_mode_count == 1


def test_pcm_mode_does_not_block_cluster(mgr_pcm):
    mgr_pcm.send("cluster", 0x647, b"\x80\xFD\xFF\x7F\x00\x00\x00\xC1")
    assert mgr_pcm.blocked_pcm_mode_count == 0


def test_pcm_mode_wins_over_listen_only_disarmed(mgr_pcm):
    """Even with listen-only disarmed, PCM mode keeps TX blocked."""
    mgr_pcm.set_can1_listen_only(False)
    assert mgr_pcm.send("can1", 0x7E0, b"\x00" * 8) is False
    assert mgr_pcm.blocked_pcm_mode_count == 1


# ---------------------------------------------------------------------------
# CAN1 mode = Diagnostic → TX allowed unless listen-only armed
# ---------------------------------------------------------------------------

def test_diagnostic_mode_listen_only_off_allows_can1(mgr_diag):
    blocked, _ = mgr_diag.is_can1_tx_blocked()
    assert blocked is False
    # send() will fail because no bus open, but NOT due to safety gate
    mgr_diag.send("can1", 0x7E0, b"\x00" * 8)
    assert mgr_diag.blocked_pcm_mode_count == 0
    assert mgr_diag.blocked_listen_only_count == 0


def test_diagnostic_mode_listen_only_on_blocks_can1(mgr_diag):
    mgr_diag.set_can1_listen_only(True)
    blocked, reason = mgr_diag.is_can1_tx_blocked()
    assert blocked is True
    assert reason == "listen_only"
    assert mgr_diag.send("can1", 0x7E0, b"\x00" * 8) is False
    assert mgr_diag.blocked_listen_only_count == 1


# ---------------------------------------------------------------------------
# Mode change hot-update
# ---------------------------------------------------------------------------

def test_set_can1_mode_changes_behavior(mgr_diag):
    # Start in diagnostic — TX allowed
    blocked, _ = mgr_diag.is_can1_tx_blocked()
    assert blocked is False
    # Switch to PCM
    mgr_diag.set_can1_mode("pcm")
    blocked, reason = mgr_diag.is_can1_tx_blocked()
    assert blocked is True
    assert reason == "pcm_mode"
    # Switch back
    mgr_diag.set_can1_mode("diagnostic")
    blocked, _ = mgr_diag.is_can1_tx_blocked()
    assert blocked is False


# ---------------------------------------------------------------------------
# Airbag takes priority over PCM mode counter
# ---------------------------------------------------------------------------

def test_airbag_id_in_pcm_mode_counts_as_airbag(mgr_pcm):
    """If both gates would fire, airbag wins (counted as forbidden)."""
    assert mgr_pcm.send("can1", 0x040, b"\x00" * 8) is False
    assert mgr_pcm.blocked_forbidden_count == 1
    assert mgr_pcm.blocked_pcm_mode_count == 0


# ---------------------------------------------------------------------------
# Total blocked counter sums all gates
# ---------------------------------------------------------------------------

def test_total_blocked_count_multi_gate(mgr_pcm):
    mgr_pcm.send("cluster", 0x040, b"\x00" * 8)   # airbag
    mgr_pcm.send("can1",    0x572, b"\x00" * 8)   # airbag
    mgr_pcm.send("can1",    0x7E0, b"\x00" * 8)   # pcm_mode
    assert mgr_pcm.blocked_tx_count == 3
    assert mgr_pcm.blocked_forbidden_count == 2
    assert mgr_pcm.blocked_pcm_mode_count == 1
