# ArcLoRaM Domain Language

## Node Class

Three node classes exist. The class is a **compile-time constant** (enabling dead
code elimination) and a **shared memory constant** (so both CM4 and CM0+ can read
it without each maintaining their own copy). Both representations coexist.

| Class | Role |
|---|---|
| `C1` | End node. Cluster-only. Sensor data producer. Communicates solely with its cluster master. |
| `C2` | Relay. Cluster master for C1 nodes. Mesh backbone participant. May also collect sensor data. |
| `C3` | Gateway. Mesh backbone terminus. SyncAnchor — originates Sync packets, never enters `Scanning`. May act as cluster master. |

## Node ID

The one-byte identity a node carries in its packets (`node_id`, `source_node_id`, `upstream_node_id`) and in its logs.
It is not derived from anything: each board is registered once in a compiled table (`Common/Protocol/node_id.c`) that maps the MCU 96-bit unique ID (UID) to its Node ID, and every board runs the same build of its node class.
Values 1-254 are assignable; `0x00` means unprovisioned (the board's UID is not in the table) and `0xFF` is reserved for broadcast.
The CM0+ `BOOT` line logs both (`id=`, `uid=`), and `arclog` flags `id=0` with the table entry to add.
A Node ID belongs to the board, not to its node class or its serial port (see ADR-0017).

## CM4 Wake Sources

CM4 exits Stop2 on two independent hardware interrupt sources. These are
orthogonal — either can fire at any time regardless of the other.

| Source | Trigger | CM4 action |
|---|---|---|
| RTC Alarm B | Sensor acquisition schedule (programmed by CM0+ on CM4's behalf — see `AlarmBRequest`) | Run sensor acquisition cycle, write result to appropriate TX queue, write next `AlarmBRequest` to shared memory |
| IPCC interrupt (MbMux) | CM0+ fires Application Signal Channel notification | Process signal (`RX_READY`, `TX_NO_ACK`, `SYNC_LOCKED`, `ACK_RECEIVED`, `RX_TIMEOUT`, or `SYNC_LOST`), send ACK |

On receiving `SYNC_LOCKED`: CM4 writes an `AlarmBRequest` to shared memory if
not already pending, then returns to Stop2. CM0+ programs Alarm B on its next
wakeup. If a sensor cycle is already running (re-sync after sync loss), CM4 does
nothing — the existing cycle continues uninterrupted.

### AlarmBRequest
Shared memory structure written by CM4, read and consumed by CM0+. Contains the
desired next Alarm B wake time as RTC time fields (BCD, matching the RTC
register format — no conversion) and a `pending` flag (`uint8_t`, naturally atomic
on ARM Cortex-M). CM4 writes the time fields then sets `pending = 1`. CM0+
acts only when `pending == 1`, programs Alarm B, then clears `pending`. This
eliminates the race where CM0+ reads a partially-written or stale request.
CM4 writes the minimum next-alarm across all due sensors — sensor scheduling
details are deferred (see Deferred section). CM0+ is the sole writer to RTC
registers (see ADR-0007).

## TDMA Scheduling

### Frame
The top-level configurable repeating period of the TDMA schedule. Duration is a
runtime parameter (`frame_duration`). Every Frame is structurally identical — the
schedule repeats without nesting. (example: 1Hour, 24H..)

### Phase
A contiguous time subdivision of a Frame with a given Phase Type. A Frame is an
ordered array of Phases. A Phase is identified by its **index** in that array —
Phase Type is descriptive, not a unique key. The same Phase Type may appear more
than once in a Frame.

### Phase Type
Five-value enum describing the network role of a Phase. MAC-layer concept;
distinct from Radio State.

| Value | Layer | Role |
|---|---|---|
| `Mesh_Beacon` | Mesh | Link detection for routing (C2/C3 nodes) |
| `Mesh_Uplink` | Mesh | Node-to-gateway sensor data transmission |
| `Mesh_Downlink` | Mesh | C3-originated commands/config pushed down the mesh. C2 decides per-packet: relay further down the mesh, distribute to its cluster (stopping mesh propagation), or act locally. |
| `Cluster_Exchange` | Cluster | Bidirectional local communication between cluster master (C2) and end nodes (C1) |
| `Sync` | Network | Forwards a time synchronisation packet across the network. Sole purpose: propagate Frame Epoch corrections hop by hop. No data, no routing cost. Simple Cell × N, no Header/Footer Slot. |

**Mesh** phases operate at the backbone level (C2/C3, long-range, high SF).
**Cluster** phases operate at the star-subnet level (C1/C2, short-range, low SF).

### Cell
The repeating unit within a Phase. All Cells in a Phase are identical. A Phase
is a sequence of `cell_count` identical Cells. A Cell contains one or more
Slots (`slot_count`). When a Cell contains multiple Slots, the individual
Slots may have different Radio States — see Cell Role.

### Slot
The atomic radio access opportunity within a Cell — the only level at which the
radio hardware is actually commanded to Tx, Rx, or Sleep. Each Slot corresponds
to a single packet (Beacon, Sync, Data, or ACK). A Slot is *not* a complete
exchange (e.g., Data + ACK). Guard time is applied by the TDMA Machine — it is
not a separate Slot type.

### Cell Role
The logical characterization of what a node does during a Cell. Determined by
the phase's `DirectionMode` and the MAC State Machine. Three values:

| Role | Meaning |
|---|---|
| `Tx-role` | The node initiates a primary exchange (data, sync, or beacon transmission) in this Cell. |
| `Rx-role` | The node receives a primary exchange in this Cell. |
| `Skip` | The node sleeps through the Cell entirely — no Slot is active. |

Cell Role is defined by the radio direction of the cell's primary exchange.
When a Cell contains multiple Slots (e.g., a data packet followed by an
acknowledgement), different Slots within the same Cell may have different
Radio States. The MAC State Machine resolves each Slot's Radio State from
the Cell Role and slot position.

In a single-slot Cell (`slot_count = 1`), Cell Role and the Slot's Radio
State coincide — which is why the two concepts are easily conflated.

`DirectionMode` governs how Cell Role is assigned:
- `DIRECTION_CELL_SKIP`: Cell Role derived from `CellEligibilityMask` +
  hop-count residue. The MAC computes the mask internally from `hop_count`
  and uses it in `MAC_OnSlotOpportunity` to return `SLOT_SKIP` for ineligible
  cells. The TDMA Machine does not read the mask to skip cells — it wakes for
  every cell and delegates to the MAC. For guard-time prediction only, the
  TDMA Machine queries the mask via MAC accessor functions.
- `DIRECTION_MAC_CELL`: MAC assigns Cell Role per-cell from internal state.
  The TDMA Machine does not pre-filter — it wakes for every cell and passes
  the opportunity to the MAC, which may assign Tx-role, Rx-role, or Skip.
  For `Mesh_Beacon`, the MAC never assigns Skip (every cell is Tx or Rx).
  For `Sync`, C3 is Tx in the first `SYNC_TX_BUDGET` cells, Skip in the rest.
- `DIRECTION_MAC_PHASE`: `phase_tx_flag` assigns the same Cell Role to
  every Cell in the Phase. Currently unused — no phase requires uniform
  per-phase direction. Retained for future use.

### Slot Kind
Qualifies how a Slot is accessed:
- `SCHEDULED`: the Cell Role is predetermined by the phase's `DirectionMode`
  (via `CellEligibilityMask` or MAC-internal state). Each
  Slot's Radio State follows from the Cell Role.
- `CONTENTION`: any eligible node may attempt to transmit after channel sensing
  and random backoff. The MAC State Machine decides Tx vs Rx at runtime.

### Phase Header Slot
An optional single Slot anchored to the **start** of a Phase, outside the Cell × N
repetition. Has its own `duration_ms` and per-class bitmap. Used for management
or control frames (e.g., cluster management packet at the start of Cluster_Exchange).
Absent when `duration_ms == 0`.

### Phase Footer Slot
An optional single Slot anchored to the **end** of a Phase, outside the Cell × N
repetition. Has its own `duration_ms` and Slot Kind. In `Cluster_Exchange` this
is a CONTENTION slot: nodes sense the channel, then attempt to transmit a data
packet. The MAC State Machine handles packet type selection, sensing, and backoff
entirely. Absent when `duration_ms == 0`.

### Slot Active Duration
A per-Phase field (`slot_active_ms`) representing the worst-case duration of one
 Slot.
Actual airtime may be shorter as modulation parameters vary, but the alarm chain
always uses `slot_active_ms` to guarantee no overlap. The next alarm is programmed
as `slot_start + slot_active_ms + gap_slots_ms[i]`.

### Radio State
The hardware operating state of the radio chip: `Tx | Rx | Sleep`.
Maps directly to SX127X / STM32WL PHY states. A hardware-layer concept — not to be confused with Phase Type (MAC layer).

### Guard Time
A timing margin that absorbs RTC clock drift accumulated since the last Frame
Epoch correction. Not stored in the TDMA Table.

**Version 1** uses a global protocol constant (`MAX_GUARD_TIME_MS = 100`,
equivalent to 3·T_S at SF12/BW125). A dedicated module — the **Guard Time
Resolver** (`GuardTimeResolver_GetGuardMs()`) — provides this value. The TDMA
Machine calls it and applies it in two places:

1. **Alarm look-ahead:** when programming the next RTC Alarm, if the TDMA
   Machine predicts the next Slot will be Rx (via `next_slot_is_rx()`), it
   subtracts `guard_ms` from the nominal alarm time — waking the node early.
2. **Rx window opening:** the receive window opens at the early wake,
   `guard_ms` before nominal start.
   Where it closes is not a guard-time question: see Rx Window.

The Rx prediction (`next_slot_is_rx()`) covers both used DirectionModes.
The governing principle: guard time is applied when the next slot is
deterministically Rx *or* when its role cannot be determined with certainty.
Only when the next slot is deterministically Tx is guard withheld. In theory
all cases are deterministic; the conservative Rx default is a safety net.

When a node wakes early (guard applied) and the MAC then decides Tx, the
TDMA Machine holds the transmission until its fire instant, as for any Tx:
the packet starts on air at the nominal slot start regardless of when the
node woke (see Tx Start).

- `DIRECTION_CELL_SKIP`: queries `MAC_GetCellEligibilityMask_Uplink()` or
  `MAC_GetCellEligibilityMask_Downlink()` (selected by `phase->type`) +
  `MAC_GetHopCount()` + `cell_index`. If the cell is ineligible → Skip → no
  guard. If eligible, the hop-count residue formula determines Tx vs Rx:
  `cell_index % 3 == hop_count % 3` → Tx → no guard; otherwise Rx → guard.
  Both `hop_count` and `cell_index` are stable at prediction time, so the
  prediction is deterministic. The MAC owns the mask (CM0+-internal, not
  shared memory); the TDMA Machine reads it via accessor functions.
- `DIRECTION_MAC_CELL`: prediction depends on the phase type:
  - `Mesh_Beacon`: prediction depends on node class. C3: always Tx →
    no guard. C1: always Rx → guard. C2: `BeaconTxBudget > 0` → next cell
    is Tx → no guard; `== 0` → Rx → guard. K is conserved across phase
    occurrences (not re-selected at each entry), so the prediction is
    deterministic even across phase boundaries. On first boot before any
    beacon is received, budget is 0 → conservative Rx (guard applied).
    When the deferred random Tx gate is implemented, the prediction must
    switch to conservative Rx (guard applied, delay to nominal if Tx)
    because Tx can no longer be predicted from the budget alone.
  - `Sync`: the prediction is **state-aware**, consulting the MAC's Epoch
    Received flag (see Epoch Received). The alarm for the next cell is
    first programmed at the start of the current one, before its packet
    arrives; the TDMA Machine re-decides it when the current Rx ends
    (`TdmaMachine_OnRxEnd`, logged as `WAKE_ADJ`), so the prediction
    reflects the MAC state after the current cell's packet:
    - C1: always Rx in every cell → guard. Deterministic.
    - C3: first `SYNC_TX_BUDGET` cells = Tx → Tx lead, no guard; remaining cells = Skip → Tx lead. Deterministic.
    - C2: the epoch may be received in any cell (Cell 0 for hop-1, Cell 1
      for hop-2, Cell N for hop-(N+1)) depending on the node's depth in the
      relay chain. Before the epoch is received → Rx → guard. After the
      epoch is received (flag set) and `SyncTxBudget > 0` → Tx → Tx lead.
      When `SyncTxBudget == 0` (budget exhausted) → Rx → guard. Because the
      flag and budget are re-read when the current cell's Rx ends, the
      prediction for the next cell is always deterministic.
      Should a Tx cell still be woken a guard early, the TDMA Machine waits
      for the fire instant as for any Tx (`WaitUntilMs`, at most one guard time).
    Node class is a compile-time constant, so the prediction branches at
    compile time — no runtime class check.

`DIRECTION_MAC_PHASE` is currently unused; if re-enabled, its prediction
would read `MAC_GetPhaseTxFlag()` as before.

Prediction inputs are stable across phase boundaries — no boundary
special-casing is needed. The Epoch Received flag is reset at Sync phase
entry (in `MAC_OnSlotOpportunity`), so it is never stale when consulted
for the next cell's prediction. K is conserved across Mesh_Beacon phase
occurrences, so it is never stale either.

A packet always starts on air at its slot's nominal start (see Tx Start): the
Tx side applies no guard, only its Tx lead, which receivers never see. The Rx
guard must therefore absorb bilateral drift — both the local and the peer's
RTC divergence from true time.

**Version 2** (issue #36, ADR-0022) replaces the constant with `ratio × D(t) + latency`, from the drift estimate.
`D(t)` is the clock uncertainty seen from the Sync sender: the Residual times the time since the last Sync, plus 3 sigma of the stamp noise, plus one RTC tick, plus the offset a Sync error under the Correction Threshold leaves in the clock.
The ratio is 3, and the Rx start latency (radio wake, TCXO, PLL) is added after it, being a delay and not an uncertainty.
The guard is never below `SYNC_PARTICIPATE_THRESHOLD_MS` (a packet that is still Tier 1 must be inside the window) and never above `MAX_GUARD_TIME_MS`.
It is the cap while the estimate is not valid, while the clock is not `CLOCK_WARM`, and always on C3, the time reference, which has no estimate and is powered: a long Rx window costs it nothing that matters.
In a phase that is not Sync the sender is a peer with its own error, which the estimate says nothing of: the uncertainty doubles.
`GuardTimeResolver_GetGuardMs(slot_start_ms, sync_link)` gathers the inputs and the pure `GuardPolicy_Ms` / `GuardPolicy_PeerMs` (`guard_policy.h`) compute it.

**Current guard vs maximum guard.**
The *current guard* (`GuardTimeResolver_GetGuardMs()`) is how early this node wakes for an Rx slot, and, in `CLOCK_WARM`, how late after nominal a packet may still start in its window.
The *maximum guard* (`MAX_GUARD_TIME_MS`) is a property of the slot grid: the most any node may wake early, which the gaps are sized for (every gap ≥ 2 × `MAX_GUARD_TIME_MS`), and the cap of the guard.
They are equal while the guard is the cap; the Rx Window end uses the maximum guard for its cap and, while the clock is not `CLOCK_WARM`, for its geometric end.

### Rx Window
The span during which a synced node listens in an Rx slot.
It opens at the early wake (current guard) and closes at the **latest packet start**:

  `latest_start = nominal_start + slot_active_ms + MAX_GUARD_TIME_MS − ToA(expected packet)`

A packet starting later could not end by `slot end + MAX_GUARD_TIME_MS`, the latest instant that never reaches the next slot whatever guard its nodes apply.
The window is closed by the radio's own Rx timer, which stops on preamble detection: a packet whose preamble arrives in time is always received in full, a later one is not received.
A **cap** at `slot end + MAX_GUARD_TIME_MS` aborts any reception still running there (a false preamble detection, or a packet detected within the detection margin but too late to fit).
In `CLOCK_WARM` every packet starts at its nominal start (see Tx Start), so one can only be late by clock error and the window closes at `nominal_start + guard` instead, when that is earlier: an empty window then listens `2 × guard + 262 ms` (the preamble margin the platform adds), where the geometric end made it 1970 ms at a guard of 100 ms.
A CONTENTION slot keeps the geometric end: its packet starts after channel sensing and a backoff, not at nominal.
The trace names the guard used (`g`) and the span handed to the radio (`win`) in `RX_WIN`.
The expected packet is the Sync packet for every slot for now (issue #38).
Preamble rather than header detection is a deliberate choice (issue #39): it is the earliest proof of a packet, and the Sync packet will move to implicit header.
Sync timing does not use the preamble detection time (see SyncStamp).
The radio's symbol timeout is off: noise never closes a window early.
_Avoid_: Rx extension, `slot_active_ms + 2 × guard`

### Tx Start
Every scheduled packet starts on air exactly at its slot's nominal start, whatever the phase, the node class or the hop (ADR-0016).
Receivers therefore expect every packet at the nominal start: no constant is added or subtracted on the receiving side, and the lateness of a Sync packet can only be clock error.
The sender makes it so:

- **Tx lead** (`TX_LEAD_MS`, 20 ms): a Tx slot is woken this early, as an Rx slot is woken one guard early.
  The lead absorbs all variable work before the radio fires: Stop2 exit, slot task, logs, compliance, payload build.
  It is at most `MAX_GUARD_TIME_MS`, so the gaps sized for the guard hold it.
- **Fire instant**: the nominal start minus the **Tx ramp** (`tx_ramp_ms`, the time from `Radio.Send` to the first preamble symbol: radio wake-up, TCXO start-up, PLL lock, PA ramp; a platform constant, 4 ms on the bench, measured as `TX_DONE` start − `SYNC_TX` send).
  The node busy-waits for it (`WaitUntilMs`, to within one RTC tick) and sends.
- **Late fire instant** (`TX_LATE`): the lead was too short.
  A Sync packet is dropped, since receivers would read its lateness as clock error; any other packet is sent late, the slot margin absorbing it.

Both constants are sender-local: a board with a different software path or radio may use different values without affecting any other node.
Skip slots are woken like Tx slots: the look-ahead does not tell them apart, and a slot with no radio action has nothing to time.
At boot, C3's first slot starts one Tx lead after the boot wake.
_Avoid_: `TX_START_DELAY_MS` (the rejected alternative: transmit a fixed delay after the nominal start and have receivers add it to every expected arrival)

### Scanning Rx
How a `CLOCK_COLD` node (C1/C2) listens: continuously on the **discovery channel** (`SCAN_FREQ_HZ`, 868.3 MHz, the frequency the SyncAnchor transmits Sync on), with no timeout, re-armed after every reception.
There is no TDMA alarm chain while cold: with no schedule position, slot-sized windows would only overlap the SyncAnchor's transmissions by chance.
The MCU sleeps (Stop2) and is woken by the radio.
The alarm chain starts on the first Sync packet (bootstrap) and stops whenever the node falls back to `CLOCK_COLD`.
See ADR-0015.
_Avoid_: wide Rx, discovery windows

### TDMA Table
The read-only schedule descriptor for one Frame. An ordered array of Phase structs.
Describes *when* and *what kind* of radio activity is scheduled. Node-agnostic:
it does not encode hop-count rules, contention logic, or sleep decisions.

```c
typedef enum { SLOT_SCHEDULED, SLOT_CONTENTION } SlotKind;
typedef enum {
    DIRECTION_CELL_SKIP,   // MAC-internal CellEligibilityMask (3-bit); MAC returns SLOT_SKIP for ineligible cells — Mesh_Uplink, Cluster_Exchange, Mesh_Downlink
    DIRECTION_MAC_CELL,    // MAC decides Tx/Rx per-cell from internal state; every cell woken — Mesh_Beacon, Sync
    DIRECTION_MAC_PHASE,   // phase_tx_flag (uint8_t); whole-phase Tx or Rx — currently unused (reserved)
} DirectionMode;

struct AnchorSlot {
    uint32_t duration_ms;    // 0 = absent
    SlotKind kind;
    uint32_t bitmap[3];      // [C1, C2, C3]; ignored if kind == CONTENTION
    uint32_t gap_after_ms;   // sleep gap after this slot before the next slot/phase; 0 = none
};

struct Phase {
    PhaseType     type;
    uint8_t       participant_mask;  // bit0=C1, bit1=C2, bit2=C3
                                     // non-participants skip Phase entirely
    DirectionMode direction_mode;    // CELL_SKIP: MAC writes CellEligibilityMask (uplink or downlink formula)
                                     // MAC_CELL: MAC decides per-cell from internal state (Mesh_Beacon K-of-N, Sync three-tier)
                                     // MAC_PHASE: unused (reserved) — phase_tx_flag not written by any phase

    uint8_t       cell_count;        // Cell repetitions in this Phase (0-32)
    uint8_t       slot_count;        // Slots per Cell (1–16)
    uint32_t      slot_active_ms;    // worst-case duration of one Slot exchange

    uint16_t      gap_slots_ms[16];  // network-wide sleep gap after slot i
    AnchorSlot    header;
    AnchorSlot    footer;
};
```

`DIRECTION_CELL_SKIP` applies to `Mesh_Uplink`, `Cluster_Exchange`, and
`Mesh_Downlink` — per-cell eligibility depends on runtime state (hop count, Cell
Permit). The MAC computes `CellEligibilityMask` internally from `hop_count` and
uses it in `MAC_OnSlotOpportunity` to return `SLOT_SKIP` for ineligible cells.
The TDMA Machine wakes for every cell and delegates the skip decision to the
MAC; it does not read the mask to pre-filter cells. For eligible cells, the MAC
assigns Cell Role from the hop-count residue formula: uplink uses the ascending
pattern; downlink uses the mirror descending pattern (see Hop-Count Cell
Eligibility).

`DIRECTION_MAC_CELL` applies to `Mesh_Beacon` and `Sync` — the TDMA Machine
wakes for **every** cell and passes the opportunity to the MAC. The MAC
assigns Cell Role (Tx-role, Rx-role, or Skip) per-cell from internal state.

For `Mesh_Beacon`, at phase entry, the MAC randomly selects K cell indices (K
is a provisioned constant) as Tx-role; all other cells are Rx-role. No bitmap
is written to shared memory. Random K-of-N selection ensures a node can
discover peers at any hop depth — including same-hop-count peers, which would
never be heard under a deterministic mod-3 assignment since both sides would
transmit simultaneously in the same cells.

For `Sync`, the MAC assigns Cell Role per-cell based on the three-tier relay
model (see CT Sync Propagation Model and Sync Algorithm). C3 is Tx in the
first `SYNC_TX_BUDGET` cells, Skip in the rest. C2 is Rx in Cell 0, then Tx
in Cells 1+ if the received epoch error is below
`SYNC_PARTICIPATE_THRESHOLD_MS`, otherwise continues Rx.
C2 Tx is gated by `SyncTxBudget` (see SyncTxBudget): after `SYNC_TX_BUDGET`
transmissions, remaining cells return Rx. C1 is Rx in all cells. The MAC makes
the relay decision after processing Cell 0 reception; subsequent cells follow
from that decision.

`DIRECTION_MAC_PHASE` is currently unused. It was originally designed for
`Sync` (uniform Tx or Rx for the entire phase via `phase_tx_flag`), but
Sync's C2 relay model requires a mid-phase role switch (Rx in Cell 0, Tx in
Cells 1+) which violates the uniform-role contract. Sync has been moved to
`DIRECTION_MAC_CELL`. The `DIRECTION_MAC_PHASE` enum value and `phase_tx_flag`
shared memory field are retained for future use if a phase requiring truly
uniform per-phase direction is introduced.

### Regime
A named way of running the whole network, chosen among a set compiled into every node: its own TDMA Table, MAC strategy and frequency plan.
Every node runs the same Regime; only the C3 decides which one, and a newly booted network starts in the Deployment Regime.
Each Regime exists in both Sync Profiles (`DEV` and `PROD`).
Phase 2 Regimes: **Deployment** (installing nodes in the field), **Low Power** (the default once installed), **Throughput** (a temporary push of uplink data).
_Avoid_: system, mode, network mode, profile, Pairing Regime (Pairing is the MAC's `Paired` step)

### Survey Node
A handheld device with a screen, outside the network, that an installer carries offline in the field to read the link quality (RSSI, SNR, loss) of the placed nodes during the Deployment Regime.
It has its own code; the network only guarantees what it can hear.
_Avoid_: sniffer, tester

### Regime Transition
The network-wide change from one Regime to another, decided by the C3.
The C3 puts the new Regime in its next Sync phase, and every node that receives that phase switches at its end; a node that missed it switches at the next Common Sync Phase.
Nothing else changes with it: routes, queues, duty-cycle credit and clock state carry over.
_Avoid_: mode switch, table switch

### Common Sync Phase
A Sync Phase that every Regime holds at the same instant and with the same structure (cells, slot duration, discovery channel, Sync MAC): the Sync phases of the slowest Regime, one grid per Sync Profile.
Every Regime's Frame starts on one; a faster Regime adds its own Sync phases between them.
_Avoid_: anchor Sync, shared Sync

### Participant Mask
A per-Phase 3-bit field (`participant_mask`) indicating which Node Classes
participate in this Phase. Non-participant classes set no alarms within the Phase
and remain asleep for its entire duration. The TDMA Machine checks this
at Phase entry before programming any alarms.

Example: `Mesh_Uplink` has `participant_mask = 0b110` (C2 + C3 only; C1 sleeps
through the entire Phase).

### TDMA Machine
The traversal engine on CM0+. Consults the TDMA Table via a single accessor.
Never modifies it. Responsible purely for timing mechanics — when to wake and which slot is active.

**TDMA Table responsibilities** (what the table owns):
- Which Phase Types appear and in what order
- Which Node Classes participate in each Phase (`participant_mask`)
- Which `DirectionMode` governs per-cell direction (`direction_mode`)
- Slot active duration and inter-slot gaps (`slot_active_ms`, `gap_slots_ms`)
- Phase Header and Footer Slot definitions
- Cell repetition count (`cell_count`)

**TDMA Machine responsibilities** (what the table does NOT own):
- Advancing the Frame Cursor and computing absolute slot times
- Checking `participant_mask` at Phase entry — if local class is excluded, skip
  Phase entirely and program alarm for next Phase start
- For `DIRECTION_CELL_SKIP` phases: waking for every cell and delegating to
  `MAC_OnSlotOpportunity`, which returns `SLOT_SKIP` for cells whose
  `CellEligibilityMask` bit is clear (see CellEligibilityMask)
- For `DIRECTION_MAC_CELL` phases (Mesh_Beacon, Sync): waking for every cell
  and passing the opportunity to the MAC; the MAC assigns Tx-role, Rx-role,
  or Skip per-cell
- Running the alarm chain only out of `CLOCK_COLD`: C1/C2 start it on the
  first Sync packet (bootstrap) and stop it for Scanning Rx whenever the
  clock drops back to `CLOCK_COLD`; C3 runs it from boot
- Programming Alarm A (RTC Alarm A) for each slot wake-up (alarm-chain sleep model)
- Applying Guard Time on Rx Slots and bounding each Rx Window
- Correcting Frame Epoch on Mesh_Beacon or Sync reception

**Sleep strategy — alarm-chain model:**
Nodes are always in deep sleep between Slots. There is no idle polling or
spin-waiting. After completing Slot i, the TDMA Machine computes:

  `next_alarm = slot_start(i) + slot_active_ms + gap_slots_ms[i] - (next_slot_is_rx() ? guard_ms : 0)`

  `slot_active_ms` is replaced by `header.duration_ms` or `footer.duration_ms`
  when the current slot is an AnchorSlot (see Anchor Slot Boundaries below).
  `gap_slots_ms[i]` is the network-wide sleep gap after slot i; when
  `i = slot_count - 1` it doubles as the inter-cell gap before the next Cell
  begins. The guard-time subtraction applies only when the predicted next
  slot is Rx (see Guard Time).

and programs Alarm A before returning to sleep. On the next wake, it resumes
from the updated Frame Cursor. For long inter-Phase gaps (hours), the TDMA
Machine jumps the cursor directly to the next active Slot for the local Node
Class, using the precomputed `phase_start_offset[]` array to avoid iterating
through intermediate Phases and Cells.

### Anchor Slot Boundaries

The general alarm formula uses the current slot's own duration and the gap
that follows it:

  `next_alarm = current_slot_start + current_slot_duration + gap_after_current - (next_slot_is_rx() ? guard_ms : 0)`

| Current slot | `current_slot_duration` | `gap_after_current` |
|---|---|---|
| Phase Header | `header.duration_ms` | `header.gap_after_ms` |
| Cell slot i | `slot_active_ms` | `gap_slots_ms[i]` |
| Phase Footer | `footer.duration_ms` | `footer.gap_after_ms` |

When `i = slot_count - 1`, `gap_slots_ms[i]` is the inter-cell gap: the gap
before the first slot of the next Cell. When the current Cell is the last Cell
in the Phase, this same gap precedes the Footer (if present) or the next
Phase's Header (if no Footer). The Footer's `gap_after_ms` is the gap before
the next Phase begins.

**Key boundary**: TDMA Table = schedule data (read-only, node-agnostic).
TDMA Machine = timing mechanics (node-aware, alarm-driven).

### MAC State Machine
The per-opportunity decision layer on CM0+. Companion to the TDMA Machine:
where the TDMA Machine determines *when* a radio opportunity occurs, the MAC
State Machine determines *whether* that opportunity is taken and *how*.

**MAC State Machine responsibilities:**
- Maintaining the node's operational state (see states below)
- At each opportunity in `DIRECTION_CELL_SKIP` phases: assigning Cell Role
  from the hop-count residue formula for that phase (uplink ascending
  or downlink mirror — see Hop-Count Cell Eligibility)
- After each beacon update: computing `cell_eligibility_mask` for
  `Mesh_Uplink` (uplink formula) and `cell_eligibility_mask_dl` for
  `Mesh_Downlink` (downlink mirror formula) — stored internally as
  `s_cell_elig_ul` / `s_cell_elig_dl` (see CellEligibilityMask)
- At each `DIRECTION_MAC_CELL` cell opportunity (Mesh_Beacon and Sync):
  assigning Cell Role from MAC-internal state. For `Mesh_Beacon`: K-cell
  schedule selected at phase entry, gated by `BeaconTxBudget`. For `Sync`:
  three-tier relay decision (C2 Rx Cell 0, Tx Cells 1+ if epoch received
  with error below `SYNC_PARTICIPATE_THRESHOLD_MS`, gated by `SyncTxBudget`;
  C3 Tx first `SYNC_TX_BUDGET` cells, Skip rest; C1 Rx all cells)
- Executing contention logic in CONTENTION footer Slots (CSMA + backoff)
- Selecting packet type (data vs join request) in `Cluster_Exchange` footer
- Decoding the `Cluster_Exchange` header and writing the Cell Permit
- Accepting state transition commands from CM4

CM4 can influence the MAC State Machine by writing state transition commands
to shared memory. CM0+ never waits for CM4 — it reads pre-computed state at
opportunity boundaries.

**States:**

| State | Applies to | Condition | Radio behaviour |
|---|---|---|---|
| `Scanning` | C1, C2 | No sync established (`ClockState = CLOCK_COLD` or `CLOCK_ACQUIRING`). Boot default for C1/C2. | No TX. `CLOCK_COLD`: Scanning Rx (continuous, discovery channel, no alarm chain). `CLOCK_ACQUIRING`: alarm chain running, Rx Window in every slot. |
| `Synchronized` | C1, C2 | Two consecutive Sync packets with SyncStamp error below `SYNC_PARTICIPATE_THRESHOLD_MS` after initial RTC set (`ClockState = CLOCK_WARM`). Seeking peers — discovery behaviour is implied, not a separate state. | Listens on known frame boundaries. Attempts cluster join or mesh peer exchange. No data TX. |
| `Active` | C3 | Boot default for C3. C3 is the SyncAnchor — it requires no sync acquisition. Immediately operational: transmitting Sync packets, listening for peer connections. | Follows TDMA Table (C3 slot pattern). Originates Sync packets. Accepts mesh and cluster connections. |
| `Paired` | C1, C2, C3 | C1/C2: connected to cluster master or mesh backbone. C3: at least one node (C2 or C1) is connected. | Full TDMA schedule: TX from phase-specific buffers when non-empty. |

**C3 sleep mode:** C3 does not enter Stop2. Its default operating mode between
slots is active (Run or Sleep, not deep Stop2). The TDMA Machine still programs
RTC alarms and advances the Frame Cursor identically — this is purely an LPM
configuration difference. With node class as a compile-time constant, the sleep
call at slot completion resolves to the correct mode at compile time.

**Backward transitions:**
- `Paired → Synchronized` (C1/C2): node loses cluster master or mesh partners
  but retains sync. Re-enters peer-seeking behaviour.
- `Paired → Active` (C3): all connected nodes disconnect. C3 resumes listening
  for new connections. Sync is never lost — C3 is the source.
- C1/C2 any state → `Scanning`: sync lost (RTC drift exceeds threshold, a bad
  packet while `CLOCK_ACQUIRING`, or too many consecutive Sync packets missed).
  Full three-packet acquisition restarts.
- C3 never enters `Scanning`.

### Frame Cursor
The TDMA Machine's live position within the TDMA Table.
Advances each Slot; resets to zero at Frame end.

```c
struct FrameCursor {
    uint8_t  phase_index;
    uint16_t cell_index;
    uint8_t  slot_index;
};
```

### Sync Phase Epoch
The absolute timestamp of the **start of the current Sync Phase occurrence**
(not the frame start). Carried in `SyncPayload_t` and used by receivers to set
their RTC and anchor the binary-ms clock-error computation.

`Frame Epoch` (frame start) is an internal TDMA Machine concept only, derived
when needed as:
`frame_epoch_ms = sync_phase_epoch_ms − TdmaTable_PhaseStartOffset_ms(sync_phase_index)`

**Time-format boundary.** BCD calendar fields live in HAL call sites
(`HAL_RTC_SetTime` / `HAL_RTC_GetTime`). All MAC timing arithmetic —
elapsed-ms, expected-offset, clock-error — operates in binary milliseconds via
`GetTimerTicks()` (`CM0PLUS/Core/Src/timer_if.c`). The boundary is the HAL
call site in `subghz_phy_task.c`.

**Day domain.** The RTC reads ms since midnight and wraps at `MS_PER_DAY`, so
every time the TDMA Machine, the MAC and the CM0+ platform keep is in
[0, `MS_PER_DAY`): sums are reduced modulo a day (`DayMs_Add`) and differences
are signed and taken the short way round the day (`DayMs_Diff`, valid under
12 h), so a slot, a Sync phase, a Tx fire instant or a silence timeout can
span midnight like any other instant (`day_ms.h`, issue #54). Plain `uint32_t`
subtraction is wrong here: 2³² is not a multiple of a day.
The one exception is the Monotonic Clock below.

### Monotonic Clock
A CM0+ millisecond counter that follows real time and never goes back: not at midnight, and not when a Sync correction writes the RTC (`TIMER_IF_GetMonotonicMs`, `DayMsClock_t` in `day_ms.h`, issue #54).
It is derived from the RTC, the only clock running in Stop2: every read adds the step since the previous one, and the platform brackets each RTC write (`TIMER_IF_RtcWriteBegin` / `End`) so the jump is never counted.
A calendar set (Packet 1, Tier 3) is excluded at once; a SHIFTR (Tier 2, and the sub-second part of a set) is excluded when the hardware actually applies it (SHPF clear), which may be after the write returns.

| Used by | For |
|---|---|
| UTIL_TIMER (`timer_if.c`: context, elapsed time, `StartTimer`) | software timers: Rx cap, radio Tx timeout |
| `HAL_GetTick` (`sys_app.c`) | HAL timeouts, `HAL_Delay` |
| Compliance Engine (through `HAL_GetTick`) | duty-cycle credit refill |

It is a duration base only.
Anything that is a time of day (slots, alarms, SyncStamp, Sync epoch, logs) reads the RTC time of day (`UTIL_TIMER_GetCurrentTime`), never this counter.
_Avoid_: using `HAL_GetTick` or `TIMER_IF_GetMonotonicMs` as a time of day

---

## Frame Cursor Synchronisation

### SyncAnchor
The node designated to originate the sync packet in slot 0 of every `Sync` Phase.
Sole time authority for the network. All other nodes derive their wall-clock time
from the SyncAnchor transitively through relay hops.

### SyncPayload
The on-wire payload of a sync packet. Carries the Sync Phase Epoch as a single
binary millisecond field plus BCD date, `sync_phase_index`, and `sync_cell_index`.
C3 generates the epoch; C2 relays C3's received epoch verbatim (never self-generates).

```c
typedef struct __attribute__((packed)) {
    uint8_t  packet_type_id;               /* Packet type discriminator */
    uint32_t ms_since_midnight_sync_phase; /* Sync phase start time, binary ms,
                                              range 0–86,399,999. C3: the phase's
                                              nominal start from the schedule (cell 0
                                              on air), not an RTC reading; the date
                                              is the date of that epoch (read from the
                                              RTC at phase entry, moved by a day when
                                              the read is across midnight from it).
                                              Receiver: target_ms =
                                              this + sync_cell_index × per_cell_ms;
                                              decompose → H:M:S for HAL_RTC_SetTime. */
    uint8_t  day;                          /* BCD 01–31 */
    uint8_t  month;                        /* BCD 01–12 */
    uint8_t  year;                         /* BCD 00–99, years since 2000. Must be kept for leap year*/
    uint8_t  sync_phase_index;             /* TDMA Table phase index of this Sync Phase.
                                              Receiver uses it to look up per_cell_ms
                                              and bootstrap the FrameCursor correctly
                                              in multi-Sync-phase frames. */
    uint8_t  sync_cell_index;              /* Cell within this Sync Phase (0-based).
                                              Receiver: target_ms = ms_since_midnight_sync_phase
                                              + sync_cell_index × per_cell_ms.
                                              One slot per cell for Sync*/
} SyncPayload_t;                           /* 10 bytes */
```

### sync_cell_index
Index of the Cell within the Sync Phase in which this packet was transmitted (renamed
from `sync_slot_index`). Under concurrent transmission, all C2/C3 nodes in the same
Cell stamp the same `sync_cell_index`. The receiver uses this field together with
`ms_since_midnight_sync_phase` to compute the expected packet start (SyncStamp):

```
expected_arrival_ms = ms_since_midnight_sync_phase + sync_cell_index × per_cell_ms
```

The sum is taken modulo `MS_PER_DAY` (see Day domain): a cell after midnight in
a phase that started before it is expected at its time of day.

**Date across midnight (issue #28).**
The date in the payload is the date of the epoch, the Sync phase's nominal start.
A receiver sets its RTC to the target time (epoch + `sync_cell_index` × `per_cell_ms` + the age carry) with that date, and moves the date by one day when the sum, taken before the modulo, reaches `MS_PER_DAY`: the cell is on the day after the epoch.
C3 stamps the epoch's date: the RTC is read at phase entry, a Tx lead before the cell on air, so when that read and the epoch are on either side of midnight the date it read is moved by one day (forward for an epoch just after midnight, back for a phase entered mid-way after midnight).
The calendar step is software (`date_bcd.h`, BCD, 2000-2099, host tested), not `HAL_RTC_DST_Add1Hour`: the HAL call cannot be tested off target, and the MAC no longer sees a target time above a day, since it is reduced to the day domain.

### CT Sync Propagation Model
The current implementation uses a **three-tier relay model** with partial
Concurrent Transmission: C2 nodes at the same hop count transmit the same sync
packet concurrently in the same Cell, benefiting from capture effect at
receivers. C2 nodes at different hop counts relay sequentially across Cells
within the Sync Phase. C3 originates the epoch; C2 never self-generates an
epoch, it only forwards what it receives from C3 (or from an upstream C2 relay).

**Scientific basis.** The viability of Concurrent Transmission in LoRa is
established by Liao et al., *"Multi-Hop LoRa Networks Enabled by Concurrent
Transmission"*, IEEE Access, vol. 5, pp. 21430-21446, 2017
([doi:10.1109/ACCESS.2017.2795403](https://ieeexplore.ieee.org/document/8048465)).
The paper identifies three physical-layer mechanisms that make CT work in LoRa:
capture effect (~3 dB power offset sufficient for receiver lock), time-domain
energy spreading (maximum benefit at 0.5·T_S symbol-time offset between
concurrent transmitters), and a preamble-locking failure ceiling at ~3·T_S
(where the receiver locks onto a weak leading packet's preamble and fails SFD
detection when a stronger packet arrives later).

**Symbol duration and derived thresholds.** The LoRa symbol duration is:

```
T_S = 2^SF / BW
```

At the confirmed sync modulation parameters (SF12, BW125 = 125 kHz):

```
T_S = 2^12 / 125000 = 4096 / 125000 ≈ 32.77 ms
```

All sync timing thresholds are derived from this symbol duration:

| Threshold | Derivation | Value | Role |
|---|---|---|---|
| `SYNC_PARTICIPATE_THRESHOLD_MS` | 0.25·T_S | 8 ms | Tier 1 participation boundary; acquisition consecutive-good threshold |
| `SYNC_LOCK_THRESHOLD_MS` | 0.5·T_S | 16 ms | CT drift budget for pure CT model (deferred) |
| Preamble-locking ceiling | 3·T_S | ~100 ms | Hard CT failure mode; coincides with `MAX_GUARD_TIME_MS` |

If the sync modulation parameters change, all three values must be recomputed
from the new T_S. They are provisioned constants, not hardcoded magic numbers.

`SYNC_CORRECT_THRESHOLD_MS` (1 ms, ADR-0022) is not derived from T_S: it is the Correction Threshold, set from the noise of the stamp (see Correction Threshold) and the concurrent-transmission budget.

**Pure CT model (deferred):** the design in which every eligible C2 transmits
the sync packet simultaneously from Cell 0 using its locally-stored epoch from
the previous Sync Phase — collapsing sync propagation to O(1) regardless of
network depth. This requires all C2 clocks to stay within 16 ms drift tolerance
(0.5·T_S at SF12/BW125) between Sync Phases, providing ~6× margin against the
preamble-locking failure ceiling (~100 ms, 3·T_S). Significant problems must be
solved before this model can be implemented; see
`ArcLoRaM_CT_Sync_Design.md` for the full design and open empirical items.

**CT beyond Sync (design intent, not implemented):** CT is not only a Sync airtime saving, so its value holds even if Sync becomes rare (issue #49).
It is also the intended path for `Mesh_Downlink` (a C3 command reaching every depth in one network-wide pulse instead of hop by hop) and for low-latency uplink (early-warning alerts, see ADR-0010, travelling upstream without waiting for the hop-staggered `Mesh_Uplink` cells).
The alignment it requires therefore stays a protocol requirement whatever a node synchronises from: two nodes transmitting concurrently must stay within 16 ms (0.5·T_S) of each other.
When each node tracks its own upstream peer, the offset between two concurrent transmitters is the sum of the per-hop errors along both paths to their common ancestor, so the per-hop error budget is `2 × depth × per_hop_error < 16 ms`.
The CT failure boundary is validated on hardware in issue #46 (H3/H4).

**Sync Phase structure**: N identical Cells × 1 Slot. `participant_mask` for
Sync: C2 + C3 transmit, C1 RX-only. Under the three-tier model, Sync Phase
airtime scales with network depth (each hop relays in a subsequent Cell).

Under the deferred pure CT model, airtime would be `1 × packet_length`
regardless of network depth.

**C2 sync relay model**: C2 always enters Rx Radio state in Cell 0 of every
Sync Phase occurrence, and continues until it actually receives the C3's (or
upstream C2's) epoch, then relays it in Cells 1+ if the measured error is below
`SYNC_PARTICIPATE_THRESHOLD_MS`, up to `SYNC_TX_BUDGET` transmissions per
occurrence (see SyncTxBudget). C2 never self-generates an epoch; it only
forwards what it receives. Three-tier per-occurrence dispatch (see Sync
Algorithm) replaces the former audit/participate cycle alternation (which
was part of the pure CT model and is also deferred).

**Variable TX power**: C2 nodes use differentiated TX power across participants
to provide power offset at receivers near the equidistant-failure zone.
Policy (randomised per cycle, hop-count-based, or deterministic per node) is
deferred pending empirical validation.

**Receiver-side algorithm unchanged**: SyncStamp capture,
`expected_offset_ms` computation, three-packet acquisition, and
`SYNC_LOCK_THRESHOLD_MS` validation all remain as documented in the Sync
Algorithm section.

### SyncStamp
The start on air of a received Sync packet, in the receiver's RTC **in day ticks** (1/4096 s, 244 us; issue #82): `RxDone - ToA - RX_DONE_LATENCY`.
The RxDone time is the RTC read in ticks at entry of the radio IRQ, taken first thing in `SUBGHZ_Radio_IRQHandler` (`SubGhzPhyTask_OnRadioIrq`), before HAL dispatch.
`ToA` is the time on air in microseconds from the LoRa formula with the modem configuration (`LoraToa_Us`, 991 232 us for a Sync packet), which must match the sender's; the driver's whole-ms figure (991) left a constant 0.2 ms in the stamp.
`RX_DONE_LATENCY` is the delay from the last symbol to the IRQ stamp, well under a millisecond (0 until measured with a common time reference, #83).
It is what `MAC_OnSyncPacketReceivedTicks` receives; `MAC_OnSyncPacketReceived` takes whole ms and converts them to the first tick not before.
The **Sync error** is the stamp minus the expected arrival, taken in microseconds (`SyncStamp_ErrorUs`, the expected arrival being an exact schedule ms) and judged in microseconds for every tier; the trace gives it in ms (`err`, rounded) and in us (`erru`).
The `IRQ_PREAMBLE_DETECTED` and `IRQ_HEADER_VALID` times are logged alongside (`pre`, `hdr`) as diagnostics only.
The preamble detection time is not a usable timing reference: at SF12/BW125 it lands one symbol (32.8 ms) early or late from packet to packet, while `RxDone - ToA` and `HEADER_VALID` track the sender's TX start to within ~3 ms (bench, 2026-09-26).
`HEADER_VALID` does not exist once the Sync packet uses implicit header (issue #39); RxDone does.
Never persisted.
_Avoid_: PreambleStamp

### Epoch Received
A MAC-internal boolean flag (`s_epoch_received_this_phase`) indicating whether
the MAC has received a valid sync epoch during the current Sync Phase
occurrence — set when `MAC_OnSyncPacketReceived` processes a Tier 1 packet
(error below `SYNC_PARTICIPATE_THRESHOLD_MS`). The epoch can arrive in any
cell depending on the node's depth in the relay chain (Cell 0 for hop-1
C2, Cell 1 for hop-2, Cell N for hop-(N+1)). Reset to `false` at Sync phase
entry (when `MAC_OnSlotOpportunity` detects a phase index change). All node
classes track this flag (C1 receives epochs too); however, only C2 uses it
for a Tx decision — C1 never relays, C3 originates. Exposed to the TDMA
Machine via `MAC_GetEpochReceivedThisPhase()` for state-aware guard-time
look-ahead (see Guard Time).

### ClockState
Three-value enum tracking RTC synchronisation quality.

| Value | Meaning |
|---|---|
| `CLOCK_COLD` | No Sync packet received. Boot default. MAC State Machine is in `Scanning`. No alarm chain: Scanning Rx. |
| `CLOCK_ACQUIRING` | At least one Sync packet processed. RTC partially calibrated; sub-second precision not yet confirmed. |
| `CLOCK_WARM` | Two consecutive Sync packets with SyncStamp error below `SYNC_PARTICIPATE_THRESHOLD_MS = 8ms` (after initial RTC set on Packet 1). Node is fully Synchronized. |

### Sync Silence Timeout
A wall-clock duration (`SYNC_SILENCE_TIMEOUT_MS`, provisioned at 15 minutes) after
which a node degrades `ClockState` to `CLOCK_COLD` when no Sync packet of any tier
has been received.
Measured in wall-clock time, not slot counts, so it is decoupled from TDMA table
structure.
Fires from both `CLOCK_WARM` and `CLOCK_ACQUIRING`.
Any received Sync packet (Tier 1, 2, or 3) resets the timer.
This is the "no evidence either way" degradation path, distinct from Tier 3 ("evidence
the clock is wrong"), which remains immediate.
See ADR-0013.

### Rate Offset
How much faster (positive) or slower the local RTC runs than its Sync sender's clock, in ppb, with no calibration applied.
It belongs to the crystal, not to the lock: it is kept across `CLOCK_COLD`.
_Avoid_: drift (it also means the accumulated error), skew

### Drift Estimator
The module that estimates the Rate Offset from the Sync errors, with every correction the node made to its own clock removed.
It reports a **Residual** and is valid only after a minimum baseline of Sync packets.
Phase 1 (#34) is a least squares fit; the Kalman estimator of Phase 2 (#43) replaces it behind the same interface.

### Smooth Calibration
The RTC's own rate correction (`RTC_CALR`): pulses added or masked over a 32 s cycle, in discrete **Calibration Steps** of about 0.954 ppm, between -487.1 and +488.5 ppm.
It is the only correction that acts on the clock's rate; the Tier 2 shift and the RTC set act on its phase.
C1 and C2 calibrate against their Sync sender; C3, the time reference, does not.
_Avoid_: CALR (the register), trimming

### Production Clock
The SiTime SiT1552 32.768 kHz MEMS TCXO of the production node, grade E: +-5 ppm over -40 to +85 C, +-1 ppm ageing the first year.
It is temperature compensated inside the device, so it has no parabola: its error is bounded, not a function of temperature the node must model.
Every statement of this project that does not name its clock means this one.
_Avoid_: the crystal, the RTC clock (without saying which)

### NUCLEO Clock
The 32.768 kHz crystal of the NUCLEO development board (NDK NX3215SA): +-20 ppm at 25 C, a parabola of -0.04 ppm/C^2 around a turnover between 20 and 30 C, so up to about 200 ppm slow at -40 C and 5.6 ppm/C of rate change in the cold.
The bench boards carry it, and it is the clock of every bench result until the production hardware is on the bench: a result, a record or an ADR that comes from it says "NUCLEO".
Its figures do not transfer to the Production Clock (a calibration residual, a drift rate, a temperature behaviour).

### Sync Profile
The compile-time choice of how often the network synchronises: `DEV` (the default; a Sync phase every 200 s, one packet per node per phase, inside the duty-cycle budget, for work that assumes synchronised clocks), `PROD` (the product period derived from the drift bound, provisional until the Production Clock is measured, #112) and `BRINGUP` (the former 30 s schedule, host tests only).
It also fixes the Sync Silence Timeout. A trace names it in the `BOOT` line (`sync=`).
It is orthogonal to the Regime: every Regime has a `DEV` and a `PROD` variant.
An option, the **boot burst** (`SYNC_BOOT_BURST`), makes the first Sync phase after the C3 boots send several packets so that boards booted with it lock at once.
_Avoid_: Sync mode, Sync rate

### Baseline Calibration
The Smooth Calibration setting of a board measured once against an ideal clock and loaded at boot: the common absolute footing of every node.
Until a factory value exists, the setting read from the register at boot stands for it.
_Avoid_: static calibration, factory trim

### Calibration Trim
What the runtime estimate adds to the Baseline Calibration, applied minus baseline: it follows what changes after the baseline and is bounded to a window that belongs to the clock type.
It does not follow a neighbour's clock.
_Avoid_: runtime calibration (it is the baseline plus the trim that is applied)

### Residual
The rate the clock still gains with the Smooth Calibration applied: the Rate Offset minus the correction applied.
It is what the guard and the Sync period must absorb.

### Correction Threshold
The Sync error (`SYNC_CORRECT_THRESHOLD_MS`, 1 ms) below which a `CLOCK_WARM` node leaves its clock alone, so as not to chase the noise of the stamp, and from which it corrects the phase with a shift taken from the error in microseconds.
It is above the noise floor of the stamp (3 sigma of 106 us and a constant of at most 0.25 ms, about 0.57 ms, NUCLEO) and far under the concurrent-transmission budget.
The offset it leaves in the clock, up to the threshold, is a term of the Rx guard.
It is not the participation threshold (8 ms, relay eligibility) nor the resync threshold (100 ms, re-anchor).
_Avoid_: Tier 2 threshold

### Sync Algorithm — summary

**Acquisition (CLOCK_COLD → CLOCK_WARM):** requires 1 RTC-set packet + 2 consecutive
good packets (error below `SYNC_PARTICIPATE_THRESHOLD_MS = 8ms`).

**Packet 1 (`CLOCK_COLD` → `CLOCK_ACQUIRING`):** parse `SyncPayload` →
`target_ms = ms_since_midnight_sync_phase + sync_cell_index × per_cell_ms` →
`age = rtc_now − SyncStamp` (the MAC runs at RxDone, about one airtime after the packet start; ignored above `SYNC_STAMP_MAX_AGE_MS`) →
`rtc_set(target_ms + age)` (`HAL_RTC_SetTime` + `SetDate` + SHIFTR sub-second), so the new RTC domain reads `target_ms` at the stamp instant →
call `sync_bootstrapped` hook to re-anchor TDMA cursor and start the alarm chain → `s_sync_consecutive = 0`,
`ClockState = CLOCK_ACQUIRING`.
Without the age carry, the new domain would lag the sender by the airtime while every later stamp-to-expected comparison still read zero error.

**Packets 2+ (`CLOCK_ACQUIRING`):** take the `SyncStamp` →
`expected_arrival = ms_since_midnight_sync_phase + sync_cell_index × per_cell_ms` →
`clock_error = |SyncStamp − expected_arrival|`.
Packet 1 put the RTC on the sender's timeline, so the packet is checked against its own epoch and may come from any later Sync Phase occurrence.
If `clock_error < SYNC_PARTICIPATE_THRESHOLD_MS (8ms)`: `s_sync_consecutive++`;
if `s_sync_consecutive ≥ 2`: `ClockState = CLOCK_WARM`, MAC transitions to `Synchronized`.
If `clock_error ≥ 8ms` the packet disagrees with Packet 1, and either may be the wrong one: `ClockState = CLOCK_COLD` (`why=acq_bad`), the alarm chain stops for Scanning Rx, and the next packet is a new Packet 1.

**CLOCK_WARM ongoing check (three-tier per Sync Phase occurrence):**
C2 always receives Cell 0 to measure error against the incoming epoch:
- **Tier 1 — error < 8ms (0.25·T_S):** relay in Cells 1+ (store `ms_since_midnight_sync_phase` for TX), up to `SYNC_TX_BUDGET` transmissions per occurrence (see SyncTxBudget).
  From `SYNC_CORRECT_THRESHOLD_MS` (1 ms) the phase is also corrected, as below; under it the clock is left alone (see Correction Threshold).
- **Tier 2 — 8ms ≤ error < 100ms (MAX_GUARD_TIME_MS):** SSR-only correction via `HAL_RTCEx_SetSynchroShift`
  (shift_ticks = the error in µs to the nearest RTC tick); receive only this occurrence.
- **Tier 3 — error ≥ 100ms (MAX_GUARD_TIME_MS):** full `rtc_set` re-anchor with the same age carry as Packet 1; `ClockState = CLOCK_COLD`; the alarm chain stops for Scanning Rx.

**Cursor suspect:** when the TDMA Machine wakes more than 1.5 × `slot_active_ms` away from the programmed wake, the FrameCursor is untrusted.
It calls `MAC_OnCursorSuspect()`: C1/C2 drop to `CLOCK_COLD` / `Scanning` without writing the RTC (the next Sync packet re-anchors through Packet 1), and the TDMA Machine stops its alarm chain for Scanning Rx.
C3 ignores it: its TDMA Machine resumes the alarm chain from the actual wake time, so the next alarm is never in the past.

**Rejected Sync packet:** a `SyncPayload` whose `sync_phase_index` is not a Sync phase is dropped before it touches the RTC, the ClockState or the silence timer (corrupt or foreign packet).
It could not anchor the FrameCursor, so accepting it would leave a node out of `CLOCK_COLD` with no alarm chain.

Constants: `SYNC_PARTICIPATE_THRESHOLD_MS = 8` (0.25·T_S at SF12/BW125) ·
`SYNC_RESYNC_THRESHOLD_MS = MAX_GUARD_TIME_MS = 100` (3·T_S at SF12/BW125).
Both are derived from the symbol duration T_S = 2^SF / BW (see CT Sync
Propagation Model for the full derivation). If the sync modulation parameters
change, both values must be recomputed.

`SYNC_LOCK_THRESHOLD_MS = 16` (0.5·T_S) is the CT drift budget for the deferred
pure CT model. It is defined in `mac_state_machine.h` but is not used by the
current three-tier relay implementation, which uses `SYNC_PARTICIPATE_THRESHOLD_MS`
for both acquisition confirmation and the `SYNC_LOCKED` signal to CM4. When the
pure CT model is implemented, `SYNC_LOCK_THRESHOLD_MS` will govern the
acquisition threshold instead.

The literal value `8` ms is not a universal constant — it derives from the
symbol duration T_S at the chosen modulation parameters (SF12, BW125):
`SYNC_PARTICIPATE_THRESHOLD_MS = 0.25 × T_S = 0.25 × 2^12 / 125000 ≈ 8.19 ms`,
rounded to 8 ms. If modulation changes, this value must be recomputed from the
new T_S. It is a provisioned constant, not hardcoded.

---

## Packet Assembly Model

CM4 owns payloads and routing state. CM0+ owns packet assembly.

At TX time, CM0+ pulls a payload from the appropriate phase-specific TX queue and
wraps it with routing headers drawn from CM4's current **Routing State** — the
best peer reachable at this moment. The routing decision is deferred to the last
possible moment (TX slot boundary), not made when the payload was enqueued.

This guarantees that dynamic topology changes are absorbed automatically: if a
peer becomes unreachable between enqueue and TX, CM0+ reads the updated Routing
State and targets a different peer. Pre-assembled packets with embedded
destinations would carry stale routing information and require CM4 to
reassemble the entire queue on every topology change — a dead end at scale.

### Routing State
Shared memory structure written by CM4, read by CM0+ at packet assembly time.
Contains per-peer reachability and route cost information. CM4 updates it in
response to received payloads, `TX_NO_ACK` events, and topology signals.
CM0+ consults it each time it assembles a packet for transmission.

### TX_NO_ACK Payload
When a TX slot completes without an ACK, CM0+ fires `TX_NO_ACK` via the
Application Signal Channel with a payload identifying: the destination peer that
did not respond, and the phase context. CM4 uses this to increment the peer's
failure counter, eventually declare it unreachable, and update the Routing State.
CM0+ uses it internally within the MAC State Machine for contention strategy
(backoff timing, retry decisions in CONTENTION slots).

---

## Inter-Core Signaling

### Application Signal Channel
A single MbMux `NOTIF_ACK` feature (`FEAT_INFO_APP_ID`) used exclusively by
CM0+ to signal CM4 of asynchronous events. CM0+ fires a notification; the IPCC
interrupt wakes CM4 from Stop2; CM4 processes the event and sends the ACK.

Three signal types are multiplexed on this one channel via `MsgId`:

| Signal | Fired when | Payload |
|---|---|---|
| `RX_READY` | CM0+ has written a new packet to a phase-specific RX buffer. CM4 should read and process it. | Phase type identifying which RX buffer to read. |
| `TX_NO_ACK` | A TX slot completed without receiving an ACK. | Destination peer ID + phase context. CM4 updates Routing State; CM0+ uses internally for contention strategy. |
| `SYNC_LOCKED` | Third Sync packet received with SyncStamp error below `SYNC_PARTICIPATE_THRESHOLD_MS`. `ClockState` → `CLOCK_WARM`. | None — signal alone is sufficient. |
| `ACK_RECEIVED` | A TX slot completed with a successful ACK. CM4 must dequeue the delivered payload. | Queue entry identifier (e.g. sequence number assigned by CM4 at enqueue time). CM4 locates and removes the entry. |
| `RX_TIMEOUT` | A scheduled RX slot expired with no packet received. CM4 uses this to track RX-side Packet Error Rate. | Phase type identifying which link (mesh vs cluster) the missed slot belongs to. |
| `SYNC_LOST` | `ClockState` degraded from `CLOCK_WARM` to `CLOCK_ACQUIRING` or `CLOCK_COLD`. CM4 records the event for the Frame Cursor drift metric. | None — signal alone is sufficient. |

### Application Signal Channel — Timing Invariant
The IPCC channel latch stays set until CM4 ACKs. A second `MBMUX_NotificationSnd`
before the ACK returns `-1` and silently drops the event. No re-fire mechanism is
implemented, because the scenario cannot occur:

CM0+ follows the alarm-chain model — it wakes for exactly one slot, performs one
radio action, programs the next alarm, and returns to sleep. At most one signal
fires per CM0+ wake. The minimum inter-slot gap (`gap_after_ms` minimum: 20–30 ms)
gives CM4 a window that exceeds all CM4 processing paths by an order of magnitude.
The IPCC channel is always free before CM0+ wakes for the next slot.

---

## Inter-Core Buffers

Phase-specific shared memory structures. TX Queues are written by CM4 and pulled
by CM0+ at the corresponding Phase. RX Buffers are written by CM0+ on packet
reception and read by CM4 for routing and protocol logic.

### TX Queue Priority Tiers
Three-tier priority used in all priority-bearing TX Queues. CM4 inserts packets
at the appropriate tier; CM0+ always pulls from the highest non-empty tier.

| Tier | Use |
|---|---|
| `HIGH` | Management frames, join requests, urgent alerts |
| `NORMAL` | Regular sensor data and relay payloads |
| `LOW` | Bulk or aggregated data, non-urgent telemetry |

**Overflow policy:** when a tier is full and CM4 enqueues a new payload:
- `HIGH` tier — **drop newest** (reject incoming payload). Management frames
  already queued are more important than a duplicate or competing new one.
- `NORMAL` and `LOW` tiers — **drop oldest** (evict head to make room). Fresh
  sensor data is more relevant than stale readings already waiting to be sent.
- Stalling (blocking CM4 until space opens) is never permitted — CM4 must
  return to Stop2 promptly.

### MeshUplinkQueue
TX Queue for `Mesh_Uplink` Phase. Multi-packet FIFO with three-tier priority
(`HIGH` / `NORMAL` / `LOW`). Written by CM4 (own sensor data or relayed cluster
payloads). Holds **payloads only** — routing headers are added by CM0+ at TX
time from the Routing State (see Packet Assembly Model). Pulled by CM0+ when a
Mesh_Uplink TX slot is active. C2/C3 only.

### MeshUplinkBuffer
RX Buffer for `Mesh_Uplink` Phase. Written by CM0+ on packet reception. Read by
CM4 for routing decisions (e.g. relay to cluster, consume locally). C2/C3 only.

### MeshDownlinkQueue
TX Queue for `Mesh_Downlink` Phase. Simple FIFO, no priority. Written by CM4.
Pulled by CM0+ when a `Mesh_Downlink` downlink-relay cell is active (cell where
`cell_index % 3 == (hop_count + 2) % 3`) and data is available. C2/C3 only. Packets carry a type field; CM4 inspects it at receive
time to dispatch: apply locally (config update), relay down the mesh
(re-enqueue to `MeshDownlinkQueue` — **once only per packet**), or distribute
to cluster (enqueue to `ClusterQueue`). Detailed downlink packet structure and
dispatch rules are not yet specified.

**C3 multi-queue strategy:** to improve the probability that all intended
recipients receive a downlink payload, C3 enqueues the same payload multiple
times into `MeshDownlinkQueue`. Each C2 relay node de-duplicates by payload
identity (sequence number) and relays **exactly once** regardless of how many
copies it receives. All subsequent copies of the same payload are discarded at
CM4 level. The number of copies queued by C3 is a provisioned constant.

**Priority:** downlink payloads are `HIGH` tier where priority queues are used
(e.g., `ClusterQueue`). `MeshDownlinkQueue` itself is a simple FIFO but C3
inserts downlink payloads ahead of lower-priority outbound traffic.

### MeshDownlinkBuffer
RX Buffer for `Mesh_Downlink` Phase. Written by CM0+ on reception. Read by CM4
for dispatch (local application, mesh relay, or cluster distribution). C2/C3 only.

### DownlinkAck
Application-level acknowledgement produced by the targeted node upon receiving
and processing a downlink payload. Distinct from slot-level ACKs (which are
radio-layer confirmations of individual packet reception).

When a C1 or C2 node processes a downlink payload destined for it, CM4 enqueues
a `DownlinkAck` at `HIGH` priority:
- **C1** → `ClusterQueue` HIGH tier (relayed to gateway via C2's `MeshUplinkQueue`)
- **C2** → `MeshUplinkQueue` HIGH tier (delivered directly to gateway)

The `DownlinkAck` travels back to C3 via the normal uplink path — no dedicated
return channel. C3/gateway correlates it against the original downlink payload
to confirm delivery. Wire format is not yet specified.

### ClusterQueue
TX Queue for `Cluster_Exchange` Phase. Multi-packet FIFO with three-tier priority
(`HIGH` / `NORMAL` / `LOW`). Written by CM4 (sensor data for C1, management
frames for cluster master). Holds **payloads only** — routing headers added by
CM0+ at TX time from the Routing State. Pulled by CM0+ when a Cluster_Exchange
TX slot is active and the Cell Permit grants it. C1, C2, C3.

### ClusterBuffer
RX Buffer for `Cluster_Exchange` Phase. Written by CM0+ on reception. Read by
CM4 for routing decisions and application processing. C1, C2, C3.

### BeaconPayload
Single structured packet slot for `Mesh_Beacon` Phase. Not a queue — CM4
overwrites it each time routing state changes; CM0+ reads it once per Beacon
Phase. Contains `hop_count` (this node's depth in the mesh, used by receivers
to derive their own hop count) and up to 4 routes each with a `node_id` and
`route_cost`. Exact wire structure to be determined. C2/C3 only.

### Hop-Count Cell Eligibility
Within `Mesh_Uplink`, a node's **uplink-transmit** cell satisfies
`cell_index % 3 == hop_count % 3`. This staggers relay traffic: nodes one hop
from C3 use Cell 1, two hops Cell 2, three hops Cell 0, four hops back to Cell 1.
Prevents all relay nodes competing for the same Cell. The node's `hop_count` is
read from the last received `BeaconPayload`.

C3 uses an **effective `hop_count` of 3** for this formula (`3 % 3 = 0`), placing
its uplink-transmit residue at 0 and its relay-receive residue at 1. This avoids
a special case: C3 participates in `Mesh_Uplink` identically to a C2 (it is
wall-powered and never truly sleeps, but will not ACK outside its window).

Each node has two active cell groups per mod-3 cycle (Cell Role in parentheses):
- `cell_index % 3 == hop_count % 3`: **uplink-transmit** (Tx-role) — primary exchange is Tx DATA to upstream
- `cell_index % 3 == (hop_count + 1) % 3`: **relay-receive** (Rx-role) — primary exchange is Rx DATA from downstream
- `cell_index % 3 == (hop_count + 2) % 3`: **sleep** (Skip) — node skips this cell entirely

C3 (effective hop 3) has no upstream, so its uplink-transmit role (residue 0) is
vacant. C3 is active only in relay-receive cells (residue 1: cells 1, 4, 7, …) —
the same cells where hop=1 C2 nodes uplink-transmit — and skips all others.

**`Mesh_Downlink` uses a mirror pattern** — the relay chain flows in reverse (C3
→ C2). A node's sleep residue inverts: it sleeps at its uplink-Tx residue and
is active at the two complementary residues:

- `cell_index % 3 == (hop_count + 2) % 3`: **downlink-relay** (Tx-role) — primary exchange is Tx DATA downward
- `cell_index % 3 == (hop_count + 1) % 3`: **downlink-receive** (Rx-role) — primary exchange is Rx DATA from upstream
- `cell_index % 3 == hop_count % 3`: **sleep** (Skip) — same residue as uplink-transmit

| Node | dl-relay Tx residue | dl-receive Rx residue | sleep |
|---|---|---|---|
| C3 (eff hop=3) | (3+2)%3 = 2 | (3+1)%3 = 1 | 0 |
| C2 hop=1 | (1+2)%3 = 0 | (1+1)%3 = 2 ← matches C3 Tx ✓ | 1 |
| C2 hop=2 | (2+2)%3 = 1 | (2+1)%3 = 0 ← matches hop=1 Tx ✓ | 2 |
| C2 hop=3 | (3+2)%3 = 2 | (3+1)%3 = 1 ← matches hop=2 Tx ✓ | 0 |

A node's downlink-sleep residue is exactly its uplink-Tx residue: the cells in
which uplink data flows up are the cells the same node sleeps through during
downlink propagation.

### CellEligibilityMask
MAC-internal value (CM0+ RAM, not inter-core shared memory) computed and stored
by the MAC State Machine. Used in all `DIRECTION_CELL_SKIP` phases (`Mesh_Uplink`,
`Mesh_Downlink`, `Cluster_Exchange`).
A 3-bit mask: bit N = 1 means the node wakes for cells where `cell_index % 3 == N`.

The MAC computes it from `hop_count`. Two formulas exist — one per phase direction:

**Uplink formula** (`Mesh_Uplink`):
```c
uint8_t uplink_residue  = hop_count % 3;
uint8_t relay_residue   = (hop_count + 1) % 3;
cell_eligibility_mask   = (1 << uplink_residue) | (1 << relay_residue);
// sleep residue = (hop_count + 2) % 3
```

**Downlink mirror formula** (`Mesh_Downlink`):
```c
uint8_t dl_relay_residue   = (hop_count + 2) % 3;
uint8_t dl_receive_residue = (hop_count + 1) % 3;
cell_eligibility_mask_dl   = (1 << dl_relay_residue) | (1 << dl_receive_residue);
// sleep residue = hop_count % 3  (same as uplink-Tx residue)
```

The MAC computes the appropriate mask internally after each beacon update.
The TDMA Machine does not read the mask to skip cells — it wakes for every cell
and calls `MAC_OnSlotOpportunity`, which returns `SLOT_SKIP` for ineligible cells.
For guard-time prediction only, the TDMA Machine queries the mask via
`MAC_GetCellEligibilityMask_Uplink()` / `MAC_GetCellEligibilityMask_Downlink()`:
```c
if ((MAC_GetCellEligibilityMask_Uplink() >> (cell_index % 3)) & 1)
    // eligible — MAC will decide Tx vs Rx from hop-count residue
else
    // ineligible — MAC will return SLOT_SKIP; no guard needed
```

Default value before first beacon: `0x00` (skip all cells). A node without a
valid `hop_count` must not transmit in either mesh phase.

For `Cluster_Exchange`, the Cell Permit serves the equivalent role — it is decoded
from the phase header slot rather than derived from `hop_count`.

### BeaconTxBudget
MAC State Machine counter governing reactive beacon transmission in `Mesh_Beacon`
phases. When a node receives a beacon carrying meaningfully new routing
information, it transmits its own `BeaconPayload` in the next K cells —
wherever they fall in the phase, not necessarily starting from cell 0.

**Trigger** — set to `BEACON_K_TX_CELLS` upon receiving a `Mesh_Beacon` packet
that carries meaningfully new routing information:
- `hop_count` changed relative to current known value, **or**
- `route_cost` crossed a configured threshold (large increase or decrease).
This includes the first-ever beacon reception (no prior value → unconditional
trigger) and any subsequent reception that represents a structural routing change.

**Decrement** — decremented by 1 each time the MAC actually transmits in a
cell. The counter persists across frames — if only one Tx opportunity
remains in the current phase, the second transmission fires in the next
`Mesh_Beacon` phase occurrence. There is no absolute cell-index constraint:
the budget counts down from K regardless of which cell the beacon was
received in.

**No-reset rule** — receiving a near-identical beacon (minor `route_cost`
fluctuation, same or subsequent phase, no structural routing change) does
**not** reset `BeaconTxBudget`. This prevents CT-simultaneous duplicate
receptions from inflating the transmission count.

**Exhaustion** — when `BeaconTxBudget == 0`, the MAC receives in all cells
until the next trigger fires.

**Random Tx gate (deferred)** — a second layer will randomly allow or deny
each Tx opportunity within the budget window to avoid interference between
concurrent nodes. When implemented, the guard-time look-ahead must switch to
conservative Rx (apply guard, delay to nominal if Tx) because Tx can no
longer be predicted deterministically from the budget alone.

`BeaconTxBudget` is MAC-internal state; it is not written to shared memory.
It is exposed to the TDMA Machine via `MAC_GetBeaconTxBudget()` for
guard-time look-ahead (see Guard Time).

**C3 exception:** C3 bypasses `BeaconTxBudget` entirely and transmits in
every `Mesh_Beacon` cell unconditionally. C3 has no upstream to receive
a beacon from, so no trigger can fire; and as the mesh root its beacon is the
anchor all C2 nodes depend on - suppressing it would be a protocol hazard.

### SyncTxBudget
MAC State Machine counter governing sync relay transmission in `Sync` phases
(C2 only). Limits the number of cells in which C2 transmits a relayed sync
packet per Sync Phase occurrence to `SYNC_TX_BUDGET` (default 3). After the
budget is exhausted, C2 returns `SLOT_RX` for the remaining cells in the
occurrence instead of `SLOT_TX`.

**Rationale.** Under the three-tier relay model, C2 transmits in every cell
1+ after receiving a valid epoch. On a phase with many cells, the node stays
radio-active far longer than necessary. After 3 transmissions the entire
reachable network has had multiple reception opportunities. Keeping the
radio on for the remaining cells wastes energy with no benefit.

**Reset** - reset to `SYNC_TX_BUDGET` at each Sync Phase entry (detected via
`s_last_phase_idx` change in `MAC_OnSlotOpportunity`).

**Decrement** - decremented by 1 each time `MAC_OnSlotOpportunity` returns
`SLOT_TX` for a Sync cell 1+. When the counter reaches 0, all remaining Sync
cells in the occurrence return `SLOT_RX`.

**C1** - unaffected. C1 never relays (always Rx in Sync).

**C3** - also governed by `SyncTxBudget`. C3 transmits in the first
`SYNC_TX_BUDGET` cells of each Sync Phase occurrence, then skips the rest.
The counter is reset at phase entry and decremented on each Tx. This gives
downstream C2 nodes multiple reception opportunities across consecutive
cells (benefiting CT capture effect) while limiting C3 radio-on time.

`SyncTxBudget` is MAC-internal state; it is not written to shared memory.
It is exposed to the TDMA Machine via `MAC_GetSyncTxBudget()` for
state-aware guard-time look-ahead (see Guard Time).

### phase_tx_flag
Shared memory value previously written by the MAC State Machine before each
`DIRECTION_MAC_PHASE` phase. A `uint8_t` flag that assigned Cell Role
uniformly to every Cell in the Phase: `1` = Tx-role, `0` = Rx-role.

**Currently unused.** `DIRECTION_MAC_PHASE` is not used by any phase. Sync
was the last consumer and has been moved to `DIRECTION_MAC_CELL` because
C2's mid-phase role switch (Rx Cell 0, Tx Cells 1+) violated the uniform-role
contract. The flag and its shared memory slot are retained for future use
if a phase requiring truly uniform per-phase direction is introduced.

Historical behaviour (for reference): C3 (SyncAnchor) always wrote `1`. C1
always wrote `0`. C2 did not pre-write this flag — C2 participation was
determined reactively per-occurrence based on the three-tier error check on
Cell 0. Under `DIRECTION_MAC_PHASE`, the guard-time look-ahead read this flag
directly, which meant C2 always applied guard (flag = 0) even in Cells 1+
where it might Tx — waking early on an unreliable prediction. The migration
to `DIRECTION_MAC_CELL` replaces this with state-aware prediction using the
Epoch Received flag (see Epoch Received, Guard Time).

---

## Mesh Routing

### Route Entry
Each candidate upstream peer is stored as a triple `(node_id, hop_count, route_cost)`.
`hop_count` is the peer's mesh depth (receiver sets its own to `peer.hop_count + 1`).
`route_cost` is a **cumulative path cost**: each node computes its own as
`parent.route_cost + f(energy, load)`, where `f` is a local function of the node's
remaining energy and current load. The value published in `BeaconPayload` is the
node's accumulated path cost from root; receivers add their own `f(energy, load)` to
derive their own `route_cost` for forwarding. Both `hop_count` and `route_cost` are
strictly increasing along any path from root — this invariant makes the anti-circular
filter reliable. `load` is the node's current TX queue depth (higher backlog = higher
cost, discourages routing through congested nodes). Exact formula for `f` is not yet
specified.

### Route Selection Policy
A C2 selects the route with the **lowest `route_cost`**. On a tie, it selects the
**lowest `hop_count`**. This is evaluated at every `Mesh_Beacon` Phase when CM4
updates the Routing State.

### Route Update
Because `BeaconPayload` carries `node_id`, a C2 can update the `route_cost` of an
already-known peer when a fresher beacon arrives. This allows C2 to detect battery
depletion on its current upstream and switch routes proactively.

### Anti-Circular Route Filter
When a new `BeaconPayload` arrives, C2 discards it if both conditions hold:

```
new.hop_count == current_selected.hop_count + 1
AND
new.route_cost > current_selected.route_cost
```

A beacon satisfying both conditions originates from a node one level downstream
that has a higher cost than the current upstream. Accepting it would create a
routing loop in the event of peer reselection (e.g., the downstream node previously
chose this node as its upstream and is now being considered as an upstream in return).
The strictly-increasing invariant on both fields makes this filter sufficient to
prevent circular exchanges.

### Pairing via Beacon
A C2 transitions from `Synchronized` to `Paired` upon receiving its first valid
`BeaconPayload` that passes the route filter. No handshake. The received route is
written to the Routing State immediately; CM0+ uses it for the next TX assembly.

---

## Cluster Protocol

### Cell Permit
CM4's decoded conclusion after reading the Cluster_Exchange header payload. States
which cell(s) this node is permitted to use for uplink in the current
`Cluster_Exchange` Phase. Written by CM4 to shared memory; read by CM0+ at each
cell opportunity to decide whether to transmit or sleep. Cluster topology only.

The Cluster_Exchange header slot must therefore include a mandatory gap before the
first C1 uplink cell, sized to accommodate: CM0+ flagging CM4, CM4 finishing any
in-flight sensor work, CM4 decoding the header, and CM4 writing the Cell Permit to
shared memory.

---

## CM4 Sensor Acquisition Model

### MbMux CPU Budget

Due to IPCC channel serialisation and TDMA schedule coupling, the worst-case
interval between two consecutive MbMux Application Signal Channel notifications
is **20–30 ms**. CM4 must complete all ISR processing and shared-memory writes
within this window. This is the governing constraint for all CM4 services.

### Sensor Interface CPU Burden

| Interface | DMA for data | CPU-free during transfer? | Caveat |
|---|---|---|---|
| SPI | Yes | Essentially yes | Software CS toggle — microseconds only |
| USART / RS-485 data | Yes | Yes | UART IDLE interrupt for variable-length Rx |
| RS-485 DE/RE | N/A | Yes | STM32 hardware DE auto-toggle — no GPIO bit-bang needed |
| ADC | Yes | Yes | Continuous scan → circular DMA buffer, zero CPU per sample |
| I2C | Yes | Mostly | DMA moves bytes; START/STOP/NACK managed by I2C peripheral + short IRQ |
| SDI-12 | Partial | No — protocol requires CPU staging | See SDI-12 State Machine below |

**Key distinction:** DMA eliminates per-byte CPU intervention; it does not
eliminate the ~microsecond DMA Transfer Completion ISR. Those ISRs are too short
to threaten the 20–30 ms budget. What consumes the budget is **polling mode**:
`HAL_I2C_Master_Receive`, `HAL_ADC_PollForConversion`, or any busy-wait loop.
All sensor interfaces must use DMA + completion callbacks; polling mode is
prohibited.

### DMA Transfer Completion ISR
The short (~microsecond) interrupt fired by the DMA controller when a transfer
finishes. Does not block the 20–30 ms MbMux CPU budget. The permissible CM4
interrupt form — as opposed to polling mode which does block the budget.

### SDI-12 State Machine
SDI-12 mandates a fixed wakeup sequence: pull line low for ≥12 ms (break), then
mark for 8.33 ms, then transmit at 1200 baud. The 12 ms break cannot be handed
to DMA — it is an inherently timed state. Implementation: a hardware timer ISR
sequences the four stages: `BREAK → MARK → DMA-Tx → DMA-Rx`. CPU involvement is
limited to short ISRs at each state transition; no polling loop. SDI-12 is the
sole sensor interface that requires a timer-driven state machine rather than pure
DMA.

**SDI-12 and the MbMux window:** the 12 ms break overlaps with the 20–30 ms
budget if an MbMux notification fires during a SDI-12 command cycle. The CM4
scheduler must account for this: either (a) SDI-12 commands are never issued
within a Phase where MbMux traffic is expected, or (b) the SDI-12 state machine
is interruptible at stage boundaries and can defer its next stage.

---

## Non-Volatile Memory (NVM)

Two NVM backends coexist on the board:

| Backend | Medium | Interface | CS | Owner |
|---|---|---|---|---|
| Internal Flash | STM32WL on-chip flash | Memory-mapped (CPU write) | N/A | CM4 |
| External Flash | External SPI NOR flash | Dedicated SPI peripheral + GPIO software CS | Software-toggled GPIO | CM4 |

**Internal Flash** stores boot-time provisioning constants only (device EUI, join
keys, node class, libration offsets). Written once at factory; never erased in
the field. No MbMux budget conflict.

**External Flash** stores runtime data (sensor logs near-term; OTA firmware
images as a future path). Uses an **append-only circular buffer** over pre-erased
sectors. Sector erases are issued during idle windows only — never in the sensor
acquisition hot path.

### External Flash State Machine
Non-blocking SPI driver following the same timer-driven pattern as the
SDI-12 State Machine. CM4 issues an erase or page-program command over SPI DMA
(microseconds of bus time), then returns to Stop2. On each subsequent CM4 wake
(for any reason — Alarm B or MbMux), CM4 checks a persistent flash operation
state variable. If a flash operation is pending, it reads the flash STATUS
register over SPI to check the BUSY flag. If ready, it advances to the next
stage; if still busy, it returns to Stop2. No dedicated wake source is required
for flash polling — status is checked opportunistically on every CM4 wake.

The external flash SPI peripheral is dedicated — no sensor shares this bus.
Flash DMA and sensor DMA streams are fully independent.

---

## Watchdog and Core Liveness

STM32WL has a single hardware IWDG shared by both cores. Per-core hardware
watchdogs do not exist. Liveness is split by role:

| Mechanism | Protects | Owner | Timeout |
|---|---|---|---|
| IWDG | CM0+ | CM0+ kicks on every TDMA slot wake | 2× max inter-slot gap (seconds range) |
| CM4 Heartbeat | CM4 | CM4 writes a counter to shared memory on every wake; CM0+ checks it | N consecutive unanswered MbMux notifications |

**CM4 Heartbeat:** CM4 increments a shared memory counter on every wake (Alarm B
or MbMux). CM0+ monitors this counter when processing Application Signal Channel
events. If CM4 fails to respond to N consecutive MbMux notifications, CM0+
resets CM4 independently via RCC (STM32WL supports isolated CM4 reset without
disturbing CM0+ or the radio state).

Detection latency for a CM4 hang is bounded by the MbMux notification rate —
worst case, the first notification after the hang is detected. During long sensor
sleep gaps (hours), CM4 hangs go undetected until the next MbMux event fires.
This is acceptable for multi-year field deployments where a few-hour detection
window is tolerable.

The hardware IWDG is **not used in production**. LSI is not enabled. Rationale:
IWDG maximum timeout (~32 s) is incompatible with hour-long inter-Phase sleep
gaps — satisfying it requires periodic sub-alarms that cost more energy than the
protection is worth for this deployment profile.

### Debug Console
CM4 owns the only UART (USART2, PA2/PA3, 9600 8N1), transmitted by DMA from the `stm32_adv_trace` FIFO.
Present in all builds (debug and production).
At 9600 baud the link carries about 960 B/s, roughly 11–15 ArcLog lines per second sustained; bursts are bounded by the two 512 B trace FIFOs (about 6–8 lines).

### Trace Channel (CM0+ → CM4)
Implemented via STM32 middleware: `mbmuxif_trace` + `stm32_adv_trace` utility.

CM0+ formats each line (`UTIL_ADV_TRACE_COND_FSend`) into its own 512 B FIFO in shared RAM (`MB_MEM3`).
The FIFO output driver passes a pointer and length to CM4 with `MBMUX_NotificationSnd` (no wait).
CM4 copies the bytes into its own 512 B FIFO from the IPCC interrupt and acknowledges at once; its UART DMA then drains that FIFO.
When a FIFO is full the line is dropped (`MEM_FULL`); there is no overrun callback.
Losses are made visible by the ArcLog sequence number, not prevented.
The verbosity level (`VLEVEL_*`) filters lines at runtime on each core.

### ArcLog
The structured trace format of both cores (`Common/Log/arclog.h`), one event per line:

```
260925T123456.7890 0Y M #41 SYNC_RX ph=0 ce=1 ep=45120000 st=45123004 exp=45123000 err=4 clk=WARM act=t1
```

| Field | Meaning |
|---|---|
| `260925T123456.7890` | RTC calendar `YYMMDDTHHMMSS.ssss` (100 µs digits, 244 µs resolution), written by `TimestampNow` on both cores; both read the same RTC |
| `0` / `4` | core: CM0+ / CM4 |
| `T M Y R X S P` | module: TDMA, MAC, Sync/clock, Radio, MbMux, System, Power |
| `A L M H` | verbosity: `VLEVEL_ALWAYS`, `VLEVEL_L`, `VLEVEL_M`, `VLEVEL_H` |
| `#41` | per-core sequence number (hex, wraps at 0xff), consumed only by lines that pass the verbosity filter; a gap means lost lines |
| `SYNC_RX` | event name |
| `k=v` | fields; enums by name (`COLD ACQ WARM`, `SCAN SYNC ACTIVE PAIRED`, `TX RX SKIP`) |

Emitted with `ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_M, "EVENT", "k=%u ...", ...)`.
Format strings go through `tiny_vsnprintf_like` built with `TINY_PRINTF`: no length modifiers (`%lu` prints literally and shifts the following arguments) and no floats; 32-bit values use `%u` / `%d` with `(unsigned)` / `(int)` casts.
In host unit tests the lines are captured instead (`Tests/stubs/arclog_capture.h`, `TEST_ASSERT_ARCLOG`), so the events a state machine emits are part of its tested contract.
ST-generated trace lines outside CubeMX USER CODE regions keep their free text; the host tool classifies them as LEGACY.

Key sync events: `CLK` and `MAC_ST` (every ClockState / MacState transition, with `why`), `SYNC_RX` (one per received Sync packet: stamp, expected, signed error, decision), `SYNC_TX` (`plan` = nominal start = intended on-air start, `send` = RTC at `Radio.Send`, `ep` = epoch carried), `TX_LATE` (fire instant passed before the slot task reached it, see Tx Start), `RX_DONE` (`pre`, `hdr`, `rxd` IRQ stamps and the SyncStamp `st`), `TX_DONE` (radio TX start = end − ToA), `RTC_SET` / `RTC_SHIFT` (every clock correction).
Rx timing events: `SCAN` (alarm chain stopped, Scanning Rx starts, `why=boot|lost`), `RX_WIN` (Rx Window opened: latest packet start and cap), `RX_LATE` (woke after the latest packet start), `RX_TIMEOUT` (no preamble in time), `RX_CAP` (reception aborted at the cap), `SYNC_REJ` (Sync packet with a non-Sync phase dropped).
Sync packets are matched across nodes by `(ep, ph, ce)`, which both sender and receiver log.
The event table, host capture, viewer, merge and sync report live in `tools/arclog/` (see its README); a test there fails when the firmware's `ARCLOG()` calls and the tool's schema disagree.
See ADR-0014.

---

## Self-Diagnostics

DiagnosticPayloads serve a dual purpose: **node health monitoring** and
**live network topology visibility**. Every payload carries `source_node_id`,
`upstream_node_id`, and `hop_count` — sufficient for the gateway to reconstruct
the full mesh tree edge by edge as payloads arrive. The periodic `DIAG_HEARTBEAT`
cadence keeps the topology view fresh without dedicated topology-discovery traffic.
`DIAG_COHORT` provides the cluster membership layer: which C1s are attached to
each C2/C3 master and their link quality.

### Diagnostic Aggregator
CM4 is the sole aggregator of all health metrics. This follows directly from the
existing ownership model: CM4 already owns sensors, external flash, and receives
all RF events (RSSI, SNR, TX_NO_ACK) through the existing MbMux channels. No new
inter-core communication is required — RF metrics flow to CM4 via the same
`RxDone` Notif/Ack and Application Signal Channel paths that already exist. CM0+
accumulates no diagnostic state and is unaware of the diagnostic layer.

### DiagnosticPayload
The health snapshot CM4 packages and enqueues into `MeshUplinkQueue`. Follows
the **Late-Binding Packet Assembly** model exactly as sensor payloads do: CM4
produces content only; CM0+ wraps it with MAC variables (routing headers, hop
count, destination) at TX time. No special assembly path. Priority: `HIGH` tier
(diagnostic data must not be displaced by sensor backlog, and must reach the
gateway to trigger operator action).

Metrics captured in a snapshot (not all fields finalized):

| Metric | Source | Notes |
|---|---|---|
| `source_node_id` | CM4 (provisioned node identity) | Identifies originating node regardless of relay hops. Mandatory on all DiagnosticPayload variants. |
| `upstream_node_id` | CM4 (selected Route Entry from Routing State) | ID of the node's current upstream peer (lowest `route_cost` entry). Combined with `source_node_id`, lets the gateway reconstruct the mesh tree edge by edge. |
| `hop_count` | CM4 (from last received BeaconPayload) | Node's current depth in the mesh. Together with `upstream_node_id`, fully positions the node in the topology. |
| Battery voltage | CM4 ADC ch14 (VBAT, internal ÷3 bridge) | 12-bit native; 16-bit via hardware oversampling (256 samples). Converted value × 3 = VBAT. VREFINT (ch13, factory-calibrated, stored in engineering bytes) used as reference for accuracy. ADC auto-shutdown between conversions. |
| Internal temperature | CM4 ADC ch12 (TSENSE) | Factory-calibrated per part; calibration data in device engineering bytes (read-only). Suitable for absolute measurement after calibration. Not for inter-node comparison without individual calibration offsets applied. |
| Link RSSI (rolling) | CM4 (accumulated from RxDone notifications) | Per-link-instance window average (mesh and cluster tracked separately) |
| Link SNR (rolling) | CM4 (accumulated from RxDone notifications) | Per-link-instance window average |
| TX PER | CM4 (`tx_nack / tx_total` per link instance) | Separate per mesh link and cluster link |
| RX PER | CM4 (`rx_timeout / rx_total` per link instance) | Separate per mesh link and cluster link |
| Sensor liveness | CM4 (DMA completion flags + staleness timestamps) | `sensor_liveness` bitmask: two bits per sensor — `interface_ok` \| `data_fresh`. `interface_ok=0` = hard failure. `interface_ok=1, data_fresh=0` = wired but stuck. Sensor count and bitmask width deferred. |
| MeshUplinkQueue depth | CM4 (read at snapshot time) | Sustained depth signals congestion or schedule misconfiguration |
| ClusterQueue depth | CM4 (read at snapshot time) | Same |
| Route cost | CM4 (current `route_cost` from Routing State) | Rising trend indicates battery depletion or congestion along the upstream path |
| ClockState | CM4 (from `SYNC_LOST` / `SYNC_LOCKED` signals) | `sync_miss_count` incremented on each `SYNC_LOST`; reset on `SYNC_LOCKED` |
| CM4 liveness | CM0+ (heartbeat counter monitoring) | Reported via CM0+-generated `DIAG_ALERT` — see CM0+ Emergency Alert |
| Tamper flag | CM4 GPIO (deferred) | Not yet wired |

### DiagType
A single-byte enum field embedded in every `DiagnosticPayload`. Allows the
receiving gateway and server to distinguish report category without inspecting
metric values.

| Value | Name | Trigger | Airtime cost |
|---|---|---|---|
| `0x01` | `DIAG_HEARTBEAT` | Periodic — every N frames (N configurable) | Predictable, budgeted |
| `0x02` | `DIAG_ALARM` | Threshold crossing — any metric breaches its configured limit | Immediate, aperiodic |

### DiagnosticHeartbeat
A `DiagnosticPayload` with `DiagType = DIAG_HEARTBEAT`. Queued by CM4 on a
slow periodic cadence (every N frames). Confirms to the operator that a silent
node is alive and within normal bounds. Resets on its own cadence regardless of
whether a `DiagnosticAlarm` fired in the same window.

### DiagnosticAlarm
A `DiagnosticPayload` with `DiagType = DIAG_ALARM`. Queued by CM4 immediately
when any monitored metric crosses a configured threshold (e.g., battery below
critical level, RSSI window average worse than floor, sensor liveness failure).
Independent of the heartbeat cadence — queued alongside it, not instead of it.
The server reacts to a received `DiagnosticAlarm` by issuing a `Mesh_Downlink`
command (parameter adjustment, alert escalation, or operator notification).

### C1 Diagnostic Path
C1 cannot reach `MeshUplinkQueue` directly (`Mesh_Uplink` participant_mask
excludes C1). C1 enqueues its `DiagnosticPayload` into `ClusterQueue`
(`HIGH` tier) like any other C1 uplink data. C2 receives it via `ClusterBuffer`,
recognises the `DiagType` field, and re-enqueues it verbatim into its own
`MeshUplinkQueue` (`HIGH` tier). C2 acts as a transparent relay — no diagnostic
interpretation is applied. The `DiagnosticPayload` wire format is identical
whether it originates from C1 or C2, ensuring per-node identity is preserved
end-to-end at the gateway.

### C2 Diagnostic Roles
C2 produces two categories of `DiagnosticPayload`, both enqueued into
`MeshUplinkQueue`:

1. **Self-report** — C2's own health metrics (battery, temperature, RF link to
   upstream C3/C2, TX_NO_ACK rate on mesh backbone). Same structure as any node's
   self-report.

2. **CohortReport** — an aggregate health summary of C2's C1 children. Contains
   per-C1 link quality metrics (RSSI/SNR observed during Cluster_Exchange
   receptions), liveness flags (which C1s responded in the last N frames), and
   any other cluster-level signal relevant to an Arctic operator. This is a
   separate payload from C2's self-report, not folded into it. `DiagType`
   carries a distinct value for cohort reports (see DiagType table).

### CohortReport
A `DiagnosticPayload` variant produced exclusively by C2 (and C3 when acting
as cluster master). Carries aggregate health data about the cluster's C1 nodes.
Wire format: `source_node_id` (C2/C3) + `upstream_node_id` + `hop_count` +
a flat list of `CohortEntry` tuples, one per known C1 child:

```c
struct CohortEntry {
    uint8_t  node_id;         // C1 node identity
    int16_t  rssi;            // last observed RSSI on cluster link (dBm)
    int8_t   snr;             // last observed SNR on cluster link (dB)
    uint8_t  liveness_bits;   // bit0 = responded in last frame, bit1–7 reserved
};
```

Gateway correlates CohortReport entries with individual C1 self-reports relayed
through the same C2 to build a complete cluster health picture. CohortReport is
included in the DiagType enum:

| Value | Name | Trigger | Producer |
|---|---|---|---|
| `0x01` | `DIAG_HEARTBEAT` | Periodic — every N frames | C1, C2, C3 (self) |
| `0x02` | `DIAG_ALARM` | Threshold crossing | C1, C2, C3 (self) |
| `0x03` | `DIAG_COHORT` | Same cadence as C2 heartbeat, or on C1 alarm relay | C2 only |
| `0x04` | `DIAG_ALERT` | CM0+ detects CM4 heartbeat failure; CM4 cannot produce its own payload | CM0+ only — exception to Late-Binding rule |

### Diagnostic Flash Recording Policy
Only `DIAG_ALARM` payloads are written to external flash. `DIAG_HEARTBEAT` and
`DIAG_COHORT` are ephemeral — their value is real-time over-the-air reception;
they are not persisted. Alarms are written to flash **before** being enqueued
into `MeshUplinkQueue`. This ordering guarantees that if the node fails before
transmitting (dead battery, crash), the alarm record survives for field retrieval.
No transmit-success flag is stored — the flash record is a write-only audit log.

### DiagnosticState
CM4 RAM structure tracking live health metrics. Never shared with CM0+. Survives
Stop2 (RAM retention); resets on CM4 reset. One instance per active link type,
indexed by phase context (`Mesh_Uplink` / `Cluster_Exchange`). The `phase_type`
field already carried in `RX_TIMEOUT` and `RX_READY` signals identifies which
instance to update — no signal protocol change needed.

```c
struct DiagnosticState {
    uint16_t tx_total;
    uint16_t tx_nack;
    uint16_t rx_total;
    uint16_t rx_timeout;
    int16_t  rssi_window[DIAG_WINDOW_SIZE];   // circular, N = provisioned constant
    int8_t   snr_window[DIAG_WINDOW_SIZE];
    uint8_t  window_head;
    uint16_t last_vbat_mv;                    // updated on Alarm B; shared across instances
    int8_t   last_temp_c;                     // same
    uint16_t frames_since_heartbeat;          // same
    uint8_t  sync_miss_count;                 // incremented on SYNC_LOST, reset on SYNC_LOCKED; shared
    uint8_t  mesh_queue_depth;                // snapshot of MeshUplinkQueue occupancy; shared
    uint8_t  cluster_queue_depth;             // snapshot of ClusterQueue occupancy; shared
    uint16_t current_route_cost;              // from Routing State at snapshot time; shared
};
```

Instances per node class:

| Class | Instances | Links tracked |
|---|---|---|
| C1 | 1 | Cluster link to master (C2 or C3) |
| C2 | 2 | Mesh uplink to upstream C3/C2 · Cluster link to C1 children |
| C3 | 2 | Mesh reception link from upstream C2s · Cluster link to C1 children |

C3 may act as cluster master and therefore maintains a cluster-link instance
alongside its mesh-reception instance. `last_vbat_mv`, `last_temp_c`, and
`frames_since_heartbeat` are logically shared — one snapshot per Alarm B wake
regardless of instance count.

`DIAG_ALARM` packets for mesh-link and cluster-link failures are emitted as
separate payloads, preserving link identity at the gateway. C2's cluster-link
RX PER and `DIAG_COHORT` are complementary: the former is C2's own receive
reliability on the cluster link; the latter is the per-C1-child health picture.

### CM0+ Emergency Alert
The sole exception to the rule that CM4 produces all `DiagnosticPayload` content.
When CM0+ detects CM4 heartbeat failure (N consecutive MbMux notifications
unanswered — see Watchdog and Core Liveness), it writes a minimal
`DiagnosticPayload` with `DiagType = DIAG_ALERT` directly into a dedicated
single-entry **EmergencyAlertSlot** in shared memory (not `MeshUplinkQueue`,
which CM4 owns). At the next `Mesh_Uplink` TX opportunity, CM0+ checks the
`EmergencyAlertSlot` before `MeshUplinkQueue` and, if populated, assembles and
transmits it at `HIGH` priority. The payload carries only: node ID, `DIAG_ALERT`
type, and the RTC timestamp of failure detection. CM4 reset follows via RCC
(existing mechanism) after the alert is queued. The `EmergencyAlertSlot` is
cleared on CM4 restart.

### Diagnostic Trigger Model
Three trigger paths coexist:

1. **Heartbeat timer (CM4)** — CM4 counts elapsed frames on each Alarm B wake.
   When the counter reaches N, it snapshots all metrics and enqueues a
   `DIAG_HEARTBEAT`. Counter resets to zero.

2. **Threshold evaluator (CM4)** — CM4 evaluates each metric on every Alarm B
   wake and on each IPCC signal (`RX_TIMEOUT`, `TX_NO_ACK`, `SYNC_LOST`).
   If any metric crosses its provisioned threshold, it immediately writes the
   alarm to external flash, then enqueues a `DIAG_ALARM`. Heartbeat counter
   is unaffected.

3. **CM0+ heartbeat watchdog** — CM0+ monitors the CM4 Heartbeat counter. On
   N consecutive failures, it writes a `DIAG_ALERT` to the `EmergencyAlertSlot`
   and triggers CM4 reset. Does not involve CM4 at all.

Thresholds are provisioned constants (internal flash, boot-time only, not
field-adjustable — see Non-Volatile Memory). Field-adjustable thresholds via
`Mesh_Downlink` are a future path not yet specified.

### DownlinkDiagCmd
The command type field carried in a diagnostic-reactive `Mesh_Downlink` packet.
Wire format reserves a `DownlinkDiagCmd` byte to accommodate future command
categories. A pure acknowledgement / informational response is not a valid
command — diagnostic downlinks must be actionable. Two command categories are
enabled in the wire format but deferred pending operator confirmation:

| Value | Name | Action | Status |
|---|---|---|---|
| `0x01` | `DIAG_CMD_PARAM_UPDATE` | Adjust runtime parameters (sampling rate, heartbeat period, TX power) to extend node lifetime until servicing. Requires CM4 parameter-write path without reboot. | Deferred — operator preference not yet confirmed |
| `0x02` | `DIAG_CMD_SELFTEST` | Trigger an immediate out-of-schedule diagnostic snapshot. CM4 samples all metrics and enqueues a fresh `DIAG_ALARM`. Useful before dispatching a technician. | Deferred — operator preference not yet confirmed |

Exact command set is an Arctic operator decision. Wire format is stable; command
semantics are not yet finalised.

---

**Resolved — TDMA Table direction split (final):** `DirectionMode` is a three-value enum governing Cell Role assignment. `DIRECTION_CELL_SKIP` (`Mesh_Uplink`, `Cluster_Exchange`, `Mesh_Downlink`): MAC-internal `CellEligibilityMask` / Cell Permit; the MAC returns `SLOT_SKIP` for ineligible cells in `MAC_OnSlotOpportunity`; the TDMA Machine wakes every cell and delegates; uplink uses ascending hop-count residue, downlink uses mirror descending pattern (Skip at uplink-Tx residue). `DIRECTION_MAC_CELL` (`Mesh_Beacon`, `Sync`): TDMA wakes every cell; MAC assigns Cell Role per-cell from internal state. For Mesh_Beacon: K random Tx cells at phase entry; for Sync: three-tier relay (C3 Tx first SYNC_TX_BUDGET cells, C2 Rx Cell 0 then Tx Cells 1+, C1 Rx all). `DIRECTION_MAC_PHASE`: currently unused — previously applied to Sync but moved to `DIRECTION_MAC_CELL` because C2's mid-phase role switch violated the uniform-role contract. `phase_tx_flag` shared memory field retained for future use. `DIRECTION_STATIC` removed. See ADR-0010 (original split) and ADR-0011 (three-value finalisation, revised).

**Resolved — Spectrum Access Compliance Engine:** A MAC-agnostic compliance component resides entirely on CM0+. It exposes two hooks to the MAC State Machine: `RequestChannel(freq_hz, expected_toa_ms, tx_power_dbm) → Result` (called before any TX) and `ReportTxDone(freq_hz, actual_toa_ms)` (called after TX completes). The MAC State Machine calls these; the engine knows nothing about the protocol above it. Initial strategy: ETSI duty-cycle time-credit accounting per `RegionProfile` / `Band`. Interface is open to future strategies (LBT, FHSS dwell-time). CM4 is not involved in compliance decisions. `expected_toa_ms` is `slot_active_ms` from the TDMA Table (conservative, always compliant — flagged for optimization to per-packet ToA calculation). TX power is passed explicitly so the engine can validate against `Band.max_tx_power_dbm` and return `POWER_TOO_HIGH` without reading radio hardware state directly.

MAC State Machine behavior on `RequestChannel` result is slot-kind-aware:

| Result | SCHEDULED slot | CONTENTION slot |
|---|---|---|
| `GRANTED` | proceed with TX | proceed with TX |
| `RESTRICTED { wait_ms }` | skip slot, write `ComplianceStatus` | if `wait_ms ≤ remaining_slot_time`: wait, retry `RequestChannel`; else skip, write `ComplianceStatus` |
| `POWER_TOO_HIGH` | skip slot, write `ComplianceStatus` | skip slot, write `ComplianceStatus` |
| `BAND_UNKNOWN` | skip slot, write `ComplianceStatus` | skip slot, write `ComplianceStatus` |

In CONTENTION slots the MAC already tracks `remaining_slot_time` to bound the CSMA backoff loop — `wait_ms` from the compliance engine becomes a lower bound on that backoff with no new state required.

**Resolved — FrequencyResolver Machine:** A dedicated machine on CM0+ that produces `freq_hz` for the current slot. Input: `FrameCursor`. Reads `FrequencyResolverState` from shared memory — CM4 is the **sole writer**, CM0+ is read-only. `FrequencyResolver` is separate from the TDMA Table: TDMA Table owns timing and direction; `FrequencyResolver` owns channel assignment. Called for both Tx and Rx slots: output feeds `Radio_SetChannel()` unconditionally, and additionally feeds `RequestChannel()` on Tx slots only.

Each phase has independent frequencies for its header, footer, and cells. Cell frequency supports three modes:

```c
typedef enum {
    CELL_FREQ_STATIC,    // fixed freq for all cells in phase
    CELL_FREQ_HOP,       // hop sequence advanced from seed by CM0+
    CELL_FREQ_OVERRIDE,  // per-cell table written by CM4; indexed by cell_index
} CellFreqMode;

struct PhaseFrequency {
    uint32_t     header_freq_hz;                     // CM4 writes; 0 = absent
    uint32_t     footer_freq_hz;                     // CM4 writes; 0 = absent
    CellFreqMode cell_mode;
    uint32_t     cell_freq_or_seed;                  // CELL_FREQ_STATIC: static freq;
                                                     // CELL_FREQ_HOP: LFSR seed (CM0+ advances)
    uint8_t      override_table_idx;                 // CELL_FREQ_OVERRIDE: index into
                                                     // g_freq_override_tables pool; 0xFF = unassigned
    uint8_t      _pad[3];                             // alignment
};

// Separate pool for per-cell override tables.
// Only phases using CELL_FREQ_OVERRIDE consume entries.
uint32_t       g_freq_override_tables[MAX_OVERRIDE_TABLES][MAX_CELLS_PER_PHASE];

struct FrequencyResolverState {
    struct PhaseFrequency phases[MAX_PHASES];        // indexed by phase_index
};
```

The per-cell override table is split out of `PhaseFrequency` into a separate
pool. This avoids every phase paying 128 bytes (32 x 4 B) for the
`CELL_FREQ_OVERRIDE` union member when most phases use `CELL_FREQ_STATIC` or
`CELL_FREQ_HOP` (4 bytes each). A phase in OVERRIDE mode references its
per-cell table by index (`override_table_idx`).

Sizing: `MAX_PHASES = 20`, `MAX_CELLS_PER_PHASE = 32`,
`MAX_OVERRIDE_TABLES = 5`. Per-phase: 20 bytes. Override pool: 5 x 128 =
640 bytes. Total `FrequencyResolverState` + override pool: 20 x 20 + 640 =
1040 bytes in shared SRAM2.

**Resolved — ComplianceStatus shared memory:** CM0+ writes a `ComplianceStatus` structure to shared memory after every `RequestChannel` call. CM4 reads it opportunistically on its next wake (no new MbMux signal — compliance events can be frequent and must not flood the Application Signal Channel). CM4 uses it to track `compliance_skip_count` per link instance in `DiagnosticState` and to avoid generating payloads when a band is known to be exhausted. CM4 is read-only on this structure; CM0+ is the sole writer.

```c
struct ComplianceStatus {
    uint32_t last_freq_hz;          // frequency of the last RequestChannel call
    uint8_t  last_result;           // GRANTED / RESTRICTED / POWER_TOO_HIGH / BAND_UNKNOWN
    uint32_t wait_ms;               // valid when last_result == RESTRICTED
    uint16_t skip_count_mesh;       // cumulative skipped TX slots on mesh link
    uint16_t skip_count_cluster;    // cumulative skipped TX slots on cluster link
    uint8_t  band_unknown_count;    // incremented on BAND_UNKNOWN; non-zero signals misconfiguration
};
```

`BAND_UNKNOWN` behavior: assert in debug builds (fatal misconfiguration — a frequency outside all declared Bands is a certification risk); silent skip + `band_unknown_count` increment in production. A non-zero `band_unknown_count` observed by CM4 triggers a `DIAG_ALARM`.

**Resolved — RegionProfile residency:** `RegionProfile` (static Band boundaries, duty-cycle limits, max TX power) is compiled into CM0+ flash as read-only constants. No runtime region swap; no roaming support. The `EnforcementStrategy` abstraction and `RegionProfile` structure remain open to adding region-specific strategies (including a future Greenland regulatory profile) without redesigning the engine.

**Deferred [NEXT PRIORITY] — Sensor scheduling machine:** how CM4 tracks multiple sensor descriptors with independent periods, computes the minimum-next-alarm across all due sensors on each Alarm B wake, and handles coincident sensors in one acquisition cycle. `AlarmBRequest.pending` flag is settled (Option B); scheduling logic is not yet specified.

**Deferred [NEXT PRIORITY] — Sensor power management:** whether sensor interfaces require a switched power rail before acquisition, and the worst-case sensor startup time to accommodate in the TDMA header gap. Initial assumption: switched power rail controlled by a hardware GPIO (always-on not affordable). Awaiting hardware confirmation from colleague.

**Deferred — All `Cluster_Exchange` internals:** header content and wire format, Cell Permit encoding, per-node cell assignment model (`Cluster_Join` phase, join request packet, acceptance policy, cluster capacity limits, MAC State Machine integration). None of this is a current priority.

**Deferred — CT empirical validation (see `ArcLoRaM_CT_Sync_Design.md`):**
(1) Confirm 8-symbol preamble length for CT-LoRa.
(2) Measure worst-case inter-C2 drift under Arctic temperatures; size Sync Phase
cadence to stay below 16 ms.
(3) Select variable TX power scheme (randomised / hop-count-based / per-node).
(4) Tune C2 audit cycle ratio from starting value of 0.5.
(5) Determine if offset-CT timing jitter is needed beyond TX power + multi-slot.

**Deferred — Factory provisioning:** how boot constants are written to internal flash at manufacturing (SWD programmer, UART bootloader, or other) is not yet decided.

**Deferred — CM0+ hang detection:** if CM0+ hangs, it stops sending MbMux
signals; TX queue entries age indefinitely. A future mechanism — CM4 triggering a
system reset via `SCB_AIRCR` when payload age exceeds N frames — is a viable
path. Not a priority until core TDMA and sensor mechanisms are operational.

**Resolved — `actual_toa_ms` source for `ReportTxDone`:** the radio middleware provides `Radio.TimeOnAir(modem, bandwidth, datarate, coderate, preambleLen, fixLen, payloadLen, crcOn)` ([radio.h:268](Middlewares/Third_Party/SubGHz_Phy/radio_driver/radio.h#L268)), callable after `SetTxConfig`. CM0+ calls it immediately after packet assembly (when `actual_payload_len` is known) and passes the result to `ReportTxDone`. This is a computed value from real radio parameters — valid for ETSI duty-cycle accounting. Pre-TX check uses `slot_active_ms` (conservative); post-TX deduction uses the accurate computed airtime.
