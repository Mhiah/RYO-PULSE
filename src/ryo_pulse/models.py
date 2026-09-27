"""The Pulse skill contract: request, snapshot and result shapes."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import to_utc

SPEC_VERSION = "pulse/1"


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


class PulseStatus(str, Enum):
    PERSISTENT = "persistent"   # keeps showing up across the window
    EMERGING = "emerging"       # showing up now, only recently
    TRANSIENT = "transient"     # showed up, but not as a pattern (flash, or faded)
    ABSENT = "absent"           # enough good scans, never showed up
    INSUFFICIENT = "insufficient"  # not enough comparable, successful scans to say


class ScanProfile(BaseModel):
    """What was scanned. Only snapshots with the same profile hash are comparable."""

    model_config = ConfigDict(frozen=True)

    tool: str = "scan_market"
    params: dict[str, Any] = Field(default_factory=dict)

    def normalized(self) -> dict[str, Any]:
        params = {
            str(k).strip().lower(): (v.strip().lower() if isinstance(v, str) else v)
            for k, v in self.params.items()
            if v is not None and v != ""
        }
        return {"tool": self.tool.strip().lower(), "params": params}

    @property
    def hash(self) -> str:
        from .profile import profile_hash

        return profile_hash(self)


class Snapshot(BaseModel):
    """One scan_market call, normalised. A failed call is still a snapshot (ok=False)."""

    snapshot_id: str
    captured_at: datetime
    profile: ScanProfile
    ok: bool = True
    error: str | None = None
    tokens: list[str] = Field(default_factory=list, description="Ranked symbols, best first")
    source: Literal["live", "fixture"] = "live"
    context: dict[str, str] = Field(default_factory=dict, description="e.g. market_overview regime")
    raw_sha256: str | None = None

    @field_validator("captured_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return to_utc(v)

    @field_validator("tokens")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for s in v:
            n = normalize_symbol(s)
            if n and n not in seen:
                seen.append(n)
        return seen

    @model_validator(mode="after")
    def _failed_has_no_tokens(self) -> "Snapshot":
        if not self.ok and self.tokens:
            raise ValueError("a failed snapshot cannot carry tokens")
        return self


class PulseThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_valid: int = Field(3, ge=1, description="Comparable successful scans needed to judge")
    min_coverage: float = Field(0.6, ge=0, le=1, description="valid / total below this -> insufficient")
    persistent_ratio: float = Field(0.6, gt=0, le=1, description="hits / valid needed for persistent")
    persistent_min_hits: int = Field(3, ge=1)


class PulseRequest(BaseModel):
    token: str
    snapshots: list[Snapshot]
    profile_hash: str | None = Field(
        None, description="Profile to judge against. Default: profile of the newest successful snapshot."
    )
    thresholds: PulseThresholds = Field(default_factory=PulseThresholds)

    @field_validator("token")
    @classmethod
    def _token(cls, v: str) -> str:
        n = normalize_symbol(v)
        if not n:
            raise ValueError("token is required")
        return n


class Reason(BaseModel):
    code: str
    detail: str


class Counts(BaseModel):
    total: int
    valid: int
    failed: int
    drifted: int
    hits: int


class Sighting(BaseModel):
    snapshot_id: str
    captured_at: datetime
    rank: int


class PulseResult(BaseModel):
    spec_version: str = SPEC_VERSION
    token: str
    status: PulseStatus
    profile_hash: str | None
    counts: Counts
    coverage: float = Field(description="valid / total snapshots in the window")
    hit_ratio: float | None = Field(description="hits / valid, None when no valid snapshots")
    current_streak: int = Field(description="Consecutive valid snapshots, newest backwards, containing the token")
    first_seen: datetime | None
    last_seen: datetime | None
    window_start: datetime | None
    window_end: datetime | None
    best_rank: int | None
    sightings: list[Sighting]
    context: dict[str, str] = Field(default_factory=dict)
    provenance: Literal["live", "fixture", "mixed", "none"]
    reasons: list[Reason]
    thresholds: PulseThresholds
    snapshot_ids: list[str] = Field(description="Every snapshot in the request, oldest first (for replay)")
    replay_hash: str
