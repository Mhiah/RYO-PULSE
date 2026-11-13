"""Time /api/board's work over the saved real scans for growing watchlists (audit evidence for F-01)."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ryo_pulse.dashboard import build_board  # noqa: E402
from ryo_pulse.store import SnapshotStore  # noqa: E402

snaps = SnapshotStore(ROOT / "hosted" / "snapshots").load()
print(f"{len(snaps)} saved snapshots")
for n in (0, 50, 500, 2000):
    watch = [f"W{i}" for i in range(n)]
    t = time.perf_counter()
    build_board(snaps, watchlist=watch)
    print(f"watch={n:5d}  {time.perf_counter() - t:7.2f}s")
