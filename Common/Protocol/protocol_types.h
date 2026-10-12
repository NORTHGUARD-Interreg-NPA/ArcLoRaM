/*!
 * \file      protocol_types.h
 *
 * \brief     ArcLoRaM protocol type definitions shared by all CM0+ machines
 *            and CM4 application logic.
 *
 * \code
 *              ____  ______  _         ___   _   _  __  __
 *             / ___||  ____|| |       |_ _| | | | ||  \/  |
 *            | |    | |__   | |        | |  | | | || |\/| |
 *            | |___ |  __|  | |___    _| |_ | |_| || |  | |
 *             \____||______| \_____| |_____| \___/ |_|  |_|
 *            (C)2025-2026 Celium
 *
 * \endcode
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef PROTOCOL_TYPES_H
#define PROTOCOL_TYPES_H

#include <stdint.h>
#include <stddef.h>

/* =========================================================================
 * Node class constants
 * ========================================================================= */

#ifndef NODE_CLASS_C1
/*!
 * End node. Cluster-only. Sensor data producer.
 *
 * \remark Compile-time constant. Set NODE_CLASS to one of these three values
 *         via a compiler define (-DNODE_CLASS=NODE_CLASS_C2). Dead-code
 *         elimination removes all code branches for the unused classes.
 */
#define NODE_CLASS_C1  1u
#endif

#ifndef NODE_CLASS_C2
/*!
 * Relay. Cluster master for C1 nodes. Mesh backbone participant.
 *
 * \remark See \ref NODE_CLASS_C1 for the compile-time constant rationale.
 */
#define NODE_CLASS_C2  2u
#endif

#ifndef NODE_CLASS_C3
/*!
 * Gateway. Mesh backbone terminus. SyncAnchor — originates Sync packets.
 *
 * \remark See \ref NODE_CLASS_C1 for the compile-time constant rationale.
 *         C3 never enters Stop2 and never enters \ref MAC_STATE_SCANNING.
 */
#define NODE_CLASS_C3  3u
#endif

/* =========================================================================
 * Participant mask bit positions
 * ========================================================================= */

/*!
 * Bit 0 of \ref Phase_t::participant_mask — C1 participates in this Phase.
 */
#define PARTICIPANT_C1  (1u << 0)

/*!
 * Bit 1 of \ref Phase_t::participant_mask — C2 participates in this Phase.
 */
#define PARTICIPANT_C2  (1u << 1)

/*!
 * Bit 2 of \ref Phase_t::participant_mask — C3 participates in this Phase.
 */
#define PARTICIPANT_C3  (1u << 2)

/* =========================================================================
 * Enumerations
 * ========================================================================= */

/*!
 * \brief   MAC-layer type of a TDMA Phase.
 *
 * \details Descriptive label only — not a unique key. The same
 *          \ref PhaseType_t may appear more than once in a frame.
 */
typedef enum {
    PHASE_TYPE_MESH_BEACON,       /*!< Link detection for routing (C2/C3). */
    PHASE_TYPE_MESH_UPLINK,       /*!< Node-to-gateway sensor data uplink. */
    PHASE_TYPE_MESH_DOWNLINK,     /*!< C3-originated commands pushed down the mesh. */
    PHASE_TYPE_CLUSTER_EXCHANGE,  /*!< Bidirectional cluster communication (C1/C2). */
    PHASE_TYPE_SYNC,              /*!< Frame Epoch correction propagation. */
} PhaseType_t;

/*!
 * \brief   Slot access kind — scheduled direction or open contention.
 */
typedef enum {
    SLOT_SCHEDULED,   /*!< Direction predetermined by the TDMA Table. */
    SLOT_CONTENTION,  /*!< Any eligible node may transmit after CSMA backoff. */
} SlotKind_t;

/*!
 * \brief   Per-Phase rule governing how the TDMA Machine determines
 *          Tx/Rx direction at each cell boundary.
 */
typedef enum {
    DIRECTION_CELL_SKIP,  /*!< \ref CellEligibilityMask_t governs which cells wake. */
    DIRECTION_MAC_CELL,   /*!< MAC decides Tx/Rx per-cell from internal K-of-N state. */
    DIRECTION_MAC_PHASE,  /*!< \ref PhaseTxFlag_t governs the whole phase direction. */
} DirectionMode_t;

/*!
 * \brief   Position of a slot within a Phase — used by the Frequency Resolver
 *          to select the correct channel.
 */
typedef enum {
    SLOT_POS_HEADER,  /*!< Header anchor slot at the start of the Phase. */
    SLOT_POS_CELL,    /*!< Regular cell slot within the Cell × N repetition. */
    SLOT_POS_FOOTER,  /*!< Footer anchor slot at the end of the Phase. */
} SlotPosition_t;

/*!
 * \brief   RTC synchronisation quality tracked by the MAC State Machine.
 */
typedef enum {
    CLOCK_COLD,       /*!< No Sync received. Boot default for C1/C2. */
    CLOCK_ACQUIRING,  /*!< At least one Sync processed; sub-second not confirmed. */
    CLOCK_WARM,       /*!< Three packets below threshold. Fully synchronised. */
} ClockState_t;

/*!
 * \brief   Radio action returned by \ref MAC_OnSlotOpportunity.
 */
typedef enum {
    SLOT_TX,    /*!< Transmit in this slot. */
    SLOT_RX,    /*!< Open a receive window in this slot. */
    SLOT_SKIP,  /*!< Sleep through this slot entirely. */
} SlotDecision_t;

/*!
 * \brief   MAC State Machine operational states.
 */
typedef enum {
    MAC_STATE_SCANNING,      /*!< No sync. Continuous wide-RX. C1/C2 boot default. */
    MAC_STATE_SYNCHRONIZED,  /*!< CLOCK_WARM. Seeking peers. No data TX. */
    MAC_STATE_ACTIVE,        /*!< C3 only. Boot default. Originating Sync packets. */
    MAC_STATE_PAIRED,        /*!< Connected to cluster master or mesh backbone. */
} MacState_t;

/* =========================================================================
 * TDMA Table structs
 * ========================================================================= */

/*!
 * \brief   Optional single slot anchored to the start or end of a Phase,
 *          outside the Cell × N repetition.
 *
 * \details Absent when \c duration_ms == 0.
 */
typedef struct {
    uint32_t   duration_ms;  /*!< 0 = slot absent. */
    SlotKind_t kind;         /*!< Scheduled or contention access. */
    uint32_t   bitmap[3];    /*!< Per-class bitmaps [C1, C2, C3]; ignored when
                               *  \c kind == \ref SLOT_CONTENTION. */
    uint32_t   gap_after_ms; /*!< Sleep gap after this anchor slot before the next
                              *  slot/phase. 0 = none. For a header: gap before
                              *  cell 0. For a footer: gap before the next phase. */
    uint8_t    max_packet_len; /*!< Largest packet, in bytes on air, this slot can
                                *  carry. Read it through
                                *  \ref TdmaTable_MaxPacketLen. */
} AnchorSlot_t;

/*!
 * \brief   Single Phase descriptor — one entry in the TDMA Table.
 *
 * \details The TDMA Table is a read-only, node-agnostic ordered array of
 *          these structs. The TDMA Machine traverses it without modifying it.
 */
typedef struct {
    PhaseType_t     type;             /*!< MAC-layer role of this Phase. */
    uint8_t         participant_mask; /*!< Bit field: bit0=C1, bit1=C2, bit2=C3.
                                       *  Non-participants skip the Phase entirely. */
    DirectionMode_t direction_mode;   /*!< Cell-direction governing rule. */
    uint8_t         cell_count;       /*!< Cell repetitions in this Phase (0–32). */
    uint8_t         slot_count;       /*!< Slots per Cell (1–16). */
    uint32_t        slot_active_ms;   /*!< Worst-case duration of one Slot exchange. */
    uint16_t        gap_after_slot_ms; /*!< Network-wide sleep gap after every slot
                                        *   within a cell. When the current slot is
                                        *   the last in the cell, this gap precedes
                                        *   the next cell (or footer/phase). */
    const uint8_t  *slot_max_packet_len; /*!< Largest packet, in bytes on air, that
                                        *   each Slot of a Cell can carry: an array
                                        *   of \c slot_count entries indexed by the
                                        *   slot index. Read it through
                                        *   \ref TdmaTable_MaxPacketLen. */
    AnchorSlot_t    header;           /*!< Optional header slot (absent if duration_ms==0). */
    AnchorSlot_t    footer;           /*!< Optional footer slot (absent if duration_ms==0). */
} Phase_t;

/* =========================================================================
 * Frame Cursor
 * ========================================================================= */

/*!
 * \brief   Live position of the TDMA Machine within the TDMA Table.
 *
 * \details Advances each Slot; resets to {0,0,0} at Frame end.
 */
typedef struct {
    uint8_t         phase_index;  /*!< Index into the TDMA Table phase array. */
    uint16_t        cell_index;   /*!< Current Cell within the Phase (0-based). */
    uint8_t         slot_index;   /*!< Current Slot within the Cell (0-based). */
    SlotPosition_t  slot_pos;     /*!< Header, cell, or footer — selects the timing
                                   *  formula and the MAC decision path. */
} FrameCursor_t;

/* =========================================================================
 * On-wire payload structs
 * ========================================================================= */

/*!
 * \brief   On-wire payload of a Sync packet — 10 bytes.
 *
 * \details Packed to ensure no padding between the uint32_t time field and its
 *          neighbours. This is an on-wire format struct; always \c memcpy the
 *          \c ms_since_midnight_sync_phase field into a local \c uint32_t before
 *          arithmetic on CM0+ (no hardware unaligned-load support at odd offsets).
 *
 *          C3 is the sole epoch authority: it writes \c GetTimerTicks() into
 *          \c ms_since_midnight_sync_phase at Sync Phase entry. C2 relays C3's
 *          received value verbatim — it never self-generates an epoch.
 *
 *          Receiver Packet 1 path:
 *            target_ms = ms_since_midnight_sync_phase + sync_cell_index × per_cell_ms
 *            H = target_ms / 3600000;  M = (target_ms % 3600000) / 60000;
 *            S = (target_ms % 60000) / 1000;
 *            HAL_RTC_SetTime(BIN: H, M, S) + HAL_RTC_SetDate(BCD: day, month, year)
 *
 *          See ADR-0005 and CONTEXT.md — Sync Algorithm.
 */
typedef struct __attribute__((packed)) {
    uint8_t  packet_type_id;               /*!< Packet type discriminator. */
    uint32_t ms_since_midnight_sync_phase; /*!< Sync Phase start time, binary ms,
                                             *  range 0–86,399,999. */
    uint8_t  day;                          /*!< BCD 01–31. */
    uint8_t  month;                        /*!< BCD 01–12. */
    uint8_t  year;                         /*!< BCD 00–99, years since 2000. */
    uint8_t  sync_phase_index;             /*!< TDMA Table phase index of this Sync
                                             *  Phase — used to look up per_cell_ms
                                             *  and bootstrap the FrameCursor. */
    uint8_t  sync_cell_index;              /*!< Cell within the Sync Phase (0-based).
                                             *  Renamed from sync_slot_index. */
} SyncPayload_t;                           /* 10 bytes: 1+4+1+1+1+1+1 */

/*!
 * \brief   Beacon payload written by CM4, read by CM0+ once per
 *          Mesh_Beacon Phase.
 *
 * \details Wire format is not yet finalised; this is a placeholder that
 *          satisfies forward declarations in the MAC State Machine.
 */
typedef struct {
    uint8_t  hop_count;   /*!< Sender's mesh depth (receiver sets own to hop+1). */
    uint8_t  node_id;     /*!< Sender's node identity. */
    uint16_t route_cost;  /*!< Cumulative path cost from root. */
} BeaconPayload_t;

/* =========================================================================
 * Frame Epoch
 * ========================================================================= */

/*!
 * \brief   Absolute timestamp of the current Frame's start.
 *
 * \details Stored as raw RTC fields in RTC_BINARY_NONE mode — BCD calendar
 *          plus raw SubSeconds SSR (counts down from PREDIV_S). Never
 *          converted to a flat integer. All FrameCursor time computations
 *          are relative to this epoch. Set or corrected on every
 *          Mesh_Beacon or Sync packet reception.
 */
typedef struct {
    uint8_t  hours;       /*!< BCD 00–23. */
    uint8_t  minutes;     /*!< BCD 00–59. */
    uint8_t  seconds;     /*!< BCD 00–59. */
    uint32_t subseconds;  /*!< Raw SSR (counts DOWN from PREDIV_S). */
    uint8_t  day;         /*!< BCD 01–31. */
    uint8_t  month;       /*!< BCD 01–12. */
    uint8_t  year;        /*!< BCD 00–99, years since 2000. */
} FrameEpoch_t;

#endif /* PROTOCOL_TYPES_H */
