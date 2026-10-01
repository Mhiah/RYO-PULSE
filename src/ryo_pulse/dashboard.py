"""Track 2 half: a local, read-only dashboard to configure and monitor RYO Pulse.

It never calls RYO. It reads the snapshot store that `ryo-pulse collect` writes (or a fixture)
and runs the same engine as the skill, so every number on screen is reproducible with the CLI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .engine import classify
from .models import PulseRequest, PulseStatus, PulseThresholds, Snapshot, normalize_symbol

# Most decision-relevant first: a new signal, then a lasting one, then faded ones.
IMPORTANCE = [
    PulseStatus.EMERGING,
    PulseStatus.PERSISTENT,
    PulseStatus.TRANSIENT,
    PulseStatus.ABSENT,
    PulseStatus.INSUFFICIENT,
]
PRESENCE_CELLS = 24
# Reason codes that explain the status itself (others are context: failures, drift, regime, fixtures).
DECISION_CODES = {"REPEATED", "RECENT_ONLY", "FADED", "SPORADIC", "NEVER_SEEN", "TOO_FEW_SCANS", "LOW_COVERAGE"}


def _why(result) -> str:
    for r in result.reasons:
        if r.code in DECISION_CODES:
            return r.detail
    return result.reasons[-1].detail if result.reasons else ""


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _latest_regime(snapshots: list[Snapshot], phash: str) -> str | None:
    """Most recent market mood among good scans (a scan without one doesn't blank the tile)."""
    for s in sorted(snapshots, key=lambda x: x.captured_at, reverse=True):
        if s.ok and s.profile.hash == phash and s.context.get("regime"):
            return s.context["regime"]
    return None


def build_board(
    snapshots: list[Snapshot],
    thresholds: PulseThresholds | None = None,
    window: int | None = None,
    watchlist: list[str] | None = None,
) -> dict[str, Any]:
    """Everything the page shows, as plain JSON. Pure: same inputs, same output."""
    th = thresholds or PulseThresholds()
    snaps = sorted(snapshots, key=lambda s: (s.captured_at, s.snapshot_id))
    if window:
        snaps = snaps[-window:]
    watch = [normalize_symbol(w) for w in (watchlist or []) if normalize_symbol(w)]

    probe = classify(PulseRequest(token="_", snapshots=snaps, thresholds=th))
    phash = probe.profile_hash
    valid = [s for s in snaps if s.ok and s.profile.hash == phash]
    universe = sorted({t for s in valid for t in s.tokens} | set(watch))

    def run(token: str, subset: list[Snapshot]):
        return classify(PulseRequest(token=token, snapshots=subset, profile_hash=phash, thresholds=th))

    results = {t: run(t, snaps) for t in universe}

    # What changed: classify again without the newest snapshot and diff the statuses.
    changes: list[dict[str, Any]] = []
    if len(snaps) >= 2:
        before = snaps[:-1]
        for t in universe:
            old, new = run(t, before).status, results[t].status
            if old != new:
                changes.append(
                    {
                        "token": t,
                        "from": old.value,
                        "to": new.value,
                        "why": _why(results[t]),
                    }
                )
    changes.sort(key=lambda c: (IMPORTANCE.index(PulseStatus(c["to"])), c["token"]))

    latest_valid = valid[-1] if valid else None
    prev_valid = valid[-2] if len(valid) >= 2 else None
    entered = sorted(set(latest_valid.tokens) - set(prev_valid.tokens)) if latest_valid and prev_valid else []
    dropped = sorted(set(prev_valid.tokens) - set(latest_valid.tokens)) if latest_valid and prev_valid else []

    recent_valid = valid[-PRESENCE_CELLS:]
    rows = []
    for t, r in results.items():
        rows.append(
            {
                "token": t,
                "status": r.status.value,
                "watched": t in watch,
                "hits": r.counts.hits,
                "valid": r.counts.valid,
                "hit_ratio": r.hit_ratio,
                "streak": r.current_streak,
                "best_rank": r.best_rank,
                "latest_rank": (latest_valid.tokens.index(t) + 1) if latest_valid and t in latest_valid.tokens else None,
                "first_seen": _iso(r.first_seen),
                "last_seen": _iso(r.last_seen),
                "presence": [t in s.tokens for s in recent_valid],
                "why": _why(r),
                "reasons": [x.model_dump() for x in r.reasons],
                "replay_hash": r.replay_hash,
            }
        )
    rows.sort(
        key=lambda x: (
            not x["watched"],
            IMPORTANCE.index(PulseStatus(x["status"])),
            -(x["hit_ratio"] or 0),
            x["latest_rank"] or 999,
            x["token"],
        )
    )

    latest = snaps[-1] if snaps else None
    return {
        "profile_hash": phash,
        "profile_params": (latest_valid or latest).profile.params if (latest_valid or latest) else {},
        "provenance": probe.provenance,
        "thresholds": th.model_dump(),
        "window": window,
        "scans": {
            "total": probe.counts.total,
            "valid": probe.counts.valid,
            "failed": probe.counts.failed,
            "drifted": probe.counts.drifted,
            "coverage": probe.coverage,
            "latest_at": _iso(latest.captured_at) if latest else None,
            "latest_ok": bool(latest and latest.ok and latest.profile.hash == phash),
            "latest_error": latest.error if latest and not latest.ok else None,
            "regime": _latest_regime(snapshots, phash),
        },
        "timeline": [
            {
                "id": s.snapshot_id,
                "at": _iso(s.captured_at),
                "state": "ok" if s.ok and s.profile.hash == phash else ("failed" if not s.ok else "drifted"),
                "tokens": len(s.tokens),
                "error": s.error,
            }
            for s in snaps[-48:]
        ],
        "changes": changes,
        "entered": entered,
        "dropped": dropped,
        "counts": {st.value: sum(1 for x in rows if x["status"] == st.value) for st in IMPORTANCE},
        "rows": rows,
    }


def _params(query: dict[str, list[str]]) -> tuple[PulseThresholds, int | None, list[str]]:
    def one(name: str, cast: Callable, default):
        vals = query.get(name)
        return cast(vals[0]) if vals and vals[0] != "" else default

    d = PulseThresholds()
    th = PulseThresholds(
        min_valid=one("min_valid", int, d.min_valid),
        min_coverage=one("min_coverage", float, d.min_coverage),
        persistent_ratio=one("persistent_ratio", float, d.persistent_ratio),
        persistent_min_hits=one("persistent_min_hits", int, d.persistent_min_hits),
    )
    window = one("window", int, None)
    if window is not None and window < 1:
        raise ValueError("window must be >= 1")
    watch = [w for w in one("watch", str, "").replace(" ", ",").split(",") if w.strip()]
    return th, window, watch


def make_handler(load: Callable[[], list[Snapshot]]) -> type[BaseHTTPRequestHandler]:
    web = resources.files("ryo_pulse").joinpath("web")
    pages = {
        "/": ("landing.html", "text/html; charset=utf-8"),
        "/index.html": ("landing.html", "text/html; charset=utf-8"),
        "/board": ("board.html", "text/html; charset=utf-8"),
        "/static/theme.css": ("theme.css", "text/css; charset=utf-8"),
        "/static/fonts/DejaVuSans.woff2": ("fonts/DejaVuSans.woff2", "font/woff2"),
        "/static/fonts/DejaVuSans-Bold.woff2": ("fonts/DejaVuSans-Bold.woff2", "font/woff2"),
    }
    files = {route: (web.joinpath(name).read_bytes(), ctype) for route, (name, ctype) in pages.items()}

    class Handler(BaseHTTPRequestHandler):
        server_version = "RyoPulse/0.1"

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: Any) -> None:
            self._send(code, json.dumps(data).encode("utf-8"), "application/json")

        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            url = urlparse(self.path)
            if url.path in files:
                body, ctype = files[url.path]
                return self._send(HTTPStatus.OK, body, ctype)
            if url.path == "/api/board":
                try:
                    th, window, watch = _params(parse_qs(url.query))
                except (ValueError, TypeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": f"bad setting: {exc}"})
                try:
                    snaps = load()
                except Exception as exc:  # a bad file in the store should not take the page down
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"could not read snapshots: {exc}"})
                return self._json(HTTPStatus.OK, build_board(snaps, th, window, watch))
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def log_message(self, fmt: str, *args: Any) -> None:  # keep the collector's terminal quiet
            return

    return Handler


def serve(load: Callable[[], list[Snapshot]], host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(load))
