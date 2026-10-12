# The largest packet of each slot is declared in the TDMA Table

The Rx Window of a slot closes at the latest instant a packet can start and still end by `slot end + MAX_GUARD_TIME_MS`.
That needs the time on air of the largest packet the slot can carry, and it used to be the Sync packet's for every slot, because Sync was the only traffic (ADR-0015).
The TDMA Table now declares that size, in bytes on air, for every slot: `slot_max_packet_len` on the Phase (one byte per Slot of its Cell, a const array beside the Phase), and `max_packet_len` on the Header and on the Footer.
The TDMA Machine reads it only through `TdmaTable_MaxPacketLen` and passes it to `RadioTimeOnAir` (issue #38).

## Considered Options

**Ask the MAC for the current slot.**
Rejected: which packet a slot carries is a fact of the schedule, known at compile time, and the TDMA Table is already the node-agnostic schedule (ADR-0003).
The MAC decides roles at runtime; making it answer a size too would put packet sizes in a module that otherwise does not know them.

**One value per Phase.**
Rejected: a Cell with a Data Slot and an ACK Slot would close the ACK window for the Data size, and a Header or Footer with its own packet would share the Cell's.
Safe (a window never runs into the next slot), but late ACKs would be missed.

**A fixed array of 16 entries in every Phase.**
Rejected: it costs about 24 bytes of flash per Phase whatever `slot_count` is, and it grows with the 16-slot limit.
With the 10 to 15 Phases a full Frame will have, the compact array is about half.

**A packet kind per slot, with one central table from kind to length.**
Not chosen now: lengths would be defined once instead of in each Phase, but it adds an enum and an indirection.
The accessor hides the layout, so it can be adopted later (the packet description of issue #90) without touching the TDMA Machine.

## Consequences

- **Footprint is flash only.** `AnchorSlot_t` gains one byte (4 with alignment) and `Phase_t` a pointer (4 bytes), plus `slot_count` bytes of array: about 13 bytes for a one-slot Phase, about 200 bytes for 15. No RAM. Phases whose cells carry the same packets share one array (the two Sync Phases do).
- **`0` means undeclared.** The accessor returns it for a missing array or a slot index past `slot_count`, rather than reading through a bad pointer on the CM0+. A window sized from it would be too wide, but the cap still bounds the reception. The host test of the real table forbids `0` for every slot a Phase has, so a new Phase that forgets its lengths does not build green.
- **The length matters in some windows only.** A warm clock closes one guard after the nominal start whatever the packet; the length decides the end while acquiring, in a CONTENTION slot, and for a packet near the slot size.
- **Sync slots are unchanged.** Both Sync Phases declare `sizeof(SyncPayload_t)`, the size the TDMA Machine used for every slot before.
- **Issue #90 builds on it.** The slot duration derived from the packet and the compliance request come from the same data.
