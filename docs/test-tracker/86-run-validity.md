# Issue #86: run manifest and validity check for long runs

Purpose: acceptance. Issue: #86 (run manifest and validity check for long runs).

A long session has to say what it was (a manifest) and whether its data can be used (a validity check), by itself.
The host part is covered by host tests (below); the bench part is a real cut of a connection, which only hardware can show: does the capture's reader notice a USB cable or a TCP link that is gone, within the 10 s rule.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/run-validity-local.toml` | Two local boards, about 10 min (Node 1 flashed as C3, Node 3 watched). Left alone, `bench run` exits 0 and its manifest says `valid = true`. With Node 3's USB cable unplugged for about 15 s at +3 min, it exits 4 with a `port_down` cause naming Node 3's COM port and an interval of at least 10 s |
| `tools/bench/scenarios/run-validity-pinodes.toml` | The same on Pi Nodes only (Node 2 flashed as C3, Node 5 watched), with Node 5's log link cut for about 15 s. The marks must show the cut: a dead TCP link that the reader keeps up until its keepalive fires fails this scenario |

Each scenario is run twice, with the cut and without.
A local board and a Pi Node never share a session: they would share a radio network, a forbidden setup.
Node 3 and Node 5 are only watched, because the unplug power-cycles a NUCLEO (its trace UART and its power are the same USB cable), and a reboot of a flashed node would end the run as a FAIL at once.

## Hardware

Checked against `bench boards` on 2026-10-07: 2 boards connected, both Pi Nodes; no local board.

| Item | Needed | Today |
|---|---|---|
| Local session: Node 1 (bench C3) flashed as C3, on USB | yes | missing: no local board connected |
| Local session: Node 3 (bench C2, second), watched, on USB | yes | missing: no local board connected |
| Local session: a hand to unplug Node 3's USB cable for about 15 s and plug it back, at +3 min | yes | needs Simon at the bench |
| Pi session: Node 2 (`nuna-node-03`) flashed as C3, Node 5 (`nuna-node-01`) watched | yes | connected, but both are in the #101 soak until about 02:13Z on 2026-10-08 |
| Pi session: a way to cut Node 5's log link for about 15 s without touching its board | yes | to settle (see below) |
| Host on AC power, Windows sleep off, for each session | yes | per session |
| The always-on capture restarted from a build that writes marks (`bench capture up --replace`) | yes | not yet: it runs the code from before marks, and the restart leaves a short gap in what other sessions read |

Hands on the bench: the USB unplug and replug of the local session.
No CubeMX regeneration.

Cutting a Pi Node's log link: it must cut only the controller's connection and leave the Pi, the board and the collector alone.
A proposal, not yet agreed: `tailscale down`, 15 s, `tailscale up` on the controller, which cuts every tailnet connection of this PC for that time and kills the TCP session without a close, which is the case the criterion is about.

## Host level (done, branch `feat/86-run-manifest`)

- `arclog/tests/test_marks.py`: a mark every 10 s on the monotonic clock, a port's transitions with their time, a repeated state keeping the time it began, a UTC step moving UTC and not the cadence, a restart appending with no mark for the stopped time, rotation at UTC midnight, a torn last line not read.
- `arclog/tests/test_capture.py`: `tcp_lines` against a loopback log server (up, closed, up again) and `serial_lines` against a fake `serial` module report their state; `capture()` writes the marks next to the daily files and a connected TCP port shows up in them.
- `arclog/tests/test_validity.py`: a port down 12 s is a capture gap naming the port and the interval (8 s is not); a UTC step of 5 s names its size and sign; no marks for 5 min (inside, at the edges, or all of the window) is a host that was not running; which causes invalidate (`invalidating`) and the exit code of a run (`exit_code`).
- `arclog/tests/test_trace_causes.py`: every unplanned reboot, lost-lines and wrong-build event of a run is listed, not only the first, and a board that never booted the Build ID.
- `arclog/tests/test_silence.py`: a node silent for 5 min with its port up is a node fault; with its port down it is a capture gap.
- `bench/tests/test_collector.py`: a Pi clock 6 s off or unsynchronised, a Pi Node the collector lost, lines lost across a reconnect after the start: invalid, naming the Pi Node; a quiet node is a heads-up, and a node silent past `max_silence` fails the run without invalidating it.
- `bench/tests/test_manifest.py`: the manifest of a local C3 and, apart, of a Pi Node C2 (no address in it), read back; the time service (`w32tm`) parsed; notes with their time; the running session found.
- `bench/tests/test_validate.py`: `bench validate` on a stored record gives the exit code the end of the run gave, for a pass, a fail, a timeout and an invalid run; 0, 1, 2, 3 and 4 stay apart.

## Protocol

Local session (needs Node 1 and Node 3 connected, the capture restarted with marks):

1. `bench boards` shows Node 1 and Node 3; `bench capture status` shows both recorded; check that `tools/arclog/runs/bench/marks-<today>.log` gets a line every 10 s.
2. Without the cut: `bench run tools/bench/scenarios/run-validity-local.toml`. Expected exit 0.
3. With the cut: the same command; at +3 min unplug Node 3's USB cable for about 15 s, plug it back, and `bench note "Node 3 USB unplugged 15 s"`. Expected exit 4.
4. `bench validate <record>` on both records gives the exit code the run gave.

Pi session (Node 2 and Node 5 free, after the #101 soak): the same with `run-validity-pinodes.toml`, the cut being the log link of Node 5.

## Criteria

- [x] Host: the marks writer, with a fake clock and fake ports: a mark every 10 s, a port's transitions recorded with their time, no mark while the process is stopped. `test_marks.py`, `test_capture.py`.
- [x] Host: the check on synthetic sessions. A port down 12 s is invalid and names the port and the interval. A port up with a node silent for 5 min is not a capture gap (a node fault when `max_silence` is set). A UTC step of 5 s between marks is invalid and names its size. No marks for 5 min is invalid. A sequence jump, an unplanned reboot and a wrong Build ID are listed, and invalidate only when `dataset = true`. `test_validity.py`, `test_trace_causes.py`, `test_silence.py`, `test_validate.py`.
- [x] Host: the check on a collector-decided session. A Pi clock 6 s off, a collector that lost a Pi Node and a stream gap after a reconnect are invalid and name the Pi Node and the cause. A node quiet while its Pi is heard is a node fault, not an invalid run. `test_collector.py`.
- [x] Host: a manifest built from a run record and read back, for a local C3 and, apart, for a Pi Node C2; a note appears with its time. `test_manifest.py`.
- [x] Host: `bench validate` on a stored record gives the verdict the end of the run gave; the exit codes 0, 1, 2, 3 and 4 stay distinct. `test_validate.py`.
- [ ] Bench, local: a session of about 10 min on two local boards with one USB replug of more than 10 s is invalid with that cause (the port and the interval), and the same session without the replug is valid. `run-validity-local.toml`.
- [ ] Bench, Pi Nodes: the same on Pi Nodes only (Node 2 and Node 5 exist), with the log link of one of them cut for more than 10 s. The marks must show the cut: if the reader keeps a dead TCP link up until its keepalive fires, this criterion fails and the port state needs a liveness check of its own. `run-validity-pinodes.toml`.
- [x] The docs of item 4: the invalid-run rule of `docs/test-tracker/README.md` points to the check, and the manifest format is in `tools/bench/README.md` (Run record, manifest and validity).

## Findings

- Not yet run on hardware.
- What to watch in the Pi session: the reader's TCP keepalive (idle 30 s, then probes every 10 s) decides how late a dead link shows as down in the marks. Not measured on this Windows host.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
