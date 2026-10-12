# Issue #60: one capture process records every port for 24 h

Purpose: acceptance. Issue: #60 (open, deferred: not urgent as of 2026-10-02).

Code done (`4f71ff3`, `1fc6e1f`); a reset check passed on 2026-09-29. Left: the issue's 24 h criterion.

## Schedule

Decision 2026-10-12 (Simon, comments on #60 and #76): the 24 h check is done inside the session of #76 "Phase 1 baseline test", with its 4 to 5 active nodes, and not on its own.
Until then boards are unplugged and replugged while iterating, which would only break a 24 h window.
When the baseline starts, the always-on capture stays running and untouched for the whole session (no `bench capture up --replace`, no restart), the host stays on AC power with sleep off, and the criteria below are scored on the capture files of that session.

This record is stale for Pi Nodes and is rewritten before then: it still names three USB boards (COM6, COM8, COM9), a USB replug of the COM6 board and a `SLOT` every 3 s.
A lone C3 now logs a `SLOT` about every 20 s and the Pi a `[pi]` heartbeat line, so the gap rule takes each port's cadence from its first hour.
The marks do not show a dead TCP link as down (see "Finding" in #86 "Run manifest and validity check for long runs"): the first 1.5 h of the capture of 2026-10-12 give a first data point (resets of four boards, one Pi outage logged once in `capture.err`), kept in the run records of that day.

## Scenarios

No scenario file: the check is on the always-on capture itself (`bench capture`), not on a `bench run`.

## Hardware

| Item | Needed |
|---|---|
| Three boards on USB (COM6, COM8, COM9 on 2026-09-29; `bench boards` decides) | yes |
| The COM6 board (dead radio, trace UART only) | yes, for the replug step |
| Host on AC power, Windows sleep off, USB selective suspend off, for the whole 24 h | yes |

Hands on the bench: one USB replug of the COM6 board (step 3).

## Protocol

1. T0: preconditions met; `bench capture status` shows one process recording all three ports. Write down its PID and the UTC time.
2. T0 + about 1 h: `bench reset 2`. Write down the UTC time.
3. T0 + about 2 h: unplug the COM6 board's USB for 30 s, then plug it back. Write down both UTC times.
4. Leave it until T0 + 24 h. `bench run`s may happen in the window: they read the capture files and never open a port.
5. T0 + 24 h: `bench capture status` shows the same PID still recording all three ports.

## Criteria

- [ ] **Coverage.** For each port, the files of both UTC days together run from T0 to T0 + 24 h; the first host timestamp of the second day's file is within 5 s of the last of the first day's.
- [ ] **No silent gap.** A board running the TDMA machine logs a `SLOT` every 3 s, so a gap over 10 s in host timestamps on COM8/COM9 is a capture fault. For COM6, take its cadence from the first hour. The only allowed gap is COM6 during the replug (unplugged time + one 2 s reconnect period).
- [ ] **Reset.** At step 2, COM9 has both cores' `BOOT` and `CORE_SYNC stage=linked`; COM6 and COM8 keep recording through that minute.
- [ ] **Replug.** `capture.err` logs COM6 unavailable once (not at every retry); COM6 lines resume within 5 s of the replug.
- [ ] **Downstream unchanged.** `arclog merge` on the six files prints one time-ordered stream; `arclog report` on them completes with no schema problems and no lost lines on the C3 and C2.
- [ ] **Errors.** `capture.err` holds only the startup lines, the COM6 replug, and port outages that recovered on their own.

## Findings

- 2026-09-29: the capture stopped after 52 min (both ports at once, `ClearCommError PermissionError(13)`, never recovered in the files).
- 2026-10-01 21:23:39-21:30:55 UTC: the same error on both ports at once; the Windows System log shows the host entering sleep at that instant and resuming at 21:30:55. The laptop was on battery (sleep after 10 min on battery, 15 min on AC). The capture (pid 14892) reconnected both ports by itself after the resume. The 2026-09-29 drop is very likely the same sleep.
- Since 2026-10-01 the host has sleep off on AC. Sleep on battery is still 15 min: keep it on AC for any long run.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-09-29 00:16 | always-on capture | `66a35a2-d67ece1` | FAIL (bench) | `tools/arclog/runs/bench/com8-20260929.log`, `com9-20260929.log` | Stopped after 52 min, both ports at once. |
| 2026-10-01 21:16 | always-on capture | (several) | outage, recovered | `tools/arclog/runs/bench/capture.err` | Host sleep 21:23:39-21:30:55; reconnected on resume. |
