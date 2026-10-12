# Issue #101: find the cause of the core stalls (crash record, reset cause, checkpoints)

Purpose: acceptance. Issue: #101 (Find the cause of the core stalls: crash record, checkpoints and the reset cause on both cores).

No core may stay down: when either core (CM4 or CM0+) stops, the trace and a record that survives must say which core, where and why.
The issue stays open until a real stall is explained by these records, so this Test Record is written for the whole issue and filled slice by slice.

- **Slice 1: the crash record on both cores, the CM4's BusFault/UsageFault/MemManage handlers, and the reset cause in `BOOT`.** They change no behaviour; they cover criteria 1 and 5.
- **Slice 2: the heartbeat, the checkpoints and the promised wake of each core, watched by the CM4 (the "Core State").** They add a few stores per checkpoint; they cover criteria 3 and 7 and part of 2.
- Not started: the survivor's report in the other direction and the dead-CM4 case (criterion 4), the audit of the waits (criterion 6), the bad jump (criterion 8), the soak that waits for the next stall (criterion 9).

Clock: NUCLEO (development crystal), not the production TCXO, for every run of this record.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/core-stall-crash-cm4.toml` | Node 6 (C2) jumps to an address with no memory from its CM4 main loop (`BENCH_FAULT_CM4=1`) and stops in its fault handler. After the bench reset at +4 min, its `BOOT` is followed by `CORE_CRASH core=cm4 kind=bus why=ibuserr pc=60000000` and `CORE_CRASH_SCB cfsr=00000100`. The C3 (Node 1) reports no crash |
| `tools/bench/scenarios/core-stall-crash-cm4-forced.toml` | The same fault with the BusFault handler off (`BENCH_FAULT_CM4=4`) arrives as the forced HardFault of silence events 3 and 4: `kind=hard why=ibuserr`, `hfsr=40000000` |
| `tools/bench/scenarios/core-stall-crash-cm4-data.toml` | A data read of an address with no memory (`BENCH_FAULT_CM4=3`) is reported with its address: `why=preciserr`, `cfsr=00008200`, `bfar=60000000` |
| `tools/bench/scenarios/core-stall-crash-cm0.toml` | The CM0+ jumps to an address with no memory (`BENCH_FAULT_CM0=1`) while the CM4 keeps running; after the bench reset the CM0+ reports `CORE_CRASH core=cm0 kind=hard why=na pc=60000000` and no `CORE_CRASH_SCB` (it has no status registers) |
| `tools/bench/scenarios/core-stall-reset-cause.toml` | `BOOT` of both cores carries `rst`, and after the flash and after a bench reset (the Pi Node's OpenOCD drives the NRST pin) all four `BOOT` lines read `rst=pin` |
| `tools/bench/scenarios/core-stall-state-cm0-loop.toml` | Node 6's CM0+ loops for ever at the checkpoint `rx_set` (`BENCH_LOOP_CM0=4`). The CM4 prints `CORE_STATE core=cm0 st=overdue cp=rx_set step=rx_set alive=0`, and the next `BOOT` prints the state the previous run left, `st=boot cp=rx_set` |
| `tools/bench/scenarios/core-stall-state-lost-wake.toml` | Node 6 skips one Alarm A programming (`BENCH_SKIP_ALARM=1`): `CORE_STATE core=cm0 st=overdue step=alarm_set alive=1 over>=1`, a live core whose promised wake did not come |
| `tools/bench/scenarios/core-stall-state-cm4-loop.toml` | Node 6's CM4 loops for ever in its SysTick handler (`BENCH_LOOP_CM4=35`), the context of silence event 4. Its next `BOOT` prints `CORE_STATE core=cm4 st=boot cp=systick` (the CM0+ cannot print for a dead CM4) |
| `tools/bench/scenarios/core-stall-state-cost.toml` | Measurement (`BENCH_PROBE=1`): what a checkpoint, a heartbeat, a read of the tick and the promise of a wake cost on the CM0+ |

A forced act is a Build Override (`BENCH_FAULT_CM4` or `BENCH_FAULT_CM0` picks the kind of fault, `BENCH_LOOP_CM4` or `BENCH_LOOP_CM0` the checkpoint to loop at, `BENCH_SKIP_ALARM` loses one wake, `BENCH_FAULT_NODE` the one board to fault, `BENCH_FAULT_AFTER_S` the seconds after boot, 60 in the scenarios); no other build contains it.
The board must be named: an override reaches every board a run flashes, so without it the C3 faults too.
The fault handler stops the core, as it always did: restarting it is #106 "Fault handlers record the fault and restart the core instead of hanging".
So the scenarios do what a human does after a stall: reset the node, and read what its next `BOOT` says.

## Hardware

Checked against `bench boards` on 2026-10-12: 4 boards connected, all Pi Nodes, 4 with a known Node ID.
Node 4 was held by another session's run (#86 "Run manifest and validity check for long runs") when the first run was tried and has not answered since, so the C2 is a spare, Node 6.
Any two onboarded Pi Node boards do: the crash record does not depend on the board.

| Item | Needed | Today |
|---|---|---|
| Node 6 (`nuna-node-08`, UID `001e00114c42500d20363552`) flashed as C2, the board to fault | yes | connected |
| Node 1 (`nuna-node-06`, UID `0014008f3232501420383543`) flashed as C3, the Sync source and the control | yes | connected |
| The ST-LINK probe and trace port 4000 of each Pi Node | yes | on the Pis |
| A hard power cycle of Node 6's board (or its Pi), for the power-up reset cause | yes | needs Simon at the bench |
| A press of Node 6's NRST button, for the pin reset cause | yes | needs Simon at the bench |
| Node 2 (`nuna-node-03`), the board that stalled four times, flashed as a C2 | for criterion 9 and the swapped-roles soak | missing: does not answer, its CM4 was last seen locked up and was never reset |
| Node 5 (`nuna-node-01`), the C3 of that soak | for the same | missing: does not answer |

Not usable today: Node 7 (`nuna-node-09`) trace flows but its Pi's OpenOCD cannot read the SWD IDR (`Error connecting DP: cannot read IDR`), so a flash is refused; restarting `openocd.service` did not clear it (2026-10-12 13:39 UTC), a hard power cycle of that Pi is waiting for Simon.
Node 4 (`nuna-node-04`) did not answer from 13:38 UTC.
No antenna, attenuator, hub or power switch is needed.

Hands on the bench: the power cycle and the NRST press of the reset-cause criterion.
No CubeMX regeneration (the fault entries are in USER CODE regions only).

## Host level

- `Tests/unit/test_core_crash.c`: the record built from a stacked frame (the frame of silence event 4 as the fixture), the stack pointer before the exception, the status words and the stack words; valid only while the magic and the check word agree (one flipped bit in any of the 27 words is seen, RAM never written is not a record); a CM0+ record with no status says `na`; an unreadable frame still leaves a record; the first fault wins and the later ones are counted to 15 without spilling into the flags; the names of core and kind; the cause from CFSR and HFSR (each bus, memory and usage bit, a stacking error named before the fault it hides, `forced` and `vecttbl` only when no configurable fault speaks, event 4's registers read `ibuserr`).
- `Tests/unit/test_reset_cause.c`: the RCC_CSR flags of each kind of reset decode to their cause (`PINRSTF` accompanies the others and does not hide them, `BORRSTF` is a power-up or a brown-out), several flags at once give the most telling one, the radio flags and the bits that are not flags name nothing.
- `Tests/unit/test_core_state.c`: the state is valid only with its magic; a checkpoint keeps the last id and the last one that is not plumbing (a trace call or a Stop2 entry or exit does not hide where the flow was); the plumbing mask is exactly the eight ids; the ids of the two cores never meet; the survivor's look: a core that keeps its promises is fine, a silence inside the promise and its 5 s margin is a sleep, past it it is overdue by silent minus promised, a core that promised nothing is never overdue, a promise served clears the silence, a live core that lost its wake is overdue and still beating (`alive`), a dead one is overdue and not beating, a loop around the checkpoints does not hide a stalled core, the first look is only a baseline, an invalid state is not judged, the silence saturates.
- `tools/arclog/tests/test_schema.py` (existing): every `ARCLOG` call of the firmware matches the event table, so `CORE_CRASH`, `CORE_CRASH_SCB`, `CORE_CRASH_REGS`, `CORE_CRASH_STK`, `CORE_STATE` and the new `rst` and `rsf` of `BOOT` are checked against the C sources.

What is target glue and not host-testable: the naked fault entry (it picks MSP or PSP from EXC_RETURN and calls the C function), the SCB and RCC reads, the placement in the shared region.
The scenarios cover them.

## Findings and decisions of slice 1

- **Nothing incremented `g_cm4_heartbeat`.** A search of both cores' sources finds it only in `shared_mem.c` and `shared_mem.h`.
  The zero read on the dead CM4 of event 4 therefore said nothing about the CM4. The heartbeat is slice 2.
- **The CM0+ has no fault status registers.** It is a Cortex-M0+ (ARMv6-M): no CFSR, HFSR, BFAR or MMFAR.
  The issue's item 1 lists them for both cores. The CM0+ record holds the stacked frame, the stack pointer and EXC_RETURN, and `CORE_CRASH` says `why=na`.
  Criterion 1 ("the PC, LR, xPSR and the fault status") is read as: the fault status where the core has one.
- **The record lives in the shared region**, after the existing variables, which `SharedMem_Init` does not zero.
  Both cores see it at the same address (so a later survivor report can read the other core's record), no linker script is edited, and a reset does not clear it.
  The reset flags are kept there too: the CM4 reads and clears `RCC_CSR` first thing after any reset and the CM0+, released later, prints the same cause.
- **The fault entry is a naked function.** The handlers are CubeMX's, so only their USER CODE regions change: the macro `CORE_FAULT_ENTRY` is placed in the region and the function is declared `naked` in the USER CODE region of `stm32wlxx_it.h`.
  With a compiler prologue the stack pointer would already have moved and the frame could not be found. The disassembly of the built image is checked in the first run.
- **A lockup records nothing.** Silence events 3 and 4 were lockups (`clearing lockup after double fault`), and a lockup takes no handler.
  Reading the post-mortem dump of event 4 again (`postmortem-node2-cm4-20261009.txt`): `VTOR` is 0 (the table is in flash) and `CCR` is `0x200`; the 8 words at `MSP` are not a hardware frame (the 7th and 8th would be `pc` and `xpsr`, they hold `0x20007f00` and `0xFFFFFFF9`) but the SysTick handler's own saved registers (its `lr` `0xFFFFFFF9`, its `r7` equal to the live `r7`); the frame of the interrupted thread is at `0x20007ed0`.
  So the halted core had `IPSR` 3 and `LR` `0xFFFFFFF1` with no HardFault frame stacked: the first fault was taken on the exception entry or return path of the SysTick handler, not inside code a handler could have recorded.
  With the BusFault handler enabled (SysTick has priority `0xF0`, BusFault `0`), a bus error in that context is a BusFault that preempts it, is entered and records. Whether that is enough is what the next real stall will say.
  A stall with no record is a lockup: slices 2 and 3 (a heartbeat that stopped, the survivor's report) are what tells it from a stalled loop.
- **The forced faults jump to `0x60000001`** (a Thumb address in the range of events 3 and 4, where the STM32WL has no memory).
  Expected values in the scenarios (`pc=60000000`, `cfsr=00000100`, `cfsr=00008200` with `bfar=60000000`) come from the ARMv7-M definitions, written before the first run.

## Findings and decisions of slice 2

- **The heartbeat says the core lives, not that it works.** A NUCLEO wakes for its LED timer every few seconds, so after a lost Alarm A its heartbeat kept moving and the first version of the survivor's look (judging by the heartbeat) saw nothing. A promise is now judged by `kept`, the count of promised wakes served (the slot task ran); the heartbeat only says whether the core is still beating (`alive=1`: a live core that lost a wake, `alive=0`: a core that stopped).
- **The CM4 looks at the CM0+ from a timer of its own, every 30 s.** The production board has no LED timer, so its CM4 had no periodic wake; this costs one wake per 30 s there. A silence is judged against the wake the CM0+ promised the last time it programmed Alarm A, as a duration (the two cores share no time base that survives an RTC set), plus 5 s. A core that promised nothing (Scanning Rx has no alarm chain) is never overdue.
- **The look must not print from the timer interrupt.** A line printed there, on the wake from Stop2, left with its first characters replaced by spaces (the UART is restarted later in the wake-up path) and the host could not parse it (run `20261012T191145Z`). The interrupt now only says a look is due, and the look is made and printed from the idle hook, after the UART is back.
- **No time stamp per checkpoint, against the issue's wording.** A read of the tick costs 527 us on the CM0+ (see the measurement), thirty times a checkpoint. The survivor measures the silence in its own time, which is all the judgement needs; a time stamp can be added to the few checkpoints that already read the clock.
- **`step` next to `cp`.** The last checkpoint of a lost wake is `stop_in`, behind the alarm; `step` keeps the last one that is not a trace call or a sleep (`alarm_set`), so the report names the alarm.
- **The report of the next `BOOT` cannot tell a lost wake from a sleep**: a healthy node also ends on `stop_in`. Only the silence the survivor measured can, so the lost-wake scenario expects the live line only. The `BOOT` report is the evidence for a core that cannot be seen alive, the CM4 (the loop scenarios).
- **The trap of the forced acts stays**: the same gate, in the same shared RAM; see "State after the second session".

### Measurement: the cost of the liveness record (criterion 7)

CM0+ at 4 MHz (`hz=4000000`), NUCLEO Clock, Debug build (no optimisation, `-O0`), `BENCH_PROBE=1`, 100 or 10 calls per segment with the loop overhead (260 us per 100 iterations) taken off. The probe lines are in the run records `20261012T185730Z` (the first measurement, checkpoint not yet inlined), `20261012T190450Z` and `20261012T200338Z` (final: the same figures).

| What | Segment | Total | Per call |
|---|---|---|---|
| A checkpoint, first version | `cp_x100` 3545 us | 3285 us net | 33 us |
| A checkpoint, inlined and with the plumbing test as a mask | `cp_x100` 1871 us | 1611 us net | **16 us** |
| A heartbeat | `beat_x100` 621 us | 361 us net | **3.6 us** |
| A read of the tick (`HAL_GetTick`) | `tick_x10` 5272 us | 5272 us | 527 us |
| A read of the RTC (`plat_get_rtc_ms`) | `rtc_x10` 2381 us | 2381 us | 238 us |
| The promise of a wake (a read of the RTC and `DayMs_Diff`) | `promise_x10` 2698 us | 2698 us | 270 us |

- A locked C2 slot passes about 25 to 30 checkpoints (the slot task, the MAC decision, `rx_set`, `rx_armed`, `alarm_set`, the radio interrupts, a pair for every trace call, the two Stop2 crossings): **about 0.5 ms**, with the heartbeat (a few passes, about 20 us) and **one promise per alarm programming, 0.27 ms**: **about 0.8 ms per slot**, one slot every 20 s on the DEV Sync profile (0.004 %).
- On the path to the radio in Rx only the checkpoints before it count, about six (the slot task, the MAC decision, two trace calls, `rx_set`): **about 0.1 ms** added to the Rx start latency the guard policy carries (`GUARD_RX_START_LATENCY_US`), against a guard of 10 ms. The promise is computed in `plat_program_alarm_a`, after the radio is armed (`tdma_machine.c` programs the alarm after `RadioSetRx` and `RadioSend`). The Rx start probe of #36 should be run again before that constant is tightened.
- A `-Os` production build will cost less; the figures above are the bench build's.
- **RAM: 48 bytes**, two records of 24 bytes in the shared region (`g_core_state_cm0`, `g_core_state_cm4`), nothing else.

## Criteria

- [x] A forced HardFault on the CM0+ and on the CM4 (a Build Override each) leaves a crash record with the PC, LR, xPSR and, on the CM4, the fault status, and a `CORE_CRASH` line at the next `BOOT` (or, for a dead core, the record read by `bench postmortem` of #67). Slice 1: the four `core-stall-crash-*` scenarios, each PASS in Runs (CM4 bus fault `3a9712f-dfe26d7-o485b25`, forced HardFault `-o655361`, data bus error `bef45d4-ob2c30b`, CM0+ `bef45d4-o614be6`). The line comes at the next `BOOT`, the reset being bench's: a halted core cannot print, and the read of a halted core is #67
- [ ] A forced endless loop at each checkpoint of each core (the Override picks the id) gives a `CORE_STATE` line that names that core and that checkpoint. Slice 2: the mechanism and the report are proven for `rx_set` on the CM0+ (live, from the CM4, and at the next `BOOT`) and for `systick` on the CM4 (at the next `BOOT`); the other ids differ only by the place of the macro and are not each forced yet
- [x] A forced lost wake (the Override skips one Alarm A programming) gives the alarm checkpoint and an overdue deadline. Slice 2: `core-stall-state-lost-wake.toml`, PASS in Runs (`step=alarm_set`, `over` past the promise, `alive=1`)
- [ ] A dead CM4 with a live CM0+ is reported as such, not as a silent CM0+. Slice 3
- [ ] `BOOT` carries the reset cause on both cores, checked after a power cycle, a pin reset and a `bench reset`. Slice 1: `core-stall-reset-cause.toml` for the `bench reset`, and by hand for the power cycle and the pin reset
- [ ] The audit of the waits of both cores is written down (in the Test Record or an ADR) with each decision. Slice 4
- [x] The cost of the checkpoints is measured with `timing-probe` (microseconds per slot) and the RAM it takes is stated. Slice 2: the measurement above, `core-stall-state-cost.toml`: 16 us a checkpoint, about 0.8 ms a slot, 48 bytes
- [ ] The cause of the jump to the bad address is found, or the records show what is missing to find it. Slice 5
- [ ] The next real stall is explained by these records. The issue stays open until one is. Needs Node 2 as a C2 (missing today)

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-12 13:21 | `core-stall-reset-cause.toml` | `3a9712f-db82670` | invalid | `tools/arclog/runs/20261012T132143Z-3a9712f-db82670/` | Wrong expectation: `count = 4` can never be met, only lines after arming count and the boot after the flash is before it (fixed to 2). The data held: first boot after the flash `rst=power rsf=0c004000` on both cores (flags accumulated since the board's last power-up, the old firmware never cleared them), boot after the bench reset `rst=pin rsf=04004000` on both cores |
| 2026-10-12 13:28 | `core-stall-reset-cause.toml` | `3a9712f-d7895ee` | invalid | `tools/arclog/runs/20261012T132805Z-3a9712f-d7895ee/` | Same wrong count. First boot `rst=pin rsf=04004000`, boot after the reset `rst=pin` |
| 2026-10-12 13:34 | `core-stall-reset-cause.toml` | `3a9712f-d7895ee` | PASS | `tools/arclog/runs/20261012T133457Z-3a9712f-d7895ee/` | Node 6: both cores print `rst=pin` after a bench reset (criterion 5, the `bench reset` part) |
| 2026-10-12 13:37 to 14:26 | `core-stall-crash-cm4.toml` (5 attempts) | `3a9712f-d7895ee-o3b9443`, `3a9712f-d4967ea-o1faac9`, `3a9712f-df8d62b-o1faac9` | invalid | `tools/arclog/runs/20261012T134035Z-3a9712f-d7895ee-o3b9443/`, `...T134440Z-...`, `...T142707Z-3a9712f-d4967ea-o1faac9/`, `...T143601Z-3a9712f-df8d62b-o1faac9/` | Causes, in order: the Pi of Node 7 refused the flash (SWD `cannot read IDR`); the smoke window of 30 s ran out with two Pi flashes in turn (bench fix in `arclog expect`, with tests); the forced fault fired from the first main-loop passes, before the flash finished (counting passes, then reading the CM4 tick before its timer was initialised); a flash `MIS-MATCHED` on Node 6; the C3 faulted too because an override reaches every flashed board (`BENCH_FAULT_NODE` added). The crash record itself was right in every one of them |
| 2026-10-12 14:38 | `core-stall-crash-cm4.toml` | `3a9712f-dfe26d7-o485b25` | PASS | `tools/arclog/runs/20261012T143819Z-3a9712f-dfe26d7-o485b25/` | Node 6 faulted 60 s after boot and stopped; after the bench reset: `CORE_CRASH core=cm4 kind=bus why=ibuserr pc=60000000 lr=08000edd xpsr=81000000 sp=20007fd0 exc=fffffff9`, `CORE_CRASH_SCB cfsr=00000100 hfsr=00000000 icsr=00400805 shcsr=00070002`. The C3 reported no crash. The naked entry has no prologue (disassembly checked) and both cores place the shared symbols at the same addresses |
| 2026-10-12 14:44 | `core-stall-crash-cm4-forced.toml` | `3a9712f-dfe26d7-o655361` | PASS | `tools/arclog/runs/20261012T144424Z-3a9712f-dfe26d7-o655361/` | With BusFault off: `kind=hard why=ibuserr pc=60000000`, `cfsr=00000100 hfsr=40000000`: the forced HardFault of silence events 3 and 4, recorded by the handler |
| 2026-10-12 14:50 | `core-stall-crash-cm4-data.toml` | `3a9712f-dfe26d7-ob2c30b` | invalid | `tools/arclog/runs/20261012T145035Z-3a9712f-dfe26d7-ob2c30b/` | The capture links of both Pi Nodes were down together for 13 s (14:51:33 to 14:51:46), probably the other session's tailnet cut (#86), and a line was lost: exit 4 |
| 2026-10-12 14:53 and 15:02 | `core-stall-crash-cm4-data.toml` | `3a9712f-dfe26d7-ob2c30b` | invalid | `tools/arclog/runs/20261012T145317Z-3a9712f-dfe26d7-ob2c30b/`, `...T150206Z-...` | Both failed on a leftover, not on the firmware. The invalid run above had left Node 6 halted after its fault with the gate set; the next flash boot reported that old record before arming (`kind=bus why=preciserr cfsr=00008200 bfar=60000000`, as predicted for this fault) and consumed the gate, so the new run's own fault never fired. Worse, the boot after that run's reset found the gate clear, re-armed, and faulted 60 s later: the leftover of the second attempt. Both the record and the gate are retained through a flash, which resets nothing. Not a verdict on the record: the scenario has not passed yet |
| 2026-10-12 16:16 | `core-stall-crash-cm0.toml` | `bef45d4-o614be6` | PASS | `tools/arclog/runs/20261012T161635Z-bef45d4-o614be6/` | Node 6 (C2): its CM0+ jumped to `0x60000001` 60 s after boot and stopped; the CM4 kept running and the node's trace went silent, as in silence event 2. After the bench reset: `CORE_CRASH core=cm0 kind=hard why=na pc=60000000 lr=080213c9 xpsr=81000000 sp=2000ffd0 exc=fffffff9`, then `CORE_CRASH_REGS` and `CORE_CRASH_STK`, and no `CORE_CRASH_SCB` (the CM0+ has no status registers). The C3 reported no crash |
| 2026-10-12 16:22 | `core-stall-crash-cm4-data.toml` | `bef45d4-ob2c30b` | PASS | `tools/arclog/runs/20261012T162243Z-bef45d4-ob2c30b/` | From a clean board state: `CORE_CRASH core=cm4 kind=bus why=preciserr` and `CORE_CRASH_SCB cfsr=00008200 bfar=60000000`, as predicted. The two earlier attempts were the leftover of the invalid run above |
| 2026-10-12 18:57 | `core-stall-state-cost.toml` | `bef45d4-dc208b5-od66516` | measurement | `tools/arclog/runs/20261012T185730Z-bef45d4-dc208b5-od66516/` | First measurement, checkpoint not inlined: 33 us a checkpoint, 10 us a heartbeat (net of the loop). The `BOOT` report of the previous run read `hb=138 seq=359` |
| 2026-10-12 19:04 | `core-stall-state-cost.toml` | `bef45d4-df9e90c-od66516` | measurement | `tools/arclog/runs/20261012T190450Z-bef45d4-df9e90c-od66516/` | Checkpoint inlined, plumbing test as a mask: 16 us a checkpoint, 3.6 us a heartbeat. A loop baseline segment was added |
| 2026-10-12 19:11 | `core-stall-state-cm0-loop.toml` | `bef45d4-df9e90c-ocbfe6f` | invalid | `tools/arclog/runs/20261012T191145Z-bef45d4-df9e90c-ocbfe6f/` | The CM4's `CORE_STATE st=overdue` line was printed at 19:16:40 (`cp=rx_set step=rx_set due=18874 silent=30000 over=11126`) but with its first characters garbled, printed from the timer interrupt before the UART was back from Stop2: unparseable, so the expectation never matched. Fixed: the look is made from the idle hook |
| 2026-10-12 19:20 | `core-stall-state-cm0-loop.toml` | `bef45d4-d137235-ocbfe6f` | PASS | `tools/arclog/runs/20261012T192015Z-bef45d4-d137235-ocbfe6f/` | The CM0+ looped at `rx_set` after its first lock; the CM4 reported it overdue at +277 s and the next `BOOT` repeated the checkpoint |
| 2026-10-12 19:29 | `core-stall-state-lost-wake.toml` | `bef45d4-d137235-oc31b7f` | invalid | `tools/arclog/runs/20261012T192909Z-bef45d4-d137235-oc31b7f/` | The node kept waking (`STOP2_WAKES` counted on) after the lost wake, so the heartbeat moved and the look saw a live core: a promise cannot be judged by the heartbeat. Fixed with `kept`. The `st=boot` expectation was also satisfied by the flash boot's report of a normal state, and was dropped |
| 2026-10-12 19:39 | `core-stall-state-lost-wake.toml` | `bef45d4-dca297e-oc31b7f` | PASS | `tools/arclog/runs/20261012T193934Z-bef45d4-dca297e-oc31b7f/` | `CORE_STATE core=cm0 st=overdue step=alarm_set alive=1` at +246 s, with `over` past the promise |
| 2026-10-12 19:48 | `core-stall-state-cm4-loop.toml` | `bef45d4-dca297e-o7084c0` | PASS | `tools/arclog/runs/20261012T194827Z-bef45d4-dca297e-o7084c0/` | The CM4 looped in SysTick; after the bench reset its `BOOT` was followed by `CORE_STATE core=cm4 st=boot cp=systick` |
| 2026-10-12 19:54 | `core-stall-state-cm0-loop.toml` | `bef45d4-dca297e-ocbfe6f` | PASS | `tools/arclog/runs/20261012T195437Z-bef45d4-dca297e-ocbfe6f/` | The final code: overdue with `alive=0` at +277 s, and the `BOOT` report at +462 s |
| 2026-10-12 20:03 | `core-stall-state-cost.toml` | `bef45d4-d85f795-od66516` | measurement | `tools/arclog/runs/20261012T200338Z-bef45d4-d85f795-od66516/` | The final code (with `kept`): the same figures as 19:04 |

## State after the second session

- All five scenarios of slice 1 have a PASS run. Criterion 1 is ticked: a forced fault on each core leaves a record with the pc, lr, xpsr and, on the CM4, the fault status, and a `CORE_CRASH` line at the next `BOOT`. Simon reworded it on 2026-10-12 from "within a minute" (a halted core cannot print before a reset; the restart is #106 "Fault handlers record the fault and restart the core instead of hanging", the read of a halted core #67 "Read-only post-mortem of a stalled board") and the issue changed first.
- Criterion 5 is half done: the `bench reset` part passed. The power cycle and the press of the NRST button need hands on the bench: after each, the `BOOT` of both cores must read `rst=power` (`rsf` with `BORRSTF`) and `rst=pin`.
- **Trap of the forced-fault Build Overrides.** The gate that stops a fault from firing again lives in the shared RAM, which a flash does not clear, and a boot cannot tell the boot after a forced fault from the first boot after a flash. After an aborted run, or an invalid one that ended before its reset, reset the board (`bench reset <Node ID>`) until it boots without a `CORE_CRASH`, or flash a normal build, before the next run. A better gate would need the flash to leave a mark; it is left as is because only these test builds contain it.
- Node 7 (`nuna-node-09`) could not be flashed on the first day: its Pi's OpenOCD could not read the SWD IDR, and restarting `openocd.service` did not clear it. Only a restart of that Pi by hand (11:12 UTC) cleared the first occurrence the same day: #124 "A Pi Node whose OpenOCD loses the board needs a hard power cycle".

## State after the third session

- Slice 2 is built and its scenarios pass (Runs): a CM0+ stuck at a checkpoint and a lost wake are reported live by the CM4, a CM4 stuck in SysTick by its next `BOOT`; the cost is measured. Criteria 3 and 7 are ticked.
- Criterion 2 is not ticked: it says "each checkpoint of each core", and two ids were forced (`rx_set`, `systick`). Forcing the other thirteen is a run of about nine minutes each for a result that depends only on where the macro stands; either they are run, or the criterion is reworded to "a checkpoint of each core" with a list of the ids covered.
- Not done: the power cycle and the NRST press of criterion 5 (hands on the bench).
- The report of a dead CM4 by the CM0+ (criterion 4) is slice 3: the CM0+ cannot print through a dead CM4, so it is the record in RAM that a post-mortem reads (#67), and #100 for the restart. The CM4 does not yet read the CM0+ record of a crash it did not make.
