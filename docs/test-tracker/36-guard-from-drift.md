# Issue #36: Rx guard from the drift estimate, on hardware

Purpose: acceptance. Issue: #36.

The clock is the NUCLEO Clock (CONTEXT.md): every figure of this record is a NUCLEO result and does not transfer to the Production Clock.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/guard-warm-window.toml` | C3 (Node 5) + C2 (Node 2), DEV profile with the boot burst: a C2 in `CLOCK_WARM` closes an empty Rx window one guard after nominal. The estimate is not valid yet, so the guard is the cap: `RX_WIN g=100` with `win` in 150..200 ms, against about 1700 ms while acquiring; no `RX_LATE`, `SLOT_SUSPECT`, `TX_LATE`, `TX_DENIED` |
| `tools/bench/scenarios/guard-from-drift.toml` | C3 (Node 5) + C2 (Node 2) for 6 h: every Sync packet the C3 sent (`TX_DONE`) is received or lost for a radio reason, never because the C2's window was not open on it; the guard of every Rx slot is logged (`g`, `win`) and falls to about 10 ms once the estimate is valid; no Tier 3 or `SYNC_LOST`. Counts of locked packets are staged every 10 so that a silent node ends the run within one stage |
| `tools/bench/scenarios/guard-from-drift-swapped.toml` | The same 6 h with the roles swapped (the C3 on Node 2, the C2 on Node 5), run after the first one stopped on a node silence of Node 2 as a C2 (`node-silences.md`, event 2): it tells the board from the role |
| flags: `--node 5=C3 --node 2=C2 -D BENCH_PROBE=1 -D SYNC_BOOT_BURST=5u --expect "2 PROBE tag=rx_open seg=setrx count=4 within=5m"` | The Rx start latency (the part of it from the slot task to a radio that listens), measured with `timing-probe`; the wake part is the `wake` of `SLOT`. The probes stay in `tdma_machine.c` (`rx_slot`, `rx_open`) and compile to nothing without `BENCH_PROBE` |

## Hardware

Checked against `bench boards` on 2026-10-05: 2 boards connected, with known Node IDs.

| Item | Needed | Today |
|---|---|---|
| Node 5 flashed as C3 (Pi Node) | yes | connected |
| Node 2 flashed as C2 (Pi Node) | yes | connected |
| Host on AC power, Windows sleep off, for the several-hour run | yes | per session |

The swapped variant flashes the same two boards the other way round: Node 2 as C3, Node 5 as C2.
Hands on the bench: none while the two Pi Nodes stay connected.
No CubeMX regeneration.
The Rx start latency measurement (`timing-probe`) needs the same two boards.

## Host level (branch `feat/36-rx-guard`)

- `test_guard_policy` (8 tests, built with the Rx start latency pinned to 0 so that its values do not move with the measured one), `test_guard_policy_latency` (a 2 ms latency), `test_guard_policy_default` (the constants as they ship): the guard from a struct literal as the estimate, every expected value worked by hand from the inputs.
  The shipped guard of the NUCLEO pair on DEV is 10 ms (3 x 1635 us + the 5000 us of latency).
  With the latency at 0: an invalid estimate gives 100; the NUCLEO case (0.36 ppm, 200 s, 106 us) is worth 5 ms by the formula, so the 8 ms floor of the Tier 1 band sets it; 5 ppm over 540 s gives 13 ms; the sign of the residual does not matter; noise 0 uses the 400 us design floor (16 ms); the cap at 100; an exact guard of 2^32 + 10 ms cannot wrap into 10 ms (a 32-bit result did, red before the fix); a peer link doubles the uncertainty (26 ms); a 2 ms latency is added after the ratio (15 ms, not 19).
- `test_tdma_machine_c2`: with the estimate stubbed at the link (`stub_drift_estimator.c`), the early wake is one guard before nominal, the `CLOCK_WARM` window ends at nominal + guard with the cap unchanged, while acquiring the guard stays the cap whatever the estimate says, the guard grows with the time since the last Sync (13 ms at 540 s), and `RX_WIN` / `RX_LATE` carry `g` and `win`.
- `test_tdma_machine_c2_phases` (a Mesh_Beacon phase with a CONTENTION footer): outside Sync the guard is the doubled one (44 ms against 22 ms), and a contention slot keeps the geometric window end.
- `test_mac_state_machine_c2`, `test_mac_fine_stamp_c1` / `_c2`: a 5 ms error is corrected and still relays; the correction starts between 4 ticks (977 us) and 5 ticks (1221 us), both signs; the shift hook receives the error in microseconds (50 049 us for a packet 50 ms late, after midnight).
- `tools/arclog` `test_schema`: `RX_WIN` and `RX_LATE` agree with the firmware.
- The build: C1, C2 and C3 compile (Build ID `a419c9b-dc1bd27`, no warning in a file of this change); the first build of each pair tripped the #73 alternation (link errors on the utilities, none on a symbol of this change) and passed on retry.

## Result (2026-10-05, Node 5 as C3 and Node 2 as C2, NUCLEO Clock)

**The window end on hardware** (`guard-warm-window.toml`, build `a419c9b-dc1bd27-o954ffd`).
The estimate is not valid yet in a run this short, so the guard is the cap (100 ms): the test is of the closing instant, not of the guard value.

| | Before (builds up to `1aba9a2`) | After (this build) |
|---|---|---|
| Empty window, from `RX_WIN` to `RX_TIMEOUT`, device clock | median **1939 ms** (p10 1937, p90 1967, max 1985), n = 214 | **466 ms** and **458 ms**, n = 2 (predicted 2 x 100 + 262 = 462) |
| Window while acquiring | `win` about 1708 (geometric end) | `RX_WIN g=100 win=1708`, unchanged |
| First window in `CLOCK_WARM` | geometric | `RX_WIN g=100 win=200`; its packet was received (`SYNC_RX act=t1 erru=-18`, preamble detected at nominal + 74 ms, inside `nominal + 100`) |

The "before" windows are every `RX_WIN` to `RX_TIMEOUT` pair of this C2 in the always-on capture before this run, timed on the device clock: the capture's own timestamps carry the transport delay of a Pi Node (they read 57 to 3704 ms for the same pairs).
No `RTC_SHIFT` in the run: the errors were under the 1 ms correction threshold (`erru` -18 us after the set).
At the floor (g = 8 ms) the window is predicted at 2 x 8 + 262 = 278 ms: to be measured once the estimate is valid, in the several-hour run.

## Result: the 6 h run (2026-10-05 22:40 to 2026-10-06 04:40 UTC), failed on a node silence after 3 h 46 min

`guard-from-drift.toml`, build `dcfaeba-o954ffd` (a commit), Node 5 as C3 and Node 2 as C2, DEV profile with the boot burst, NUCLEO Clock.
The run failed on its count of locked packets (68 of 100), because the CM0+ of Node 2 stopped at 3 h 46 min (`node-silences.md`, event 2) and a scenario cannot see a silent node until #86; it held the boards 2 h 14 min more.
The 3 h 46 min before the stop are data:

| | |
|---|---|
| Lock | `CLK to=WARM` at +71.9 s |
| Estimate valid | `DRIFT ok=1` at +1412 s, 23.5 min (n = 10, baseline 1361 s), earlier than the 33 min of ADR-0020 |
| Guard (`RX_WIN g`), 608 windows | 100 ms in 62 (acquiring, and until the estimate was valid); **10 ms in 168, 11 ms in 375, 12 ms in 3**: every window with a valid estimate under the 15 ms target of #46 |
| Empty window at the real guard, open to `RX_TIMEOUT`, device clock | g = 10: median **286 ms** (284 to 292, n = 150); g = 11: median **288 ms** (286 to 295, n = 331); g = 12: 290 ms (n = 2). Before: median 1939 ms (n = 214): **85 % less** |
| Packets of the C3 received by the C2 | 72 sent after the C2 booted, **71 received**, matched by (phase, cell, epoch); the one not received is the first of the run (`ph=0 ce=0 ep=86100032`), which leaves before the C2 is up (#70), so it is no mistiming loss |
| Locked packets | 68 `act=t1`; `erru` -750 to +1447 us, median +227 us |
| The 1 to 8 ms band | 2 `RTC_SHIFT`: packets at `erru` 1447 us (6 ticks) and 1203 us (5 ticks), each corrected and each followed by the C2's own relay at cell 1 (`SYNC_TX ph=1 ce=1`) |
| Wake, `wake` of `SLOT` | 0 ms in 593 of 609 Rx slots, 2 ms in 16, never more |
| Estimator at the end | `DRIFT n=48 base=9400 rate=-9355 resid=182 noise=111 ok=1 trim=0` |
| Never seen | `RX_LATE`, `SLOT_SUSPECT`, `TX_LATE`, `TX_DENIED`, `SYNC_SILENCE`, `CLK to=COLD`, a Tier 3 packet |

The window shows g = 10 or 11 ms where this record said "10 ms for the NUCLEO pair": the noise of this pair is 250 to 111 us, against the 106 us of the first pair, and the formula gives 11 ms at 250 us.
The criterion of the several-hour bench run stays open: it needs a PASS run.

## Measurement: the Rx start latency (2026-10-05, `timing-probe`, Node 2 as C2, NUCLEO Clock)

CM0+ at 4 MHz (`hz=4000000`), a C2 in the first minutes after a lock (the Rx slots of the DEV burst), build `a419c9b-db764f8-o9bf158` (`BENCH_PROBE=1`), run record `tools/arclog/runs/20261005T175430Z-a419c9b-db764f8-o9bf158/`, n = 4 Rx slots.
The path from the slot task to a radio armed in Rx, one `PROBE` segment each (microseconds since the previous stamp):

| Segment | Probe | us (4 slots) | What it is |
|---|---|---|---|
| channel | `rx_slot chan` | 934 to 1305 | steps 2 to 6 of the slot task, up to and including `RadioSetChannel` |
| MAC | `rx_slot dec` | 76 to 91 | `MAC_OnSlotOpportunity` |
| log | `rx_slot slotlog` | 4948 to 5009 | the `SLOT` line alone |
| guard | `rx_open guard` | 265 to 413 | the resolver and the window end (the new code) |
| log | `rx_open log` | 5320 to 5398 | the `RX_WIN` line alone |
| radio | `rx_open setrx` | 2686 to 2704 | `RadioSetRx`: the radio wake, the command, the timer |

- **Without the two log lines** (a node that logs less): 1305 + 76 + 265 + 2704 = 4350, 4332, 4495 and 934 + 89 + 413 + 2704 = 4140 us: **4.14 to 4.50 ms**.
- **With them** (a bench build, every level on): 14.5 to 14.8 ms (the first probe of the same day: 14 459 to 14 766 us).
- **The wake before it**, alarm to slot task, is the `wake` of `SLOT`: 0 ms in 2039 of 2091 Rx slots of this C2 on 2026-10-04 and 05 (under 1 ms), 1 ms in 4, 2 ms in 48, never more.
- **Not visible to a CPU probe:** what the radio does after `RadioSetRx` returns (TCXO, PLL).
- `GUARD_RX_START_LATENCY_US` is set to **5000 us**, the maximum without the log lines, rounded up. With it the guard of the NUCLEO pair on DEV is 10 ms (4905 + 5000 us), and the formula's minimum is 9 ms, so the 8 ms floor of the Tier 1 band does not bind.

**What the preamble does for the window** (252 received packets of this C2 in the always-on capture, 2026-10-04 to 06): the preamble is detected after the packet start in two groups.

- The radio was already listening (the cap guard, or an older build; 183 packets): **67 to 105 ms**, median 72.
- The radio started listening after the packet had begun (the adaptive guard of a bench build, 69 packets; the window opens at nominal - g and the two log lines delay the radio by 10 ms, so it listens from about nominal + 5 ms): mostly **129 to 134 ms**, median 131 (minimum 90). By guard: g = 10, 131 to 132 ms (18 packets); g = 11, 129 to 132 ms (41).

Joining a preamble in progress therefore costs about 57 ms of detection, and the packet is still received.
The radio's timer stops on detection and runs to `nominal + guard + 262`, so a packet that starts at nominal + e is caught when `e` plus the detection time is under `guard + 262 ms`: up to about guard + 128 ms late at the slowest detection seen.
On this bench the reach is far wider than the guard; the guard is the margin of the design (the Tier 1 band, the concurrent-transmission budget).
Only strong signals were measured (RSSI -33 to -11 dBm, SNR 5 to 10 dB): a weaker link detects later and its reach shrinks toward the guard itself (not measured).
A packet that starts well before the window opens is not tested (the radio joined by about 5 ms).

## Criteria

- [x] Host: guard from a stub estimator (valid and not valid, several residual rates, time since the last Sync), the cap, the ratio, and the fixed terms. (`test_guard_policy`, `test_guard_policy_latency`, `test_tdma_machine_c2`)
- [x] Host: the threshold bands, including a 5 ms error that is corrected and still relays, and the unchanged 8 ms and resync behaviour. (`test_mac_state_machine_c2`, `test_mac_fine_stamp_c1` / `_c2`, with the Tier 1 and Tier 3 edges unchanged)
- [x] Host: the window end in `CLOCK_WARM` and while acquiring. (`test_tdma_machine_c2`, `test_tdma_machine_c2_phases`)
- [x] Rx start latency measured with `timing-probe` and recorded here. (4.14 to 4.50 ms without the log lines, 14.5 to 14.8 ms with them; the constant is 5000 us; see Measurement)
- [x] `g` and `win` in the trace and the schema; schema test passes. (`test_tdma_machine_c2`, `test_schema`; on the bench: see Runs)
- [x] Existing MAC and TDMA tests unchanged and passing. (unchanged in meaning: the three stubs of the shift hook changed signature, and the one test that checked its arguments now checks the error in us)
- [ ] Bench, C3 + C2 for several hours, after #34: no mistiming loss; guard distribution and empty-window Rx time (before and after) reported.
- [x] ADR: the guard strategy, the ratio, the thresholds; supersedes the "Version 2" section of ADR-0012. (ADR-0022; the ratio stays provisional until the several-hour run)

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-05 17:33 | `guard-warm-window.toml` | `a419c9b-dc1bd27-o954ffd` | invalid | none (the build failed, nothing was flashed) | #73: link errors on the utilities (146 on CM4, 384 on CM0+), none on a symbol of this change; run again |
| 2026-10-05 17:37 | `guard-warm-window.toml` | `a419c9b-dc1bd27-o954ffd` | PASS | `tools/arclog/runs/20261005T173731Z-a419c9b-dc1bd27-o954ffd/` | 151 s: C2 `CLOCK_WARM` at +71.5 s, three `RX_WIN g=100 win=200` (1708 while acquiring), the empty ones timed out after 466 and 458 ms (1939 ms before, median of 214); the packet of the first WARM window was received; no `RTC_SHIFT` |
| 2026-10-05 17:4x (2 attempts) | flags: `--node 5=C3 --node 2=C2 -D BENCH_PROBE=1 -D SYNC_BOOT_BURST=5u --expect "2 PROBE tag=rx_slot seg=rx count=4 within=5m"` | `a419c9b-d384a39-o9bf158` | invalid | none (the build failed twice, nothing was flashed) | #73 alternation: the same build ID compiled when built alone, so not a compile error of the probe |
| 2026-10-05 17:48 | same flags | `a419c9b-d384a39-o9bf158` | measurement | `tools/arclog/runs/20261005T174822Z-a419c9b-d384a39-o9bf158/` | The first probe, one segment `rx` for the whole window open: 14.5 to 14.8 ms from the slot task to the radio armed, log lines included; too coarse to say how much is the radio, so the probe was split |
| 2026-10-05 17:5x | flags: as above with `tag=rx_open seg=setrx` | `a419c9b-db764f8-o9bf158` | invalid | none (the build failed; the first attempt at it never reached `bench`, run from the wrong directory) | #73 alternation, no firmware verdict |
| 2026-10-05 17:54 | flags: as above with `tag=rx_open seg=setrx` | `a419c9b-db764f8-o9bf158` | measurement | `tools/arclog/runs/20261005T175430Z-a419c9b-db764f8-o9bf158/` | The split probe: channel 0.9 to 1.3 ms, MAC 0.09, `SLOT` line 5.0, guard 0.3 to 0.4, `RX_WIN` line 5.3 to 5.4, `RadioSetRx` 2.7; 4.14 to 4.50 ms without the two log lines |
| 2026-10-05 22:3x | `guard-from-drift.toml` | `dcfaeba-o954ffd` | invalid | none (the build failed, nothing was flashed) | #73 alternation, no firmware verdict |
| 2026-10-05 22:40 | `guard-from-drift.toml` | `dcfaeba-o954ffd` | FAIL | `tools/arclog/runs/20261005T224004Z-dcfaeba-o954ffd/` | `SYNC_RX act=t1` count 68 of 100 at 6 h: node silence (event 2): the CM0+ of Node 2 stopped after `SLOT ph=1 ce=8` at 3 h 46 min, the CM4 kept logging; before it, 71 of 71 packets received, guard 10 to 12 ms, empty window median 288 ms (Result) |
| 2026-10-06 06:3x | `guard-from-drift-swapped.toml` | none (nothing was built) | invalid | none | Bench reason, no verdict: both Pi Nodes unreachable from 06:20:36 UTC, the second at which both captures stop (the names resolve, the internet is up, TCP to both log ports gets no answer): both nodes were unplugged at the lab (told by the user). `bench run` refused: no ST-LINK probe, Pi Nodes not answering. Nothing flashed |

The runs of 2026-10-05 ran on the uncommitted tree: the `-d` hash of their Build ID names that state, not a commit.
The commits of 2026-10-06 add to it the timing probes `rx_slot` and `rx_open` (the split probe run already had them) and the measured 5 ms Rx start latency; the runs from the long run on carry the commit.
