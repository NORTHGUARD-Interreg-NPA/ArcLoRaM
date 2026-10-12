# Issue #76: Sync Phase 1 baseline, C3 + 4 C2 in a line

Purpose: acceptance. Issue: #76.

The protocol, the three requirements (R1 neighbour drift below 5 ms, R2 guard from the drift estimate, R3 no mistiming loss) and the open design points are in the issue.
This record holds what the repo keeps for the test: scenario, hardware, criteria, runs.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/phase1-baseline.toml` (to write once `bench` runs 5 boards and #34, #45, #36 are in) | One long session with staged early checks: formation and hop depth per node, first intervals within the 5 ms bound and the guard range, then forbids for the rest (Tier 3, `SYNC_LOST`, `SYNC_SILENCE`, unplanned `BOOT`, lost lines). Build Overrides: none beyond the adopted Sync frequency and guard ratio, which are the firmware's own values |

## Hardware

Checked against `bench boards` on 2026-10-02: no probe connected; the bench holds at most 4 boards (6 once more are bought).

| Item | Needed | Today |
|---|---|---|
| Node ? flashed as C3 | yes | to pick |
| Node ? flashed as C2a (hop 1) | yes | to pick |
| Node ? flashed as C2b (hop 2) | yes | to pick |
| Node ? flashed as C2c (hop 3) | yes | to pick |
| Node ? flashed as C2d (hop 4) | yes | missing: 5th board, a prerequisite, over the bench limit of 4 |
| 5 probes and 5 capture ports reachable by `bench` | yes | missing: the line runs on a remote setup being prepared by a collaborator |
| Host (or remote host) up for the whole session | yes | per session |

The boards are the current NUCLEO boards, with no RF hardware added.
Hands on the bench: placing the 5 boards on the remote setup; a replug is a planned event noted in the manifest.
No CubeMX regeneration in Phase 1.

## Criteria

- [ ] Session executed and stored; invalid sessions kept with their cause.
- [ ] The line is proven by the trace: hop depth per node (cell of the first epoch) matches the line, no non-neighbour reception.
- [ ] R1: neighbour offset (`err` at Sync reception) below 5 ms; maximum and distribution per link, accumulation per hop depth.
- [ ] R2: guard of every Rx slot logged; ratio and drift estimate stated; guard distribution reported.
- [ ] R3: zero mistiming losses; every other miss attributed to a radio cause; per link received / scheduled with a confidence interval.
- [ ] RSSI and SNR stored for every reception; per link and per hop depth distributions and time series in the report; signal RSSI logged; failed receptions and empty windows done or decided (the RSSI/SNR section of the issue).
- [ ] Calibration behaviour: rate estimate and CALR over the session, residual drift.
- [ ] Time from power-on to `CLOCK_WARM` per depth.
- [ ] No Sync `TX_DENIED` after the boot burst, or each explained; airtime per node per hour.
- [ ] Dataset (raw captures, per-reception CSVs, manifest) and report under `docs/experiments/<date>-phase1-baseline/`; firmware commit tagged `phase1-baseline`.
- [ ] Questions Phase 1 could not answer listed for Phase 2.

## Also scored in this session: #60

Decision 2026-10-12 (Simon, comments on #76 and #60): the 24 h capture check of #60 "Multi-port capture in one process" is done in this session, not on its own.
During the session the always-on capture stays running and untouched (no `bench capture up --replace`, no restart) and the host stays on AC power with sleep off.
The criteria of `60-multi-port-capture-24h.md` are scored on this session's capture files, and #60 is closed from this run.
A replug of a node is a planned event noted in the manifest (`bench note`): it is the replug step of #60 too.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
