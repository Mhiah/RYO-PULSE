"""Take one live scan_market snapshot (plus optional market_overview context) and store it.

A failed call is stored as ok=False, never as an empty token list: Pulse must not read an
outage as absence. RYO marks degraded answers in-band (e.g. "status": "unavailable" with an
empty candidate list), so those are failures too, and so is an empty ranked list.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .canonical import sha256_hex
from .client import RyoClient, RyoError
from .models import ScanProfile, Snapshot
from .paths import PathError, extract
from .store import SnapshotStore

DEFAULT_FAIL_STATUSES = "unavailable,error,failed"
_REGIME_KEYS = ("regime", "market_regime", "regime_label", "market_state")
_LABEL_KEYS = ("label", "name", "state", "value", "classification")


def _label(v: object) -> str | None:
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, dict):
        for k in _LABEL_KEYS:
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
    return None


def find_regime(overview: object) -> str | None:
    """Find the market regime label in a market_overview answer without a configured path.

    Breadth-first, so a top-level `regime` wins over a nested one; falls back to the
    Fear & Greed classification when no regime field exists.
    """
    queue, greed = [overview], None
    while queue:
        node = queue.pop(0)
        if isinstance(node, list):
            queue.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        for k, v in node.items():
            key = str(k).lower()
            if key in _REGIME_KEYS and (lab := _label(v)):
                return lab
            if greed is None and "fear" in key and "greed" in key and isinstance(v, dict):
                greed = _label({k2: v[k2] for k2 in ("classification", "label", "value_classification") if k2 in v})
            if isinstance(v, (dict, list)):
                queue.append(v)
    return greed


def _scan_problem(scan: object, tokens: list[str]) -> str | None:
    """Why this scan must not count as an observation, or None if it is usable."""
    status_path = os.environ.get("RYO_SCAN_STATUS_PATH", "status")
    fail = {v.strip().lower() for v in os.environ.get("RYO_SCAN_FAIL_STATUSES", DEFAULT_FAIL_STATUSES).split(",")}
    if status_path:
        try:
            status = extract(scan, status_path)
        except PathError:
            status = []
        if status and str(status[0]).strip().lower() in fail:
            warnings = scan.get("warnings") if isinstance(scan, dict) else None
            detail = f": {warnings[0]}" if isinstance(warnings, list) and warnings else ""
            return f"RYO reported status '{status[0]}'{detail}"
    if not tokens and os.environ.get("PULSE_ALLOW_EMPTY_SCANS", "") not in ("1", "true", "yes"):
        return "scan returned no ranked candidates"
    return None


def collect_once(
    client: RyoClient,
    profile: ScanProfile,
    store: SnapshotStore,
    tokens_path: str | None = None,
    regime_path: str | None = None,
    now: datetime | None = None,
) -> Snapshot:
    tokens_path = tokens_path or os.environ.get("RYO_SCAN_TOKENS_PATH", "")
    if regime_path is None:  # unset or empty in .env means auto-detect; "off" disables the call
        regime_path = os.environ.get("RYO_OVERVIEW_REGIME_PATH", "").strip() or "auto"
    if regime_path.lower() == "off":
        regime_path = ""
    if not tokens_path:
        raise RyoError("RYO_SCAN_TOKENS_PATH is not set; run `ryo-pulse discover` first")

    at = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    snap_id = f"{at:%Y%m%dT%H%M%SZ}-{profile.hash[:6]}"
    raw: dict = {}
    context: dict[str, str] = {}

    try:
        scan = client.call_tool(profile.tool, profile.params)
        raw["scan"] = scan
        tokens = [str(x) for x in extract(scan, tokens_path) if x is not None]
        problem = _scan_problem(scan, tokens)
        if problem:
            raise RyoError(problem)
    except (RyoError, PathError) as exc:
        snap = Snapshot(snapshot_id=snap_id, captured_at=at, profile=profile, ok=False, error=str(exc)[:300])
        store.save(snap, raw or None)
        return snap

    if regime_path:
        try:
            overview = client.call_tool("market_overview", {})
            raw["market_overview"] = overview
            if regime_path.lower() == "auto":
                found = find_regime(overview)
                if found:
                    context["regime"] = found
            else:
                vals = extract(overview, regime_path)
                if vals and vals[0] is not None:
                    context["regime"] = str(vals[0])
        except (RyoError, PathError) as exc:
            context["regime_error"] = str(exc)[:120]  # context is optional; the scan still counts

    snap = Snapshot(
        snapshot_id=snap_id,
        captured_at=at,
        profile=profile,
        tokens=tokens,
        context=context,
        raw_sha256=sha256_hex(raw),
    )
    store.save(snap, raw)
    return snap
