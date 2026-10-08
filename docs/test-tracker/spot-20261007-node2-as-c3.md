# Spot check 2026-10-07: Node 2 as C3 and Node 5 as C2, to tell the board from the C2 role

Purpose: spontaneous. Issues: #101 "Find the cause of the CM0+ stalls", and the multi-hour C3 + C2 criterion of #45 "Sync schedule within the duty-cycle budget".

Node 2 has stopped logging three times as a C2.
This run swaps the roles of the two boards on the Pi Nodes: if the board is the cause, Node 2 stops again as a C3; if the C2 role is, Node 5 stops as a C2.
The same 12 h run is the multi-hour C3 + C2 run that #45 asks for, on the DEV Sync profile.
Clock: NUCLEO (development crystal), not the production TCXO.

## Why

All three node silences (`node-silences.md`, #101) are Node 2 (UID `0026001a3232501420383543`) running as a C2.
None happened on Node 5 (UID `0014001b3232501420383543`), a C3, in three days of captures.

| # | Last line (UTC) | Pi Node | Build | Ran before it | Last lines |
|---|---|---|---|---|---|
| 1 | 2026-10-04 20:37:31 | `nuna-node-02` | `32a8d25` (main) | 12.5 min after a reset | `SLOT ph=0 ce=3 dec=TX`, `SYNC_TX`, `TX_DONE` |
| 2 | 2026-10-06 02:26:16 | see #101 | `dcfaeba-o954ffd` (`feat/36-rx-guard`) | 3 h 46 min | `SLOT ph=1 ce=8 dec=RX`, then no `RX_WIN` (#101) |
| 3 | 2026-10-07 04:21:36 | `nuna-node-03` | `474afcb` (main) | 1 h 53 min after the `BOOT` at 02:28:49 | `SLOT ph=0 ce=1 dec=TX`, `SYNC_TX`, `TX_DONE` |

Events 1 and 3 end on the same three lines, after a Sync transmission.
Before event 3 the node had made 31 such transmissions without trouble.

### Event 3: what the Pi's OpenOCD saw

The firmware prints nothing on a HardFault (ADR-0001), and event 1 had listed "a hard fault or a core lockup" as hypothesis 3, impossible to check without a debug session.
Event 3 answers it for this event: the Pi Node's `journalctl -u openocd` (Pi clock CEST, UTC+2) holds, at 06:21:53 CEST, which is 04:21:53 UTC and 17 s after the last log line:

```
Error: [stm32wlx.cpu0] clearing lockup after double fault
[stm32wlx.cpu0] halted due to debug-request, current mode: Handler HardFault
xPSR: 0x80000003 pc: 0x66ba1e5a msp: 0x20007e48
Error: [stm32wlx.cpu0] Polling failed, trying to reexamine
Info : [stm32wlx.cpu0] Cortex-M4 r0p1 processor detected
```

- `stm32wlx.cpu0` is the Cortex-M4 (CM4); the journal does not look at the CM0+. The `main` build logs no periodic CM4 line (five `BOOT` and `CORE_SYNC` lines only), so the trace cannot say which core stopped first.
- `pc=0x66ba1e5a` is neither flash nor SRAM: a wild jump.
- No `BOOT` followed: the node stayed down about 3 h, until it was found at about 07:20 UTC. The collector showed it `connected: true` with 0 lines.
- The Pi was healthy: `uart-log` and `openocd` active, uptime 15 h, `vcgencmd get_throttled` = `0x0`, GPIO18 an input (the reset line was not held), the UART log connection of the collector established. The Pi's journal is persistent (`/var/log/journal` exists).
- The fault registers (CFSR, HFSR) were not read: OpenOCD's telnet port 4444 refuses connections on `nuna-node-03`, and a GDB read is outside what bench allows (#67 "Batch GDB post-mortem after a failed run"). The flash of this run resets the core and destroys them.

### How long

Node 2 as a C2: 11.4 h logged (9.55 h in `tools/arclog/runs/bench/nuna-node-02-2026100{4,5,6}.log`, as stretches of lines with no gap over 180 s, plus 1.88 h on 2026-10-07 from the log collector), three stops: one per 3.8 h.
With only three events the 95 % range of that rate is 0.054 to 0.77 per hour.
Node 4 ran as a C2 for 2.1 h and 3.9 h without a stop on build `329ce7e` (#34 record), an older build.

If the board carries the cause at the rate seen, a 12 h run shows at least one stop with probability 96 %, and 48 % if the true rate is at the bottom of its range.
So the run is capped at 12 h, ends at the first stop (the staged `SLOT` counts fail within an hour of it), and is expected to answer within about 4 h if the board is the cause.
A clean 12 h is evidence, not proof.

| Result | Reading |
|---|---|
| Node 2 (C3) stops, Node 5 (C2) does not | the board, or its Pi Node; not the C2 role |
| Node 5 (C2) stops, Node 2 (C3) does not | the C2 firmware path |
| both stop | firmware common to both roles, or something the Pi's OpenOCD does to the target |
| neither stops in 12 h | the cause needs the original pairing (Node 2 as C2 on a C3 Node 5), or the rate was lower than seen |

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/sync-dev-soak-swapped.toml` | Node 2 as C3 and Node 5 as C2, DEV profile with `SYNC_BOOT_BURST=5u`, 12 h: the C2 reaches `CLK to=WARM` within 5 min; each node logs at least 150 `SLOT` an hour (180 seen on both, 2026-10-07); no `TX_DENIED`, `TX_LATE` or `SYNC_SILENCE` |

## Hardware

Checked against `bench boards` on 2026-10-07: both boards listed with their Node ID and free.

| Item | Needed | Today |
|---|---|---|
| Node 2 flashed as C3: the board wired to Pi Node `nuna-node-03` | yes | present, free; halted by the lockup of event 3, the flash resets it |
| Node 5 flashed as C2: the board wired to Pi Node `nuna-node-01` | yes | present, free; its last `BOOT` is a C3 |
| Pi Nodes' OpenOCD (GDB 3333) and UART log (4000) | yes | both answer |
| Log collector, the record of the run | yes | `/nodes`: both connected |
| Host on AC power, Windows sleep off | for the flash only (about 2 min); the run is decided later from the collector | none needed after the flash |
| Probes and COM ports | none, the Pi Nodes replace the ST-LINKs | `nuna-node-02` is unplugged and not used |

Hands on the bench: none.
Nobody resets, replugs or reflashes Node 2 or Node 5 until the run is decided: a reset is a `BOOT` nobody planned and invalidates it.
A flash outside a bench command holds no lease, so another session must not touch these two boards either.
Neither Pi was power-cycled today; after a power-up run `bench reset <Node ID>` first (#103 "A Pi Node board stays held in reset").

## How the run is decided

The host does not stay on for 12 h.
`bench flash` arms the boards, the host may then be switched off, the Pi Nodes keep running and the log collector keeps the record.
The verdict is decided later, at any time and any number of times, by `tools/bench/src/bench/collector.py`: it writes the collector's lines as capture files under `tools/arclog/runs/<start>-<build>-collector/` and decides the scenario with the expect file `bench run` would build from it (`expect_spec`, arclog's engine).

```sh
BENCH_COLLECTOR_URL=<base URL of the collector> uv run --project tools/bench python -I -m bench.collector \
  tools/bench/scenarios/sync-dev-soak-swapped.toml --build e34092f-o954ffd --since 2026-10-07T14:06:55Z \
  --node 2=nuna-node-03 --node 5=nuna-node-01 --dir tools/arclog/runs/20261007T140655Z-e34092f-o954ffd-collector
```

- Exit 0 is a PASS, 1 a FAIL, 2 no verdict yet (nothing failed so far). It prints, per node, the lines, the last line and its age, the `BOOT`s and the `SLOT` count per hour.
- Run it from a checkout at or before `f71773e` (the worktree `.claude/worktrees/node2-c3-swap`), not from a `main` that contains #36 "Rx guard from the drift estimate".
  The judge reads the trace with the arclog schema of the checkout it runs in, and since #36 that schema requires `g` and `win` on `RX_WIN`; this build (`e34092f-o954ffd`) predates them and does not print them.
  From such a `main` it reports `FAIL nuna-node-01 RX_WIN: missing g,win` at +134 s although nothing is wrong with the run (seen on 2026-10-08).
  From the worktree, the same command at 22:22 UTC on 2026-10-07 gave every stage ok up to 1350 `SLOT` on both nodes and exit 2.
- The collector writes each line's time in ISO 8601 with its offset (its first version wrote the Pi's local time, which `--pi-tz` still converts). The tool flags a Pi whose clock is not NTP-synchronised or lags the collector's by more than 5 s, and a node the collector has not heard from for over 10 min.
- Since 2026-10-07 13:51Z the Pi buffers its lines and replays them to a collector that reconnects, so a collector restart is no longer invalid by itself: a FAIL on lost lines says whether the buffer was enough.
- A FAIL on a `SLOT` stage is a node that stopped: read its Pi's `journalctl -u openocd` around the last line before anything resets it (criterion 2), then `bench reset <Node ID>`.
- The run is invalid, and is run again, when a FAIL names lost lines (a gap in the stream) or a `BOOT` nobody planned (someone reset or flashed a node), or a `WARN` says the collector lost a node.
  Those say nothing about the firmware.
- A node that stops is caught by the first stage after its last line, at most an hour later.
  The last stage is due 12 h 5 min after arming (02:13 UTC on 2026-10-08, armed at 14:08:37Z); a first answer is expected within about 4 h (around 18:10Z).
- While the host is on, a shell loop (no agent turn) runs the command every 30 min and appends to `<dir>/checks.log`; it stops itself at 02:20Z. It dies with the host, and the command above can be run at any time.
- The tool was checked on event 3's real lines (a C2 scenario over `nuna-node-03` from its reset): FAIL `SLOT not within 11100s of arming (331/450)`, with the collector's reconnect at 06:33 UTC flagged.

## Criteria

- [x] Neither node stops logging in 12 h (150 `SLOT` an hour each, staged by the scenario), or the first stop is attributed to a board and a role in the table above. (PASS run of 2026-10-07 14:06: about 18 h, 180 `SLOT` an hour on both, no stop)
- [ ] At a stop, the Pi Node's `journalctl -u openocd` around the last line is read before the reset and quoted here. (Not triggered: no node stopped.)
- [x] No `TX_DENIED`, `TX_LATE` or `SYNC_SILENCE` in 12 h on the DEV profile after the boot burst, and the C2's `SYNC_RX` counted per phase against the C3's `SYNC_TX`; these two count for #45 and are ticked in `45-sync-schedule.md`. (None of the three in about 18 h; the C2 received 327 of the 327 Sync packets the C3 sent after the C2 was up, matched by phase, cell and epoch; the one not received is the first, sent at the C3's boot.)

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-07 08:42 | `sync-dev-soak-swapped.toml` | `16ee4e5-o954ffd` | invalid | `tools/arclog/runs/20261007T084208Z-16ee4e5-o954ffd-collector/` | `bench flash --node 2=C3 --node 5=C2 -D SYNC_BOOT_BURST=5u`: both cores of both boards booted the build, Smoke Check PASS (+19 s). Armed 08:43:55Z (Node 2 `BOOT` as C3 at 08:43:47Z, Node 5 as C2 at 08:43:55Z). The burst sent `SYNC_TX` in cells 0 to 3 within the first minute; Node 5 `CLK to=WARM` 53 s after arming. Due: 20:49Z for the full 12 h, an answer expected within about 4 h; the host is off after the flash. Node 2 had been halted by event 3 since 04:21Z: the flash recovered it. **Invalid, for two reasons that say nothing about the firmware:** the collector was disconnected from 08:44:49Z to 08:46:07Z (78 s on both nodes, 13 lines lost: the Pi did not buffer then), and both nodes were reset at 13:49:59Z and 13:56:18Z, to the same second (the collaborator, deploying the collector and Pi changes). What it did show: 5 h 06 min from the arming to 13:49:59Z without a stop, both nodes at 180 `SLOT` an hour (173 and 176 in the first), so neither Node 2 as C3 nor Node 5 as C2 stopped. Interim evidence only: at the rate seen as a C2, Node 2 would stay up 5.1 h with probability about 26 % if the board alone carried the cause |
| 2026-10-07 14:06 | `sync-dev-soak-swapped.toml` | `e34092f-o954ffd` | PASS | `tools/arclog/runs/20261007T140655Z-e34092f-o954ffd-collector/` | Second try, same boards, roles, scenario and override (the Build ID differs only because the branch was committed: the firmware sources are those of `16ee4e5`). `bench flash`: both cores of both boards booted the build, Smoke Check PASS. Armed 14:08:37Z (Node 2 `BOOT` as C3 at 14:08:29Z, Node 5 as C2 at 14:08:37Z). Due 02:13Z on 2026-10-08. The collector now writes ISO times with the offset and the Pi replays after a restart (deployed by the collaborator at 13:51Z), so the judge reads both formats. **Result, judged 2026-10-08 about 08:45 UTC, about 18 h after arming:** PASS at +36115 s, from the collector's record alone. The one-shot `bench.collector` call could not judge a run this long: the collector answers `/logs` with its newest 10000 lines unless the call passes `limit` (up to 1000000), `until` or `after`, and the judge passes none and asks for both nodes at once, so it saw only the lines from 23:35 UTC, found no `BOOT` and failed the smoke check (a truncated record, nothing the nodes did); it also overwrote the folder's capture files with that answer. The same judge with `&limit=1000000` added to its `/logs` call pulls the whole record from the collector and gives this PASS; those files are in the run folder, with a `NOTE.txt`. Making the judge use the collector's paging is #111 "Collector export and judge for days-long runs of 5 to 10 nodes". Both nodes ran the whole time with no stop and no unplanned `BOOT`: 180 `SLOT` an hour on both, hour after hour (181 and 178 in the first). No `TX_DENIED`, `TX_LATE`, `SYNC_SILENCE`, `SYNC_LOST`, `RX_LATE` or `SLOT_SUSPECT`. The C2 (main build, NUCLEO Clock) never corrected a Tier 1 error: `erru` walked from +959 us at the lock to -8074 us at 02:11:51 UTC (-0.2 ppm, `DRIFT rate=-197 ppb`), the first Tier 2 packet (`act=t2`, one `RTC_SHIFT`), then +471 us at 02:51 UTC and walking again (-3436 us at 08:18 UTC); see `perf-tx-spread.md` |

## Reading (2026-10-08)

Neither Node 2 as C3 nor Node 5 as C2 stopped in about 18 h: the fourth row of the table above, "neither stops".
With the 5 h 06 min of the invalid first try, each has about 23 h clean.
At the rate seen as a C2 (3 stops in 11.4 h, one per 3.8 h) the chance of 23 h without a stop is about 0.2 %, and 28 % at the bottom of the 95 % range (0.054 per hour), so the rate was either far lower than seen or the cause is not the board or the C2 role alone.
What is left is the original pairing, Node 2 as a C2 under a Node 5 as the C3, on the same build, to see whether Node 2 stops again there (#101 "Find the cause of the CM0+ stalls").
