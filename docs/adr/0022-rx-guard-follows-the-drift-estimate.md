# ADR-0022: The Rx guard follows the drift estimate

## Status
Accepted for the mechanism (issue #36).
The ratio is provisional until the several-hour run of the Test Record confirms it.
The Rx start latency is measured with `timing-probe` (Test Record, 2026-10-05), on the NUCLEO Clock.
Supersedes the "Deferred (Version 2)" section of ADR-0012, and the consequence of ADR-0015 that the current guard and the maximum guard are equal until Version 2.

## Context
ADR-0012 gave every Rx slot one guard, `MAX_GUARD_TIME_MS` (100 ms, 3 x T_S), and ADR-0015 ended each window at the latest packet start the slot geometry allows.
An empty window therefore listened 1970 ms: it opened at nominal - 100 ms, ran to nominal + 1608 ms (slot end + 100 - ToA of 992) and the platform added the 262 ms of preamble margin.
Nothing tied the guard to the clocks' real state, and the Sync tiers were a fixed 8 to 100 ms range.
The drift estimator (#34, ADR-0018) now gives the Residual and the noise of the stamp, which is in RTC ticks since #82.
Every packet starts on air at its nominal start (ADR-0016), so a Sync packet can only be late by clock error.

## Decision
**The guard** of an Rx slot is `ratio x D(t) + latency`, in whole ms, rounded up (`guard_policy.h`, pure, host tested).

| Term | Value | Why |
|---|---|---|
| drift | abs(Residual) x time since the last Sync | what the clock gains since the last reception |
| noise | 3 x `noise_us` of the estimate (the estimator's 400 us floor while it reads 0) | the scatter of the stamp |
| tick | 245 us | the stamp is in RTC ticks of 1/4096 s, rounded up |
| uncorrected offset | `SYNC_CORRECT_THRESHOLD_MS` (1 ms) | a packet under the threshold leaves the clock alone, so up to that much stays in it |

`D(t)` is their sum, the `ratio` is 3 (`GUARD_RATIO`, the largest that keeps the guard under the 15 ms target of #46 with R1 at 5 ms), and the Rx start latency (`GUARD_RX_START_LATENCY_US`) is added after the ratio, because it is a delay of the radio and not an uncertainty of the clock.
The latency is the CPU path from the slot task to a radio armed in Rx, 4.14 to 4.50 ms measured without the log lines of a bench build (14.5 ms with them, the `SLOT` and `RX_WIN` lines costing 5 ms each), and the constant is 5000 us, that maximum rounded up.
What the radio does after `RadioSetRx` returns (TCXO, PLL) is not visible to a CPU probe and is not in it.

- **Bounds.** Never below `SYNC_PARTICIPATE_THRESHOLD_MS` (8 ms): a packet that is still Tier 1, and so relayed, must be inside the window. With the 5 ms of latency the formula's own minimum is 9 ms, so the floor does not bind today; it keeps the rule if a constant changes. Never above `MAX_GUARD_TIME_MS` (100 ms), the property of the slot grid; a node that would need more degrades, as before.
- **When.** Only with a valid estimate and in `CLOCK_WARM`. While acquiring, or before the estimate is valid (about 33 min of packets on DEV), the guard is the cap. C3, the time reference, has no estimate and keeps the cap.
- **Outside the Sync phase** the sender is a peer with its own error, which the estimate says nothing of. It is assumed like this node's, so the four terms above double (`GuardPolicy_PeerMs`). The table of the product has only Sync phases today, so this is covered on the host and not on the bench.
- **The window end.** In `CLOCK_WARM` the latest packet start is `nominal + guard` instead of the geometric one, so an empty window listens `2 x guard + 262 ms`: 282 ms at the 10 ms of the NUCLEO pair, against a median of 1939 ms measured before; at the cap of 100 ms, measured 466 and 458 ms. The cap (slot end + `MAX_GUARD_TIME_MS`), the preamble margin and the geometric end while acquiring are unchanged. A CONTENTION slot keeps the geometric end: its packet starts after channel sensing and a backoff, not at nominal.
- **Two thresholds.** `SYNC_PARTICIPATE_THRESHOLD_MS` (8 ms) is unchanged: relay eligibility and the acquisition lock check. `SYNC_CORRECT_THRESHOLD_MS` is new, 1 ms: from there up to the resync threshold the phase is corrected (a shift, now taken from the error in microseconds); below it the clock is left alone. The bands are: under 1 ms nothing and relays; 1 to 8 ms corrected and relays; 8 to 100 ms corrected, no relay (Tier 2); 100 ms and above re-anchor (Tier 3). The resync threshold stays at the cap, not at the instantaneous guard: a packet the window caught is not re-anchored.
- **The 1 ms** is the first whole-ms value above the noise floor of the stamp (3 sigma of 106 us plus a constant of at most 0.25 ms, about 0.57 ms, NUCLEO) and keeps the concurrent-transmission budget of `2 x depth x error < 16 ms` open to a depth of 7, against 2 for the 3 ms of the issue.
- **The trace.** `RX_WIN` carries `g`, the guard in ms, and `win`, the span handed to the radio (the platform adds the preamble margin); `RX_LATE` carries `g`. A report can give the guard distribution and tell a mistimed loss from a radio one.
- **The shift hook takes the error in microseconds** (`rtc_align_subsecond(int32_t err_us)`): in whole ms it could not carry a correction near 1 ms. The shift is the nearest RTC tick.

## Considered Options
- **The issue's formula, with no uncorrected-offset term.** Rejected: on the NUCLEO pair the guard would be 2 ms against a worst case of 1.63 ms, 0.37 ms of margin left for what the model got wrong; and with the 3 ms threshold of the issue it would be 2 ms while up to 3 ms stays in the clock.
- **The latency inside the ratio.** Rejected: the ratio would multiply a delay that carries no uncertainty.
- **The Tier 1 threshold as a cap (a guard of at most 8 ms).** Rejected: a node whose estimate needs more would miss packets it can still receive and correct. 8 ms is the floor, 100 ms the cap.
- **A peer model per phase.** Not needed yet: one factor of 2 until a second phase type exists on the bench and a peer's own state can be known.
- **The resync threshold at the instantaneous guard** (comment of #36). Deferred to Phase 2 (#43), as the issue body says.
- **Skipping `RadioSetChannel` when the frequency does not change** (about 1 ms of the 5 ms latency). Rejected: the slot would depend on a prediction of the next slot's frequency, a dependency between the slot task and the frequency resolver that is not worth about 1 ms.
- **Tightening the guard in this issue** (the measured last error instead of the 1 ms bound, the two log lines after `RadioSetRx`, the correction threshold and the ratio chosen from data). Deferred to #102, which starts once the several-hour run of this issue has passed: that run is the baseline it compares with.

## Consequences
- An empty window in `CLOCK_WARM` listens 282 ms at a guard of 10 ms instead of a median of 1939 ms (the Test Record holds the measured before, n = 214, and after at the cap, 466 and 458 ms; the figure at 10 ms is for the several-hour run). The lever is the closing instant: the 262 ms of preamble margin does not depend on the guard.
- The Tier 1 band is entirely inside the window, since the guard is at least 8 ms.
- **The guard is the margin of the design, not the limit of what the radio catches, on a strong link.** The preamble is detected 67 to 105 ms after the packet start when the radio is already listening (183 packets: the cap guard, and older builds) and mostly 129 to 134 ms when it starts listening after the packet has begun (69 packets: the adaptive guard in a bench build, where the two log lines delay the radio by 10 ms, so it listens from about nominal + 5 ms): joining a preamble in progress costs about 57 ms of detection, and the packet is still received. The timer stops on detection and runs to `nominal + guard + 262`, so a late packet is caught up to about guard + 128 ms at the slowest detection seen, past the 100 ms resync threshold. Only strong signals were measured (SNR 5 to 10 dB): a weaker link detects later and its reach shrinks toward the guard itself, which is why the margin stays conservative. A packet that starts well before the window opens is not tested.
- The figures behind the thresholds (the noise, the residual of the example) and the latency are NUCLEO measurements. The Production Clock needs its own.
- The latency applies to both edges of the window, because the guard is one value: the closing instant carries 5 ms it does not need. Splitting the wake advance from the window extension (the split ADR-0012 foresaw) would save the 5 ms of the closing instant, under 2 % of an empty window, and is not done.
- `MAC_GetLastSyncMs()` is added to the MAC interface, and the shift hook changes signature: the MAC tests change their stubs, not their meaning.
- C3 keeps the cap in every Rx slot, by design and for good: it is the time reference and is powered, so a long Rx window costs it nothing that matters, and the cap is what covers the errors of the senders whose state it cannot see.
- The estimate is kept across `CLOCK_COLD`, but the guard returns to the cap in `CLOCK_ACQUIRING` and grows back only once the clock is `CLOCK_WARM` again.
