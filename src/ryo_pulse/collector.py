"""Take one live scan_market snapshot (plus optional market_overview context) and store it.

A failed call is stored as ok=False, never as an empty token list: Pulse must not read an
outage as absence.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .canonical import sha256_hex
from .client import RyoClient, RyoError
from .models import ScanProfile, Snapshot
from .paths import PathError, extract
from .store import SnapshotStore


def collect_once(
    client: RyoClient,
    profile: ScanProfile,
    store: SnapshotStore,
    tokens_path: str | None = None,
    regime_path: str | None = None,
    now: datetime | None = None,
) -> Snapshot:
    tokens_path = tokens_path or os.environ.get("RYO_SCAN_TOKENS_PATH", "")
    regime_path = regime_path if regime_path is not None else os.environ.get("RYO_OVERVIEW_REGIME_PATH", "")
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
    except (RyoError, PathError) as exc:
        snap = Snapshot(snapshot_id=snap_id, captured_at=at, profile=profile, ok=False, error=str(exc)[:300])
        store.save(snap, raw or None)
        return snap

    if regime_path:
        try:
            overview = client.call_tool("market_overview", {})
            raw["market_overview"] = overview
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
