# Tx spread between two nodes at the same depth, on hardware

Purpose: performance. Issue: #46 "Concurrent-transmission experiment and Phase 2 final run" (step H3); the figure also feeds #76 "Phase 1 baseline test" (R1, neighbour drift).

## Requirement

Stated by Simon on 2026-10-07: the Production network must reach a depth of about 60, and for the Sync to work the maximum offset between two nodes at the same depth stays strictly under 8 ms, preferably around 5 ms.

- 8 ms is the Tier 1 threshold (0.25 T_S at SF12/BW125, `SYNC_PARTICIPATE_THRESHOLD_MS`). 5 ms is R1 of #76.
- ADR-0009 sets the concurrent-transmission ceiling at 16 ms (0.5 T_S) and writes the budget as `2 x depth x per-hop error < 16 ms`. Taken as a worst case that adds linearly, 8 ms at a depth of 60 needs a per-hop error under 67 us and 5 ms needs 42 us, against a stamp noise of 106 us (1 sigma, NUCLEO Clock), a tick of 244 us and the 1 ms an uncorrected Sync error can leave. So either the same-depth offset does not grow linearly with depth (two nodes that hear the same flood share its reference), or the per-hop error falls by about 20 times. This record measures which.
- The current table carries 10 cells per Sync phase (DEV 200 s, PROD 540 s), so a depth of 60 is beyond what one phase holds: that is a schedule question (#49, #51) and not part of this record.
- The criterion is in the acceptance list of #46 since 2026-10-07 and is copied below word for word.

## Why

The only spread measured so far is the C3 to C2 link: the C2's `erru` (its clock against the C3's packet).
The spread between two relays at the same depth, and its growth per hop, has not been measured.
That is the quantity concurrent transmission depends on, and no scenario reads it.

A finding of 2026-10-07 (NUCLEO Clock) shows the correction matters.

| Build | Correction of a Sync error under 8 ms | Evidence |
|---|---|---|
| `main` (`e34092f-o954ffd`, the swap soak) | none: a Tier 1 error is stored, not corrected | Node 5 as C2, 117 Tier 1 packets from 14:09 to 20:28 UTC: `erru` walks from +959 us to -4168 us (-0.22 ppm, a rate residual of -214 ppb below the 715 ppb `CALR` write threshold), 67 packets beyond 1 ms, no `RTC_SHIFT`. Run record `tools/arclog/runs/20261007T140655Z-e34092f-o954ffd-collector/`, the always-on capture `tools/arclog/runs/bench/nuna-node-01-20261007.log` |
| `feat/36-rx-guard` (`dcfaeba-o954ffd`, the failed 6 h run) | a shift from 1 ms (`SYNC_CORRECT_THRESHOLD_MS`) | Node 2 as C2, 70 Tier 1 packets in 3 h 46 min: `erru` -750 to +1447 us, 2 `RTC_SHIFT`, the next packet after each at -750 us and -18 us. `tools/arclog/runs/20261005T224004Z-dcfaeba-o954ffd/` |

Measured on the same soak (2026-10-08): `erru` kept walking at -0.2 ppm and crossed -8 ms at 02:11:51 UTC (-8074 us), where the first Tier 2 packet appeared (`act=t2`, one `RTC_SHIFT`, no relay for that occurrence).
It was +471 us at 02:51 UTC and walking again, at -3436 us at 08:18 UTC.
So without the correction from 1 ms a C2 sits between the Tier 2 correction and 8 ms, and the offset to its sender is only bounded by the 8 ms of Tier 1.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/tx-spread.toml` (to write) | C3 + C2a + C2b + a receiver for several hours, build with the correction from 1 ms: the Tx start difference between C2a and C2b in every Sync phase, its maximum and distribution, against 8 ms and 5 ms; no Tier 2 packet |
| `tools/bench/scenarios/tx-spread-uncorrected.toml` (to write) | The same on `main`: the control, where the difference is expected to grow with the rate residual until Tier 2 corrects it at 8 ms |

How the difference between two relays is read is open, and the first run settles it:

1. **Receiver stamps, one relay at a time.** The receiver's `erru` on a packet is the sender's Tx start against the receiver's clock; the receiver's own error cancels in the difference of two packets a few seconds apart. It needs a test-only knob (a Build Override) that lets only C2a relay in one phase and only C2b in the next, as #46 H4 needs a relay delay knob. The receiver cannot separate two relays that send in the same cell: it sees one superposed packet.
2. **A logic analyzer on the Tx-enable (RF switch) pin of each relay**, on one time base. It reads the on-air start directly, independent of the stamp, and needs the pins and an analyzer; neither is in `bench boards` or known on the bench.

## Hardware

Checked against `bench boards` on 2026-10-07: 2 boards connected, with known Node IDs; a third Pi Node does not answer.

| Item | Needed | Today |
|---|---|---|
| Node ? flashed as C3 | yes | missing: Node 2 is connected on a Pi Node and held by the 12 h swap soak until 02:13 UTC on 2026-10-08 |
| Node ? flashed as C2a | yes | missing: Node 5 is connected and held by the same soak |
| Node ? flashed as C2b | yes | missing: no third board connected |
| Node ? flashed as C1 (the receiver, as in #46 H3) | yes | missing: no fourth board connected |
| Logic analyzer on two Tx-enable pins | only for method 2 | unknown |
| Host on AC power, Windows sleep off, or the Pi Nodes' collector for the long run | yes | per session |

Boards known to the Node ID table: Nodes 1, 2, 4 and 5 worked on the bench; Node 3 is suspected of a dead radio (2026-09-28) and is not used until a swap clears it.
Hands on the bench: connecting two boards, and placing the four so that C2a and C2b both hear the C3 and the receiver hears both.
No CubeMX regeneration.
Every figure of this record is a NUCLEO Clock result and does not transfer to the Production Clock.

## Criteria

From #46:

- [ ] H3 reports the offset between the Tx starts of two nodes of the same depth (C2a and C2b), per Sync phase, with its maximum and distribution, and the same offset at each depth of a line of C2 (#76 Phase 1 baseline test): strictly under 8 ms and preferably about 5 ms at every depth measured. The depth the Production network can reach is stated from the measured growth against the `2 x depth x per-hop error` budget of ADR-0009, which is corrected in place if it does not hold.

How this record covers it:

- [ ] The method that reads the Tx start difference of two nodes is settled, with its own noise measured, and recorded here.
- [ ] Bench, C3 + C2a + C2b + a receiver, several hours, correction from 1 ms: the offset between C2a and C2b (two nodes of depth 1) in every Sync phase, with its maximum, distribution and count, against 8 ms and 5 ms.
- [ ] The same run on `main` as a control, reported beside it: the offset grows with the rate residual until Tier 2.
- [ ] The offset per depth (depth 1, 2, 3 and 4 with four C2 and a C3 on a line, then as many as the bench holds; needs the line of #76), read with the same method: linear, as the ADR-0009 budget assumes, or flat because the nodes of one depth share the reference of the flood they heard.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
