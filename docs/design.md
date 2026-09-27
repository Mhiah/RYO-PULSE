# RYO Pulse: design

## Problem
`scan_market` returns ranked candidates by live momentum *right now*. A single appearance is real evidence, but agents over-read it as a lasting signal. Pulse asks one narrow question over time: **under the same scan profile, did this token keep appearing?**

## Non-goals
Pulse does no trading, price prediction, thesis writing or LLM judgement. It never calls RYO with anything but read-only research tools.

## Contract (`pulse/1`)

**Request** (`PulseRequest`)
| field | type | notes |
|---|---|---|
| `token` | str | symbol, normalised to upper case |
| `snapshots` | `Snapshot[]` | any order; the engine sorts by `(captured_at, snapshot_id)` |
| `profile_hash` | str? | profile to judge; defaults to the newest successful snapshot's |
| `thresholds` | `PulseThresholds` | `min_valid=3`, `min_coverage=0.6`, `persistent_ratio=0.6`, `persistent_min_hits=3` |

**Snapshot**: `snapshot_id`, `captured_at` (UTC), `profile {tool, params}`, `ok`, `error?`, `tokens[]` (ranked, best first), `source` (`live`/`fixture`), `context {regime?}`, `raw_sha256?`. A failed snapshot cannot carry tokens.

**Result** (`PulseResult`): `status`, `counts {total, valid, failed, drifted, hits}`, `coverage`, `hit_ratio`, `current_streak`, `first_seen`, `last_seen`, `window_start`, `window_end`, `best_rank`, `sightings[]`, `context`, `provenance`, `reasons[{code, detail}]`, `thresholds`, `snapshot_ids[]`, `replay_hash`.

Full JSON Schemas: `docs/schema/`.

## Profile hash
`sha256(canonical_json({tool, params}))[:16]`, with keys lower-cased, string values trimmed and lower-cased, and null or empty params dropped. Two scans are comparable if and only if their hashes match.

## Classification
1. valid = `ok` snapshots with the judged profile hash. `failed` and `drifted` are counted, never treated as misses.
2. `valid < min_valid` → **insufficient** (`TOO_FEW_SCANS`)
3. `valid/total < min_coverage` → **insufficient** (`LOW_COVERAGE`)
4. `hits == 0` → **absent** (`NEVER_SEEN`)
5. `hits ≥ persistent_min_hits` and `hits/valid ≥ persistent_ratio` → **persistent** (`REPEATED`)
6. present in the newest valid snapshot and first-seen index ≥ `valid // 2` → **emerging** (`RECENT_ONLY`)
7. otherwise → **transient** (`FADED` if missing from the newest scan, else `SPORADIC`)

Extra reasons: `FAILED_SCANS`, `PROFILE_DRIFT`, `FIXTURE_DATA`, `REGIME_CHANGED`.

## Replay hash
`sha256(canonical_json({spec, token, profile_hash, thresholds, snapshots[id, at, profile, ok, tokens, context], status, counts}))`. The result also carries `snapshot_ids` and `thresholds`, so `ryo-pulse replay` can rebuild the exact request from the store and confirm the hash. Input order does not affect it.

## Adapter
Transport is MCP over streamable HTTP (`initialize` → `tools/list` / `tools/call`). Output fields are **not** hard-coded: `ryo-pulse discover` saves real samples and suggests paths, and the operator confirms `RYO_SCAN_TOKENS_PATH` and `RYO_OVERVIEW_REGIME_PATH`. A wrong path or an outage produces a failed snapshot, not fake absence.

## Open items
- Align `docs/schema/tool_definition.json` with RYO's published Track 3 tool specification once confirmed.
- Confirm the MCP URL and auth header in the MCP Builder Guide.
