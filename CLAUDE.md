@AGENTS.md

## Agent skills

### Commit conventions

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
(`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, etc.). Scope is
optional but encouraged when the change is focused (`fix(tdma):`,
`docs(context):`).

### Closing out an issue

When an issue is solved, follow "Closing out an issue" and "Keeping the repo clean" in `AGENTS.md`.
In Claude Code, put the proposal of the next issue to Simon with AskUserQuestion (the recommended issue first), and write the paste-ready message only after he picks one.

### Issue tracker

Issues live in GitHub Issues (`github.com/NORTHGUARD-Interreg-NPA/ArcLoRaM`). See `docs/agents/issue-tracker.md`.
Always write an issue as its number and a condensed title ("#73 bench builds alternate ok/fail"), never the number alone: see "Naming issues" in `AGENTS.md`.

### Triage labels

Default label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Hardware list for scenarios

Every scenario or bench run you propose to Simon comes with its hardware list:
each board by Node ID with the class it is flashed as, the probes and COM ports it uses, anything beyond plugged-in boards (cables, a USB hub, an antenna or attenuator, a power switch), and every step that needs hands on the bench (a replug, a CubeMX regeneration).
Check the list against `bench boards` and say which items are missing today.

### The repo holds every test fact

Every test protocol, result, finding and run record name lives in the repo first: `docs/test-tracker/` (Test Records) and `tools/bench/scenarios/`.
An artifact or shared doc is only a view of them: write the fact to the repo before (or with) the artifact, never to the artifact alone.

Test workflow: `docs/test-tracker/README.md` (test levels, issue-record-scenario link, verdicts, closing rule). Read it before writing a Test Record or a scenario, and before closing an issue on a bench verdict.

### Timing measurements

Timing on the boards (a budget, a latency, time lost in a write) is measured, not estimated: skill `timing-probe` (`PROBE_START` / `PROBE_MARK` / `PROBE_LOG` of `Common/Log/probe.h`, compiled in only by the Build Override `BENCH_PROBE=1`, logged as `PROBE` events; numbers kept in the Test Record).

### Domain docs

Single-context layout — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
