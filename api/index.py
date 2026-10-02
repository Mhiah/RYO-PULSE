"""Vercel entry point: the public RYO Pulse dashboard.

Serves the same pages and /api/board as `ryo-pulse serve`, over real scans copied into hosted/snapshots.
Read-only and keyless: it never calls RYO, so the board shows those saved scans and the Last scan time says how fresh they are.
"""

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ryo_pulse.dashboard import make_handler  # noqa: E402
from ryo_pulse.store import SnapshotStore  # noqa: E402

store = SnapshotStore(ROOT / "hosted" / "snapshots")


def load():
    # several settings profiles may be saved; show the one with the newest scan
    best, newest = None, None
    for h in store.profiles():
        snaps = store.load(h)
        if snaps and (newest is None or snaps[-1].captured_at > newest):
            best, newest = snaps, snaps[-1].captured_at
    return best or []


class handler(make_handler(load, hosted=True)):  # noqa: N801 (name Vercel looks for)
    def do_GET(self):  # noqa: N802
        # vercel.json rewrites every path here; recover the original path if the runtime hands us the rewritten one
        url = urlparse(self.path)
        if url.path.startswith("/api/index"):
            q = parse_qs(url.query)
            route = (q.pop("route", ["/"])[0]) or "/"
            self.path = "/" + route.lstrip("/") + ("?" + urlencode(q, doseq=True) if q else "")
        return super().do_GET()
