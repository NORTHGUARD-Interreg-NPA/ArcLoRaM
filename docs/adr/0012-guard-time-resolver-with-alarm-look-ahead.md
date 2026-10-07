# ADR-0012 — Guard Time Resolver with Alarm Look-Ahead

## Status
Accepted — 2026-06-25.
The Rx window duration below (`slot_active_ms + 2 × guard`) is superseded by ADR-0015: the window now ends at the latest packet start derived from `MAX_GUARD_TIME_MS` and the packet's ToA. The alarm offset is unchanged.
The "Deferred (Version 2)" section is carried out and superseded by ADR-0022 (issue #36): the guard follows the drift estimate, and `GuardTimeResolver_GetGuardMs()` takes the slot start and whether the slot receives from the Sync sender.

## Context

The TDMA Machine applies a guard time to Rx slots so the receive window opens
before and closes after the nominal slot timing, absorbing RTC drift between
Sync corrections. In the initial implementation this is a single `#define`
(`GUARD_TIME_MS = 5u`) used inline in `TdmaMachine_SlotTask()`.

Two problems with the inline approach:

1. **Missing leading guard for non-Sync phases.** The alarm look-ahead
   (`next_slot_is_rx()`) only returns `true` for `DIRECTION_MAC_PHASE`. For
   `DIRECTION_CELL_SKIP` and `DIRECTION_MAC_CELL`, the Rx window duration
   includes `2 × GUARD_TIME_MS` but the alarm is programmed at the nominal
   time — the entire guard margin lands on the tail, not the head. This means
   the Rx node can miss a Tx preamble that arrives early due to peer clock
   drift.

2. **No upgrade path to variable guard.** Version 2 will compute guard time
   from estimated clock drift and sync age, potentially producing different
   values per slot. An inline constant cannot accommodate this without
   scattering drift logic throughout the TDMA Machine.

## Decision

### Guard Time Resolver module

Extract guard time provision into a dedicated module:

```c
/* guard_time_resolver.h */
uint32_t GuardTimeResolver_GetGuardMs(void);
```

Version 1 returns the compile-time constant `GUARD_TIME_MS`. The TDMA Machine
calls this function and applies the result in two places:

- **Alarm offset:** `alarm_ms -= GuardTimeResolver_GetGuardMs();` (wake early
  for predicted Rx slots)
- **Rx window duration:** `RadioSetRx(slot_active_ms + 2u * GuardTimeResolver_GetGuardMs());`

Single return value — the same `guard_ms` is used for both the leading offset
and each half of the window extension. Version 2 may split the interface into
`alarm_advance_ms` / `rx_extension_ms` if decoupling is needed.

### Extended Rx prediction

`next_slot_is_rx()` is extended to cover all three DirectionModes. The
governing principle: guard time is applied when the next slot is
deterministically Rx *or* when its role cannot be determined with certainty.
Only when the next slot is deterministically Tx is guard withheld. In
theory all cases are deterministic; the conservative Rx default is a safety
net. When a node wakes early (guard applied) and the MAC then decides Tx,
the TDMA Machine delays transmission until nominal slot start.

| DirectionMode        | Prediction method                                                  |
|----------------------|--------------------------------------------------------------------|
| `DIRECTION_MAC_PHASE`  | Read `MAC_GetPhaseTxFlag()` (existing)                           |
| `DIRECTION_CELL_SKIP`  | Compute from `CellEligibilityMask` (uplink or downlink mask based on `phase->type`) + `cell_index % 3` vs `hop_count % 3` |
| `DIRECTION_MAC_CELL`   | Phase-type-dependent (see below)                                  |

`DIRECTION_MAC_CELL` prediction per phase type:

- **Mesh_Beacon**: prediction depends on node class. C3: always Tx → no
  guard. C1: always Rx → guard. C2: `BeaconTxBudget > 0` (via
  `MAC_GetBeaconTxBudget()`) → next cell is Tx → no guard; `== 0` → Rx →
  guard. The budget counts down from K after a structural beacon change,
  not from phase start, so the prediction is relative to beacon reception
  not absolute cell index. When the deferred random Tx gate is
  implemented, the prediction must switch to conservative Rx.
- **Sync**: state-aware, consulting the MAC's Epoch Received flag
  (`MAC_GetEpochReceivedThisPhase()` — a new getter). The flag is read at
  alarm-programming time (end of current cell), so it always reflects the
  latest MAC state:
  - C1: always Rx → guard. Deterministic.
  - C3: Cell 0 = Tx → no guard; Cells 1+ = Skip → no alarm. Deterministic.
  - C2: the epoch may be received in any cell depending on hop depth
    (Cell 0 for hop-1, Cell 1 for hop-2, Cell N for hop-(N+1)). Before the
    epoch is received → Rx → guard. After the epoch is received (flag set)
    → Tx → no guard. Because the flag is read after the current cell
    completes, the prediction for the next cell is always deterministic.

The TDMA Machine owns the prediction (queries MAC via getters). The
resolver is a pure value function with no Rx/Tx awareness. The
`MAC_GetEpochReceivedThisPhase()` getter is added to expose the MAC's
epoch-received state for the Sync prediction.

### No phase-boundary special case

Prediction inputs are stable across phase boundaries:

- `MAC_GetPhaseTxFlag()` is effectively a compile-time constant per node class
  (C1 = 0, C3 = 1). Not used for Sync (which uses `DIRECTION_MAC_CELL`).
- `CellEligibilityMask` depends on `hop_count` (stable across phases) and
  `cell_index` (from the advanced cursor). The uplink/downlink mask is selected
  via `phase->type`, which is available from the TDMA Table.
- K is conserved across Mesh_Beacon phase occurrences, so it is never stale.
- `MAC_GetEpochReceivedThisPhase()` is reset at Sync phase entry
  (`MAC_OnSlotOpportunity` detects phase index change), so it is never
  stale when consulted for the next cell's prediction.

No boundary skip or fallback logic is needed.

### Tx node behaviour

The Tx node always transmits at nominal time — no guard adjustment on the Tx
side. If the node woke early (guard applied because the prediction was Rx or
uncertain) and the MAC decides Tx, the TDMA Machine delays transmission
until nominal slot start. The Rx guard must absorb bilateral drift (local +
peer). In V1 this is covered by the fixed 5ms constant being sized for
worst-case bilateral drift at the expected Sync correction cadence.

## Alternatives Considered

**Resolver owns Rx prediction** — `GuardTimeResolver_GetGuardMs(cursor, phase)`
returns 0 for Tx/Skip slots and `guard_ms` for Rx slots. Rejected: prediction
logic would duplicate DirectionMode awareness already present in the TDMA
Machine. The existing pattern (MAC exposes getters, TDMA Machine reads them)
is cleaner.

**Systematic phase-boundary skip** — skip look-ahead at every phase transition
as a safety net. Rejected: after analysis, all prediction inputs are stable
across boundaries. The skip would cost one unguarded leading edge per phase
transition with no correctness benefit.

**Keep guard time inline** — replace the constant with a function call but
don't create a separate module. Rejected: mixes drift estimation concerns
(V2) with cursor traversal mechanics, violating the TDMA Table / TDMA Machine
separation principle.

## Deferred (Version 2)

- Variable guard based on estimated clock drift and time since last Sync
  correction
- Bilateral drift model: Rx node estimates total drift (own + worst-case peer)
- Potential interface split: `alarm_advance_ms` and `rx_extension_ms` as
  separate return values
- Per-node guard asymmetry between communicating nodes
## Amendment — 2026-09-25

Values and behaviour have moved since this ADR was accepted; the decision itself (resolver module, TDMA Machine owns the Rx prediction) is unchanged.

- The guard constant is `MAX_GUARD_TIME_MS = 100` ms (`guard_time_resolver.h`), not `GUARD_TIME_MS = 5`.
  It is 3·T_S at SF12/BW125, the LoRa preamble-locking ceiling, and is aliased as `SYNC_RESYNC_THRESHOLD_MS` (the Tier 3 threshold).
- Sync prediction for C3: Tx in the first `SYNC_TX_BUDGET` (3) cells of each Sync Phase occurrence, Skip for the rest (see SyncTxBudget in `CONTEXT.md`), not "Cell 0 Tx, Cells 1+ Skip".
- The Tx-side paragraph's "fixed 5ms constant" reads "fixed 100 ms constant".
- The delay-to-nominal on an early Tx wake relies on `TdmaPlatform_t.WaitUntilMs`.
  The CM0+ platform (`subghz_phy_task.c`) does not provide it yet, so on target a Tx after a guard-early wake currently goes out up to one guard early.
  The ArcLog `SYNC_TX` event logs `plan` (nominal) and `send` (actual), which makes this measurable on the bench.
