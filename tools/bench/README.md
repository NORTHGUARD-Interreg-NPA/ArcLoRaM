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
uv run pytest -m scale    # a record of days: 10 nodes over 72 h, about a minute (after a change to the pull or the judge)
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

A template scenario names the board under test as Node ID 0, and `--as ID` aims it at a real board:

```sh
bench scenario check scenarios/onboard.toml --as 7
bench run scenarios/onboard.toml --as 7 --probe-uid nuna-node-09
```

The record keeps the real scenario (node 7), so `bench validate` loads it again.

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
`bench flash` arms the boards, then the scenario is decided later from the collector's lines, with the same expect file as `bench run` (`expect_spec`) and the same engine (arclog's `expect`).
Two steps, each usable alone:

```sh
# 1. Pull: copy the collector's lines into the run folder. Repeat it as often as wanted, in a shell loop if need be.
uv run --project tools/bench python -I -m bench.pull --collector <base URL of the collector> \
  --build <Build ID> --since <UTC time before the flash> \
  --node 2=nuna-node-03 --node 5=nuna-node-01 --dir tools/arclog/runs/<start>-<build>-collector

# 2. Judge: decide the scenario from the folder. With --collector (or $BENCH_COLLECTOR_URL) it pulls first.
BENCH_COLLECTOR_URL=<base URL of the collector> uv run --project tools/bench python -I -m bench.collector \
  scenarios/x.toml --build <Build ID> --since <UTC time before the flash> \
  --node 2=nuna-node-03 --node 5=nuna-node-01 --dir tools/arclog/runs/<start>-<build>-collector
```

`--no-pull` judges the folder as it is, without asking the collector.
`--until <UTC time>` ends the run when the folder holds a later run of the same nodes.
`--verbose` prints the detail too.
Exit code of the judge: 0 pass, 1 fail, 2 no verdict yet, 3 an invalid scenario or argument (or a collector that cannot be reached), 4 the record is invalid; it can be run at any time, as often as wanted.

### The pull

The collector answers `/logs` with its newest 10000 lines unless it is paged, so a run of hours, or of several nodes, does not fit one answer: a judge that asked once judged a truncated record (the swap soak of 2026-10-07 failed its smoke check for that reason, #111).
The pull asks one node per call, with `after` (the cursor of the previous answer), `format=json` and `exclude=pi` (the Pi's heartbeat), and keeps asking until the collector says `truncated=false`.
`after` pages oldest first from a cursor and there is none for a time, so a node's first pull asks for the newest lines since the start of the run and goes back with `until` while the collector says `truncated`.

The run folder is the record, and it is append-only and idempotent:

| File | Holds |
|---|---|
| `<node>-YYYYMMDD.log` | the node's trace lines, `<UTC>\t<device line>`, by UTC day (what every arclog tool reads) |
| `<node>-events.log` | what the collector saw besides the trace: openocd crash lines, its own connects and disconnects, marks |
| `pull.json` | the build, the schema, where each node's pull stands (its cursor and the time it is complete to), the collector's view of each node at the end of the pull (`pi_ntp`, `clock_lag_s`, `connected_since`, ...), and what makes a node's record unfit; it holds no address of the collector |
| `schema.json` | the event table of the build, see below |
| `judge.log` | the last judge's detail |

- A second pull adds only what the collector got since; an answer that is smaller than what the folder holds never shrinks it.
- A chunk is committed as a whole: its lines are appended, then the cursor is saved. A pull cut short is rolled back to the last committed chunk the next time, so no line is twice in the folder and none is missed.
- The collector stores a line it recovered after a reconnect after the lines that came live, with its own, older, Pi time. The folder keeps the order of arrival and the reader (`arclog.stream`) puts such a line back in place, up to the Pi's replay buffer of one hour.
- It never de-duplicates on the text of a line: two runs of the same build print the same device lines at their start. A line is told by the collector's id.
- It refuses a folder that holds capture files and no `pull.json` (a record of the earlier judge, or of `arclog capture`), a folder pulled for another build, and a start earlier than the folder's: pull into a new folder.

The collector alone is the record of a run decided this way, and its health is part of the validity check below.
The host's capture (`tools/arclog/runs/bench/`) is a backup, read by hand when the collector is in doubt; it is not merged in, because a second source needs a key that tells two lines apart, and the only one both have is the line's text.

### The judge

It streams the folder in time order, the nodes merged, and keeps only what the expectations need: its memory is the same for 18 h and for 72 h of the same nodes, and does not follow the length of the run.
What it prints is of a fixed size: the verdict, one line per node (lines, last line and its age, `BOOT`s, `SLOT` per hour as minimum, median and maximum over the whole hours), the first five causes and the number of the others, the record line.
The stage-by-stage detail (every completed stage, every node's `SLOT` per hour, every cause) is in `judge.log` in the folder.
An agent decides a run from that summary and does not read the record or the stage lines.

A bench fault makes the run invalid, exit 4 whatever the verdict, because the record says nothing about the firmware:
- a Pi clock not NTP-synchronised, or more than 5 s from the collector's;
- a Pi Node the collector does not know, or is not connected to;
- lines lost across a collector reconnect after the start (a reconnect alone is not one: the Pi replays what it buffered, and the trace's sequence numbers say whether that was enough).

A record that is not whole is not judged at all (`INVALID the record is not whole`, exit 4, no verdict), because a verdict on it would be about the export and not about the nodes: a missing `BOOT` is not a node that did not boot.
- a node that was not pulled, or whose pull did not finish (a pull cut short, or still `truncated`);
- a collector that no longer holds the start of the run (its oldest line for the node is after the start of the record);
- a collector whose storage started over or changed generation behind the cursor;
- a pull that could not go on.

Two more causes are found while reading: lines more than the replay buffer's hour out of order (the record cannot be put in time order), and a gap in the sequence numbers right after a place where the pull cut the collector's lines into chunks, which is taken for the export's and not the firmware's (pull into a new folder: if the gap moves, it was the export).

A firmware event is listed as a `WARN` too: lost lines, a reboot nobody planned, a board that never booted the Build ID.
It fails the run as the engine says; it invalidates the run (exit 4) only when the scenario says `dataset = true`.
A node fault does not invalidate the run.
With `max_silence` in the scenario, a node with no line for longer than it fails the run (exit 1) even when every expectation was met.
Without it the tool only warns when the collector has seen no line from a connected node for over 10 min.
A node that stops logging is caught by `max_silence`, or by a staged `[[expect]]` on a count of its `SLOT` events.
The clock the judge uses is the time the pull was complete to, so a stale folder never fails an expectation for lines that were not asked for yet.

#### The schema of the build

The arclog schema changes with the firmware (#36 added `g` and `win` to `RX_WIN`), so a record has to be read with the schema of the build that wrote it and not of the checkout that judges it: a checkout after #36 reported `FAIL RX_WIN: missing g,win` on a healthy run of an older build.
The first pull reads `tools/arclog/src/arclog/schema.py` as the firmware commit of the Build ID had it (`git show <commit>:...`, read as a syntax tree, never run), and keeps the table in `schema.json`.
The judge uses that table and says which one (`schema commit e34092f` in the record line).
When git does not have the commit it uses the checkout's and says so in a `WARN schema`; a dirty Build ID (`-d...`) is read at its commit, and its uncommitted schema changes are not known.

#### What the collector keeps

Read from `GET /nodes` on 2026-10-08, on the soak of 2026-10-07: each node has its own store of up to `max_bytes` = 100 MiB, and `oldest_line_at` and `stored_bytes` say what is held.
The C2 (Node 5, `nuna-node-01`) stored 2.1 MB in 31.5 h, 66 KB an hour; the C3 (Node 2, `nuna-node-03`) 1.2 MB in 31.3 h, 37 KB an hour (a line is about 130 bytes, the Pi's heartbeat included).
At the C2's rate, 10 nodes for 72 h are 48 MB in all and 4.8 MB per node, a twentieth of a store; a store reaches its cap after 64 days of a C2.
What the collector does at the cap was not observed; the pull treats a storage that starts over or changes generation as an unfit record.
`limit` is capped at 1000000 lines per call, which is not a limit of the pull: 72 h of a node are about 52000 lines, fetched 20000 at a time (`--chunk`).
Scale, from a fake collector with the same paging (`pytest -m scale`, on a laptop under WSL2, 2026-10-08): 10 nodes over 72 h are 520450 lines; the pull takes 19 s in 32 requests, the judge 16 s at a peak of 27 MB (the same 27 MB as for 18 h of the same nodes), and prints 13 lines.
The collector knew 8 Pi Nodes on that day (`nuna-node-01` to `08`), 3 of them with data.

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
| `bench` | a port down for more than 10 s, a Pi Node's TCP log link included (the OS drops it 8 s after its last sign of life, or the reader finds no byte for 45 s: `port_down`); no marks for more than 30 s, the capture or the host was not running (`no_marks`); a step of UTC of more than 1 s against the monotonic clock (`utc_step`); a Pi clock not NTP-synchronised or more than 5 s from the collector's, a Pi Node the collector does not know or lost, lines lost across a collector reconnect (`pi_clock`, `collector_node`, `collector_gap`) | always invalidates |
| `firmware` | lost lines, an unplanned reboot, a board that did not boot the run's Build ID (`lost_lines`, `unplanned_reboot`, `wrong_build`) | fails an acceptance run; invalidates a run whose scenario says `dataset = true` |
| `node` | a node with no line for longer than the scenario's `max_silence`, with no capture gap in the interval (`node_silent`) | fails the run, even if every expectation was met; never invalidates |

A node silent while its port is up is a node fault: a quiet node cannot be told from a capture outage by its lines, only by the marks.

Exit codes of `bench run`, `bench validate` and the collector judge: 0 pass, 1 fail, 2 timeout (the collector judge: no verdict yet), 3 invalid scenario or not a record, 4 invalid run.
An invalid run has no verdict on the firmware: it stays in the Test Record's Runs table with its cause and is run again.

## Checking the Test Records

```sh
bench records check              # offline: the records and the scenarios, as text
bench records check --online     # also reads each acceptance record's issue with `gh`
bench --repo <dir> records check # another checkout (the tests use a temporary one)
```

An issue, its Test Record (`docs/test-tracker/`) and its scenario (`tools/bench/scenarios/`) must say the same thing (`docs/test-tracker/README.md`, "Issue, Test Record, scenario").
`bench records check` reads them as text, touches no board, and prints one line per finding, `<repo-relative file>: <message>`, sorted by file, then a summary line.
Exit code 0 when there is no finding (`ok: ...`), 1 on any finding, 3 when the repo has no `docs/test-tracker`.
The check never rewrites a record or a scenario: a finding on a record written before the check is fixed when that record is next touched.

Offline, it checks:

- **File names.**
  A Markdown file of the tracker is `<issue>-<slug>.md` (acceptance), `perf-<slug>.md` or `spot-<YYYYMMDD>-<slug>.md`; `README.md` and `node-silences.md` are not records.
  A record has a `## Scenarios` section, a `## Criteria` section and a `## Runs` table with the six columns of the template.
- **Scenarios table.**
  Every backticked `*.toml` in the first cell exists, unless the cell says "to write".
  A cell that names no `.toml` (a `bench build` command, a host test, "none") is not a scenario file and is left alone.
- **Scenario header.**
  The leading block of `#` comment lines of a scenario names `Issue #<n>` and `Test Record docs/test-tracker/<file>.md` (the path may wrap onto the next comment line).
  The record exists, lists the scenario back, and for an acceptance record the issue is its issue.
- **Round trip.**
  A record that lists a scenario whose header names another record says so in its row ("shared with `<record>.md`"), and that record lists the scenario too.
  A scenario with no record in its header is reported once, on the scenario.
- **Slug.**
  The scenarios whose header names an acceptance record `<issue>-<slug>.md` are `<slug>.toml` or `<slug>-<variant>.toml`.
- **Runs rows.**
  Date: `YYYY-MM-DD HH:MM`; the minute may be imprecise (`17:4x`) and a suffix is free.
  Scenario: a backticked `*.toml` that exists, or free text for a flags form or a host test.
  Build ID: `<commit>[-d<hash>][-o<hash>]`, a list of them, or `none`, `n/a`, `(several)`.
  Verdict: starts with PASS, FAIL, invalid, measurement, red, running or outage, or counts runs (`48 of 48 pass`).
  Record: a `tools/arclog/runs/` path, or `none`.
- **Done means a PASS.**
  A record with at least one criterion, every one ticked, has a Runs row whose verdict starts with PASS (or counts only passes).

With `--online` it also reads the issue of every acceptance record, `gh issue view <n> --json number,state,body` run in the repo (the tracker is the clone's default repo for `gh`):

- The issue's acceptance criteria are the checklist under `## Acceptance criteria` in its body; the `> Copied from ...` lines a copied issue starts with are ignored.
- "Word for word" means the issue's criterion, whitespace normalised, is the start of the record's criterion: the record may append its evidence after ticking.
  An issue criterion with no such record criterion, and a record criterion that starts with no issue criterion, are findings.
- A closed issue has every criterion of its record ticked.
- An issue with no such checklist, or one `gh` cannot read, is a finding too.

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
# tools/bench/pi-nodes.toml (the shared fleet, in the repo), then
# ~/.config/bench/bench.toml, or the file named by $BENCH_CONFIG (this machine)
[[remote]]
name = "nuna-node-01"     # capture node and display name (default: host)
host = "nuna-node-01"     # MagicDNS name or tailnet address
gdb  = 3333               # OpenOCD GDB port of the M4 (default)
log  = 4000               # UART log port (default)
```

- Two files, same format.
  `tools/bench/pi-nodes.toml` is the fleet every checkout shares: a new Pi Node is added there, by its MagicDNS short name, so nobody rediscovers it.
  The repo is public: no tailnet domain and no address in it.
  The per-machine file is layered on top: an entry with the same `name` replaces the shared one (pin an address, change a port), a new `name` is added.
  A node that is off, or not on your tailnet, shows as "not answering" in `bench boards`.
- The files say where to look.
  Node IDs and classes stay out of them: a board is recognised by its UID, and its class is the scenario's.
- `bench boards` lists a Pi Node whose log port answers as `pi-node <name>`, and names the ones that do not answer.
  It opens only the read-only log port.
  The GDB port is a debug session, opened to read a UID (`--probe-uids`), flash or reset.
- `bench capture up` records a Pi Node's log with the local ones, in `<name>-YYYYMMDD.log`.
  Each line is stamped with the controller's UTC clock; the Pi's own stamp (its local time, no zone) is dropped.
- UID read, flash and reset go through `src/bench/pinode.py`: GDB (CubeIDE's `arm-none-eabi-gdb.exe`, or `$BENCH_GDB`) to the Pi's OpenOCD.
- A new board's first run is onboarding: read its UID, add it to `Common/Protocol/node_id.c` with the next free Node ID, flash once, then run `scenarios/onboard.toml --as <Node ID>`: a lone C3 whose flash, boot, log, radio transmit, STOP2 sleep and two resets (against a sleeping board) all show in the trace (Test Record `docs/test-tracker/spot-20261010-onboard-board.md`).
- A NUCLEO that still runs the factory firmware cannot be reached over SWD through a Pi Node (the debug port is dead a second after reset): flash it once by hand on a PC with its Pi wires removed, then move it to the Pi.
  A board whose ST-LINK says "No STM32 target found" is still wired to something that drives its SWD or NRST pins.
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
| `bench records check` | Checks, without a board, that every issue, its Test Record and its scenario agree (`--online` reads the issues with `gh`) |

A quick run without a scenario file uses the same machinery:

```sh
bench run --node 1=C3 --node 2=C2 -D TX_RAMP_MS=5
```

## Rules

- `bench` is the only way to build or flash the firmware from an agent: never the toolchain, `make` or `STM32_Programmer_CLI` directly.
- `bench` never writes option bytes or OTP, never changes readout protection or security, and never mass erases (see [What bench never does to a board](#what-bench-never-does-to-a-board)).
- `bench` never commits: a run ends with a report, and the human decides what goes in.
- `bench` never touches a board another session holds, and never takes a port from another capture unasked.
