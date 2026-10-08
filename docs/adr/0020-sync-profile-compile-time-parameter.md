# ADR-0020: The Sync profile is a compile-time parameter

## Status
Accepted. The mechanism and the DEV profile are measured on the bench (an 18 h run, #45).
The PROD period is derived below for the NUCLEO Clock and stays at 9 min; it is provisional only for the Production Clock, whose residual nobody has measured (#112).

## Context
Sync is protocol overhead and is kept as low as the clocks allow: the longest Sync period the drift bound permits (#45).
A long period has a cost for everything that is not about sync: a cold node needs three packets to lock, one per phase, and the rate estimator needs eight samples, so at the period of the product a node takes tens of minutes to lock and over an hour to learn its rate.
Work that assumes synchronised clocks (an Uplink test) must not wait for that.

The bring-up table that was the default (a Sync phase every 30 s, three packets per node per phase) is no answer to this: it is 9.9 % duty cycle against the 1 % band, the compliance credit is gone after 6.1 min and the nodes are denied from then on (measured on the bench, reproduced on the host).
A development session on it would lose its synchronisation in the middle.

## Decision
One compile-time parameter, `SYNC_PROFILE` (`Common/Protocol/sync_profile.h`), picks the Sync schedule through the single TDMA table accessor (ADR-0003):

| Profile | Sync phase | Packets per node per phase | Duty cycle of a node | Silence timeout | Use |
|---|---|---|---|---|---|
| `SYNC_PROFILE_DEV` (the default) | 200 s | 1 | 0.496 % | 15 min | development: frequent Sync, inside the budget |
| `SYNC_PROFILE_PROD` | 540 s (provisional) | 1 | 0.18 % | 27 min (3 periods) | the product; #45 replaces the numbers |
| `SYNC_PROFILE_BRINGUP` | 30 s | 3 | 9.9 % | 15 min | the host tests, which were written against it |

- **One transmission is one repetition.** `SYNC_TX_BUDGET` is 1 in the DEV and PROD profiles: the Sync airtime is one packet per node per phase.
- **DEV is sized to 0.5 % of the band**, one packet of 991 ms per 198.2 s at the least, rounded to 200 s, so the Sync overhead leaves the other half of the band to the data phases.
- **The PROD period** follows the drift bound of #45 with one lost packet tolerated, 9 min (540 s): see "The PROD period and the budget" below.
  It is provisional only for the Production Clock (#112).
- **The silence timeout belongs to the profile.** One lost packet (k = 1) must not drop a node to `CLOCK_COLD`: it outlasts two periods, three for PROD (ADR-0013).
- **Each constant stays overridable on its own** (`-D SYNC_TX_BUDGET=1u`), so a scenario can still vary one value, and the host tests pin `BRINGUP`.
- **The profile is logged** in the CM0+ `BOOT` line (`sync=`), so a trace says which schedule it ran.
- The default is DEV while PROD is provisional; it becomes PROD when #45 lands its table.
- **Boot burst, an option for faster warm-up** (`SYNC_BOOT_BURST=N`, default 1 = none): the first Sync phase after the C3 boots sends N packets in its first cells (20 s apart on DEV), drawn from the full duty-cycle credit (the 36 s bucket holds 36 packets, N is at most the 10 cells of a phase); every later phase sends one.
  Boards flashed or reset together with the C3 lock within that phase (about a minute on DEV) instead of three phases.
  N must leave three packets after the cells a node misses while it boots: boards are flashed one after the other, so a node is up 8 s or more after the C3 and misses cell 0. The bench of 2026-10-04 showed N = 3 giving a C2 two packets (the RTC set and one good) and a lock a phase later; N = 5 is the value of the scenario.
  It does not help a node reset alone later, nor a node below a relay: they keep the regular schedule.

## The PROD period and the budget (#45)

**The bound.**
One Sync interval must not grow the offset between neighbours past R1 of #76, 5 ms, with one lost packet tolerated (k = 1, decision of 2026-10-04): `(k + 1) x period x residual + fixed terms <= 5 ms`.
The fixed terms are those of `D(t)` in ADR-0022: 3 sigma of the stamp noise, one RTC tick (245 us) and the offset a Sync error under the Correction Threshold leaves in the clock.

**The inputs, all NUCLEO Clock** (`docs/test-tracker/34-runtime-calibration.md`, `36-guard-from-drift.md`):

- The Residual after calibration: 0.36 ppm measured on the pair of #34, and 0.72 ppm by design, the ceiling at which a `CALR` write is due (3/4 of the 954 ppb step).
- The stamp noise: 106 us (first pair) to 250 us (the pair of the 6 h run of #36).
- The Correction Threshold: 1 ms (ADR-0022).

| Case | Fixed terms | Room for the drift | Longest period (k = 1) |
|---|---|---|---|
| The first derivation: threshold 3 ms, whole-ms stamp, margin 3 x 0.4 ms | 3 + 1.2 = 4.2 ms | 0.8 ms | 9.3 min at 0.72 ppm |
| As built: threshold 1 ms, noise 250 us | 1 + 0.75 + 0.245 = 2.0 ms | 3.0 ms | 34.8 min at 0.72 ppm |
| As built, two neighbours each at the ceiling (relative residual 1.44 ppm, the worst case between a hop and its sender) | 2.0 ms | 3.0 ms | 17.4 min |

**The PROD period is 540 s**, the first row rounded down to a whole number of minutes.
It was derived before the threshold of #36 went from 3 ms to 1 ms, and it is under every row: with the fixed terms of the build it holds the bound up to a residual of 2.8 ppm, 3.9 times the ceiling of 0.72 ppm.
It is not the longest period the bound allows on the NUCLEO Clock (about 35 min), which the rule "Sync is protocol overhead, kept to a minimum" would take.
It stays at 540 s for two reasons: the residual of the Production Clock is not measured, and a longer period lengthens the lock of a cold node (three packets) and the rate estimate (eight samples), and with them the silence timeout, which is three periods.
Whether to lengthen it is the decision of #112, once the Production Clock is measured.

**The budget.**
The Sync packet is 10 bytes at SF12 / BW125, 991 ms on air.
The C3 and each relay of a line send one packet per phase (`SYNC_TX_BUDGET` is 1), so the worst relaying C2 spends what the C3 spends.

| Profile | Packets per node per hour | Airtime per node per hour | Share of the band | Room to the 1 % limit |
|---|---|---|---|---|
| `DEV` (200 s) | 18 | 17.8 s | 0.496 % | 2.0 times |
| `PROD` (540 s) | 6.7 | 6.6 s | 0.184 % | 5.4 times |

The boot burst (`SYNC_BOOT_BURST`, 5 on the bench) is a one-off in the first phase: 5 packets, 5.0 s of the 36 s of credit.
`Tests/unit/test_sync_budget.c` checks all of it with the real compliance engine and TDMA table, once per profile: a C3 and a relaying C2 never denied in 4 h, the burst never denied, the airtime under the profile's share and under 1 % of the time, the credit never falling near the 2500 ms a request needs; the `BRINGUP` table is denied after 6.1 min (the control).
On the bench: `DEV` for about 18 h (Test Record `docs/test-tracker/45-sync-schedule.md`), no `TX_DENIED`, every Sync packet received.
`PROD` was not run on the bench.

## Considered Options

- **A run-time switch (a command from the CM4 or the network).** Rejected: the table is compile-time with one accessor (ADR-0003), and a product node must not be able to change its Sync overhead.
- **Keep the bring-up table as the development schedule.** Rejected: it starves itself after 6.1 min.
- **A burst in every phase with a longer period.** Rejected: at the same duty cycle 3 packets per phase stretch the period 3 times, so only the average cold start improves (about 500 s to about 300 s).
- **A boot burst only.** Adopted as the option above. It shortens the cold start of a node that boots with the C3, not of one reset alone later, which would need the C3 to learn that a node is cold (an uplink request, not built).

## Consequences

- A cold node reaches `CLOCK_WARM` in about 10 min on DEV (three phases) and 27 min on PROD, and its rate estimate is valid after about 33 min on DEV and 72 min on PROD (eight samples).
  The factory baseline (#23, ADR-0019) is what covers the rate on PROD until then.
- The Sync airtime shares the 1 % band with the data phases: DEV leaves about half of it.
- The MAC's `SYNC_TX_BUDGET` and `SYNC_SILENCE_TIMEOUT_MS` are defined by the profile; the MAC header no longer carries its own defaults.
