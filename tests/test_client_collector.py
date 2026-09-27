import json

import httpx
import pytest

from ryo_pulse.client import RyoClient, RyoError
from ryo_pulse.collector import collect_once
from ryo_pulse.models import ScanProfile
from ryo_pulse.store import SnapshotStore

SCAN = {"data": {"candidates": [{"symbol": "btc"}, {"symbol": "SOL"}]}}
OVERVIEW = {"regime": "risk-on"}


def mcp_handler(fail_scan_times=0, sse=False, tool_error=False):
    state = {"scan_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        msg = json.loads(request.content)
        method = msg.get("method")
        if "id" not in msg:
            return httpx.Response(202)
        if method == "initialize":
            body = {"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}}
            return httpx.Response(200, json=body, headers={"Mcp-Session-Id": "sess-1"})
        assert request.headers.get("Mcp-Session-Id") == "sess-1"
        assert request.headers.get("Authorization") == "Bearer test-key"
        if method == "tools/list":
            result = {"tools": [{"name": "scan_market", "description": "scan"}]}
        else:
            name = msg["params"]["name"]
            if name == "scan_market":
                state["scan_calls"] += 1
                if state["scan_calls"] <= fail_scan_times:
                    return httpx.Response(503)
                payload = SCAN
            else:
                payload = OVERVIEW
            result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": tool_error}
        body = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        if sse:
            return httpx.Response(
                200, text=f"event: message\ndata: {json.dumps(body)}\n\n", headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json=body)

    return handler, state


def client(handler, **kw):
    return RyoClient(
        url="https://ryo.test/mcp", key="test-key", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw
    )


@pytest.mark.parametrize("sse", [False, True])
def test_call_tool_json_and_sse(sse):
    h, _ = mcp_handler(sse=sse)
    c = client(h)
    assert c.list_tools()[0]["name"] == "scan_market"
    assert c.call_tool("scan_market", {"limit": 10}) == SCAN


def test_retries_then_succeeds():
    h, state = mcp_handler(fail_scan_times=2)
    assert client(h).call_tool("scan_market") == SCAN
    assert state["scan_calls"] == 3


def test_gives_up_after_retries():
    h, _ = mcp_handler(fail_scan_times=99)
    with pytest.raises(RyoError):
        client(h, max_retries=2).call_tool("scan_market")


def test_collect_ok_with_regime(tmp_path):
    h, _ = mcp_handler()
    snap = collect_once(
        client(h), ScanProfile(params={"limit": 10}), SnapshotStore(tmp_path),
        tokens_path="data.candidates[].symbol", regime_path="regime",
    )
    assert snap.ok and snap.tokens == ["BTC", "SOL"]
    assert snap.context == {"regime": "risk-on"}
    assert snap.source == "live" and snap.raw_sha256
    assert SnapshotStore(tmp_path).load()[0] == snap


def test_collect_outage_is_recorded_as_failed_not_empty(tmp_path):
    h, _ = mcp_handler(fail_scan_times=99)
    snap = collect_once(
        client(h, max_retries=1), ScanProfile(), SnapshotStore(tmp_path), tokens_path="data.candidates[].symbol", regime_path=""
    )
    assert not snap.ok and snap.tokens == [] and "unreachable" in snap.error


def test_collect_wrong_path_is_failed(tmp_path):
    h, _ = mcp_handler()
    snap = collect_once(client(h), ScanProfile(), SnapshotStore(tmp_path), tokens_path="nope[].symbol", regime_path="")
    assert not snap.ok


def test_tool_error_flag(tmp_path):
    h, _ = mcp_handler(tool_error=True)
    with pytest.raises(RyoError):
        client(h).call_tool("scan_market")


def test_bad_config_fails_fast():
    h, _ = mcp_handler()
    with pytest.raises(RyoError, match="RYO_AUTH_HEADER"):
        RyoClient(url="https://ryo.test/mcp", key="k", auth_header="Authorizhttps://x/mcpation",
                  transport=httpx.MockTransport(h))
    with pytest.raises(RyoError, match="https://"):
        RyoClient(url="app-ryochan.com/api/mcp", key="k", transport=httpx.MockTransport(h))
