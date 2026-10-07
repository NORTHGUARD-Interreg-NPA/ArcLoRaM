# Test tracker

One Test Record per bench test, committed here, so every scenario that was run keeps its purpose, its hardware and its verdicts in git.
Run records under `tools/arclog/runs/` stay on this machine; a Test Record names them, it does not replace them.

## Bench preconditions

Every bench test assumes the host on AC power with Windows sleep off: a sleep cuts every COM port at once and the capture loses the run (2026-10-01, see `60-multi-port-capture-24h.md`); the capture's marks show it, and the run is then invalid.
Boards are named by Node ID; `bench boards` gives their probes and ports.

## Purpose

Every Test Record has exactly one purpose, which sets its file name and where its verdicts are reported:

| Purpose | File | Reported on GitHub |
|---|---|---|
| **acceptance**: proves an issue's acceptance criteria | `<issue>-<slug>.md` (`54-midnight-c3-c2.md`) | that issue: a comment per verdict; close it when every criterion is met |
| **performance**: measures a figure (drift, timing, current) | `perf-<slug>.md` (`perf-tx-start-delay.md`) | the issue the figure feeds, if any, as a comment |
| **spontaneous**: a one-off check outside any plan | `spot-<YYYYMMDD>-<slug>.md` | the issue it turns up or touches, if any, as a comment |

A spontaneous test that finds a defect becomes an issue; its Test Record then links it.

## Test levels

A claim about the firmware is proven at the cheapest level that can see it.

| Level | Proves | Lives in | Runs |
|---|---|---|---|
| **Host test** | logic: protocol, MAC, TDMA, estimators, conversions | `Tests/unit/` | seconds, every change |
| **Acceptance scenario** | one claim only hardware can show (radio, RTC, timing, boards talking), on 1 to 4 boards | `tools/bench/scenarios/` and a Test Record | minutes to hours, per issue |
| **Phase gate** | the long run that closes a roadmap phase: every board, at least 24 h, a dataset and a report | a scenario, a Test Record, `docs/experiments/` | once per phase |

The smoke check is part of every run.
There is no level above: the bench is manual and shared, so there are no scheduled or nightly runs.

- **Push logic down.** A criterion about logic that only a bench run can check is a design smell. Pull the logic into a pure module behind a seam (as `rtc_set_plan.h` does) so a host test covers it, and keep the scenario for what remains.
- **The regression suite is derived, not listed.** It is every scenario with a `timeout` of 10 min or less and at most 2 flashed nodes. Run it before landing a change under `CM0PLUS/SubGHz_Phy/`, `Common/Protocol/` or the RTC and timer code, and before closing a Phase 1 issue. Until `bench` has one command for it, run those scenarios by name.

## Issue, Test Record, scenario

Three things, each with one job:

- The **issue** is the requirement: what, why and the acceptance criteria. Its title carries the roadmap phase, `[1]` now, `[2]` next, `[3]` backlog. That is the only place a priority is written: a copy in a record would drift.
- The **Test Record** is the evidence: hardware, how each criterion is covered, every run. It copies the issue's criteria word for word. When a criterion changes, the issue changes first.
- The **scenario** is the executable: one claim per scenario. Its first comment lines name the purpose, the issue and the Test Record.

File names pair by slug: record `<issue>-<slug>.md`, scenario `<slug>.toml` (several: `<slug>-<variant>.toml`).
A record's state is read, not declared: no Runs row means planned; Runs but unticked criteria means in progress; every criterion ticked, each with a PASS run in Runs, means done.

## Workflow

1. **Slice.** When a plan is cut into tickets (`/to-tickets`), every ticket with a hardware criterion gets its Test Record in the same change, Hardware table included and checked against `bench boards`. What is missing is said then, not at the first run. A ticket without a record is host-only.
2. **Triage.** An issue is `ready-for-agent` when its host seams are named and, if it has a hardware criterion, its Test Record exists. Missing hardware blocks only the bench part, and the Hardware table says so.
3. **Implement.** Host tests first, at the seams (`/tdd`), then the bench loop (skill `bench`, at most 5 cycles). A fix starts with its scenario run red on the unfixed build; that run is a Runs row with "red before fix" in Notes.
4. **Evidence.** A Runs row per run. A GitHub comment per decisive verdict, on the issue: scenario, Build ID, verdict, the criteria it settles, the record path. Decisive numbers are copied into the Test Record, because run records are local and may be lost.
5. **Close.** Every criterion ticked, each with its PASS run in Runs: the Build ID in the row says which build proved it, so a pass can be reproduced from that commit.
6. **Phase gate.** The gate issue (the baseline of its phase) starts when every other issue of the phase is closed. It ends with the dataset and report committed under `docs/experiments/<date>-<slug>/`, the firmware commit tagged, and the questions the phase could not answer listed for the next one.

## Verdicts

- **Acceptance**: pass or fail on criteria that come from a requirement, fixed before the run.
- **Baseline and performance**: measured values with their counts and confidence intervals. A threshold exists only where a requirement defines it, and is never set after the data is seen.
- **Invalid run** (a host sleep, a dead radio, a wrong expectation): stays in Runs with its cause, gets no GitHub comment, and is run again.
  `bench run` and `bench validate <run dir>` compute it and exit 4: a port down for over 10 s, a host that was not running, a step of the host's clock, a Pi clock off or a gap in the log collector, and, in a scenario with `dataset = true`, a firmware event (lost lines, an unplanned reboot, a wrong Build ID).
  A dead radio or a wrong expectation is still judged by hand.
  The check and the manifest format are in `tools/bench/README.md` (Run record, manifest and validity).
- **A finding** becomes an issue; a spontaneous record links it.
- **A node that stops logging** while its capture is up is counted in `node-silences.md`; the fourth is investigated.
  A scenario sets `max_silence` to make `bench run` fail on it; the data stays valid, since it is a verdict on the firmware.

## Scenario rules

- Name the behaviour, not the boot: expectations on trace events and their fields, `forbid` for what must never happen in the run.
- One claim per scenario. A second claim that needs other boards or overrides is a second scenario in the same record.
- A long run (hours) fails early or not at all: stage its expectations (smoke, formation, first intervals, then whole-run forbids) so a bad run ends within the first hour.
- Overrides only for what the scenario varies; the Build ID carries their hash.
- A scenario that needs more boards than `bench boards` shows is written and marked missing in the Hardware table, never reduced silently.

## Test Record

```markdown
# <issue or topic>: <what is tested>

Purpose: acceptance | performance | spontaneous. Issue: #<n> (or none).

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/<file>.toml` | <the behaviour, in trace terms> |

## Hardware

| Item | Needed |
|---|---|
| Node <id> flashed as <class>, probe <serial> | yes |

Hands on the bench: <unplug, replug, CubeMX regeneration, or none>.

## Criteria

- [ ] <one per acceptance item or measured figure, each with the host test or scenario that covers it>

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-01 21:38 | `midnight-c3-c2.toml` | `2421333-o8f3c06` | PASS | `tools/arclog/runs/20261001T213849Z-2421333-o8f3c06/` | <what the record showed beyond the verdict> |
```

Runs are newest last and never deleted: a failed or invalid run stays, with its cause in Notes (a host sleep, a dead radio, a wrong expectation).
