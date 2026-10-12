---
name: bench
description: Build, flash and test the firmware on the bench boards through `tools/bench`. Use when a firmware change needs a build, when it must be verified on hardware, when the user asks to flash, reset or run a scenario, or when board traces must be read.
---

# Bench loop

`bench` is the only way to build, flash or reset the firmware (`AGENTS.md`).
It works on a Build Tree of its own on `C:` (one per worktree) and on the boards of this machine and of its configured Pi Nodes; the developer's CubeIDE can stay open.
Several sessions (worktrees) use the bench at once: a command holds a lease on each board it flashes or resets until it ends, and a board another session holds is refused.
Command details, the scenario format and the guard live in [`tools/bench/README.md`](../../../tools/bench/README.md); `uv run --project tools/bench bench <cmd> --help` is authoritative for flags.
Vocabulary (Build ID, Scenario, Run, Action, Smoke Check) is in `tools/bench/CONTEXT.md`.

Run commands from the repo root as `uv run --project tools/bench bench ...`.
A run takes minutes: start it with `run_in_background`, and read its output file when notified (progress lines are flushed as they happen).

## Start with the map

`bench map --open` shows the boards connected on this PC and on Pi Nodes, and which are free, held or recorded by another session, in the user's browser (`bench boards` is the text form).
Choose the boards, the class of each and the Build Overrides yourself when the test is simple: a smoke check, a reset, reading a trace, a rerun of a scenario that already names them.
Ask the user only for an opinionated choice: which board is the C3, or sits at which hop of a line; an override value that changes what the test measures; a run that holds boards for hours.
Ask with `bench choose --propose ID=CLASS ... -D NAME=VALUE ...`, your recommendation first: it opens a page where the user picks the class of each free board and the overrides, and prints `choice: --node ... -D ...` for you to pass to `bench run`.
If it exits 2 (no answer in time), ask in chat instead.
The C3 is the board connected to this PC unless the user says otherwise; the other classes go to Pi Nodes, or to other local boards when there are no Pi Nodes.
Say in the report which choices you made.
A Pi Node has one operator at a time, as a local board has: the lease is the only coordination `bench` needs.

The Pi Nodes bench looks for are listed in `tools/bench/pi-nodes.toml` (shared through the repo, MagicDNS short names only), plus any in `~/.config/bench/bench.toml` (this machine; an entry with the same name overrides the shared one).
A node you build goes in the shared file, so nobody rediscovers it; `bench boards` lists the ones that do not answer.

**A new Pi Node board** (each one, once):
1. A board with the factory firmware is flashed by hand with ArcLoRaM first (on a PC, ST-LINK USB, no Pi wires on it), then wired to its Pi.
2. `bench boards --probe-uid <Pi Node name>` reads its UID; add it to `Common/Protocol/node_id.c` with the next free Node ID.
3. `bench run tools/bench/scenarios/onboard.toml --as <Node ID> --probe-uid <Pi Node name>` (about 8 minutes), then `bench flash --node <Node ID>=C3` and `bench reset <Node ID>`.
4. A row in `docs/test-tracker/spot-20261010-onboard-board.md`.
Do not touch the boards or the Pis' power during a run: an unplugged board or a dropped Pi invalidates it.

## The loop

A task that changes firmware behaviour runs this loop, at most 5 build-flash-check cycles; then stop and report what is known.
Every run, whatever its purpose (acceptance, performance or spontaneous), leaves a trace in its Test Record (`docs/test-tracker/README.md`: file naming, format, where each purpose reports on GitHub).

1. **Scenario.** Write or pick a scenario that goes _red_ on the problem and _green_ on the fix: boards by Node ID, the class each is flashed as, and an `[[expect]]` or `[[forbid]]` that names the behaviour itself, not just a boot.
   Start from `tools/bench/scenarios/`; a one-off can be flags (`--node 2=C2 --expect "2 CLK to=WARM within=8m"`), `--save` it when it is worth keeping.
   Its header comment names the purpose and the issue.
   Done when `bench scenario check <file>` prints the plan you intend, and the Test Record lists the scenario, its hardware and the criteria it covers.
2. **Board state.** `bench boards` lists every board bench can reach, a local probe or a Pi Node, with its Node ID and last Build ID, and ends with the count; tell the user the count and the Node IDs before the first run.
   A board another session holds shows `HELD by <worktree>: <command>`: it is not yours, so pick other boards or wait, and leave its capture alone (`--replace` is for a port you need).
   `bench capture status` must show every board recorded, by this capture or another's (`bench capture up` otherwise).
   A board with an unknown UID is a new board: `--probe-uid <id>` reads that one (a Pi Node: a GDB session; an ST-LINK board: it reboots), then add it to `Common/Protocol/node_id.c` with the next free Node ID.
   A node whose capture file has had no new line for over 60 s while its port answers is a node silence: add a row to `docs/test-tracker/node-silences.md` (the fourth gets an investigation), then reset it.
   Done when every Node ID the scenario names is listed and free; a scenario that needs a board `bench boards` does not list says so, it is not cut down.
3. **Run.** `bench run <scenario>`: it builds the working tree (uncommitted changes included), flashes, fires the actions and decides.
   Done when it exits: 0 PASS, 1 FAIL, 2 TIMEOUT, 3 invalid scenario, 4 invalid run.
4. **Read the verdict**, then fix and go back to 3:
   - build failure: the diagnostics are printed with repo paths; the build line prints the path of the full log (`logs\build-<UTC time>-<Build ID>.log` in the worktree's Build Tree, kept for run and flash as well).
   - `FAIL`: the reason names the node, the line and the time (`+12.3s`); read the node's lines around it in the record, `tools/arclog/runs/<start>-<build>/<node>-*.log`.
   - `TIMEOUT`: the reason lists what never happened; check the record for why (a board not booting, a peer silent, a sync never reached).
   - lost lines (`N line(s) lost`) are a firmware trace problem, not noise: find which lines vanished before changing the scenario.
   A verdict is evidence about the firmware; change the scenario only when it expected the wrong thing, and say so.
   Exit 4 is an invalid run: a bench fault (a port down for over 10 s, a host that was not running, a clock step, a Pi clock off) or, in a `dataset = true` scenario, a firmware event.
   The report opens with the cause, and `bench validate <run dir>` gives it again from the record; it is no verdict on the firmware: put the cause in the Runs row and run again.
   `bench note "<text>"` adds a hand-made fact (a board moved, a replug, the ambient temperature) to the running session's manifest.
   A dead radio or any other bench reason the checks cannot see is still no verdict: note the cause and run again.
5. **Trace.** Every run, PASS or not, gets a row in the Test Record's Runs table: date, scenario, Build ID, verdict, record path, and what the record showed beyond the verdict.
   On GitHub, the issue the Test Record names gets a comment per decisive verdict: scenario, Build ID, verdict, the criteria it settles with the trace lines that show it, and the Test Record path.
   An acceptance issue is closed once every criterion in its Test Record is ticked.
   Done when the Runs row and the comment exist.
6. **Report.** The loop ends on PASS or at the cycle limit, with: the verdict, the Build ID, the record's `report.md` path, the diff, and anything the traces showed beyond the task.
   Commit only when the user asks.

## Guard

Every programmer call goes through the allowlist in `tools/bench/src/bench/programmer.py`: named probe, UID read, ELF write with verify, reset.
When bench refuses an operation, that refusal is final: report it to the user.
Option bytes, OTP, readout protection, security and mass erase are out of reach by design, and stay that way.

## Traces without a run

The bench capture writes every board's trace to `tools/arclog/runs/bench/<node>-YYYYMMDD.log` (UTC days; `<node>` is the COM port or a Pi Node's name; in a worktree that path leads to the main checkout's shared folder).
Read those files directly, by polling (`tail`, `grep`, `arclog view`), or check a live condition with `uv run --project tools/arclog arclog expect <file> --dir tools/arclog/runs/bench --since <UTC time> --follow`.

## Pi Node login (TEMPORARY, plaintext)

This file is tracked in the public repo: Simon chose on 2026-10-06 to commit and push the login anyway, and will secure it later.
When it is secured, change the password on the Pi: removing it here does not remove it from the git history.

`nuna-node-03` (verified 2026-10-06; its tailnet address is in `~/.config/bench/bench.toml`): user `user`, password `arcloram`, SSH password login (no key installed).
`sshpass` is not installed here: pass the password through an askpass helper written in the scratchpad, and delete it after the call.

```sh
printf '#!/bin/sh\necho arcloram\n' > "$S/askpass.sh"; chmod 700 "$S/askpass.sh"
SSH_ASKPASS="$S/askpass.sh" SSH_ASKPASS_REQUIRE=force ssh -o PubkeyAuthentication=no \
  -o PreferredAuthentications=password,keyboard-interactive user@<tailnet address> '<read-only command>'
rm -f "$S/askpass.sh"
```

The Pi runs two systemd units: `openocd.service` (GDB 3333, the M0+ on 3334, config `/etc/openocd/rpi-swd.cfg`) and `uart-log.service` (the UART log on TCP 4000).
Look first (`systemctl status`, `journalctl -u openocd`); change nothing on the Pi unless Simon asks.
A raw TCP connect to port 3333 is a GDB connection for OpenOCD (one at a time): probe with `bench boards`, not with `/dev/tcp`.
After a Pi power-up the board is held in reset (#103 "A Pi Node board stays held in reset"): a zero UID, a silent trace and a dead LED, with GPIO18 low on the Pi.
Run `bench reset <Node ID>` first; it releases the board, and the next `bench flash` passes.
