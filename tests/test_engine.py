import random

import pytest

from conftest import PROFILE, make
from ryo_pulse import PulseRequest, PulseStatus, PulseThresholds, ScanProfile, classify


def run(token, rows, **kw):
    return classify(PulseRequest(token=token, snapshots=make(rows), **kw))


def test_persistent():
    r = run("btc", [["BTC"], ["BTC", "ETH"], ["ETH"], ["BTC"], ["BTC"]])
    assert r.status is PulseStatus.PERSISTENT
    assert r.token == "BTC"
    assert (r.counts.hits, r.counts.valid) == (4, 5)
    assert r.current_streak == 2
    assert r.best_rank == 1


def test_emerging_single_recent_hit_is_signal_not_noise():
    r = run("PEPE", [["BTC"], ["BTC"], ["BTC"], ["BTC", "PEPE"]])
    assert r.status is PulseStatus.EMERGING
    assert r.first_seen == r.last_seen


def test_transient_faded():
    r = run("WIF", [["WIF"], ["WIF"], ["BTC"], ["BTC"], ["BTC"]])
    assert r.status is PulseStatus.TRANSIENT
    assert any(x.code == "FADED" for x in r.reasons)


def test_transient_sporadic_old_and_new():
    r = run("X", [["X"], ["A"], ["A"], ["A"], ["A"], ["X"]])
    assert r.status is PulseStatus.TRANSIENT
    assert any(x.code == "SPORADIC" for x in r.reasons)


def test_absent():
    r = run("DOGE", [["BTC"], ["ETH"], ["SOL"]])
    assert r.status is PulseStatus.ABSENT
    assert r.first_seen is None and r.best_rank is None


def test_insufficient_too_few_scans_even_if_seen():
    r = run("BTC", [["BTC"], ["BTC"]])
    assert r.status is PulseStatus.INSUFFICIENT
    assert r.counts.hits == 2


def test_failed_scans_are_not_absence():
    # 3 good scans with the token + 3 failures: coverage 50% -> insufficient, never absent/transient
    r = run("BTC", [["BTC"], None, ["BTC"], None, ["BTC"], None])
    assert r.status is PulseStatus.INSUFFICIENT
    assert r.counts.failed == 3
    assert any(x.code == "LOW_COVERAGE" for x in r.reasons)


def test_failed_scan_does_not_break_streak_or_count_as_miss():
    r = run("BTC", [["BTC"], ["BTC"], ["BTC"], ["BTC"], None])
    assert r.status is PulseStatus.PERSISTENT
    assert r.current_streak == 4
    assert r.coverage == 0.8


def test_drifted_profile_excluded():
    other = ScanProfile(params={"limit": 10, "theme": "ai"})
    r = run("FET", [["BTC"], (other, ["FET"]), ["BTC"], ["BTC"], ["BTC"]])
    assert r.status is PulseStatus.ABSENT
    assert r.counts.drifted == 1
    assert r.profile_hash == PROFILE.hash


def test_explicit_profile_hash():
    other = ScanProfile(params={"theme": "ai"})
    rows = [(other, ["FET"]), (other, ["FET"]), (other, ["FET"]), ["BTC"]]
    r = run("FET", rows, profile_hash=other.hash)
    assert r.status is PulseStatus.PERSISTENT


def test_all_failed():
    r = run("BTC", [None, None])
    assert r.status is PulseStatus.INSUFFICIENT
    assert r.profile_hash is None
    assert r.provenance == "none"


def test_empty():
    r = run("BTC", [])
    assert r.status is PulseStatus.INSUFFICIENT
    assert r.coverage == 0.0


def test_thresholds_are_respected():
    rows = [["A"], ["A"], ["B"], ["B"], ["B"]]
    assert run("A", rows).status is PulseStatus.TRANSIENT
    loose = PulseThresholds(persistent_ratio=0.4, persistent_min_hits=2)
    assert run("A", rows, thresholds=loose).status is PulseStatus.PERSISTENT


def test_order_independent_and_replay_hash_stable():
    rows = [["BTC"], ["ETH", "BTC"], None, ["BTC"], ["SOL"]]
    snaps = make(rows)
    a = classify(PulseRequest(token="BTC", snapshots=snaps))
    shuffled = snaps[:]
    random.Random(7).shuffle(shuffled)
    b = classify(PulseRequest(token="BTC", snapshots=shuffled))
    assert a.model_dump() == b.model_dump()
    assert len(a.replay_hash) == 64


def test_replay_hash_changes_with_input():
    a = run("BTC", [["BTC"], ["BTC"], ["BTC"]])
    b = run("BTC", [["BTC"], ["BTC"], ["BTC", "ETH"]])
    assert a.status == b.status
    assert a.replay_hash != b.replay_hash


def test_regime_context_and_change_reason():
    snaps = make([["BTC"], ["BTC"], ["BTC"]])
    snaps[0] = snaps[0].model_copy(update={"context": {"regime": "neutral"}})
    snaps[2] = snaps[2].model_copy(update={"context": {"regime": "risk-on"}})
    r = classify(PulseRequest(token="BTC", snapshots=snaps))
    assert r.context == {"regime_at_window_start": "neutral", "regime_at_window_end": "risk-on"}
    assert any(x.code == "REGIME_CHANGED" for x in r.reasons)


def test_failed_snapshot_cannot_carry_tokens():
    from ryo_pulse import Snapshot

    with pytest.raises(ValueError):
        Snapshot(snapshot_id="x", captured_at=make([["A"]])[0].captured_at, profile=PROFILE, ok=False, tokens=["A"])
