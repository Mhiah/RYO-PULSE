import json

import pytest

from conftest import make
from ryo_pulse import PulseRequest, ScanProfile, classify
from ryo_pulse.paths import PathError, extract, suggest
from ryo_pulse.store import SnapshotStore, load_fixture


def test_profile_hash_normalizes():
    a = ScanProfile(params={"Theme": " AI ", "limit": 10, "x": None})
    b = ScanProfile(params={"limit": 10, "theme": "ai"})
    assert a.hash == b.hash
    assert ScanProfile(params={"theme": "memes", "limit": 10}).hash != b.hash
    assert ScanProfile(tool="other", params=b.params).hash != b.hash


def test_store_roundtrip(tmp_path):
    store = SnapshotStore(tmp_path)
    snaps = make([["BTC"], None, ["ETH"]])
    for s in snaps:
        store.save(s, raw={"scan": {"ok": s.ok}})
    loaded = store.load()
    assert [s.snapshot_id for s in loaded] == [s.snapshot_id for s in snaps]
    assert store.profiles() == [snaps[0].profile.hash]
    assert len(list(tmp_path.rglob("*.raw.json"))) == 3


def test_fixture_is_labelled(demo_path):
    snaps = load_fixture(demo_path)
    assert all(s.source == "fixture" for s in snaps)
    r = classify(PulseRequest(token="BTC", snapshots=snaps))
    assert r.provenance == "fixture"
    assert any(x.code == "FIXTURE_DATA" for x in r.reasons)


def test_extract_paths():
    data = {"data": {"candidates": [{"symbol": "BTC"}, {"symbol": "ETH"}]}, "regime": "risk-on"}
    assert extract(data, "data.candidates[].symbol") == ["BTC", "ETH"]
    assert extract(data, "regime") == ["risk-on"]
    assert extract({"items": ["A", "B"]}, "items[]") == ["A", "B"]
    with pytest.raises(PathError):
        extract(data, "data.missing[].symbol")
    with pytest.raises(PathError):
        extract(data, "regime[]")


def test_suggest_paths():
    data = {"data": {"candidates": [{"symbol": "BTC", "score": 1}]}}
    assert "data.candidates[].symbol" in suggest(data)
