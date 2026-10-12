/*!
 * \file      shared_mem.h
 *
 * \brief     Inter-core shared SRAM2 structure definitions for the
 *            ArcLoRaM STM32WL55 dual-core firmware.
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
#ifndef SHARED_MEM_H
#define SHARED_MEM_H

#include <stdint.h>
#include "protocol_types.h"
#include "core_crash.h"

/* =========================================================================
 * Ownership conventions
 *
 *   CM4  writes, CM0+ reads  — AlarmBRequest_t, FrequencyResolverState_t
 *   CM0+ writes, CM4  reads  — ComplianceStatus_t
 *
 * Note: CellEligibilityMask_t and PhaseTxFlag_t are CM0+-internal values
 * (written by the MAC State Machine, read by the TDMA Machine on the same
 * core). They are defined in mac_types.h and live in CM0+ RAM, not
 * in this inter-core shared region.
 *
 * All structs use natural alignment — no __packed — to avoid unaligned
 * load/store faults on CM0+ (Cortex-M0+ has no hardware unaligned access).
 * ========================================================================= */

/* =========================================================================
 * AlarmBRequest_t
 * ========================================================================= */

/*!
 * \brief   Request written by CM4 asking CM0+ to program RTC Alarm B.
 *
 * \details CM4 writes the BCD time fields then sets \c pending = 1.
 *          CM0+ acts only when \c pending == 1: programs Alarm B, then
 *          clears \c pending. This two-phase write eliminates the race where
 *          CM0+ reads a partially-written or stale request.
 *
 *          \c pending is a \c uint8_t at offset 0 — naturally atomic on
 *          ARM Cortex-M. No mutex required for the flag itself.
 *
 *          Layout (12 bytes, naturally aligned):
 *          \code
 *            offset 0 : pending   (uint8_t) — atomic flag
 *            offset 1 : hours     (uint8_t, BCD)
 *            offset 2 : minutes   (uint8_t, BCD)
 *            offset 3 : seconds   (uint8_t, BCD)
 *            offset 4 : day       (uint8_t, BCD)
 *            offset 5 : month     (uint8_t, BCD)
 *            offset 6 : year      (uint8_t, BCD)
 *            offset 7 : _reserved (uint8_t, alignment pad)
 *            offset 8 : subseconds (uint32_t, raw SSR)
 *          \endcode
 */
typedef struct {
    uint8_t  pending;    /*!< Atomic flag: 1 = request pending, 0 = consumed. */
    uint8_t  hours;      /*!< BCD 00–23. */
    uint8_t  minutes;    /*!< BCD 00–59. */
    uint8_t  seconds;    /*!< BCD 00–59. */
    uint8_t  day;        /*!< BCD 01–31. */
    uint8_t  month;      /*!< BCD 01–12. */
    uint8_t  year;       /*!< BCD 00–99, years since 2000. */
    uint8_t  _reserved;  /*!< Explicit alignment pad — do not use. */
    uint32_t subseconds; /*!< Raw SSR (counts DOWN from PREDIV_S). */
} AlarmBRequest_t;       /* 12 bytes */

/* =========================================================================
 * FrequencyResolverState_t
 * ========================================================================= */

#ifndef FREQ_MAX_PHASES
/*!
 * \brief   Maximum number of Phases in the TDMA Table supported by the
 *          Frequency Resolver shared memory layout.
 *
 * \details Sized for full flexibility (20 phases) even though the initial
 *          frame will likely use 7-9. The per-phase struct is 20 bytes
 *          (override tables are split into a separate pool), so the total
 *          FrequencyResolverState is 400 bytes.
 *
 * \remark  Increasing this value grows FrequencyResolverState_t by 20 bytes
 *          per extra phase.
 */
#define FREQ_MAX_PHASES           20u
#endif

#ifndef FREQ_MAX_OVERRIDE_TABLES
/*!
 * \brief   Maximum number of per-cell override frequency tables.
 *
 * \details Only phases using \ref CELL_FREQ_OVERRIDE consume an entry in
 *          this pool. Phases using \ref CELL_FREQ_STATIC or \ref
 *          CELL_FREQ_HOP do not. Each table is FREQ_MAX_CELLS_PER_PHASE x
 *          4 bytes (128 bytes).
 *
 * \remark  5 tables is generous: the 5 phase types are unlikely to all
 *          use OVERRIDE mode simultaneously. Increasing this value grows
 *          the override pool by 128 bytes per extra table.
 */
#define FREQ_MAX_OVERRIDE_TABLES  5u
#endif

#ifndef FREQ_MAX_CELLS_PER_PHASE
/*!
 * Maximum number of Cells per Phase supported by the
 * \ref CELL_FREQ_OVERRIDE per-cell frequency table.
 *
 * \remark Matches Phase_t::cell_count upper bound (0–32).
 */
#define FREQ_MAX_CELLS_PER_PHASE  32u
#endif

/*!
 * \brief   Cell-frequency assignment mode for one Phase.
 */
typedef enum {
    CELL_FREQ_STATIC,    /*!< Fixed frequency for all cells in the Phase. */
    CELL_FREQ_HOP,       /*!< LFSR hop sequence seeded by \c hop_seed; advanced
                           *  by CM0+ Frequency Resolver on each cell. */
    CELL_FREQ_OVERRIDE,  /*!< Per-cell table written by CM4; indexed by
                           *  \ref FrameCursor_t::cell_index. */
} CellFreqMode_t;

/*!
 * \brief   Per-Phase frequency configuration written by CM4.
 *
 * \details CM4 is the sole writer. CM0+ Frequency Resolver is read-only.
 *          Header and footer use independent fixed frequencies. Cell
 *          frequency follows \c cell_mode. For \ref CELL_FREQ_STATIC and
 *          \ref CELL_FREQ_HOP, the frequency or seed is stored inline in
 *          \c cell_freq_or_seed. For \ref CELL_FREQ_OVERRIDE, the per-cell
 *          table is stored in the separate \c g_freq_override_tables pool,
 *          indexed by \c override_table_idx.
 */
typedef struct {
    uint32_t       header_freq_hz;      /*!< Header slot frequency in Hz; 0 = absent. */
    uint32_t       footer_freq_hz;      /*!< Footer slot frequency in Hz; 0 = absent. */
    CellFreqMode_t cell_mode;            /*!< Cell-frequency assignment mode. */
    uint32_t       cell_freq_or_seed;    /*!< CELL_FREQ_STATIC: static frequency.
                                          *   CELL_FREQ_HOP: LFSR seed (CM0+ advances).
                                          *   CELL_FREQ_OVERRIDE: unused (see override_table_idx). */
    uint8_t        override_table_idx;  /*!< CELL_FREQ_OVERRIDE: index into
                                          *   g_freq_override_tables pool. 0xFF = unassigned. */
    uint8_t        _pad[3];             /*!< Alignment pad — do not use. */
} PhaseFrequency_t;

/*!
 * \brief   Complete Frequency Resolver shared memory region.
 *
 * \details Written by CM4 at boot and on topology changes. CM0+ reads it
 *          synchronously from the Frequency Resolver on every slot boundary.
 *          Placed in SRAM2 at a fixed linker-assigned address.
 *
 *          Per-phase struct is 20 bytes (override tables are split into
 *          a separate pool — see \c g_freq_override_tables).
 *          Total: 20 phases x 20 bytes = 400 bytes.
 */
typedef struct {
    PhaseFrequency_t phases[FREQ_MAX_PHASES];  /*!< Indexed by phase_index. */
} FrequencyResolverState_t;

/* =========================================================================
 * EmergencyAlertSlot_t
 * ========================================================================= */

#define DIAG_ALERT  0x04u  /*!< DiagType: CM0+ emergency alert — CM4 liveness failure. */

/*!
 * \brief   Single-entry slot written by CM0+ when CM4 heartbeat failures exceed
 *          \c CM4_HEARTBEAT_MISS_THRESHOLD consecutive unanswered MbMux signals.
 *
 * \details CM0+ populates this slot and triggers an isolated CM4 reset.  On the
 *          next \c Mesh_Uplink TX opportunity CM0+ assembles and transmits the
 *          alert before pulling from \c MeshUplinkQueue, then clears \c valid.
 *          CM4 clears \c valid = 0 early in its boot sequence so a stale slot
 *          from a previous reset cycle is never re-transmitted.
 *
 *          Layout (12 bytes, naturally aligned):
 *          \code
 *            offset  0 : valid           (uint8_t) — 1 = populated, 0 = empty
 *            offset  1 : diag_type       (uint8_t) — always DIAG_ALERT (0x04)
 *            offset  2 : _pad[2]
 *            offset  4 : node_id         (uint8_t) — provisioned local identity
 *            offset  5 : _pad2[3]
 *            offset  8 : rtc_timestamp_s (uint32_t) — RTC seconds at detection
 *          \endcode
 */
typedef struct {
    uint8_t  valid;            /*!< 1 = slot populated; CM0+ writes, CM4 clears on restart. */
    uint8_t  diag_type;        /*!< Always \ref DIAG_ALERT (0x04). */
    uint8_t  _pad[2];          /*!< Alignment pad — do not use. */
    uint8_t  node_id;          /*!< Provisioned local node identity. */
    uint8_t  _pad2[3];         /*!< Alignment pad — do not use. */
    uint32_t rtc_timestamp_s;  /*!< RTC seconds at CM4 liveness failure detection. */
} EmergencyAlertSlot_t;        /* 12 bytes */

/* =========================================================================
 * ComplianceStatus_t
 * ========================================================================= */

/*!
 * \brief   Compliance Engine result codes returned by
 *          \c ComplianceEngine_RequestChannel().
 */
typedef uint8_t ComplianceResult_t;

#define COMPLIANCE_GRANTED        0u  /*!< TX authorised; credit deducted. */
#define COMPLIANCE_RESTRICTED     1u  /*!< Insufficient duty-cycle credit. */
#define COMPLIANCE_POWER_TOO_HIGH 2u  /*!< TX power exceeds band limit. */
#define COMPLIANCE_BAND_UNKNOWN   3u  /*!< Frequency outside all declared Bands. */

/*!
 * \brief   Compliance Engine status written to shared memory after every
 *          \c ComplianceEngine_RequestChannel() call.
 *
 * \details CM0+ is the sole writer. CM4 reads opportunistically on every
 *          wake — no dedicated MbMux signal is needed, avoiding channel
 *          saturation for frequent compliance events.
 *
 * \remark  The original PRD estimated this struct at 12 bytes, predating
 *          the final field list. Actual natural size is 16 bytes (fields
 *          are ordered for optimal packing; explicit \c _pad[2] makes the
 *          trailing padding visible rather than implicit).
 */
typedef struct {
    uint32_t           last_freq_hz;        /*!< Frequency of the last RequestChannel call. */
    uint32_t           wait_ms;             /*!< Recovery time; valid when
                                              *  \c last_result == \ref COMPLIANCE_RESTRICTED. */
    uint16_t           skip_count_mesh;     /*!< Cumulative skipped TX slots on mesh link. */
    uint16_t           skip_count_cluster;  /*!< Cumulative skipped TX slots on cluster link. */
    ComplianceResult_t last_result;         /*!< Result of the last RequestChannel call. */
    uint8_t            band_unknown_count;  /*!< Incremented on \ref COMPLIANCE_BAND_UNKNOWN;
                                              *  non-zero triggers a DIAG_ALARM from CM4. */
    uint8_t            _pad[2];             /*!< Explicit alignment pad — do not use. */
} ComplianceStatus_t;                       /* 16 bytes */

/* =========================================================================
 * Global instances in shared SRAM2 (section "SHARED_APP")
 *
 * Defined in shared_mem.c; placed by both linker scripts at 0x20009000.
 * CM0+ zeros this region via SharedMem_Init() at startup.  CM4 writes
 * CM4-owned fields after releasing CM0+ and before the first TDMA slot.
 *
 * Expected symbol offsets within the SHARED_APP section
 * (natural alignment, declaration order — verify in the .map file):
 *
 *   Symbol                       Offset   Size   Owner
 *   g_alarm_b_request            +0x000   12 B   CM4 -> CM0+
 *   g_compliance_status          +0x00C   16 B   CM0+ -> CM4
 *   g_cm4_heartbeat              +0x01C    4 B   CM4 -> CM0+
 *   g_emergency_alert_slot       +0x020   12 B   CM0+ -> CM4
 *   g_freq_resolver_state        +0x02C  400 B   CM4 -> CM0+
 *   g_freq_override_tables       +0x1BC  640 B   CM4 -> CM0+
 *   g_core_crash_cm0                     108 B   CM0+ fault handler -> CM0+ BOOT
 *   g_core_crash_cm4                     108 B   CM4 fault handler -> CM4 BOOT
 *   g_reset_flags                          4 B   CM4 -> CM0+
 *   (total: about 1.3 KB; the rest remains reserved for future buffers)
 *
 * The last three survive a reset on purpose, and SharedMem_Init() leaves them
 * alone (#101): a crash record that boot zeroed would report nothing, and the
 * reset flags are written by the CM4 before it releases the CM0+. SRAM keeps
 * its content through every reset but a power-up, so each is checked before it
 * is believed (the record by its magic and check word).
 * ========================================================================= */

extern AlarmBRequest_t          g_alarm_b_request;      /*!< CM4 writes, CM0+ reads. */
extern ComplianceStatus_t       g_compliance_status;    /*!< CM0+ writes, CM4 reads. */
extern uint32_t                 g_cm4_heartbeat;        /*!< Incremented by CM4 on every wake. */
extern EmergencyAlertSlot_t     g_emergency_alert_slot; /*!< CM0+ populates on CM4 liveness failure. */
extern FrequencyResolverState_t g_freq_resolver_state;  /*!< CM4 writes at boot, CM0+ reads per slot. */
extern uint32_t g_freq_override_tables[FREQ_MAX_OVERRIDE_TABLES][FREQ_MAX_CELLS_PER_PHASE]; /*!< CM4 writes, CM0+ reads per OVERRIDE cell. */
extern CoreCrash_t              g_core_crash_cm0;       /*!< The CM0+ fault handler writes; the CM0+ reports it at its next BOOT and clears it. */
extern CoreCrash_t              g_core_crash_cm4;       /*!< The CM4 fault handler writes; the CM4 reports it at its next BOOT and clears it. */
extern uint32_t                 g_reset_flags;          /*!< RCC_CSR reset flags, read and cleared by the CM4 at every boot; the CM0+ prints them. */
#if defined(BENCH_FAULT_CM4) || defined(BENCH_FAULT_CM0)
extern uint32_t                 g_bench_fault_gate[2];  /*!< Build Override test only: [0] CM0+, [1] CM4 forced a fault, so the restart does not force it again. */
#endif

/*!
 * \brief   Zero-initialise the SHARED_APP region, bar the crash records and
 *          the reset flags, which are meant to survive a reset.
 *
 * \details Called ONCE by CM0+ at the start of \c SubGhzPhyTask_Init().
 *          Must run before any shared-memory read or write on either core.
 *          CM4 must NOT call this function — it would clobber CM4's writes.
 *
 * \note    Boot ordering: CM4 releases CM0+ → CM0+ startup + SharedMem_Init()
 *          → CM0+ main loop → CM4 writes \c g_freq_resolver_state and other
 *          CM4-owned fields.  CM4 writes happen before the first TDMA slot.
 */
void SharedMem_Init(void);

#endif /* SHARED_MEM_H */
