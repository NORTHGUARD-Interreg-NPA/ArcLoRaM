# No alarm chain while cold; deadline-bounded Rx windows

A C1/C2 node in `CLOCK_COLD` does not run the TDMA alarm chain: it listens continuously on the discovery channel (868.3 MHz) with no timeout, re-arming after every reception, while the MCU sleeps in Stop2 until the radio wakes it.
The chain starts on the first Sync packet (`TdmaMachine_BootstrapFromSync`) and stops whenever the clock falls back to `CLOCK_COLD` (silence timeout, suspect cursor, Tier 3).
Once synced, an Rx window stays open until the latest instant a packet can start and still end by `slot end + MAX_GUARD_TIME_MS`, and the radio's own Rx timer stops on preamble detection so a packet that starts in time is always received in full.

The bench showed why: the chain ran from boot on an arbitrary RTC and cursor, and the radio's symbol timeout (5 symbols) closed every window about 140 ms after it opened.
A cold node therefore listened in 140 ms windows every 3 s, unrelated to the SyncAnchor's schedule, and caught a Sync packet only when the two happened to overlap (about 1 chance in 5 per Sync cell).

## Considered Options

**Scanning: symbol timeout 0 but keep the alarm chain from boot.**
Rejected: windows would still be slot-sized and placed on a meaningless schedule, with gaps between them and a channel taken from an unsynced cursor.
The chain has no meaning before the FrameCursor is anchored.

**Synced window end: nominal end + guard (the former `slot_active_ms + 2 × guard`).**
Rejected: it ties the window to the guard rather than to what can physically be decoded, and a window closed by the driver's software timer cuts a packet still arriving.

**Window timer stopped on header / sync-word detection instead of preamble.**
Deferred (issue #39): stronger proof of a real packet before committing, but a later decision point, and no header once the Sync packet is implicit.

## Consequences

- **Current guard vs maximum guard.** The early wake uses the current guard; the window end uses `MAX_GUARD_TIME_MS`, a property of the slot grid. They are equal until Guard Time Resolver V2 (#36). _Amended by ADR-0022: in `CLOCK_WARM` the window end follows the current guard; the maximum guard still sets the cap and the geometric end while acquiring._
- **Expected ToA is the Sync packet's for every slot** until per-slot ToA is derived from the schedule (#38).
- **Safety cap.** Because the timer stops on preamble, a false detection could hold the receiver; a platform timer aborts any reception still running at `slot end + MAX_GUARD_TIME_MS` (`RX_CAP`).
- **Preamble detection margin.** The hardware timeout is the latest packet start plus the programmed preamble length (8 symbols, 262 ms), an upper bound on detection time; a packet detected in that margin but too late to fit is aborted by the cap.
- **Sync packets with a non-Sync phase index are rejected** by the MAC before touching the RTC (`SYNC_REJ`): accepting one would leave the node out of `CLOCK_COLD` with no chain to run.
- **Radio sleeps after each synced reception** instead of idling in standby until the next slot.
- **Radio callbacks run in the radio ISR**, so the chain can be stopped (Tier 3) while a slot task is pending; a slot task that finds the chain stopped returns without touching the radio.
