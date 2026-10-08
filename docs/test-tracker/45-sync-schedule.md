# Issue #45: Sync schedule within the duty-cycle budget, on hardware

Purpose: acceptance. Issue: #45.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/sync-profile-dev.toml` | C3 (Node 1) + C2 (Node 4), NUCLEO Clock: the default DEV profile on the boards: `SYNC_TX` in cell 0 only, one per 200 s phase, no `TX_DENIED`, and a cold C2 locks (`CLK to=WARM`) within 12 min. Run 2026-10-04: PASS (see Runs) |
| `tools/bench/scenarios/sync-profile-dev-burst.toml` | The boot burst option (`SYNC_BOOT_BURST=5u`) on the same pair: `SYNC_TX` in cells 1 to 4 seen (cell 0 leaves before the run arms) and none in cell 5 or later, and the C2 booted after the C3 reaches `CLK to=WARM` within 3 min instead of about 10. Run 2026-10-04 with a burst of 5: PASS, WARM at +66.6 s instead of +606.6 s (see Runs) |
| `tools/bench/scenarios/sync-dev-soak-swapped.toml` (shared with `spot-20261007-node2-as-c3.md`) | The multi-hour C3 + C2 half of the criteria on the DEV profile, 12 h, Node 2 as C3 and Node 5 as C2 (the roles are swapped for the node-silence investigation, #101): no `TX_DENIED` after the boot burst, and the C2's `SYNC_RX` per phase counted from the record. Not run yet |
| `tools/bench/scenarios/sync-schedule.toml` (to write once the schedule is chosen) | C3 + C2 for several hours: no `TX_DENIED` on Sync slots after the boot burst, and the C2's `SYNC_RX` rate equals the schedule's |

## Hardware

Checked against `bench boards` on 2026-10-02: no probe connected.

| Item | Needed |
|---|---|
| Node ? flashed as C3 | yes |
| Node ? flashed as C2 | yes |
| Host on AC power, Windows sleep off, for the several-hour run | yes |

Hands on the bench: plugging the two boards in.
No CubeMX regeneration.
The worst-case relay airtime of a 4-hop line is a host computation here, checked on hardware in the #76 record.

## Finding (2026-10-02, from #34)

The bring-up table (two Sync phases of ten cells, a cell every 3 s, C3 and a relaying C2 sending in the first three cells of each) is 9.9 % duty cycle: 5.95 s of airtime per 60 s frame against the 1 % limit.
The 36 s compliance credit is gone after 6.1 min, then each node sends one packet per 99 s and the other Sync slots are `TX_DENIED`; the traces of 2026-09-29 and 2026-10-01 show the first denial 6.1 to 6.6 min after boot.
`Tests/unit/test_sync_budget.c` computes it with the real compliance engine and table.
This issue must replace the table; until then a multi-hour run uses the Build Overrides `SYNC_TX_BUDGET=1u` and `SYNC_CELL_GAP_MS=9500u` (a Sync phase every 120 s, 0.83 %, see `34-runtime-calibration.md`).

## Sync profiles (2026-10-04)

The schedule is now a compile-time parameter, ADR-0020: `DEV` (the default, 200 s, one packet per node per phase, 0.496 % of the band) for development, `PROD` (540 s, provisional) for the product, `BRINGUP` (the 9.9 % table above) for the host tests.
`Tests/unit/test_sync_budget.c` runs the real compliance engine and table for each: BRINGUP is denied after 6.1 min, DEV and PROD never in 4 h.
This issue replaces the provisional `PROD` numbers.

## Criteria

- [ ] The residual drift rate from #34 and the Sync period derived from it, in the ADR.
- [ ] A documented budget: Sync airtime per hour per node class (C3, the worst relaying C2) at most the band's duty cycle, with margin.
- [ ] Host: the table lint of #56 passes for each class.
- [x] Bench, multi-hour C3 + C2: no `TX_DENIED` on Sync slots after the boot burst. (DEV profile, about 18 h, `spot-20261007-node2-as-c3.md`; the PROD numbers stay provisional)
- [x] Every scheduled Sync packet is received by the C2 (except genuine radio loss), so its `SYNC_RX` rate equals the schedule's. (DEV profile: 327 of 327 after the C2 was up, one per 200 s phase, in about 18 h; the PROD numbers stay provisional)
- [ ] ADR for the chosen schedule and period.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-04 19:23 | `sync-profile-dev.toml` (first version) | `59384f3` | FAIL (scenario) | `tools/arclog/runs/20261004T192509Z-59384f3/` | The profile worked: `BOOT sync=DEV`, `SYNC_TX ce=0` only, one per 200 s, no `TX_DENIED`; the C2 (Node 4) went to ACQ at +206 s on the second packet. The scenario counted the C3's first packet, which leaves about 6 s after its boot, before the run is armed and before the second board is up, so `count = 3 within 9m` saw 2. Counted from the second packet and the window moved to 11 min; to re-run |
| 2026-10-04 19:37 | `sync-profile-dev-burst.toml` (burst of 3, the first version) | `59384f3-o36b106` | FAIL (finding) | `tools/arclog/runs/20261004T193711Z-59384f3-o36b106/` | The C3 sent the burst: `SYNC_TX ce=0,1,2` 20 s apart. The C2 booted 8 s after the C3, missed `ce=0`, took `ce=1` as the RTC set (ACQ) and `ce=2` as good, and had no third packet: WARM only a phase later. A burst of 3 does not serve boards flashed one after the other: the scenario now uses 5 (ADR-0020); to re-run |
| 2026-10-04 20:50 | `sync-profile-dev-burst.toml` (burst of 5) | `818967e-o954ffd` | PASS | `tools/arclog/runs/20261004T205046Z-818967e-o954ffd/` | The C2 (Node 4) reached `CLK to=WARM` at +66.6 s after arming, against +606.6 s without the burst (next row): about 9 times faster. `SYNC_TX ce=1` at +25.3 s and `ce=4` at +85.3 s, 20 s apart, none in cell 5 or later, no `TX_DENIED`. The C2 booted 8 s after the C3 and missed `ce=0` as before; cells 1, 2 and 3 gave the set and two good packets |
| 2026-10-04 20:55 | `sync-profile-dev.toml` (counted from the second packet) | `818967e` | PASS | `tools/arclog/runs/20261004T205510Z-818967e/` | The DEV profile on the boards: `BOOT sync=DEV`; `SYNC_TX ce=0` at +205.5, +405.5 and +605.5 s (one per 200 s phase, none in cell 1); the C2 at ACQ +206.7 s and WARM +606.6 s (10.1 min, the cold-start cost of one packet per phase); no `TX_DENIED`, `TX_LATE` or silence |
| 2026-10-07 14:06 | `sync-dev-soak-swapped.toml` | `e34092f-o954ffd` | PASS | `tools/arclog/runs/20261007T140655Z-e34092f-o954ffd-collector/` | About 18 h, Node 2 as C3 and Node 5 as C2, DEV profile with the boot burst: no `TX_DENIED`, `TX_LATE` or `SYNC_SILENCE`; the C2 received 327 of the 327 Sync packets the C3 sent after the C2 was up (one per 200 s phase). Judged from a rebuilt record, see `spot-20261007-node2-as-c3.md` |
