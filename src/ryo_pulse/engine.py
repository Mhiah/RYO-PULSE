"""Deterministic persistence classifier. Pure: same request in, same result (and replay_hash) out.

Rules, in order (see docs/design.md):
  1. Keep only successful snapshots whose profile hash matches the judged profile ("valid").
     Failed scans and drifted-profile scans never count as absence; they lower coverage.
  2. valid < min_valid, or coverage < min_coverage          -> insufficient
  3. token in no valid snapshot                             -> absent
  4. hits >= persistent_min_hits and hits/valid >= ratio    -> persistent
  5. token in the newest valid snapshot and first seen in
     the newer half of the valid window                     -> emerging
  6. anything else that was seen                            -> transient
"""

from __future__ import annotations

from .canonical import sha256_hex
from .models import (
    SPEC_VERSION,
    Counts,
    PulseRequest,
    PulseResult,
    PulseStatus,
    Reason,
    Sighting,
    Snapshot,
)


def _judged_profile(req: PulseRequest) -> str | None:
    if req.profile_hash:
        return req.profile_hash
    ok = [s for s in req.snapshots if s.ok]
    if not ok:
        return None
    newest = max(ok, key=lambda s: (s.captured_at, s.snapshot_id))
    return newest.profile.hash


def _ordered(snaps: list[Snapshot]) -> list[Snapshot]:
    return sorted(snaps, key=lambda s: (s.captured_at, s.snapshot_id))


def classify(req: PulseRequest) -> PulseResult:
    snaps = _ordered(req.snapshots)
    phash = _judged_profile(req)

    failed = [s for s in snaps if not s.ok]
    drifted = [s for s in snaps if s.ok and s.profile.hash != phash]
    valid = [s for s in snaps if s.ok and s.profile.hash == phash]

    total = len(snaps)
    coverage = round(len(valid) / total, 4) if total else 0.0

    sightings = [
        Sighting(snapshot_id=s.snapshot_id, captured_at=s.captured_at, rank=s.tokens.index(req.token) + 1)
        for s in valid
        if req.token in s.tokens
    ]
    hits = len(sightings)
    hit_ratio = round(hits / len(valid), 4) if valid else None

    streak = 0
    for s in reversed(valid):
        if req.token not in s.tokens:
            break
        streak += 1

    reasons: list[Reason] = []
    if failed:
        reasons.append(Reason(code="FAILED_SCANS", detail=f"{len(failed)} scan(s) failed; not counted as absence"))
    if drifted:
        reasons.append(
            Reason(code="PROFILE_DRIFT", detail=f"{len(drifted)} scan(s) used a different profile; excluded")
        )

    status = _decide(req, valid, sightings, hits, coverage, reasons)

    sources = {s.source for s in valid}
    provenance = "none" if not sources else (sources.pop() if len(sources) == 1 else "mixed")
    if provenance in ("fixture", "mixed"):
        reasons.append(Reason(code="FIXTURE_DATA", detail="includes fixture (non-live) snapshots"))

    context: dict[str, str] = {}
    if valid:
        first_regime = valid[0].context.get("regime")
        last_regime = valid[-1].context.get("regime")
        if first_regime:
            context["regime_at_window_start"] = first_regime
        if last_regime:
            context["regime_at_window_end"] = last_regime
        if first_regime and last_regime and first_regime != last_regime:
            reasons.append(
                Reason(code="REGIME_CHANGED", detail=f"market regime moved {first_regime} -> {last_regime} in window")
            )

    counts = Counts(total=total, valid=len(valid), failed=len(failed), drifted=len(drifted), hits=hits)
    replay_hash = sha256_hex(
        {
            "spec": SPEC_VERSION,
            "token": req.token,
            "profile_hash": phash,
            "thresholds": req.thresholds.model_dump(mode="json"),
            "snapshots": [
                {
                    "id": s.snapshot_id,
                    "at": s.captured_at,
                    "profile": s.profile.hash,
                    "ok": s.ok,
                    "tokens": s.tokens,
                    "context": s.context,
                }
                for s in snaps
            ],
            "status": status.value,
            "counts": counts.model_dump(mode="json"),
        }
    )

    return PulseResult(
        token=req.token,
        status=status,
        profile_hash=phash,
        counts=counts,
        coverage=coverage,
        hit_ratio=hit_ratio,
        current_streak=streak,
        first_seen=sightings[0].captured_at if sightings else None,
        last_seen=sightings[-1].captured_at if sightings else None,
        window_start=valid[0].captured_at if valid else None,
        window_end=valid[-1].captured_at if valid else None,
        best_rank=min((x.rank for x in sightings), default=None),
        sightings=sightings,
        context=context,
        provenance=provenance,
        reasons=reasons,
        thresholds=req.thresholds,
        snapshot_ids=[s.snapshot_id for s in snaps],
        replay_hash=replay_hash,
    )


def _decide(
    req: PulseRequest,
    valid: list[Snapshot],
    sightings: list[Sighting],
    hits: int,
    coverage: float,
    reasons: list[Reason],
) -> PulseStatus:
    t = req.thresholds
    n = len(valid)

    if n < t.min_valid:
        reasons.append(Reason(code="TOO_FEW_SCANS", detail=f"{n} comparable scan(s); need {t.min_valid}"))
        return PulseStatus.INSUFFICIENT
    if coverage < t.min_coverage:
        reasons.append(
            Reason(code="LOW_COVERAGE", detail=f"coverage {coverage:.0%} below {t.min_coverage:.0%}")
        )
        return PulseStatus.INSUFFICIENT
    if hits == 0:
        reasons.append(Reason(code="NEVER_SEEN", detail=f"absent from all {n} comparable scans"))
        return PulseStatus.ABSENT

    ratio = hits / n
    if hits >= t.persistent_min_hits and ratio >= t.persistent_ratio:
        reasons.append(Reason(code="REPEATED", detail=f"seen in {hits}/{n} comparable scans ({ratio:.0%})"))
        return PulseStatus.PERSISTENT

    ids = [s.snapshot_id for s in valid]
    first_idx = ids.index(sightings[0].snapshot_id)
    in_latest = sightings[-1].snapshot_id == ids[-1]
    if in_latest and first_idx >= n // 2:
        reasons.append(
            Reason(code="RECENT_ONLY", detail=f"first seen in scan {first_idx + 1}/{n} and present in the newest")
        )
        return PulseStatus.EMERGING

    if not in_latest:
        reasons.append(Reason(code="FADED", detail=f"seen {hits}/{n}, missing from the newest scan"))
    else:
        reasons.append(Reason(code="SPORADIC", detail=f"seen {hits}/{n}, below persistence threshold"))
    return PulseStatus.TRANSIENT
