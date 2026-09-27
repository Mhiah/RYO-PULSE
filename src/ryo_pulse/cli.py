"""ryo-pulse command line.

  ryo-pulse discover  [-p key=value ...]         list RYO tools, save a real scan_market sample, suggest paths
  ryo-pulse collect   [-p key=value ...] [--every MIN --count N]   store live snapshots
  ryo-pulse profiles                              list stored scan profiles
  ryo-pulse pulse TOKEN [--profile H | --fixture F] [--last N] [--json] [--save F]
  ryo-pulse board   [--profile H | --fixture F] [--last N]         classify every token seen
  ryo-pulse replay  RESULT.json [--fixture F]     recompute and check a saved result's replay_hash
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .engine import classify
from .models import PulseRequest, PulseResult, PulseStatus, PulseThresholds, ScanProfile, Snapshot
from .store import SnapshotStore, load_fixture

console = Console()

STATUS_STYLE = {
    PulseStatus.PERSISTENT: "bold green",
    PulseStatus.EMERGING: "bold cyan",
    PulseStatus.TRANSIENT: "yellow",
    PulseStatus.ABSENT: "dim",
    PulseStatus.INSUFFICIENT: "bold red",
}
STATUS_ORDER = list(STATUS_STYLE)


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _params(pairs: list[str] | None) -> dict:
    out: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"bad -p '{pair}', expected key=value")
        k, v = pair.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _snapshots(args) -> list[Snapshot]:
    if getattr(args, "fixture", None):
        snaps = load_fixture(args.fixture)
    else:
        store = SnapshotStore()
        profile = args.profile
        if not profile:
            profiles = store.profiles()
            if len(profiles) > 1:
                raise SystemExit(f"several profiles stored, pick one with --profile: {', '.join(profiles)}")
            profile = profiles[0] if profiles else None
        snaps = store.load(profile)
    snaps = sorted(snaps, key=lambda s: (s.captured_at, s.snapshot_id))
    if getattr(args, "last", None):
        snaps = snaps[-args.last :]
    return snaps


def _thresholds(args) -> PulseThresholds:
    return PulseThresholds(
        min_valid=args.min_valid,
        min_coverage=args.min_coverage,
        persistent_ratio=args.persistent_ratio,
        persistent_min_hits=args.persistent_min_hits,
    )


def _fmt(dt) -> str:
    return dt.strftime("%m-%d %H:%M") if dt else "-"


def cmd_pulse(args) -> int:
    snaps = _snapshots(args)
    result = classify(PulseRequest(token=args.token, snapshots=snaps, thresholds=_thresholds(args)))
    if args.save:
        Path(args.save).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    if args.json:
        print(result.model_dump_json(indent=2))
        return 0
    style = STATUS_STYLE[result.status]
    c = result.counts
    console.print(f"[bold]{result.token}[/]  [{style}]{result.status.value.upper()}[/]")
    console.print(
        f"seen {c.hits}/{c.valid} comparable scans · coverage {result.coverage:.0%} "
        f"({c.failed} failed, {c.drifted} drifted of {c.total}) · streak {result.current_streak}"
    )
    console.print(
        f"first {_fmt(result.first_seen)} · last {_fmt(result.last_seen)} UTC · best rank {result.best_rank or '-'}"
    )
    for r in result.reasons:
        console.print(f"  • [dim]{r.code}[/] {r.detail}")
    if result.provenance != "live":
        console.print(f"[bold red]data: {result.provenance}[/] (not live RYO scans)")
    console.print(f"[dim]profile {result.profile_hash} · replay {result.replay_hash[:16]}…[/]")
    return 0


def cmd_board(args) -> int:
    snaps = _snapshots(args)
    th = _thresholds(args)
    probe = classify(PulseRequest(token="_", snapshots=snaps, thresholds=th))
    tokens = sorted({t for s in snaps if s.ok and s.profile.hash == probe.profile_hash for t in s.tokens})
    results = [classify(PulseRequest(token=t, snapshots=snaps, thresholds=th)) for t in tokens]
    results.sort(key=lambda r: (STATUS_ORDER.index(r.status), -(r.hit_ratio or 0), r.best_rank or 999, r.token))
    if args.json:
        print(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
        return 0
    if not results:
        console.print("[yellow]No tokens in the stored snapshots yet.[/]")
        return 0
    first = results[0]
    table = Table(
        title=f"RYO Pulse · profile {first.profile_hash} · {first.counts.valid} comparable scans "
        f"({first.counts.failed} failed) · data: {first.provenance}"
    )
    for col in ("Token", "Status", "Seen", "Streak", "Best rank", "First", "Last"):
        table.add_column(col)
    for r in results:
        table.add_row(
            r.token,
            f"[{STATUS_STYLE[r.status]}]{r.status.value}[/]",
            f"{r.counts.hits}/{r.counts.valid}",
            str(r.current_streak),
            str(r.best_rank or "-"),
            _fmt(r.first_seen),
            _fmt(r.last_seen),
        )
    console.print(table)
    return 0


def cmd_replay(args) -> int:
    saved = PulseResult.model_validate_json(Path(args.result).read_text(encoding="utf-8"))
    pool = {s.snapshot_id: s for s in (load_fixture(args.fixture) if args.fixture else SnapshotStore().load())}
    missing = [i for i in saved.snapshot_ids if i not in pool]
    if missing:
        console.print(f"[red]missing {len(missing)} snapshot(s): {', '.join(missing[:5])}[/]")
        return 2
    again = classify(
        PulseRequest(
            token=saved.token,
            snapshots=[pool[i] for i in saved.snapshot_ids],
            profile_hash=saved.profile_hash,
            thresholds=saved.thresholds,
        )
    )
    if again.replay_hash == saved.replay_hash:
        console.print(f"[green]replay OK[/] {saved.token} {again.status.value} {again.replay_hash[:16]}…")
        return 0
    console.print(f"[red]replay MISMATCH[/] saved {saved.replay_hash[:16]}… vs {again.replay_hash[:16]}…")
    return 1


def cmd_profiles(args) -> int:
    store = SnapshotStore()
    for h in store.profiles():
        snaps = store.load(h)
        ok = sum(s.ok for s in snaps)
        params = snaps[-1].profile.params if snaps else {}
        console.print(f"{h}  {ok}/{len(snaps)} ok  {_fmt(snaps[0].captured_at)} → {_fmt(snaps[-1].captured_at)}  {params}")
    return 0


def cmd_discover(args) -> int:
    from .client import RyoClient
    from .paths import suggest

    client = RyoClient()
    out = Path("data/catalog")
    out.mkdir(parents=True, exist_ok=True)
    tools = client.list_tools()
    (out / "tools.json").write_text(json.dumps(tools, indent=2), encoding="utf-8")
    console.print(f"[bold]{len(tools)} tools[/] (saved data/catalog/tools.json)")
    for t in tools:
        console.print(f"  • {t.get('name')}: {(t.get('description') or '').splitlines()[0][:90] if t.get('description') else ''}")
    for name, params in (("scan_market", _params(args.param)), ("market_overview", {})):
        sample = client.call_tool(name, params)
        (out / f"{name}.sample.json").write_text(json.dumps(sample, indent=2), encoding="utf-8")
        console.print(f"\n[bold]{name}[/] sample saved to data/catalog/{name}.sample.json")
        for p in suggest(sample)[:15]:
            console.print(f"  candidate path: {p}")
    console.print(
        "\nConfirm the token path against the sample, then set RYO_SCAN_TOKENS_PATH "
        "(and optionally RYO_OVERVIEW_REGIME_PATH) in .env."
    )
    return 0


def cmd_collect(args) -> int:
    from .client import RyoClient
    from .collector import collect_once

    client = RyoClient()
    store = SnapshotStore()
    profile = ScanProfile(params=_params(args.param))
    console.print(f"profile {profile.hash} {profile.params or '{}'} → {store.root}")
    n = 0
    try:
        while True:
            snap = collect_once(client, profile, store)
            n += 1
            if snap.ok:
                console.print(f"[green]✓[/] {snap.snapshot_id} {len(snap.tokens)} tokens {snap.context or ''}")
            else:
                console.print(f"[red]✗[/] {snap.snapshot_id} failed: {snap.error}")
            if not args.every or (args.count and n >= args.count):
                break
            time.sleep(args.every * 60)
    except KeyboardInterrupt:
        console.print(f"\nstopped after {n} snapshot(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ryo-pulse", description="One scan shows a signal. Repeated scans reveal persistence.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def source(p):
        p.add_argument("--profile", help="profile hash in the local store")
        p.add_argument("--fixture", help="read snapshots from a fixture JSON file instead")
        p.add_argument("--last", type=int, help="only the newest N snapshots")
        d = PulseThresholds()
        p.add_argument("--min-valid", type=int, default=d.min_valid)
        p.add_argument("--min-coverage", type=float, default=d.min_coverage)
        p.add_argument("--persistent-ratio", type=float, default=d.persistent_ratio)
        p.add_argument("--persistent-min-hits", type=int, default=d.persistent_min_hits)
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("pulse", help="classify one token")
    p.add_argument("token")
    source(p)
    p.add_argument("--save", help="write the result JSON here")
    p.set_defaults(fn=cmd_pulse)

    p = sub.add_parser("board", help="classify every token seen in the window")
    source(p)
    p.set_defaults(fn=cmd_board)

    p = sub.add_parser("replay", help="re-run a saved result and check its replay_hash")
    p.add_argument("result")
    p.add_argument("--fixture")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("profiles", help="list stored scan profiles")
    p.set_defaults(fn=cmd_profiles)

    for name, fn, helptext in (
        ("discover", cmd_discover, "list RYO tools and save real samples"),
        ("collect", cmd_collect, "store live scan_market snapshots"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("-p", "--param", action="append", help="scan_market argument key=value (repeatable)")
        if name == "collect":
            p.add_argument("--every", type=float, help="minutes between scans (omit for one scan)")
            p.add_argument("--count", type=int, help="stop after N scans")
        p.set_defaults(fn=fn)
    return ap


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:  # show a clean error, not a traceback, to CLI users
        if os.environ.get("PULSE_DEBUG"):
            raise
        console.print(f"[red]error:[/] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
