# Onboarding a new board: full remote control from bench

Purpose: spontaneous. Issue: none.

Every new board on a Pi Node goes through this check once, so Simon can leave it on the bench and drive it remotely.
It is one scenario aimed at the new board's Node ID, then one reflash and one reset, all by command.
Each board gets its rows in Runs, newest last.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/onboard.toml` (a template: node 0 is the board under test, `--as <Node ID>`) | A lone C3: both cores flash, verify and boot the build; the log keeps flowing (`max_silence` 1 min); one Sync packet per boot (a lone C3 sends only cell 0 of its phase) ending in `TX_DONE`, no `TX_TIMEOUT` (the radio sends); `STOP2_WAKES` (the firmware sleeps in STOP2); two resets at +150 s and +5 min, each against a sleeping board, each rebooting both cores (no other reboot), and `SYNC_TX ph=0 ce=0` again after each (back on the air). |

## Hardware

| Item | Needed |
|---|---|
| The new board, wired to a Pi Node, registered in `Common/Protocol/node_id.c` | yes |
| The Pi Node listed in `tools/bench/pi-nodes.toml` and on the tailnet | yes |
| A second board to receive the C3's Sync packets | no, not covered here |

Hands on the bench: a board with the factory firmware must be flashed once by hand with ArcLoRaM (the on-board ST-LINK over USB, CubeProgrammer, connect under reset).
The factory firmware leaves the debug port dead a second after reset, so no Pi Node can read the UID or flash it (seen on nuna-node-08 and nuna-node-09, 2026-10-09).
After that one hand flash, bench does everything.

## Procedure

1. `bench boards`: the Pi Node answers (a log port that does not answer is a Pi that is off or not on the network).
2. `bench boards --probe-uid <Pi Node name>` reads the UID.
   If it prints `Node ID not in node_id.c`, add it to `Common/Protocol/node_id.c` with the next free Node ID.
3. `bench run tools/bench/scenarios/onboard.toml --as <Node ID> --probe-uid <Pi Node name>` takes about 8 minutes.
4. `bench flash --node <Node ID>=C3`, then `bench reset <Node ID>`: a reflash and a reset of the board the run left asleep.

## Criteria

- [x] The new board's UID reads over SWD and its Node ID is registered.
- [x] Both cores flash, verify and boot as C3 (the run's smoke check).
- [x] The log flows for the whole run (no `max_silence` failure, no lost line).
- [x] The radio sends: a `TX_DONE` after each of the three boots, no `TX_TIMEOUT`.
- [x] The firmware reaches STOP2 (`STOP2_WAKES` at least twice).
- [x] Two resets each reboot both cores, and no other reboot happens.
- [x] The C3 is back on the air after each reset (`SYNC_TX ph=0 ce=0` three times).
- [x] A reflash and a `bench reset <Node ID>` work on the board the run left asleep.

Ticked on Node 6 (nuna-node-08), run `2026-10-10 15:26`; every further board adds its rows to Runs.
Nodes 4 (nuna-node-04), 1 (nuna-node-06) and 7 (nuna-node-09) pass the scenario too, on 2026-10-12 (Node 7 after its Pi was restarted, see the fleet check at the end).

Not covered, by design: that the board's radio receives.
That needs a second board on the bench as the sender (for example `c3-c2-sync.toml` with the new board as the C2).

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-10 15:15 | `onboard.toml --as 7` | `d53cb73-d814850` | INVALID | `tools/arclog/runs/20261010T151539Z-d53cb73-d814850/` | Node 7 is the board now on nuna-node-09; this run was made while it was still on a local ST-LINK (COM14), before it was moved to the Pi. Smoke check, `SYNC_TX ph=0 ce=0`, `TX_DONE` and the first `STOP2_WAKES` (+124 s) passed. The first reset (+150 s) failed with "No debug probe detected": the USB probe left the PC mid-run (not a firmware verdict). Run again. |
| 2026-10-10 15:21 | `onboard.toml --as 6` | `d53cb73-d814850` | INVALID (wrong expectation) | `tools/arclog/runs/20261010T152104Z-d53cb73-d814850/` | Node 6 on nuna-node-08. Smoke check, first `STOP2_WAKES` (+125 s), the reset at +150 s (reboot at +172 s, `BOOT cls=C3`, `SYNC_TX ph=0 ce=0` again at +173 s) all passed. The run failed on `TX_DONE x6 within 3m`: I had assumed a Sync packet every 20 s, but a lone C3 sends only cell 0 per boot (`dec=SKIP` for cells 1 and up), so one `TX_DONE` per boot is right. Scenario corrected to `TX_DONE x3 within 7m`; the board was fine. Run again. |
| 2026-10-10 15:26 | `onboard.toml --as 6` | `d53cb73-d814850` | PASS | `tools/arclog/runs/20261010T152600Z-d53cb73-d814850/` | Node 6 on nuna-node-08 (a relay path, not direct). Flash and both-core boot +19 s; `TX_DONE` after each boot (+20 s, +174 s, +323 s); `STOP2_WAKES` at +125 s and +283 s; resets at +150 s and +5 min each rebooted both cores (+173 s, +322 s), 23 s and 22 s after the command. No unplanned reboot, no lost line. Same scenario as the INVALID row above, with `TX_DONE x3`. |
| 2026-10-10 15:34 | `onboard.toml --as 7` | `d53cb73-d1c446b` | INVALID (board unplugged) | `tools/arclog/runs/20261010T153435Z-d53cb73-d1c446b/` | Node 7 on nuna-node-09. Smoke check, `SYNC_TX ph=0 ce=0`, `TX_DONE` and the first `STOP2_WAKES` (+124 s) passed. The reset at +150 s failed with "The program is not being run": Simon had unplugged the board and replugged it (not a firmware or debug-link verdict). Run again, hands off the boards. |
| 2026-10-10 15:42 | `onboard.toml --as 1` | `d53cb73-d1c446b` | FAIL (smoke check, corrupt BOOT line) | `tools/arclog/runs/20261010T154220Z-d53cb73-d1c446b/` | Node 1 on nuna-node-06, the first flash after the board had gone silent (a half-written image after the flash cut on 2026-10-09). The flash verified and the board booted (both cores `BOOT` at 15:42:39, linked, `TX_DONE` at 15:42:40), but the CM4's first line reads `0<NUL>00000T000000.9997 4S A #00 BOOT build=...` in the capture (`tools/arclog/runs/bench/nuna-node-06-20261010.log`): a NUL byte inserted at the start of the first UART line after the reset, so the smoke check saw no BOOT on core 4. The record's own node log is empty. 1 of 11 boots today; the 10 others are clean. A reflash a minute later passed its boot check. Not a firmware verdict on the radio or reset path; the run is repeated. |
| 2026-10-10 15:44 | `onboard.toml --as 4` | `d53cb73-d1c446b` | INVALID (boards stopped) | `tools/arclog/runs/20261010T154426Z-d53cb73-d1c446b/` | Node 4 on nuna-node-04 (an old registered board, new Pi). Smoke check, first `STOP2_WAKES` (+125 s), the reset at +150 s (reboot at +173 s, back on the air at +174 s) and the second `STOP2_WAKES` (+279 s) passed. The reset at +5 min failed with "The program is not being run" while every Pi dropped at once (15:50): Simon had stopped almost all the boards. Run again, hands off the boards. |
| 2026-10-10 15:33 | `bench flash --node 6=C3`, then `bench reset 6` | `d53cb73-d1c446b` | PASS | `tools/arclog/runs/bench/nuna-node-08-20261010.log` | Reflash and reset of Node 6 about a minute after the PASS run left it running: boot check PASS (both cores, +19 s), then the reset rebooted it (`BOOT cls=C3 id=6` at 15:33:19 for the flash and 15:33:47 for the reset). |
| 2026-10-10 15:43 | `bench flash --node 1=C3`, then `bench reset 1` | `d53cb73-d1c446b` | PASS | `tools/arclog/runs/bench/nuna-node-06-20261010.log` | Node 1 on nuna-node-06 again after the FAIL row above: boot check PASS (both cores, +18 s) and a reset (`BOOT cls=C3 id=1` at 15:43:39 for the flash and 15:43:53 for the reset). The board that had been silent for 22 minutes runs again. |
| 2026-10-10 16:04 | `bench flash --node 8=C3`, then `bench reset 8` | `d53cb73-d6922bf` | PASS | `tools/arclog/runs/bench/com15-20261010.log` | The new board flashed by hand-free command on the local ST-LINK (no Pi wires on it), before it goes to a Pi: boot check PASS (both cores, +5 s), reset rebooted it (`BOOT cls=C3 id=8` at 16:04:47 and 16:05:15). Not an onboarding run: it has not been on a Pi yet. |
| 2026-10-12 10:48 | `onboard.toml --as 7` | `3fd1ec7-da86ca4` | FAIL to start (flash refused by the Pi) | `tools/arclog/runs/bench/nuna-node-09-20261012.log` (no run record: the flash never started) | Node 7 on nuna-node-09. The build was fine. `bench run` stopped at the flash: `nuna-node-09:3333: Remote communication error. Target disconnected`. OpenOCD on that Pi rejected the GDB attach (`[stm32wlx.cpu0] not examined`, `gdb-attach failed`, `Connect failed`). The board was not touched and kept running (`SLOT`, `STOP2_WAKES` in the capture). Not a verdict on the firmware: see the fleet check below. |
| 2026-10-12 10:51 | `onboard.toml --as 4` | `3fd1ec7-da86ca4` | PASS | `tools/arclog/runs/20261012T105100Z-3fd1ec7-da86ca4/` | Node 4 on nuna-node-04. Flash and both-core boot +18.5 s; `TX_DONE` after each boot (+19.5 s, +172.9 s, +322.8 s); `STOP2_WAKES` at +128.5 s and +279.9 s; resets at +150 s and +5 min each rebooted both cores (+171.7 s, +321.7 s). No unplanned reboot, no lost line. First clean PASS of Node 4. |
| 2026-10-12 10:56 | `onboard.toml --as 1` | `3fd1ec7-da86ca4` | PASS | `tools/arclog/runs/20261012T105658Z-3fd1ec7-da86ca4/` | Node 1 on nuna-node-06. Flash and both-core boot +18.5 s; `TX_DONE` at +19.4 s, +173.4 s, +323.3 s; `STOP2_WAKES` at +118.5 s and +280.3 s; resets rebooted both cores (+172.2 s, +322.1 s). No unplanned reboot, no lost line. First clean PASS of Node 1 (the 2026-10-10 FAIL row was a NUL byte in a BOOT line). |
| 2026-10-12 11:03 | `onboard.toml --as 6` | `3fd1ec7-da86ca4` | PASS | `tools/arclog/runs/20261012T110328Z-3fd1ec7-da86ca4/` | Node 6 on nuna-node-08, a repeat of the 2026-10-10 PASS on a new Build ID. `TX_DONE` at +19.9 s, +173.2 s, +323.5 s; `STOP2_WAKES` at +124.9 s and +280.2 s; resets rebooted both cores (+172.1 s, +322.3 s). No unplanned reboot, no lost line. |
| 2026-10-12 11:09 | `bench reset 7` | n/a | FAIL (Pi refused the GDB attach) | `tools/arclog/runs/bench/nuna-node-09-20261012.log` | Retry of the guarded reset on Node 7 after the other three runs: `Remote connection closed`, and OpenOCD logged the same `not examined` / `gdb-attach failed`. The refusal is persistent, not a single bad attach. |
| 2026-10-12 11:15 | `onboard.toml --as 7` | `3fd1ec7-da86ca4` | PASS | `tools/arclog/runs/20261012T111542Z-3fd1ec7-da86ca4/` | Node 7 on nuna-node-09, after Simon restarted that Pi (the board power-cycled with it, and its OpenOCD journal was clean: 26 startup lines, 0 errors). Flash and both-core boot +20.6 s; `TX_DONE` at +21.6 s, +175.4 s, +325.5 s; `STOP2_WAKES` at +128.6 s and +278.5 s; resets rebooted both cores (+174.3 s, +324.4 s). No unplanned reboot, no lost line. The only OpenOCD errors in the capture are `[stm32wlx.cpu1] Debug AP not available, will not halt after reset!`, twice (flash and reset), the same line each healthy Pi logged once at its flash. First PASS of Node 7. |

## Fleet check, 2026-10-12

Purpose: spontaneous. Simon asked for the four Pi Nodes on the bench to be tested end to end.

| Pi Node | Node ID | SSH | openocd and uart-log services | OpenOCD journal | Onboarding run |
|---|---|---|---|---|---|
| nuna-node-04 | 4 | ok | active, 0 restarts | no error | PASS |
| nuna-node-06 | 1 | ok | active, 0 restarts | no error | PASS |
| nuna-node-08 | 6 | ok | active, 0 restarts | no error | PASS |
| nuna-node-09 | 7 | ok | active, 0 restarts | about 30 errors a minute, in the journal from its first line (05:59 UTC) | flash and reset refused; PASS after Simon restarted the Pi (see below) |

All four Pis: tailnet ping ok, uptime about 1.8 days, NTP synced, 34 to 38 degrees C, no throttling, GPIO18 idle high (not held in reset), the same `/etc/openocd/rpi-swd.cfg`.
The Pis' journals are volatile, so the start of the nuna-node-09 error loop is not known.
The capture of 2026-10-10 shows one OpenOCD error in its last hour (to 16:14 UTC), so the loop started after that.

Finding: on nuna-node-09, OpenOCD never examines the CM4 (`Failed to read memory at 0xe000ef40`, `[stm32wlx.cpu0] Examination failed`, `Polling failed, trying to reexamine`), so it rejects every GDB attach.
SWD itself answers (`SWD DPIDR 0x6ba02477`), and the board runs (UART log flows, `STOP2_WAKES` counting), so the log works and remote flash and reset do not.
Nothing was changed on that Pi by the agent.
Simon then restarted the Pi (11:12 UTC, the board power-cycled with it): OpenOCD started clean (0 errors), and the onboarding run passed at 11:15 (Runs table).
The cause of the examination failure is not known, and nothing says it cannot come back: the first sign is `Examination failed` in the Pi's OpenOCD journal or in its `[openocd]` lines in the capture.
Until then a power-cycle of the Pi is the known remedy.

Not covered: a read of both cores' liveness through OpenOCD (for example `g_stop2_count` twice), which needs a GDB session outside bench.
