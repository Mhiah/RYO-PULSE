import json
import threading
import urllib.request
from datetime import timedelta

import pytest

from conftest import BASE, PROFILE, make
from ryo_pulse.dashboard import build_board, serve
from ryo_pulse.models import PulseThresholds, ScanProfile, Snapshot
from ryo_pulse.store import load_fixture


def test_board_from_fixture(demo_path):
    b = build_board(load_fixture(demo_path))
    status = {r["token"]: r["status"] for r in b["rows"]}
    assert status == {"BTC": "persistent", "SOL": "persistent", "PEPE": "emerging", "ETH": "transient", "WIF": "transient"}
    # most important first: emerging before persistent before transient
    assert [r["status"] for r in b["rows"]][:3] == ["emerging", "persistent", "persistent"]
    assert b["provenance"] == "fixture"
    assert b["scans"] == {
        "total": 8, "valid": 6, "failed": 1, "drifted": 1, "coverage": 0.75,
        "latest_at": "2026-09-28T07:00:00Z", "latest_ok": True, "latest_error": None, "regime": "risk-on",
    }
    assert [t["state"] for t in b["timeline"]].count("failed") == 1
    why = {r["token"]: r["why"] for r in b["rows"]}
    assert why["BTC"].startswith("seen in 6/6") and why["ETH"].startswith("seen 3/6, missing")


def test_changes_since_last_scan():
    snaps = make([["BTC"], ["BTC"], ["BTC"], ["BTC", "PEPE"]])
    b = build_board(snaps)
    assert b["changes"] == [
        {"token": "PEPE", "from": "absent", "to": "emerging", "why": "first seen in scan 4/4 and present in the newest"}
    ]
    assert b["entered"] == ["PEPE"] and b["dropped"] == []


def test_failed_latest_scan_is_flagged_not_absence():
    snaps = make([["BTC"], ["BTC"], ["BTC"], None])
    b = build_board(snaps)
    assert b["scans"]["latest_ok"] is False and b["scans"]["latest_error"] == "boom"
    assert b["rows"][0]["status"] == "persistent"
    assert b["changes"] == []


def test_watchlist_pinned_and_window():
    snaps = make([["A"], ["A"], ["B"], ["B"], ["B"]])
    b = build_board(snaps, watchlist=["doge"], window=3)
    assert b["rows"][0]["token"] == "DOGE" and b["rows"][0]["watched"] and b["rows"][0]["status"] == "absent"
    assert b["scans"]["total"] == 3
    assert {r["token"] for r in b["rows"]} == {"DOGE", "B"}  # A is outside the 3-scan window


def test_thresholds_flow_through():
    snaps = make([["A"], ["A"], ["B"], ["B"], ["B"]])
    loose = build_board(snaps, PulseThresholds(persistent_ratio=0.4, persistent_min_hits=2))
    assert {r["token"]: r["status"] for r in loose["rows"]}["A"] == "persistent"


def test_empty_store():
    b = build_board([])
    assert b["rows"] == [] and b["scans"]["total"] == 0 and b["profile_hash"] is None


@pytest.fixture
def server(demo_path):
    httpd = serve(lambda: load_fixture(demo_path), port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def test_http_page_and_api(server):
    code, ctype, body = get(server + "/")
    assert code == 200 and "text/html" in ctype and b"Repeated scans" in body and b'href="/board"' in body
    code, ctype, body = get(server + "/board")
    assert code == 200 and "text/html" in ctype and b"What changed since the last scan" in body
    code, ctype, body = get(server + "/static/theme.css")
    assert code == 200 and "text/css" in ctype and b"#c5ff4a" in body
    code, ctype, _ = get(server + "/static/theme.js")
    assert code == 200 and "javascript" in ctype
    assert get(server + "/static/../dashboard.py")[0] == 404
    code, _, body = get(server + "/api/board?window=8&persistent_ratio=0.6&watch=INJ")
    data = json.loads(body)
    assert code == 200 and data["rows"][0]["token"] == "INJ"
    code, _, body = get(server + "/api/board?min_valid=abc")
    assert code == 400 and "bad setting" in json.loads(body)["error"]
    assert get(server + "/nope")[0] == 404
