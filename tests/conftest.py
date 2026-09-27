from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ryo_pulse.models import ScanProfile, Snapshot

FIXTURES = Path(__file__).parent / "fixtures"
BASE = datetime(2026, 9, 28, tzinfo=timezone.utc)
PROFILE = ScanProfile(params={"limit": 10})


def make(rows, profile=PROFILE):
    """rows: list of token lists, None for a failed scan, or (profile, tokens) for another profile."""
    out = []
    for i, r in enumerate(rows):
        at = BASE + timedelta(hours=i)
        sid = f"s{i:02d}"
        if r is None:
            out.append(Snapshot(snapshot_id=sid, captured_at=at, profile=profile, ok=False, error="boom"))
        elif isinstance(r, tuple):
            out.append(Snapshot(snapshot_id=sid, captured_at=at, profile=r[0], tokens=r[1]))
        else:
            out.append(Snapshot(snapshot_id=sid, captured_at=at, profile=profile, tokens=r))
    return out


@pytest.fixture
def demo_path():
    return FIXTURES / "demo_window.json"
