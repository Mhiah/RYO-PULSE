"""Property-based checks of the classifier's core promises (needs hypothesis; skipped without it)."""

from datetime import timedelta

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from conftest import BASE, PROFILE  # noqa: E402
from ryo_pulse.engine import classify  # noqa: E402
from ryo_pulse.models import PulseRequest, PulseStatus, PulseThresholds, ScanProfile, Snapshot  # noqa: E402

SYMS = ["BTC", "ETH", "SOL", "PEPE"]
OTHER = ScanProfile(params={"limit": 99})

scan = st.one_of(
    st.none(),  # failed scan
    st.lists(st.sampled_from(SYMS), max_size=4, unique=True),
    st.tuples(st.just("drift"), st.lists(st.sampled_from(SYMS), max_size=4, unique=True)),
)
thresholds = st.builds(
    PulseThresholds,
    min_valid=st.integers(1, 6),
    min_coverage=st.floats(0, 1),
    persistent_ratio=st.floats(0.01, 1),
    persistent_min_hits=st.integers(1, 6),
)


def build(rows):
    out = []
    for i, r in enumerate(rows):
        at, sid = BASE + timedelta(hours=i), f"s{i:03d}"
        if r is None:
            out.append(Snapshot(snapshot_id=sid, captured_at=at, profile=PROFILE, ok=False, error="x"))
        elif isinstance(r, tuple):
            out.append(Snapshot(snapshot_id=sid, captured_at=at, profile=OTHER, tokens=r[1]))
        else:
            out.append(Snapshot(snapshot_id=sid, captured_at=at, profile=PROFILE, tokens=r))
    return out


@settings(max_examples=300, deadline=None)
@given(rows=st.lists(scan, min_size=1, max_size=12), th=thresholds, token=st.sampled_from(SYMS), data=st.data())
def test_classifier_invariants(rows, th, token, data):
    # pin the judged profile so drifted scans are well defined
    snaps = build(rows)
    req = PulseRequest(token=token, snapshots=snaps, profile_hash=PROFILE.hash, thresholds=th)
    r = classify(req)
    c = r.counts

    # counts partition the window
    assert c.valid + c.failed + c.drifted == c.total == len(snaps)
    assert 0 <= c.hits <= c.valid

    # deterministic, and order of input does not matter
    shuffled = data.draw(st.permutations(snaps))
    again = classify(PulseRequest(token=token, snapshots=shuffled, profile_hash=PROFILE.hash, thresholds=th))
    assert again.replay_hash == r.replay_hash and again.status == r.status

    # a failed or drifted scan is never evidence of absence
    if r.status == PulseStatus.ABSENT:
        assert c.hits == 0 and c.valid >= th.min_valid
    # appending a failed scan never turns a token into "absent" or changes hits
    failed = Snapshot(snapshot_id="zzz", captured_at=BASE + timedelta(days=1), profile=PROFILE, ok=False, error="x")
    worse = classify(PulseRequest(token=token, snapshots=[*snaps, failed], profile_hash=PROFILE.hash, thresholds=th))
    assert worse.counts.hits == c.hits
    if r.status != PulseStatus.ABSENT:
        assert worse.status != PulseStatus.ABSENT

    # persistent really means the thresholds were met
    if r.status == PulseStatus.PERSISTENT:
        assert c.hits >= th.persistent_min_hits and c.hits / c.valid >= th.persistent_ratio
    if r.status in (PulseStatus.EMERGING, PulseStatus.TRANSIENT, PulseStatus.PERSISTENT):
        assert c.hits > 0 and r.coverage >= th.min_coverage

    # any change to the evidence changes the replay hash
    if snaps and snaps[-1].ok:
        tampered = [*snaps[:-1], snaps[-1].model_copy(update={"tokens": [*snaps[-1].tokens, "ZZZ"]})]
        t = classify(PulseRequest(token=token, snapshots=tampered, profile_hash=PROFILE.hash, thresholds=th))
        assert t.replay_hash != r.replay_hash
