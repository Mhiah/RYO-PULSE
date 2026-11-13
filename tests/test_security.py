"""Regression tests for the 2026-11 audit: public endpoint limits, browser headers, client hardening."""

import base64
import hashlib
import importlib.util
import json
import re
import threading
import urllib.request
from pathlib import Path

import httpx
import pytest

from ryo_pulse.client import RyoClient, RyoError
from ryo_pulse.collector import collect_once
from ryo_pulse.dashboard import MAX_WATCH, make_handler, serve
from ryo_pulse.models import ScanProfile
from ryo_pulse.store import SnapshotStore, load_fixture

ROOT = Path(__file__).parent.parent


def _start(httpd):
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def server(demo_path):
    httpd = serve(lambda: load_fixture(demo_path), port=0)
    yield _start(httpd)
    httpd.shutdown()
    httpd.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url) as r:  # noqa: S310 (local test server)
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


# -- F-01: unbounded watchlist on the public /api/board ------------------------------------
def test_watchlist_is_capped(server):
    ok = ",".join(f"T{i}" for i in range(MAX_WATCH))
    code, _, body = get(server + "/api/board?watch=" + ok)
    assert code == 200 and sum(r["watched"] for r in json.loads(body)["rows"]) == MAX_WATCH
    too_many = ",".join(f"T{i}" for i in range(MAX_WATCH + 1))
    code, _, body = get(server + "/api/board?watch=" + too_many)
    assert code == 400 and "at most" in json.loads(body)["error"]
    code, _, body = get(server + "/api/board?watch=" + "A" * 33)
    assert code == 400


@pytest.mark.parametrize(
    "query",
    ["min_coverage=nan", "min_coverage=inf", "persistent_ratio=nan", "persistent_ratio=0", "min_valid=0",
     "min_valid=-1", "window=0", "window=-5", "window=1.5", "persistent_min_hits=x"],
)
def test_bad_settings_fail_safe(server, query):
    code, headers, body = get(server + "/api/board?" + query)
    assert code == 400 and headers["Content-Type"] == "application/json"
    json.loads(body)  # still valid JSON (no NaN leaking out)


# -- F-02: browser security headers ----------------------------------------------------------
def test_security_headers_and_csp_match_inline_blocks(server):
    for path in ("/", "/board", "/static/theme.css", "/api/board", "/nope"):
        _, h, _ = get(server + path)
        assert h["X-Frame-Options"] == "DENY"
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["Referrer-Policy"] == "no-referrer"
        csp = h["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp and "default-src 'none'" in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    for path in ("/", "/board"):
        _, h, body = get(server + path)
        csp = h["Content-Security-Policy"]
        blocks = re.findall(rb"<(script|style)>(.*?)</\1>", body, re.S)
        assert len(blocks) == 2
        for _, block in blocks:
            digest = base64.b64encode(hashlib.sha256(block).digest()).decode()
            assert f"'sha256-{digest}'" in csp


# -- F-03: hosted error responses do not echo internals --------------------------------------
def test_hosted_error_hides_details():
    def broken():
        raise ValueError("/var/task/hosted/snapshots/secret-path.json: bad")

    for hosted, leaks in ((True, False), (False, True)):
        httpd = serve(broken, port=0)
        httpd.RequestHandlerClass = make_handler(broken, hosted=hosted)
        url = _start(httpd)
        code, _, body = get(url + "/api/board")
        httpd.shutdown()
        httpd.server_close()
        assert code == 500
        assert ("secret-path" in body.decode()) is leaks


# -- F-04: key must not travel over plain http -----------------------------------------------
def test_plain_http_url_rejected_except_localhost():
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    with pytest.raises(RyoError, match="https://"):
        RyoClient(url="http://app-ryochan.com/api/mcp", key="k", transport=t)
    with pytest.raises(RyoError, match="https://"):
        RyoClient(url="http://localhost.evil.com/mcp", key="k", transport=t)
    RyoClient(url="http://127.0.0.1:9000/mcp", key="k", transport=t)
    RyoClient(url="http://localhost:9000/mcp", key="k", transport=t)


# -- F-05: a malformed upstream answer must be a failed scan, not a crashed collector -------
def _client(handler):
    return RyoClient(url="https://ryo.test/mcp", key="k", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def _init_then(answer):
    def handler(request):
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        return answer(msg)

    return handler


@pytest.mark.parametrize(
    "answer",
    [
        lambda m: httpx.Response(200, text="<html>captive portal</html>", headers={"content-type": "text/html"}),
        lambda m: httpx.Response(200, json=[1, 2, 3]),
        lambda m: httpx.Response(200, json={"jsonrpc": "2.0", "id": m["id"], "result": ["not", "a", "dict"]}),
        lambda m: httpx.Response(200, text="data: [1]\n\n", headers={"content-type": "text/event-stream"}),
    ],
    ids=["html-200", "json-list", "result-list", "sse-list"],
)
def test_malformed_response_is_recorded_as_failed_scan(tmp_path, answer):
    c = _client(_init_then(answer))
    with pytest.raises(RyoError):
        c.call_tool("scan_market")
    snap = collect_once(_client(_init_then(answer)), ScanProfile(), SnapshotStore(tmp_path),
                        tokens_path="data.candidates[].symbol", regime_path="")
    assert not snap.ok and snap.tokens == []
    assert SnapshotStore(tmp_path).load()[0].ok is False


def test_decoding_error_is_retried_then_reported():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.DecodingError("bad gzip", request=request)

    c = RyoClient(url="https://ryo.test/mcp", key="k", transport=httpx.MockTransport(handler),
                  sleep=lambda s: None, max_retries=2)
    with pytest.raises(RyoError, match="unreachable"):
        c.call_tool("scan_market")
    assert calls["n"] == 3


# -- Hosted entry point (api/index.py) as Vercel calls it ------------------------------------
@pytest.fixture
def hosted_server():
    spec = importlib.util.spec_from_file_location("vercel_index", ROOT / "api" / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mod.handler)
    yield _start(httpd)
    httpd.shutdown()
    httpd.server_close()


def test_hosted_entry_serves_saved_real_scans(hosted_server):
    code, h, body = get(hosted_server + "/api/index?route=board")
    assert code == 200 and b"What changed since the last scan" in body and "Content-Security-Policy" in h
    code, _, body = get(hosted_server + "/api/index?route=api/board&watch=BTC")
    d = json.loads(body)
    assert code == 200 and d["hosted"] is True and d["provenance"] == "live"
    assert d["scans"]["total"] == 114 and d["rows"][0]["token"] == "BTC"
    code, _, _ = get(hosted_server + "/api/index?route=../../api/index.py")
    assert code == 404
    code, _, body = get(hosted_server + "/api/index?route=api/board&watch=" + ",".join(["X"] * 51))
    assert code == 400
