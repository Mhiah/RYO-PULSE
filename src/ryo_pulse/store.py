"""Append-only local snapshot store. One JSON file per snapshot, plus the raw response next to it.

Restart-safe: nothing is rewritten, so a crash mid-collection loses at most the scan in flight.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from .models import Snapshot


class SnapshotStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.environ.get("PULSE_DATA_DIR") or "data/snapshots")

    def _path(self, snap: Snapshot) -> Path:
        day = snap.captured_at.strftime("%Y-%m-%d")
        return self.root / snap.profile.hash / day / f"{snap.snapshot_id}.json"

    def save(self, snap: Snapshot, raw: object | None = None) -> Path:
        path = self._path(snap)
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            raw_path = path.with_suffix(".raw.json")
            _atomic_write(raw_path, json.dumps(raw, indent=2, sort_keys=True, default=str))
        _atomic_write(path, snap.model_dump_json(indent=2))
        return path

    def load(self, profile_hash: str | None = None) -> list[Snapshot]:
        base = self.root / profile_hash if profile_hash else self.root
        if not base.exists():
            return []
        snaps = [
            Snapshot.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(base.rglob("*.json"))
            if not p.name.endswith(".raw.json")
        ]
        return sorted(snaps, key=lambda s: (s.captured_at, s.snapshot_id))

    def profiles(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_fixture(path: str | Path) -> list[Snapshot]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items: Iterable[dict] = data["snapshots"] if isinstance(data, dict) else data
    return [Snapshot.model_validate({**item, "source": "fixture"}) for item in items]
