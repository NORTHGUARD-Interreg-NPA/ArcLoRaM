# Issue #86: run manifest and validity check for long runs

Purpose: acceptance. Issue: #86 (run manifest and validity check for long runs).

A long session has to say what it was (a manifest) and whether its data can be used (a validity check), by itself.
The host part is covered by host tests (below); the bench part is a real cut of a connection, which only hardware can show: does the capture's reader notice a Pi Node's log link that is cut, within the 10 s rule.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/run-validity-pinodes.toml` | Two Pi Nodes, about 10 min (Node 4 flashed as C3, Node 1 watched). Left alone, `bench run` exits 0 and its manifest says `valid = true`. With Node 1's log link cut for 15 s at +3 min, it exits 4 with a `port_down` cause naming Node 1's log and an interval of at least 10 s |

The scenario is run twice, with the cut and without.
Node 1 is only watched: the cut leaves its board running, and a reboot of a flashed node would end the run as a FAIL at once.
The scenario was written for Node 2 (`nuna-node-03`) and Node 5 (`nuna-node-01`), whose Pis do not answer on 2026-10-12.
It was retargeted to the free boards of that day.

Local boards are out of scope (Simon, 2026-10-12): the real setup is Pi Nodes only, and a local board stays a temporary aid for small tests.
The criterion of a USB unplug and its scenario `run-validity-local.toml` were dropped from the issue and from this record.
The COM-port path of the marks (`serial_lines`) therefore has host tests with a fake `serial` module only.

## Hardware

Checked against `bench boards` on 2026-10-12: 4 boards connected, all Pi Nodes; no local board is needed.

| Item | Needed | Today |
|---|---|---|
| Node 4 (`nuna-node-04`, Node ID 4) flashed as C3 | yes | connected, free |
| Node 1 (`nuna-node-06`, Node ID 1) watched | yes | connected, free |
| A way to cut Node 1's log link for 15 s without touching a Pi: `tools/bench/scripts/cut-link.ps1` | yes | agreed with Simon on 2026-10-12, run elevated after one UAC approval |
| Host on AC power, Windows sleep off, for each session | yes | on AC; but the PC went to sleep twice on 2026-10-12 (see the findings), so no lid or sleep key during a run |
| The always-on capture restarted from a build that writes marks and dates a stalled link | yes | done on 2026-10-12, stopped 13:09:22Z, recording again 13:09:35Z (about 16 s without marks) |

Nodes 2 and 5 (the original pair) are not needed once retargeted; their Pis do not answer today.
Hands on the bench: none, apart from the one UAC approval for the cut script.
No CubeMX regeneration.

Cutting a Pi Node's log link, agreed with Simon on 2026-10-12, with no change to a Pi (its code, its services, its board and its power stay as they are):

- `cut-link.ps1` adds a Windows Firewall block rule for this PC's TCP traffic to that one Pi's address and log port, both directions, when a trigger file appears, and removes it 15 s later (in a `finally`, and at the next start).
  The other three Pi Nodes, the rest of the tailnet and every other session are untouched.
- The alternative was `tailscale down` for 15 s, which also cuts the always-on capture of the other three Pi Nodes and any session reading the collector. It was not used.
- What the cut is and is not: Windows refuses its own packets to that address and port, so the connection is reset locally at the next packet Windows has to send (an ACK of the next line, or a keepalive probe), and the reconnect attempts are refused at once until the rule goes. It is not the silent black hole of a Pi that lost power, where nothing comes back and nothing is refused. See the findings.

## Host level (done, branch `fix/86-pi-link-liveness`, from `feat/86-run-manifest`)

- `arclog/tests/test_marks.py`: a mark every 10 s on the monotonic clock, **and one at every change of a port** (a link dropped and recovered between two ticks leaves a trace), a port found dead dated to when it was last known alive and keeping that date over retries, a repeated state keeping the time it began, a UTC step moving UTC and not the cadence, a restart appending with no mark for the stopped time, rotation at UTC midnight, a torn last line not read.
- `arclog/tests/test_capture.py`: `tcp_lines` against a loopback log server (up, closed, up again) and `serial_lines` against a fake `serial` module report their state; **a link with no byte for 45 s is down since its last byte, the Pi heartbeat (every 31 s) keeps a quiet link up, an OS error after silence is dated to when the keepalive began to fail (8 s), a reset right after data and a link that never connects are down from now, the OS keepalive is set to 3 s idle, 5 probes 1 s apart, and a dropped TCP port is marked down and up between two ticks**; `capture()` writes the marks next to the daily files.
- `arclog/tests/test_validity.py`: a port down 12 s is a capture gap naming the port and the interval (8 s is not); **an outage dated to the last proof of life counts from there, whatever the mark grid**; a UTC step of 5 s names its size and sign; no marks for 5 min (inside, at the edges, or all of the window) is a host that was not running; which causes invalidate (`invalidating`) and the exit code of a run (`exit_code`).
- `arclog/tests/test_trace_causes.py`: every unplanned reboot, lost-lines and wrong-build event of a run is listed, not only the first, and a board that never booted the Build ID.
- `arclog/tests/test_silence.py`: a node silent for 5 min with its port up is a node fault; with its port down it is a capture gap.
- `bench/tests/test_collector.py`: a Pi clock 6 s off or unsynchronised, a Pi Node the collector lost, lines lost across a reconnect after the start: invalid, naming the Pi Node; a quiet node is a heads-up, and a node silent past `max_silence` fails the run without invalidating it.
- `bench/tests/test_manifest.py`: the manifest of a local C3 and, apart, of a Pi Node C2 (no address in it), read back; the time service (`w32tm`) parsed; notes with their time; the running session found.
- `bench/tests/test_validate.py`: `bench validate` on a stored record gives the exit code the end of the run gave, for a pass, a fail, a timeout and an invalid run; 0, 1, 2, 3 and 4 stay apart.

Bold: added by the liveness fix of the Pi session (206 arclog and 527 bench tests pass).

## Protocol

Pi session (Node 4 and Node 1 free; the capture restarted from the fixed build):

1. `bench boards` shows Node 4 and Node 1; `bench capture status` shows every Pi port recorded; `tools/arclog/runs/bench/marks-<today>.log` gets a line every 10 s.
2. Start `cut-link.ps1` elevated for `nuna-node-06` (one UAC approval; it waits for a trigger file and cuts 15 s each time it appears).
3. Without the cut: `bench run tools/bench/scenarios/run-validity-pinodes.toml`. Expected exit 0 and `valid = true`.
4. With the cut: the same command; at +3 min create the trigger file, then `bench note "Node 1 log link cut 15 s (firewall rule on this PC)"`. Expected exit 4 with a `port_down` cause naming `nuna-node-06` and at least 10 s.
5. `bench validate <record>` on both records gives the exit code the run gave.

No board, Pi or power is touched during a run.

## Criteria

- [x] Host: the marks writer, with a fake clock and fake ports: a mark every 10 s, a port's transitions recorded with their time, no mark while the process is stopped. `test_marks.py`, `test_capture.py`.
- [x] Host: the check on synthetic sessions. A port down 12 s is invalid and names the port and the interval. A port up with a node silent for 5 min is not a capture gap (a node fault when `max_silence` is set). A UTC step of 5 s between marks is invalid and names its size. No marks for 5 min is invalid. A sequence jump, an unplanned reboot and a wrong Build ID are listed, and invalidate only when `dataset = true`. `test_validity.py`, `test_trace_causes.py`, `test_silence.py`, `test_validate.py`.
- [x] Host: the check on a collector-decided session. A Pi clock 6 s off, a collector that lost a Pi Node and a stream gap after a reconnect are invalid and name the Pi Node and the cause. A node quiet while its Pi is heard is a node fault, not an invalid run. `test_collector.py`.
- [x] Host: a manifest built from a run record and read back, for a local C3 and, apart, for a Pi Node C2; a note appears with its time. `test_manifest.py`.
- [x] Host: `bench validate` on a stored record gives the verdict the end of the run gave; the exit codes 0, 1, 2, 3 and 4 stay distinct. `test_validate.py`.
- [x] Bench, Pi Nodes: a session of about 10 min on Pi Nodes only with the log link of one of them cut for more than 10 s is invalid with that cause (the port and the interval), and the same session without the cut is valid. The marks must show the cut: if the reader keeps a dead TCP link up until its keepalive fires, this criterion fails and the port state needs a liveness check of its own. `run-validity-pinodes.toml`.
- [x] The docs of item 4. The invalid-run rule of `docs/test-tracker/README.md` points to the check, and the manifest format is in `tools/bench/README.md` (Run record, manifest and validity).

## Findings

1. **The marks did not show a dead TCP link, as the issue anticipated** (a finding of 2026-10-12, before any cut).
   The Pi behind `nuna-node-09` lost power: its last log line is 11:12:11Z and it was back at about 11:14:08Z.
   `marks-20261012.log` shows `nuna-node-09=up@10:43:45Z` until the mark of 11:13:48Z and first changes to `up@11:14:13Z`; the file has no `nuna-node-09=down` mark but the capture's start line, and `check_marks` finds no cause in that window.
   About 2 minutes of a dead link were never marked down.
   Why: the reader's keepalive was 30 s idle, then 10 probes 10 s apart (about 130 s), and it fired only when the Pi was back and answered the probe with a reset.
   The reader then reconnected within the same 10 s tick, so no mark ever showed `down`.
2. **"No line, heartbeat included, for 10 s means down" cannot work.**
   The Pi's heartbeat (`[pi] uart_idle=7s ntp=synced`) comes every 31.4 s on all four Pis, not every 10 to 30 s, and a quiet node logs a line every 5 to 20 s: in the capture of 10:44 to 12:20Z on 2026-10-12, 252 of 653 gaps between two lines of `nuna-node-04` were over 10 s (162 over 15 s, 44 over 20 s, median 6.3 s), and the other three Pis give the same.
   A 10 s silence rule would mark a healthy quiet node down all day.
   A silence rule works at 45 s (the heartbeat plus its margin): it finds a dead Pi, not a cut of 15 s.
3. **The fix** (commit `924e669`): the OS keepalive probes after 3 s of silence and drops the connection after 5 unanswered probes 1 s apart, so a stalled link is found 8 s after its last sign of life; a link with no byte for 45 s is dead whatever the OS thinks; a port found dead is `down` since the last time it was known alive; and the marks get a line at every change of a port, not only every 10 s.
   The options (3 s, 1 s, 5) are set and read back on a connected Windows socket (Python 3.12, against a real Pi), and the new reader runs on Windows against a Pi.
4. **Red before the fix, green after, on the same cut** (the rows of 13:05 and 13:10 below).
   A cut of 16.0 s (13:05:28.6Z to 13:05:44.6Z) of `nuna-node-06` port 4000 on the old reader: the connection was reset at 13:05:40.54Z, 12 s into the cut, 55 ms after the first line that arrived with the rule on, and the marks show `down@13:05:40.542Z` and `up@13:05:44.655Z`: 4.1 s, no cause.
   The same cut (13:10:09.9Z to 13:10:25.9Z) on the restarted capture: `down@13:10:02.348Z` written at 13:10:10.348Z, 0.4 s after the cut began, `up@13:10:26.560Z`, and `check_marks` says `nuna-node-06: port down for 24 s (13:10:02 - 13:10:26 UTC)`.
5. **The two runs of the scenario** (rows of 13:12 and 13:22 below): left alone, exit 0, `valid = true`, 53 marks over 9 min, `bench validate` agrees; with the cut at +3 min, exit 4, and the manifest holds the causes `port_down` (bench fault: `nuna-node-06: port down for 22 s (13:25:13 - 13:25:36 UTC)`) and `lost_lines` (2 lines lost before core 0 #83), the note with its time, and `bench validate` gives 4 again.
6. **The interval is wider than the cut, on purpose.**
   The real cut of run 2 was 16.0 s (13:25:19.8Z to 13:25:35.7Z), the marks say 22.3 s: the start is the last byte before the abort (13:25:13.97Z), at most 8 s before the OS reported it, and the end is the reconnect, 0.6 s after the rule went.
   The check can overstate an outage by up to 8 s and never understates it; a glitch of a few seconds on a quiet node can therefore read as about 10 s.
7. **The firewall cut is not a silent black hole.**
   Windows refuses its own packets to that address and port, so the connection is reset locally at the next packet it must send (both cuts: within 0.4 s of the cut's start), and reconnect attempts fail at once until the rule goes.
   An inbound block rule does not touch an established flow (a line arrived through it), and `tailscale down` would cut all four Pis.
   A Pi that loses power sends and refuses nothing: that path (the keepalive probes vanishing) is covered by host tests and by the options being set, not by a run.
   No tool of this PC drops one peer's packets without an error, and the only ones that could change a Pi were not used.
   Open, in a follow-up issue: reproduce a silent black hole of one Pi's log link and read the marks.
8. **Lost lines inside a port outage are listed as a firmware fault** (`lost_lines`, `fault = "firmware"`), next to the `port_down` that explains them.
   The exit code is right (a bench fault always invalidates), the label is not, and the first lost line ends the run engine at once: run 2 stopped at +201 s, not after 9 min.
   Open, in a follow-up issue.
9. **The host went to sleep twice on 2026-10-12, and the marks caught both**: no marks from 11:40:59Z to 11:47:46Z (`no marks for 407 s`) and from 12:02:47Z to 12:03:44Z.
   The Windows event log says `Sleep Reason: Button or Lid` for both, with the sleep timer set to never on AC.
   The Pi sockets survived, and after the wake every line of the stall was stamped in one burst (11:48:16.4Z on all four nodes), because the capture stamps on arrival.
   Nobody should close a lid or press a sleep key during a run; the preflight of #87 "Five boards and multi-hour runs" does not look at that trigger.
10. **`bench capture --replace up` does not restart the bench's own capture**: it prints `running` and keeps the old code.
    The restart needed the capture's tree stopped by hand and `bench capture up`: stopped 13:09:22Z, back 13:09:35Z, 15.7 s without marks (13:09:19.7Z to 13:09:35.4Z), most of it `uv` rebuilding the project.
    Open, in a follow-up issue.
11. A stall of less than about 10 s may go unseen: the Pi's TCP replays what it held, late, and the capture stamps it on arrival.
    The README of `arclog` says so.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-12 13:05 | a 15 s cut of Node 1's log link on the running capture (`tools/bench/scripts/cut-link.ps1`), no scenario | n/a | red (before fix) | `tools/arclog/runs/bench/marks-20261012.log` | The capture from 10:43Z, old reader. Cut 13:05:28.6Z to 13:05:44.6Z. The marks show `down@13:05:40.542Z` and `up@13:05:44.655Z` (4.1 s, a local reset 12 s into the cut): no cause. Finding 4. |
| 2026-10-12 13:10 | the same cut, on the capture restarted from the fix | n/a | measurement (green after fix) | `tools/arclog/runs/bench/marks-20261012.log` | Cut 13:10:09.9Z to 13:10:25.9Z. `down@13:10:02.348Z`, `up@13:10:26.560Z`: `port down for 24 s (13:10:02 - 13:10:26 UTC)`. One SLOT line (#40) lost. Finding 4. |
| 2026-10-12 13:12 | `run-validity-pinodes.toml`, no cut | `297aee2-o954ffd` | PASS | `tools/arclog/runs/20261012T131229Z-297aee2-o954ffd/` | Node 4 flashed as C3, Node 1 watched; armed +18.7 s, 27 SLOT by +538.5 s. Exit 0, `valid = true`, 53 marks, no cause; `bench validate` exit 0. |
| 2026-10-12 13:22 | `run-validity-pinodes.toml`, Node 1's log link cut 15 s at +3 min | `297aee2-o954ffd` | PASS (exit 4, the invalid run the cut must give) | `tools/arclog/runs/20261012T132219Z-297aee2-o954ffd/` | Cut 13:25:19.8Z to 13:25:35.7Z (16.0 s). `port_down` of `nuna-node-06`, 22 s (13:25:13 - 13:25:36 UTC), and `lost_lines` (2 lines lost before core 0 #83, +201.1 s, which ended the run). Exit 4, `bench validate` exit 4, the note in the manifest at 13:25:36.8Z. Findings 5 to 8. |
