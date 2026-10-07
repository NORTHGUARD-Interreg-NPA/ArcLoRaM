# bench

Host tool that builds, flashes and tests the ArcLoRaM firmware on the bench boards without a human in the loop.
It is what lets an agent (Claude Code) close the edit, build, flash, check loop on its own.

The design and the reasons behind it are in `docs/adr/`, the vocabulary in `CONTEXT.md`.
This folder holds everything about the bench automation: the records stay out of the firmware's own `docs/adr/` and `CONTEXT.md`.

## Status

`bench build`, `boards`, `capture`, `flash`, `reset` and `run` with scenarios work (#62, #63, #64).
Work is tracked as GitHub issues with the `bench` label.

## Setup

```sh
cd tools/bench
uv sync
uv run pytest
```

Run it from anywhere in the repo with `uv run --project tools/bench bench ...`.

## Build

```sh
bench build --class C2                      # both cores of Debug_C2
bench build --class C2 --class C3 -D TX_RAMP_MS=5u
bench build --class C3 --clean              # rebuild everything
```

1. Rsyncs the repo (uncommitted changes included) into the Build Tree, `C:\Users\Simon\arcfw-bench\tree` (a worktree has its own, `arcfw-bench-<worktree>`), without `.git`, `tools`, `Tests`, `docs`.
   Build outputs (`Debug_*`) and the override header in the tree are kept, so builds are incremental.
   So are the folders CubeIDE creates for the projects' linked files (`CM4/Drivers`, `CM4/Utilities`, `CM0PLUS/Middlewares/...`, read from each `.project`): the repo has none of them, and a sync that deleted them made the next build lose the HAL and Utilities sources (issue #73).
2. Computes the Build ID (`a1b2c3d`, `-d<hash>` for uncommitted firmware changes, `-o<hash>` for overrides) and writes it with the overrides to `Common/Bench/bench_overrides.h` in the tree, only when it changed.
3. Builds both cores of each configuration with `stm32cubeidec.exe` headless, in its own workspace (`arcfw-bench\workspace`): the developer's CubeIDE can stay open.
4. Maps every compiler diagnostic back to a repo path, and checks that each ELF contains the Build ID.
   An image without it after an error-free build holds stale objects (make misses a header that appeared after the objects were compiled); that configuration is rebuilt clean once.

Exit code 0 when every image is built and carries the Build ID, 1 otherwise.
Every build, whether from `bench build`, `bench run` or `bench flash`, ok or failed, keeps its CubeIDE command lines and full output in `arcfw-bench\logs\build-<UTC time>-<id>.log` (a worktree: `arcfw-bench-<worktree>\logs`), and prints the path.
The time is in the name so a rebuild of the same sources never overwrites the log of the build before it.
A first build takes about a minute, a build with nothing to recompile about 20 s.

## Boards

```sh
bench boards                  # probe, COM port, UID, Node ID, last build
bench boards --probe-uids     # also read unknown UIDs over SWD (reboots those boards)
```

A board is found by its ST-LINK serial number; its trace COM port is the one whose USB parent is that probe (Windows device tree).
Its UID comes from the last CM0+ `BOOT` in the capture, and its Node ID from the firmware's table, `Common/Protocol/node_id.c`, parsed in its strict one-entry-per-line format.
Reading a UID over SWD needs a connection under reset: the firmware sleeps in STOP2, where the debug port is off.

## Capture

```sh
bench capture status
bench capture up              # start or complete the always-on capture
bench capture up --replace    # also stop another capture holding the ports
```

One multi-port `arclog capture` on Windows records every connected probe's port into `tools/arclog/runs/bench/` of the main checkout (a worktree's `tools/arclog/runs` leads there) (files `com9-YYYYMMDD.log`, stderr in `capture.err`).
It is started detached (`Win32_Process.Create`) and outlives the session.
`up` restarts the bench capture when a port is missing from it, and never stops a capture it did not start unless told to (`--replace`).

## Flash

```sh
bench flash --node 2=C2                  # build Debug_C2, flash Node ID 2, check its boot
bench flash --node 1=C3 --node 2=C2 -D TX_RAMP_MS=5u
```

1. Finds the boards, brings the capture up, builds the classes needed.
2. For each board, reads its UID over SWD and refuses to flash if it is not the expected Node ID (a board moved since its last boot).
3. Writes and verifies both cores' images in one programmer session (CM0+ then CM4), then resets.
4. Waits (`--timeout`, 60 s) for both cores to `BOOT` the new Build ID as the assigned class, linked, with no lost line (the arclog `expect` engine); a reboot of the old image during the flash is waited through.

Exit code 0 when every board booted the build, 1 otherwise.

```sh
bench reset 2                            # reset Node ID 2, no flash
```

## Run

A run flashes a scenario's boards, fires its actions, decides it from the capture and keeps a record.

```sh
bench scenario check scenarios/c2-rejoin-after-reset.toml    # validate and show the plan, no board touched
bench run scenarios/c2-rejoin-after-reset.toml
bench run --node 1=C3 --node 2=C2 --expect "2 CLK to=WARM within=8m" --save scenarios/mine.toml
```

Exit code 0 pass, 1 fail, 2 timeout, 3 invalid scenario, 4 invalid run (see [Run record, manifest and validity](#run-record-manifest-and-validity)).
Progress is printed as it happens (`ok ...`, `act ...`, `note ...`), and the record goes to `tools/arclog/runs/<start>-<build>/`: `report.md`, `manifest.toml`, `run.log`, `scenario.toml`, each node's capture lines of the run window (readable by `arclog report`) and the capture's marks for the run's ports.

### Scenarios

Boards are named by Node ID.
The smallest scenario is two lines:

```toml
[nodes]
2 = "C2"
```

A fuller one ([`scenarios/`](scenarios/) has examples to copy):

```toml
description = "C2 rejoins after a reset"
timeout = "20m"                  # default 10m
dataset = false                  # true for a session whose data is kept: see below
max_silence = "5m"               # optional: a node quiet this long (its port up) fails the run; default off

[nodes]
1 = "C3"                         # flash as C3
2 = "C2"                         # flash as C2
3 = "watch"                      # not flashed: its trace is checked, it keeps its image

[overrides]                      # Build Overrides for this run only
TX_RAMP_MS = "5u"

[[action]]
reset = 2                        # reset Node ID 2...
after = { node = 2, event = "CLK", where = { to = "WARM" } }
delay = "10s"                    # ...10 s after that line (or: at = "+90s" after arming)

[[expect]]
node = 2                         # optional: any node
event = "CLK"
where = { to = "WARM" }          # optional: field values; a number range, bounds inclusive:
                                 #   where = { err = { min = -1, max = 1 } }
count = 2                        # optional, default 1
within = "18m"                   # optional: from arming, default the timeout

[[forbid]]
event = "TX_LATE"
```

- The Smoke Check is always on: every flashed board boots the run's Build ID on both cores, as its class, linked, with no lost line.
- Every reset must reboot its node (the run cannot pass before it), and no other reboot is allowed.
- `max_silence` is off unless the scenario sets it, because schedules differ.
  A node that logged nothing for longer than it, with no capture gap in that interval, has stopped: the run fails (a verdict on the firmware, exit 1) even if every expectation was met, and the data stays valid.
- `dataset = true` marks a session whose data is kept (a phase baseline).
  Lost lines, an unplanned reboot and a board that never booted the run's Build ID then invalidate the run instead of only failing it; without the key they fail the run and its data stays valid.
  A fault of the bench itself (the capture, the host, a clock, the collector) invalidates every run.
- Event and field names are checked against arclog's event table, with a suggestion for a typo (`unknown event 'SYNC_RXX' (did you mean SYNC_RX?)`).
- A range never matches a field that is not a number; `{ min = 2 }` or `{ max = -2 }` alone bound one side.
- On the command line, `--expect "<ID|any> <EVENT> [field=value ...] [within=8m] [count=2]"`, a range as `err=-1..1`, `err=..-2` or `err=2..`, `--watch ID`, `--forbid EVENT`, `-D NAME=VALUE`, `--timeout`; `--save FILE` writes them as a scenario file.

Reset is the only action for now.
Halting a core needs a hot-plug SWD connection, which fails while the firmware sleeps in STOP2 with the debug port off; it needs the firmware to keep debug alive in STOP2 first.

### What bench never does to a board

Every programmer call goes through one allowlist (`src/bench/programmer.py`): connect to a named probe, read the chip UID, write an ELF image, verify, reset.
Everything else is refused before the programmer runs, in particular anything that cannot be undone by flashing again:

- option bytes: readout protection (level 2 is permanent), write protection, boot configuration, security;
- OTP, the one-time programmable area (`0x1FFF7000`-`0x1FFF73FF`);
- mass erase, direct memory writes, binary files written at a given address.

A Pi Node follows the same rule: `src/bench/pinode.py` builds every GDB command list itself and checks it against a grammar before GDB runs.
It allows the connection to the configured endpoint, `monitor reset halt|run`, a read of the three UID words, `file <image>.elf`, `load`, `compare-sections` and `detach`.
It refuses every other `monitor` command (mass erase, option bytes), memory writes, `load` with an argument, and `shell`, `python` or script files.
A `MIS-MATCHED` section fails the flash whatever GDB's exit code says.

Before writing, every loadable segment of each image must fall inside its own core's half of main flash (CM4 `0x08000000`-`0x0801FFFF`, CM0+ `0x08020000`-`0x0803FFFF`); anything else, including a swapped image, is refused.

## Judging from the log collector

A run of many hours does not need the host on: the log collector (a server that stores every Pi Node's UART log) keeps the record while the host is off.
`bench flash` arms the boards, then the scenario is decided later from the collector's lines, with the same expect file as `bench run` (`expect_spec`) and the same engine (arclog's `expect`):

```sh
BENCH_COLLECTOR_URL=<base URL of the collector> uv run --project tools/bench python -I -m bench.collector \
  scenarios/x.toml --build <Build ID> --since <UTC time before the flash> \
  --node 2=nuna-node-03 --node 5=nuna-node-01 --dir tools/arclog/runs/<start>-<build>-collector
```

Exit code 0 pass, 1 fail, 2 no verdict yet, 3 an invalid scenario or argument, 4 the record is invalid; it can be run at any time, as often as wanted.
It prints per node the lines, the last line and its age, the `BOOT`s and the `SLOT` count per hour, and a `WARN` line for every cause it finds.
A bench fault makes the run invalid, exit 4 whatever the verdict, because the record says nothing about the firmware:
- a Pi clock not NTP-synchronised, or more than 5 s from the collector's;
- a Pi Node the collector does not know, or is not connected to;
- lines lost across a collector reconnect after the start (a reconnect alone is not one: the Pi replays what it buffered, and the trace's sequence numbers say whether that was enough).

A firmware event is listed as a `WARN` too: lost lines, a reboot nobody planned, a board that never booted the Build ID.
It fails the run as the engine says; it invalidates the run (exit 4) only when the scenario says `dataset = true`.
A node fault does not invalidate the run.
With `max_silence` in the scenario, a node with no line for longer than it fails the run (exit 1) even when every expectation was met.
Without it the tool only warns when the collector has seen no line from a connected node for over 10 min.
The collector writes the Pi's local time without a zone and matches `since=` against it: the tool converts through `--pi-tz` (default `Europe/Paris`) and flags a Pi clock that differs from the collector's receive time.
A node that stops logging is caught by `max_silence`, or by a staged `[[expect]]` on a count of its `SLOT` events.

## Run record, manifest and validity

A long session has to say what it was, and whether its data can be used, without anyone reading the capture by hand.
`report.md` is for a person.
`manifest.toml`, written when the boards are armed and completed at the end, is the structured copy, to be read a year later.
It holds no tailnet address: a Pi Node is named, never addressed, because a manifest is committed with its dataset.

| Table | Holds |
|---|---|
| `[run]` | the command, the worktree, the bench commit, the Build ID, the firmware commit and whether it was dirty, the Build Overrides, the scenario keys (`dataset`, `max_silence`, `timeout`), the window (`start`, `end`), the verdict and the exit code |
| `[[board]]` | per board: Node ID, role (`flashed`, `watched` or `not in the scenario`), class, kind (`stlink` or `pinode`), UID, probe (the ST-LINK serial, or the Pi Node's name), COM port (not for a Pi Node), the capture's name for it (`log`), when it was flashed, and the `BOOT` line of each core as logged |
| `[controller]` | UTC beside the host's monotonic clock at the start and at the end, and the time service's state at both (`w32tm /query /status`: source, last sync, stratum, poll) |
| `[[pi_clock]]` | per Pi Node, when `BENCH_COLLECTOR_URL` is set: what the collector reports of its clock (`pi_ntp`, `clock_lag_s`) at the start and at the end |
| `[[note]]` | the notes made by hand during the session (`bench note "text"`), each with its UTC time |
| `[validity]` | `valid`, the exit code, every cause (`kind`, `fault`, `node`, `text`, `start`, `end`) and what could not be checked |

`bench note "the board was moved to the window"` appends a timestamped line to the running session: the newest record folder whose manifest has no `end` yet and whose timeout has not long passed.

### Validity

`bench validate RUN_DIR` computes the verdict and the causes again from the record alone, with no board, capture or network; the end of `bench run` does the same on the record it has just written, so the two agree.
It prints whether the run is valid, what the trace alone gives, every cause, and says if the exit code stored in the manifest differs.
A record from before manifests is refused (exit 3); one from before marks is judged on the rest and says what it could not check.

Every cause has a fault, which decides what it does to the run:

| Fault | Causes | Effect |
|---|---|---|
| `bench` | a port down for more than 10 s (`port_down`); no marks for more than 30 s, the capture or the host was not running (`no_marks`); a step of UTC of more than 1 s against the monotonic clock (`utc_step`); a Pi clock not NTP-synchronised or more than 5 s from the collector's, a Pi Node the collector does not know or lost, lines lost across a collector reconnect (`pi_clock`, `collector_node`, `collector_gap`) | always invalidates |
| `firmware` | lost lines, an unplanned reboot, a board that did not boot the run's Build ID (`lost_lines`, `unplanned_reboot`, `wrong_build`) | fails an acceptance run; invalidates a run whose scenario says `dataset = true` |
| `node` | a node with no line for longer than the scenario's `max_silence`, with no capture gap in the interval (`node_silent`) | fails the run, even if every expectation was met; never invalidates |

A node silent while its port is up is a node fault: a quiet node cannot be told from a capture outage by its lines, only by the marks.

Exit codes of `bench run`, `bench validate` and the collector judge: 0 pass, 1 fail, 2 timeout (the collector judge: no verdict yet), 3 invalid scenario or not a record, 4 invalid run.
An invalid run has no verdict on the firmware: it stays in the Test Record's Runs table with its cause and is run again.

## Map

```sh
bench map                     # writes tools/arclog/runs/bench-map.html and says where
bench boards --json           # the same data as JSON
```

One self-contained page (it follows the viewer's light or dark theme) of what is connected: the probes on this PC and the Pi Nodes on the tailnet, each board with its Node ID, UID, last build and whether it is free, held by another session (with the command and since when), a new board, or not answering.
A board recorded by another worktree's capture is marked as possibly in use, because a session on code from before leases takes none.
It is a snapshot of the moment it was made; `bench map` makes a new one.
`bench map --open` also opens the page in the browser (under WSL, the Windows one).

### Choosing a deployment

```sh
bench choose --propose 1=C3 --propose 2=C2 -D TX_RAMP_MS=5u
```

Asks the user which board runs as which class, and with which Build Overrides, in a page that opens by itself, and brings the answer back:

1. It surveys the boards (as `bench boards`), starts a server on `127.0.0.1` and opens the page in the user's browser.
2. The page shows the boards as the map does. Each free board has a class menu (not in the run, C3, C2, C1, watch only) with the recommendation preselected, and there is a field for overrides and one for a note.
3. The user presses **Send to the agent**. The command prints `choice: --node 1=C3 --node 2=C2 -D TX_RAMP_MS=5u`, then the answer as JSON, and exits 0; the options go straight to `bench run` or `bench flash`.
4. No answer in `--timeout` (default 15 minutes) exits 2; a recommendation that is not possible exits 3 (a board held by another session, or not connected).

Without `--propose` the recommendation is the C3 on the lowest free local board and C2 on every free Pi Node.
A board held by another session, a new board without a Node ID and a Pi Node that does not answer are shown but cannot be picked.
A board another session's capture records can be picked, with a warning.
`--no-open` only prints the address.

The server accepts only the Host names of loopback, answers under a secret path, takes one valid answer and stops; the answer is checked against the boards that were shown before it is accepted.
The strategy is that of the Lavish editor (a page opens, the user answers in it, the answer comes back to the agent); nothing here needs npm or the network.


## Several sessions at once

Agents work in parallel worktrees and share the boards (ADR-0003).
Each worktree has its own Build Tree, and the capture and the run records are shared, in the main checkout.
A board has one writer at a time:

- `flash`, `reset`, a UID read over SWD and `run` hold a lease on each board they flash or reset until they end; a run only watching a board holds a shared lease on it.
- A board another session holds is refused at once: `bench run: 4 is held by 82-fine-stamps: bench run ... (pid 53266, since 19:43:20 UTC) - another session works on it: use other boards, or wait`.
- `bench boards` marks such a board `HELD by <worktree>: <command>`, and a UID read leaves it alone.
- A lease is released by the system when its command ends, however it ends, so there is nothing to clean up.
- A capture that another worktree started and that holds only other boards is left alone; `bench capture up --replace` takes ports from it and is for a board this command needs.

## Remote boards (Pi Nodes)

A board wired to a Raspberry Pi (`github.com/Nuna-Systems/Pi-node`: OpenOCD for SWD over the Pi's GPIO header, the UART as a TCP log server, both on the tailnet) is a Board like any other.
Its ST-LINK is bypassed, so bench reaches it by name, not by probe serial number or COM port.
The design and its open points are in `docs/adr/0002-remote-boards-through-pi-nodes.md`.

```toml
# ~/.config/bench/bench.toml, or the file named by $BENCH_CONFIG
[[remote]]
name = "nuna-node-01"     # capture node and display name (default: host)
host = "nuna-node-01"     # MagicDNS name or tailnet address
gdb  = 3333               # OpenOCD GDB port of the M4 (default)
log  = 4000               # UART log port (default)
```

- The file says where to look.
  Node IDs and classes stay out of it: a board is recognised by its UID, and its class is the scenario's.
- `bench boards` lists a Pi Node whose log port answers as `pi-node <name>`, and names the ones that do not answer.
  It opens only the read-only log port.
  The GDB port is a debug session, opened to read a UID (`--probe-uids`), flash or reset.
- `bench capture up` records a Pi Node's log with the local ones, in `<name>-YYYYMMDD.log`.
  Each line is stamped with the controller's UTC clock; the Pi's own stamp (its local time, no zone) is dropped.
- UID read, flash and reset go through `src/bench/pinode.py`: GDB (CubeIDE's `arm-none-eabi-gdb.exe`, or `$BENCH_GDB`) to the Pi's OpenOCD.
- A new board's first run is onboarding: read its UID, add it to `Common/Protocol/node_id.c` with the next free Node ID, flash once.
  `bench boards --probe-uid nuna-node-02` reads that one board's UID (a GDB session); `--probe-uids` reads every unknown board, which on a Pi Node is a debug session on a shared board.
- OpenOCD's own README, and where its command reference is, are in `docs/reference/`.
- Status: host-tested, and run end to end on `nuna-node-02` on 2026-10-04 (UID read, flash of both cores, a scenario run, a reset; `docs/test-tracker/spot-20261004-pinode-c2.md`).
  Open: the Pi's clock stamps local time (the live stream does not use it) and the Pi-side recorder for long runs (ADR-0002).

## Overview

`bench` is a Python CLI (uv project, like `tools/arclog`) run from WSL.
It drives only Windows tools through WSL interop, so the ST-LINK probes and their serial ports stay on Windows and nothing is attached with `usbipd`:

- `stm32cubeidec.exe` (STM32CubeIDE headless build),
- `STM32_Programmer_CLI.exe` (flash, UID read, reset, halt),
- `arclog` through `uv.exe` (capture and checks).

Commands:

| Command | Does |
|---|---|
| `bench build` | Syncs the working tree to the Build Tree and builds the configurations a run needs |
| `bench flash` | Flashes both cores of the named boards from one build, then resets them |
| `bench run <scenario>` | Build, flash, arm, fire the scheduled actions, check the expectations, report |
| `bench reset <node>` | Resets or halts/resumes a board without flashing |
| `bench capture up` / `status` | Starts or checks the always-on capture of every board |
| `bench map` | Draws the connected boards, local and on Pi Nodes, and whether each is free (`--open` opens the page) |
| `bench choose` | Opens a page where the user picks the class of each board and the overrides, and prints the answer as options for `run` |

A quick run without a scenario file uses the same machinery:

```sh
bench run --node 1=C3 --node 2=C2 -D TX_RAMP_MS=5
```

## Rules

- `bench` is the only way to build or flash the firmware from an agent: never the toolchain, `make` or `STM32_Programmer_CLI` directly.
- `bench` never writes option bytes or OTP, never changes readout protection or security, and never mass erases (see [What bench never does to a board](#what-bench-never-does-to-a-board)).
- `bench` never commits: a run ends with a report, and the human decides what goes in.
- `bench` never touches a board another session holds, and never takes a port from another capture unasked.
