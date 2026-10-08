# Node silences

A flashed node that stops logging while its capture is up.
One row per event, so that a cause is looked for when there is a pattern and not before.

Rule (Simon, 2026-10-04): the counter only counts until it passes 3.
The fourth event gets a proper investigation: an issue, the rows below as its evidence, and a scenario that waits for it.

**Count: 4**

The fourth event (2026-10-08) is the one this rule investigates: the investigation is #101 "Find the cause of the CM0+ stalls: crash record, checkpoints and the reset cause", the rows below are its evidence, and `spot-20261007-node2-as-c3.md` is the scenario that waited for it (the swap soak).
Events 3 and 4 are CM4 HardFault lockups read from the Pi's OpenOCD journal, not stalls of the CM0+ alone; events 1 and 2 were never read that way.

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
| 3 | 2026-10-07 04:21:36 | about 07:20 | `nuna-node-03`, Node 2 as a C2 on a Pi Node | `474afcb` (main) | 4 h 21 min, until the flash | the flash of the swap soak at 08:43 UTC reset the core; the lockup is in the Pi's OpenOCD journal (`pc 0x66ba1e5a`), the fault registers were not read (`spot-20261007-node2-as-c3.md`) |
| 4 | 2026-10-08 18:41:27 | 22:24 (the judge of the #36 run warned that the node had been silent for 223 min) | `nuna-node-03`, Node 2 as a C2 on a Pi Node | `77adecb-o954ffd` (main, with the guard of #36) | still halted at 22:28 UTC; 9 h 50 min after the run was armed, 4 h 20 min after the scenario passed | not reset: the halted CM4 was read first (Event 4 below); the reset waits for Simon |

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

## Event 4: what the trace and the post-mortem show

Found at 22:24 UTC by the judge of the 6 h run of #36 (`guard-from-drift.toml`, judged from the log collector), which had passed at +19813.6 s (5 h 30 min, 14:21 UTC) and warned of a node silent for 13394 s.

- The C2 ran 9 h 50 min without a stop (180 `SLOT` an hour, then 151 in the tenth hour), then the last line is `RX_WIN last=35100042 cap=35102632 g=10 win=20` of `SLOT ph=1 ce=0` at 18:41:27.9 UTC, an Rx window for the C3's packet of that cell. No line of either core followed. The C3 sent the packet of that cell at 18:41:27.9, and the C2 did receive it (below): the log misses it only because nothing printed it.
- The collector carried the Pi's OpenOCD lines: at 18:41:28.8 UTC `Error: [stm32wlx.cpu0] clearing lockup after double fault`, `halted due to debug-request, current mode: Handler HardFault`, `xPSR: 0x01000003 pc: 0x607b3300 msp: 0x20007eb0` (`stm32wlx.cpu0` is the CM4). The Pi was healthy: uptime 2 d 6 h, `vcgencmd get_throttled` 0x0, GPIO18 an input, `openocd` and `uart-log` active.
- The node was not reset. At 22:28 UTC, with Simon's go, a read-only GDB session on the CM4 (registers and memory only, no write, no reset, no flash; the journal logged `New GDB Connection ... state: halted`) read the stalled core. The raw output is `tools/arclog/runs/20261008T085115Z-77adecb-o954ffd-collector/postmortem-node2-cm4-20261009.txt`.

| Register | Value | Reading |
|---|---|---|
| `pc` | `0x607b3300` | no memory behind it (not flash, not SRAM): a wild jump, as in event 3 (`0x66ba1e5a`) |
| `xpsr`, `lr` | `0x01000003`, `0xFFFFFFF1` | HardFault handler mode, entered from a handler (return to handler mode on MSP) |
| `msp`, `psp` | `0x20007eb0`, `0` | the firmware runs on the main stack, 336 bytes in use |
| CFSR `0xE000ED28` | `0x00000100` | BusFault status `IBUSERR`: an instruction fetch from a bad address; no valid fault address (BFARVALID and MMARVALID clear) |
| HFSR `0xE000ED2C` | `0x40000000` | `FORCED`: the bus fault was escalated to a HardFault |
| SHCSR `0xE000ED24` | `0x00000800` | `SYSTICKACT` (the SysTick handler was active), `BUSFAULTENA` clear (hence the escalation) |
| ICSR `0xE000ED04` | `0x04437003` | `VECTACTIVE` 3, `RETTOBASE` clear (nested exceptions), an interrupt pending; NVIC `IABR` 0: no external interrupt was active |

- **What the stack shows.** The hardware frame stacked by the exception at `0x20007ed0` holds `PC 0x08009336`, `LR 0x0800932d`, `xPSR 0x01000000`: in the CM4 image bench built for the C2 (`Debug_C2`, build time 08:50 UTC) both are in `tiny_vsnprintf_like` (`Utilities/misc/stm32_tiny_vsnprintf.c`, lines 505 and 690), the trace line formatter. The thread code was formatting a trace line when an exception was taken. Higher on the stack, flash addresses resolve to `UTIL_ADV_TRACE_COND_FSend`, `UTIL_SEQ_Run`, `MX_SubGHz_Phy_Process` and `main` (a stack holds stale return addresses too: only the stacked `PC` and `LR` are certain).
- **The line being formatted** is on the stack as text: `000102T094500.6962`, a device stamp of 09:45:00.6962. The nominal start of that packet is 35100032 ms and the `hdr` (header valid) of every `RX_DONE` of the run is 664 ms after it, so the stamp is the header-valid instant of the packet the C2 was receiving. The last line logged is that window's `RX_WIN` at 09:45:00.0292.
- **The vector table of the image is valid**: its HardFault entry is `0x08000be9`, in flash. The `pc` at the lockup is not that handler.
- **Not read**: the CM0+ registers. OpenOCD on the Pi never examined the CM0+ (`stm32wlx.cpu1 not examined`, so its GDB port refuses the connection), and examining it takes `monitor` commands that bench forbids. The CM4's access port reads all memory, so the CM0+ was read through its RAM.

### What the CM0+ shows (read through RAM at about 22:40 and 22:43 UTC, two snapshots about 3 min apart, `postmortem-node2-ram-*.txt`)

- **The CM0+ was alive** almost 4 h after the CM4 stopped. Its Stop2 wake counter (`g_stop2_count`) was 24960 in the last `STOP2_WAKES` line it queued (09:46:03 device time), 33990 at the first read and 34090 at the second: 100 wakes in about 3 min, and about 9000 since the queue filled, at the rate it had before the fault (256 wakes in the 6 min before). 137 words of its RAM changed between the two reads, its stack among them. The CM4 heartbeat in shared memory (`g_cm4_heartbeat`) reads 0 both times.
- **Its trace is stuck, not the core.** `ADV_TRACE_Buffer` (2 KB, `0x20009588`) is full of lines the CM4 never printed: 25 lines from `RX_DONE` of the packet in flight (`ph=1 ce=0`, `rssi -13`, `snr 8`, `hdr` at nominal + 664 ms, as in every packet of the run) through `SYNC_RX act=t1` (`erru` 471), `DRIFT`, its own relay `SYNC_TX ph=1 ce=1` and `TX_DONE`, to `RX_WIN` of `ph=1 ce=7` at 09:47:20. The guard stayed 10 ms in all of them. The next line would overwrite the oldest, so the queue stopped there.
- **IPCC**: channel 2 from the CM0+ to the CM4 is occupied (`C2TOC1SR` = 0x2) and the CM0+ has the "channel free" interrupt of channel 2 unmasked (`C2MR` bit 17 clear): that fits a CM0+ waiting for the CM4 to release a channel the CM4 will never read.
- **The CM4's own queue** (SRAM1) ends at line `#65` (`RX_WIN`), the last line printed.
- **What it changes**: a node whose lines stop is not necessarily a node that stopped. Here the C2 kept receiving Sync and relaying it for hours with the CM4 dead, and the log called it silent. Events 1 and 2 never had a RAM read: whether the CM0+ was alive in them is unknown, and the way to know is to read `g_stop2_count` twice on the next silent node (the CM4 was alive in event 2 and the CM0+ lines stopped, the opposite of this one, so a stuck trace channel is one thing to rule in or out).

## Reading (2026-10-09)

- Events 3 and 4 are the same fault on the same board and role: a CM4 double fault, `pc` in the `0x6xxxxxxx` range, `msp` near the top of SRAM (`0x20007e48`, `0x20007eb0`), the Pi healthy. Event 3 came after a Tx done (`TX_DONE`), event 4 at the header-valid instant of an Rx.
- Events 1 and 3 were on `main` builds before the guard of #36 existed (`32a8d25`, `474afcb`), and the swap soak (`e34092f`, no guard) ran 18 h with the roles swapped without a stop. The guard resolver runs on the CM0+ before `RX_WIN`; the faulting code is the CM4's trace formatter and an exception taken inside it. One event cannot exclude the guard; it does not point at it.
- Open for #101: whether the CM0+ was alive in events 1 and 2 (read `g_stop2_count` twice); what jumped to `0x607b3300` (the SysTick handler or the code it interrupted), what the CM4 formats at the header-valid instant of an Rx, and whether `BUSFAULTENA` and a HardFault handler that records the registers (#106 "Fault handlers record the fault and restart the core") would have left this evidence without a debugger.
