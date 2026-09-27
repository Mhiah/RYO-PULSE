"""Canonical JSON + hashing. Every hash in Pulse goes through here so replays are stable."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return to_utc(obj).strftime("%Y-%m-%dT%H:%M:%SZ")
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"not canonicalisable: {type(obj).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, default=_default, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
