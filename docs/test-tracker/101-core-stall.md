# Issue #101: find the cause of the core stalls (crash record, reset cause, checkpoints)

Purpose: acceptance. Issue: #101 (Find the cause of the core stalls: crash record, checkpoints and the reset cause on both cores).

No core may stay down: when either core (CM4 or CM0+) stops, the trace and a record that survives must say which core, where and why.
The issue stays open until a real stall is explained by these records, so this Test Record is written for the whole issue and filled slice by slice.

- **Slice 1 (this record's first runs): the crash record on both cores, the CM4's BusFault/UsageFault/MemManage handlers, and the reset cause in `BOOT`.** They change no behaviour; they cover criteria 1 and 5.
- Not started: the heartbeat and the checkpoints (criteria 2, 3, 7), the report from the survivor (criterion 4), the audit of the waits (criterion 6), the bad jump (criterion 8), the soak that waits for the next stall (criterion 9).

Clock: NUCLEO (development crystal), not the production TCXO, for every run of this record.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/core-stall-crash-cm4.toml` | Node 6 (C2) jumps to an address with no memory from its CM4 main loop (`BENCH_FAULT_CM4=1`) and stops in its fault handler. After the bench reset at +4 min, its `BOOT` is followed by `CORE_CRASH core=cm4 kind=bus why=ibuserr pc=60000000` and `CORE_CRASH_SCB cfsr=00000100`. The C3 (Node 1) reports no crash |
| `tools/bench/scenarios/core-stall-crash-cm4-forced.toml` | The same fault with the BusFault handler off (`BENCH_FAULT_CM4=4`) arrives as the forced HardFault of silence events 3 and 4: `kind=hard why=ibuserr`, `hfsr=40000000` |
| `tools/bench/scenarios/core-stall-crash-cm4-data.toml` | A data read of an address with no memory (`BENCH_FAULT_CM4=3`) is reported with its address: `why=preciserr`, `cfsr=00008200`, `bfar=60000000` |
| `tools/bench/scenarios/core-stall-crash-cm0.toml` | The CM0+ jumps to an address with no memory (`BENCH_FAULT_CM0=1`) while the CM4 keeps running; after the bench reset the CM0+ reports `CORE_CRASH core=cm0 kind=hard why=na pc=60000000` and no `CORE_CRASH_SCB` (it has no status registers) |
| `tools/bench/scenarios/core-stall-reset-cause.toml` | `BOOT` of both cores carries `rst`, and after the flash and after a bench reset (the Pi Node's OpenOCD drives the NRST pin) all four `BOOT` lines read `rst=pin` |

A forced fault is a Build Override (`BENCH_FAULT_CM4` or `BENCH_FAULT_CM0` picks the kind, `BENCH_FAULT_NODE` the one board to fault, `BENCH_FAULT_AFTER_S` the seconds after boot, 60 in the scenarios); no other build contains it.
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
- `tools/arclog/tests/test_schema.py` (existing): every `ARCLOG` call of the firmware matches the event table, so `CORE_CRASH`, `CORE_CRASH_SCB`, `CORE_CRASH_REGS`, `CORE_CRASH_STK` and the new `rst` and `rsf` of `BOOT` are checked against the C sources.

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

## Criteria

- [ ] A forced HardFault on the CM0+ and on the CM4 (a Build Override each) leaves a crash record with the PC, LR, xPSR and the fault status, and a `CORE_CRASH` line (or, for a dead CM4, the record read by `bench postmortem` of #67) within a minute. Slice 1: the four `core-stall-crash-*` scenarios. The line comes at the next `BOOT`, the reset being bench's (a halted core cannot print; the read of a halted core is #67)
- [ ] A forced endless loop at each checkpoint of each core (the Override picks the id) gives a `CORE_STATE` line that names that core and that checkpoint. Slice 2
- [ ] A forced lost wake (the Override skips one Alarm A programming) gives the alarm checkpoint and an overdue deadline. Slice 2
- [ ] A dead CM4 with a live CM0+ is reported as such, not as a silent CM0+. Slice 3
- [ ] `BOOT` carries the reset cause on both cores, checked after a power cycle, a pin reset and a `bench reset`. Slice 1: `core-stall-reset-cause.toml` for the `bench reset`, and by hand for the power cycle and the pin reset
- [ ] The audit of the waits of both cores is written down (in the Test Record or an ADR) with each decision. Slice 4
- [ ] The cost of the checkpoints is measured with `timing-probe` (microseconds per slot) and the RAM it takes is stated. Slice 2
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

## State at the end of the first session

- Not run: `core-stall-crash-cm0.toml`; the power cycle and the NRST press of criterion 5 (they need Simon at the bench).
- `core-stall-crash-cm4-data.toml` has not passed yet: the two attempts failed on a leftover (see the Runs table), the scenario itself is unchanged.
- **Trap of the forced-fault Build Overrides.** The gate that stops a fault from firing again lives in the shared RAM, which a flash does not clear, and a boot cannot tell the boot after a forced fault from the first boot after a flash. After an aborted run, or an invalid one that ended before its reset, reset the board (`bench reset <Node ID>`) until it boots without a `CORE_CRASH`, or flash a normal build, before the next run. A better gate would need the flash to leave a mark; it is left as is because only these test builds contain it.
- Node 7 (`nuna-node-09`) could not be flashed: its Pi's OpenOCD cannot read the SWD IDR, and restarting `openocd.service` did not clear it. Only a restart of that Pi by hand (11:12 UTC) cleared the first occurrence the same day: #124 "A Pi Node whose OpenOCD loses the board needs a hard power cycle".
