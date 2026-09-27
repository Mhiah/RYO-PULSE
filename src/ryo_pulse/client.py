"""Minimal MCP (streamable HTTP, JSON-RPC 2.0) client for the RYO research tools.

Only protocol-level fields are assumed here (initialize, tools/list, tools/call). Everything
about a tool's *output* shape is left to the adapter, which reads only confirmed paths.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any

import httpx

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
PROTOCOL_VERSION = "2025-06-18"


class RyoError(RuntimeError):
    pass


class RyoClient:
    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
        auth_header: str | None = None,
        auth_scheme: str | None = None,
        timeout: float = 45.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.url = url or os.environ.get("RYO_MCP_URL", "")
        if not self.url:
            raise RyoError("RYO_MCP_URL is not set (see .env.example)")
        key = key if key is not None else os.environ.get("RYO_MCP_KEY", "")
        if not self.url.startswith(("https://", "http://")):
            raise RyoError(f"RYO_MCP_URL must start with https:// (got '{self.url[:40]}')")
        header = (auth_header or os.environ.get("RYO_AUTH_HEADER") or "Authorization").strip()
        if not _HEADER_NAME.match(header):
            raise RyoError(f"RYO_AUTH_HEADER is not a valid header name: '{header[:40]}' (usually: Authorization)")
        scheme = auth_scheme if auth_scheme is not None else os.environ.get("RYO_AUTH_SCHEME", "Bearer")
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if key:
            headers[header] = f"{scheme} {key}".strip() if scheme else key
        self._http = httpx.Client(timeout=timeout, headers=headers, transport=transport)
        self._max_retries = max_retries
        self._sleep = sleep
        self._session_id: str | None = None
        self._next_id = 0
        self._initialized = False

    # -- protocol -------------------------------------------------------------------------
    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else {}
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.post(self.url, json=payload, headers=headers)
            except httpx.LocalProtocolError as exc:  # our request is malformed; retrying won't help
                raise RyoError(f"invalid request to RYO: {exc}") from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = exc
            else:
                if resp.status_code not in RETRY_STATUS:
                    if resp.status_code >= 400:
                        raise RyoError(f"HTTP {resp.status_code} from RYO: {resp.text[:200]}")
                    return resp
                last = RyoError(f"HTTP {resp.status_code} from RYO")
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit() and attempt < self._max_retries:
                    self._sleep(min(float(retry_after), 60.0))
                    continue
            if attempt < self._max_retries:
                self._sleep(min(2**attempt, 30) + random.uniform(0, 0.5))
        raise RyoError(f"RYO unreachable after {self._max_retries + 1} attempts: {last}")

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        resp = self._post(payload)
        if sid := resp.headers.get("Mcp-Session-Id"):
            self._session_id = sid
        msg = _parse_rpc(resp, self._next_id)
        if "error" in msg:
            raise RyoError(f"{method} failed: {msg['error']}")
        return msg.get("result")

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ryo-pulse", "version": "0.1.0"},
            },
        )
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else {}
        try:
            self._http.post(
                self.url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers
            )
        except httpx.HTTPError:
            pass  # notification is best-effort
        self._initialized = True

    # -- public ---------------------------------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_init()
        return (self._rpc("tools/list") or {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Returns the tool's payload: structuredContent if present, else JSON-decoded text content."""
        self._ensure_init()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}) or {}
        if result.get("isError"):
            raise RyoError(f"{name} returned an error: {_text_of(result)[:300]}")
        if "structuredContent" in result:
            return result["structuredContent"]
        text = _text_of(result)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"text": text}

    def close(self) -> None:
        self._http.close()


def _text_of(result: dict[str, Any]) -> str:
    return "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def _parse_rpc(resp: httpx.Response, want_id: int) -> dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == want_id:
                    return msg
        raise RyoError("no JSON-RPC response in event stream")
    return resp.json()
