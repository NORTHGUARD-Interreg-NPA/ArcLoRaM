# Node silences

A flashed node that stops logging while its capture is up.
One row per event, so that a cause is looked for when there is a pattern and not before.

Rule (Simon, 2026-10-04): the counter only counts until it passes 3.
The fourth event gets a proper investigation: an issue, the rows below as its evidence, and a scenario that waits for it.

**Count: 2**

## What counts

- The node logged normally, then no line for over 60 s while its capture connection was up: a COM port that is open, or a Pi Node whose log port accepts a connection.
- Not counted: a capture outage (a port down, the host asleep), a planned reset or reflash, a node that was never started.
- Found today by the age of the last line in `tools/arclog/runs/bench/<node>-YYYYMMDD.log` against the clock.
  #86 makes the capture say whether the connection was up, and a scenario can then ask for `max_silence`.
- On finding one: add a row here, then reset the node (`bench reset <Node ID>`).

## Events

| # | Last line (UTC) | Found | Node | Build | Silent | Recovery |
|---|---|---|---|---|---|---|
| 1 | 2026-10-04 20:37:31 | 21:11 | `nuna-node-02`, a C2 on a Pi Node | `32a8d25` (main) | 43 min | `bench reset 2` at about 21:21; `BOOT cls=C2 id=2` at 21:21:18, logging again |
| 2 | 2026-10-06 02:26:16 | 04:40 (end of a 6 h run) | `nuna-node-02`, a C2 on a Pi Node | `dcfaeba-o954ffd` (`feat/36-rx-guard`) | at least 3 h 54 min (02:26 to 06:20), until both nodes were unplugged | both nodes were unplugged at the lab at 06:20 UTC; the stalled state was not read |

## Event 1: what the trace shows

- The node ran normally for 12.5 min after its reset at 20:25:02: 243 slots, 26 relay transmissions, 12 Sync receptions.
- The last lines are `SLOT ph=0 ce=3 dec=TX`, `SYNC_TX`, `TX_DONE` (20:37:31). Then no `SLOT` (the next one, ce=4, is due 3 s later) and no `STOP2_WAKES`, on either core.
- The node had passed the same cell five times before (`ph=0 ce=3 dec=TX` at 20:26:30, 20:27:30, 20:29:30, 20:32:30 and 20:34:30, each followed by ce=4), so it is not that cell.
- The reset worked and the node booted and logged at once: the Pi, its UART link and its log server were fine, and the node was not logging.
- `nuna-node-01` (the sender, the collaborator's `dev` build) kept logging throughout.
- No bench command touched the node's debug port between its reset at 20:25 and the silence.

## Hypotheses for event 1 (none established)

1. **A lost Alarm A wake after the transmission: the symptom of #80.** It fits: the last line is the end of a Tx, nothing follows, the radio stays asleep, and the node stays silent until a reset. #80 says no cause has been observed, so this may be its first occurrence.
2. **Alarm A programmed from two contexts (#75).** `WAKE_ADJ` reprograms the alarm from the radio ISR and the slot task programs it from thread context, with no critical section. Against: the case #75 describes needs an RTC write, and none is logged in the last 12 s (no `RTC_SET`, no `RTC_SHIFT`, `DRIFT ... ok=0 trim=0`), and the last `WAKE_ADJ` came 5.7 s before the stop. For: nine `WAKE_ADJ` in the 12 minutes, so the interleaving was exercised.
3. **A hard fault or a core lockup.** A HardFault leaves nothing in the trace (ADR-0001). It cannot be excluded: reading the core needs a debug session, which bench does not allow and nobody asked for.
4. **Specific to this build.** The node ran main at `32a8d25`, `nuna-node-01` runs the collaborator's `dev`. One event cannot tell; each row records the build so that a pattern shows.

Judged unlikely: the Pi's UART or log server stuck (the node logged again right after the reset), and a dip of the Pi's 5 V (it resets the MCU, which logs a `BOOT`).

## Event 2: what the trace shows

Found at the end of the 6 h run `guard-from-drift.toml` (Test Record of #36), which could not see it: nothing in a scenario detects a silent node until #86 (`max_silence`), so it held the boards for 2 h 14 min more and failed on its count of locked packets (68 of 100).

- The C2 locked at +72 s and ran 3 h 46 min: 608 Rx windows of the new guard (62 at the cap before the estimate was valid, 546 at 10 to 12 ms), every packet the C3 sent to a running C2 received (71 of 71), no `RX_LATE`, `SLOT_SUSPECT`, `TX_LATE` or `TX_DENIED`.
- The last line of the CM0+ is `SLOT ph=1 ce=8 dec=RX wake=0 nom=13260032` (device 03:41:00.0236, host 02:26:16 UTC). The line that follows it in every other Rx slot, `RX_WIN` (5.3 ms later, after the log call of `SLOT`), never came, and no CM0+ line ever did.
- **The CM4 kept logging**: `4P STOP2_WAKES n=1728` at 02:41:29 UTC and `n=1792` at 03:48:41 (one line per 64 wakes, about one an hour). So the board and its log path were alive and only the CM0+ stopped. In event 1 no CM4 line was due in the 43 min, so the two cannot be compared on that.
- The C3 (`nuna-node-01`, same build) logged to the end of the run, and the capture gaps of 2026-10-05 (45, 90 and 250 min) hit both nodes at the same times: capture outages, not silences. In three days of captures the only node silences are on `nuna-node-02`.
- The last line is a log line in both events (a `TX_DONE` in event 1, a `SLOT` here), and the stop is in thread or ISR context respectively.
- **What the change under test runs between `SLOT` and `RX_WIN`:** `RadioTimeOnAir`, the guard resolver (`MAC_GetClockState`, `MAC_GetLastSyncMs`, `DriftEstimator_Get`, `GuardPolicy_Ms`), and the contention check. `DriftEstimator_Get` only copies cached fit values (no loop), the policy is integer arithmetic without loops, and the same path ran 608 times in the run before the stop.

## Hypotheses for event 2 (none established)

1. **The same family as event 1: a lost wake or a lockup of the CM0+ on this board (#80, #75).** Fits: the same node, the same role, a stop right after a log line.
2. **A hard fault** in the slot task or in the trace code. It leaves nothing in the trace (ADR-0001), and only a debug read of the stalled core (the program counter and the fault registers) can tell, which bench does not do.
3. **Specific to this board, its Pi Node or the C2 role.** The only silences of three days are on Node 2, and it is the only C2 role board of the two. A pair with the roles swapped (the C3 on Node 2, the C2 on Node 5) would tell the board from the role.
4. **The guard code of this change.** It cannot be excluded by one event, and it is on the path where the core stopped; against it, event 1 happened on main before it existed, and the path ran 608 times.

The state of the stalled CM0+ was the best evidence there was, and it is gone: both nodes were unplugged at the lab at 06:20 UTC on 2026-10-06 before anyone read it. #101 makes the next stall leave that evidence (a crash record, checkpoints, the reset cause), and #100 restarts the stalled core.
