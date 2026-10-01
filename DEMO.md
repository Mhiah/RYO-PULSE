# Demo script (about 3 minutes)

1. **Problem (20s).** "`scan_market` tells you who shows up *now*. One hit isn't a pattern. Pulse asks: did it keep showing up under the same scan?"
2. **Live collection (30s).** Show `ryo-pulse profiles`: one profile hash, N live snapshots over hours or days, including any failed ones.
3. **Board (45s).** Run `ryo-pulse board`. Walk through one row per status: persistent, emerging, transient, absent. Point at `data: live`.
4. **One token (30s).** Run `ryo-pulse pulse <TOKEN>` and read the reasons, coverage and streak aloud. If there is a failed scan, show that coverage dropped but it did not become "absent".
5. **Honesty rules (30s).** Run the offline fixture (`ryo-pulse board --fixture tests/fixtures/demo_window.json`) and show that it is labelled `fixture`, that the drifted-profile scan is excluded and that the outage yields coverage, not absence.
6. **Replay (15s).** Run `ryo-pulse pulse <TOKEN> --save r.json` then `ryo-pulse replay r.json` to get "replay OK".
7. **Dashboard, Track 2 (40s).** Run `ryo-pulse serve`. Open on the landing page (press `t` to flip day/night), click **Open the dashboard**, then show "What changed since the last scan", then filter with `1`–`5`, expand a row with Enter (reasons and replay hash), add a watchlist token in Settings, and use only the keyboard to show it works for everyone. Point at Scan health and the "failed, not counted as absence" legend.
8. **Ship it (10s).** Show `docs/schema/tool_definition.json`, which is the skill contract ready to sit next to RYO's tools. Then run `pytest`.
