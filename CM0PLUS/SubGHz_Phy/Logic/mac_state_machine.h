/*!
 * \file      mac_state_machine.h
 *
 * \brief     MAC State Machine — per-opportunity decision layer on CM0+.
 *            Called synchronously by the TDMA Machine and by radio receive
 *            callbacks. Owns state transitions, three-packet Sync acquisition,
 *            CellEligibilityMask computation, phase_tx_flag management, and
 *            BeaconTxBudget gating.
 *
 * \details   Three separate compilation units implement this interface:
 *            \c mac_state_machine_c1.c (end node),
 *            \c mac_state_machine_c2.c (relay), and
 *            \c mac_state_machine_c3.c (gateway / SyncAnchor).
 *            Exactly one is linked per firmware image.
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
#ifndef MAC_STATE_MACHINE_H
#define MAC_STATE_MACHINE_H

#include <stdint.h>
#include <stdbool.h>
#include "protocol_types.h"
#include "sync_profile.h"     /* SYNC_TX_BUDGET, SYNC_SILENCE_TIMEOUT_MS: the Sync profile */
#include "mac_types.h"
#include "day_ms.h"          /* MS_PER_DAY, DayMs_* */

/* =========================================================================
 * Constants
 * ========================================================================= */

#ifndef SYNC_LOCK_THRESHOLD_MS
/*!
 * \brief   CT drift budget for the deferred pure CT sync model.
 *
 * \details 0.5 × T_S at SF12/BW125 ≈ 16 ms, where T_S = 2^SF / BW is the LoRa
 *          symbol duration. This is the maximum inter-node drift tolerance
 *          for the pure CT model, providing ~6× margin against the 3 × T_S
 *          preamble-locking failure ceiling (~100 ms). See Liao et al., IEEE
 *          Access 2017, and CONTEXT.md — CT Sync Propagation Model.
 *
 *          Not used by the current three-tier relay implementation, which
 *          uses \ref SYNC_PARTICIPATE_THRESHOLD_MS for acquisition confirmation
 *          and the SYNC_LOCKED signal. Will govern the acquisition threshold
 *          when the pure CT model is implemented.
 */
#define SYNC_LOCK_THRESHOLD_MS  16u
#endif

#ifndef BEACON_K_TX_CELLS
/*!
 * Number of cells per Mesh_Beacon phase in which this node transmits its
 * own BeaconPayload (when BeaconTxBudget allows).
 *
 * \remark Deterministic selection: the first K cells (indices 0…K−1) are
 *         the TX candidates. Randomisation is deferred pending empirical
 *         validation (see CONTEXT.md — BeaconTxBudget).
 */
#define BEACON_K_TX_CELLS  2u
#endif

/* SYNC_TX_BUDGET: the maximum Sync transmissions per Sync Phase occurrence
 * (C2 and C3), defined by the Sync profile (sync_profile.h).
 *
 * C2: after this many SLOT_TX returns in Sync cells 1+, the MAC returns
 * SLOT_RX for the remaining cells in the occurrence. C3: transmits in the
 * first SYNC_TX_BUDGET cells, then returns SLOT_SKIP for the rest. One
 * transmission is one repetition. Reset to this value at each Sync Phase
 * entry. C1 is unaffected (always Rx in Sync). */

#ifndef ROUTE_COST_CHANGE_THRESHOLD
/*!
 * Minimum absolute route_cost delta that counts as a structural routing
 * change and triggers a BeaconTxBudget reset to 2.
 */
#define ROUTE_COST_CHANGE_THRESHOLD  100u
#endif

#ifndef SYNC_STAMP_MAX_AGE_MS
/*!
 * \brief   Maximum plausible age of a SyncStamp when the Sync packet is
 *          processed.
 *
 * \details The MAC processes a Sync packet at RxDone, about one airtime
 *          (~1 s at SF12) after its SyncStamp (the packet start), and carries
 *          that elapsed time into \c rtc_set. An older stamp cannot come from the packet
 *          being processed, so it is ignored (no elapsed-time carry).
 */
#define SYNC_STAMP_MAX_AGE_MS  5000u
#endif

/* SYNC_SILENCE_TIMEOUT_MS is defined by the Sync profile (sync_profile.h).
 *
 * Wall-clock silence timeout for ClockState degradation. When no Sync packet
 * of any tier is received for this duration,
 * the node degrades \ref ClockState to \ref CLOCK_COLD and fires
 * \c sync_lost. Measured in wall-clock time (RTC), not slot counts, to
 * decouple degradation from TDMA table structure. Any received Sync packet
 * (Tier 1, 2, or 3) resets the timer. See ADR-0013. It must outlast the
 * packets the profile tolerates losing: 15 min for the frequent profiles,
 * three periods (27 min) for PROD, so that one lost packet is not a COLD. */

/* =========================================================================
 * Injected hook struct
 * ========================================================================= */

/*!
 * \brief   Hardware and notification hooks injected at \ref MAC_Init.
 *
 * \details Any hook may be NULL; the MAC silently skips the call in that
 *          case. In production set each pointer to the real CM0+ function.
 *          In unit tests inject stub counters.
 */
typedef struct {
    /*!
     * Packet 1 hook — set the RTC from a pre-computed binary target time.
     *
     * \param target_ms  ms-since-midnight of the current cell's nominal start:
     *                   ms_since_midnight_sync_phase + sync_cell_index × per_cell_ms.
     *                   Caller decomposes: H = ms/3600000, M = (ms%3600000)/60000,
     *                   S = (ms%60000)/1000; calls HAL_RTC_SetTime(BIN) + SetDate(BCD).
     * \param day        BCD day 01–31 (pass-through from SyncPayload).
     * \param month      BCD month 01–12.
     * \param year       BCD year 00–99 (years since 2000).
     */
    void (*rtc_set)(uint32_t target_ms,
                    uint8_t  day, uint8_t month, uint8_t year);

    /*!
     * Phase correction hook — CLOCK_WARM,
     * SYNC_CORRECT_THRESHOLD_MS ≤ error < SYNC_RESYNC_THRESHOLD_MS.
     *
     * \details Applies HAL_RTCEx_SetSynchroShift without a full calendar
     *          re-anchor. Must not block. Called only from
     *          \ref MAC_OnSyncPacketReceivedTicks. Below
     *          SYNC_PARTICIPATE_THRESHOLD_MS the packet is still relayed;
     *          from there up, relay is suppressed for this sync occurrence.
     *
     * \param err_us  SyncStamp - expected arrival, microseconds, signed (a
     *                positive error means the local clock is ahead and is
     *                delayed), as logged in SYNC_RX `erru`: a correction near
     *                1 ms must not be lost to a whole-ms rounding.
     */
    void (*rtc_align_subsecond)(int32_t err_us);

    /*!
     * Atomic snapshot of the current RTC time and date.
     *
     * \details Called at Sync Phase entry (C3) to capture the date of the
     *          outgoing SyncPayload, and immediately after \c rtc_set
     *          (C2 Packet 1 path) to read the new RTC domain. A single call
     *          avoids the race where two separate reads straddle midnight.
     *
     * \param[out] ms    GetTimerTicks() — ms since midnight in new RTC domain.
     * \param[out] day   BCD day 01–31.
     * \param[out] month BCD month 01–12.
     * \param[out] year  BCD year 00–99.
     */
    void (*get_rtc_snapshot)(uint32_t *ms,
                              uint8_t  *day, uint8_t *month, uint8_t *year);

    /*!
     * Packet 1 hook — re-anchors the TDMA cursor and alarm chain.
     *
     * \param sync_phase_idx   Phase index from \c SyncPayload.sync_phase_index.
     * \param sync_cell_idx    Cell index from \c SyncPayload.sync_cell_index.
     * \param nominal_start_ms  Schedule-derived nominal start of the received
     *                          cell: \c ms_since_midnight_sync_phase +
     *                          sync_cell_index * per_cell_ms. This is the same
     *                          value passed to \c rtc_set. The hook must use
     *                          this value (not a hardware readback) as the base
     *                          for alarm programming so that all nodes wake at
     *                          the same absolute slot boundary, not at
     *                          \c target_ms + per-node execution latency.
     */
    void (*sync_bootstrapped)(uint8_t  sync_phase_idx,
                               uint8_t  sync_cell_idx,
                               uint32_t nominal_start_ms);

    /*! Fired when ClockState transitions to CLOCK_WARM (2 consecutive good packets). */
    void (*sync_locked)(void);

    /*! Fired when ClockState degrades back to CLOCK_COLD. */
    void (*sync_lost)(void);

    /*!
     * Drift sample hook (C1, C2): one per Sync packet whose error is a
     * usable measure of the local clock's rate (issue #34).
     *
     * \details Called for the ACQUIRING packets judged good and for the
     *          WARM packets of Tier 1 and Tier 2, never for Packet 1 (the
     *          set), a bad ACQUIRING packet or Tier 3. It runs after the
     *          packet is logged and, for Tier 2, before
     *          \c rtc_align_subsecond, so the error is the one measured
     *          before the shift it causes.
     *
     * \param err_us  SyncStamp - expected arrival, microseconds, signed, as
     *                logged in SYNC_RX (`erru`).
     */
    void (*sync_sample)(int32_t err_us);
} MAC_Hooks_t;

/* =========================================================================
 * Public API
 * ========================================================================= */

/*!
 * \brief   Inject hooks and reset all MAC state to boot defaults.
 *
 * \details C1/C2: MacState = Scanning, ClockState = CLOCK_COLD.
 *          C3: MacState = Active (SyncAnchor — no acquisition needed).
 *          Call once at CM0+ startup before any slot opportunities fire.
 *
 * \param   [in] hooks - Pointer to hook struct. May be NULL (all hooks
 *                       silently skipped). Struct need not remain valid
 *                       after the call — values are copied internally.
 */
void MAC_Init(const MAC_Hooks_t *hooks);

/*!
 * \brief   TDMA Machine entry point — decide what to do at a slot opportunity.
 *
 * \details Called once per slot by the TDMA Machine after it wakes and
 *          determines this node participates in the current Phase.  The MAC
 *          writes \ref PhaseTxFlag_t at Sync phase entry and decrements
 *          \ref BeaconTxBudget on Beacon TX cells.
 *
 * \param   [in] cursor - Live TDMA position (phase_index, cell_index,
 *                        slot_index).
 * \param   [in] phase  - Phase descriptor for the current slot. Passed
 *                        directly by the TDMA Machine; the MAC must not
 *                        look it up again from the TDMA Table.
 * \param   [in] slot_start_ms - Nominal start of the current slot
 *                        (ms-since-midnight): the instant a packet sent in
 *                        this slot starts on air. C3 derives the Sync Phase
 *                        Epoch from it; C1/C2 ignore it.
 *
 * \retval  \ref SlotDecision_t Radio action:
 *          \ref SLOT_TX, \ref SLOT_RX, or \ref SLOT_SKIP.
 */
SlotDecision_t MAC_OnSlotOpportunity(const FrameCursor_t *cursor,
                                      const Phase_t       *phase,
                                      uint32_t             slot_start_ms);

/*!
 * \brief   Called by the CM0+ RxDone wrapper when a Sync packet is decoded.
 *
 * \details Drives the three-packet ClockState acquisition state machine
 *          (C1/C2). C3 ignores this call (it is the SyncAnchor).
 *
 * \param   [in] payload              - Decoded SyncPayload from the received
 *                                      packet.
 * \param   [in] stamp_ms  - SyncStamp: the packet's start on air in the
 *                          RTC domain, RxDone IRQ time minus the airtime
 *                          (see CONTEXT.md - SyncStamp).
 */
void MAC_OnSyncPacketReceived(const SyncPayload_t *payload,
                               uint32_t             stamp_ms);

/*!
 * \brief   A Sync packet was received; the SyncStamp is in RTC ticks (issue #82).
 *
 * \details The packet start on air in day ticks (1/4096 s), \ref
 *          SyncStamp_FromRxDone. The error against the expected arrival is
 *          taken in microseconds (\ref SyncStamp_ErrorUs), logged as `erru`
 *          beside `err` (ms, rounded), and every tier is judged on it. The
 *          millisecond entry point above is this one with the ms converted to
 *          the nearest tick.
 */
void MAC_OnSyncPacketReceivedTicks(const SyncPayload_t *payload,
                                   uint32_t             stamp_ticks);

/*!
 * \brief   Check whether the sync silence timeout has expired.
 *
 * \details Called from the TDMA slot wake path on each wake. If no Sync
 *          packet of any tier has been received for \ref SYNC_SILENCE_TIMEOUT_MS
 *          and \ref ClockState is \ref CLOCK_WARM or \ref CLOCK_ACQUIRING,
 *          degrades to \ref CLOCK_COLD and fires \c sync_lost.
 *          \ref CLOCK_COLD is a no-op (already degraded). C3 is always a
 *          no-op (always \ref CLOCK_WARM, never degrades). See ADR-0013.
 *
 * \param   [in] rtc_now_ms - Current RTC time in ms-since-midnight.
 */
void MAC_CheckSyncTimeout(uint32_t rtc_now_ms);

/*!
 * \brief   Called by the TDMA Machine when a slot wake lands far from the
 *          expected wake time (cursor integrity checkpoint).
 *
 * \details The FrameCursor can no longer be trusted, so C1/C2 drop to
 *          \ref CLOCK_COLD / \ref MAC_STATE_SCANNING and fire \c sync_lost.
 *          The RTC is not written: the next received Sync packet re-anchors
 *          it through the normal \ref CLOCK_COLD path. No-op when already
 *          \ref CLOCK_COLD. C3 (SyncAnchor) ignores it.
 */
void MAC_OnCursorSuspect(void);

/*!
 * \brief   Called by the CM0+ RxDone wrapper when a Beacon packet is decoded.
 *
 * \details Updates CellEligibilityMask (uplink and downlink formulas),
 *          evaluates BeaconTxBudget trigger, applies the anti-circular
 *          route filter, and transitions to Paired on first valid beacon.
 *
 * \param   [in] beacon - Decoded BeaconPayload from the received packet.
 */
void MAC_OnBeaconReceived(const BeaconPayload_t *beacon);

/*!
 * \brief   GPS / external sync acquisition hook — stub body only.
 *
 * \details Reserved for a future path where an external GPS provides the
 *          Frame Epoch directly, bypassing the three-packet Sync acquisition.
 *          Body is empty; the function must compile and link.
 *
 * \param   [in] epoch - Frame Epoch from the external time source.
 */
void MAC_OnExternalSyncAcquired(const FrameEpoch_t *epoch);

/*!
 * \brief   Return the current MAC operational state.
 *
 * \retval  \ref MacState_t Current state.
 */
MacState_t MAC_GetState(void);

/*!
 * \brief   Return the current RTC synchronisation quality.
 *
 * \retval  \ref ClockState_t Current clock state.
 */
ClockState_t MAC_GetClockState(void);

/*!
 * \brief   Return the uplink CellEligibilityMask last written by the MAC.
 *
 * \details Valid after the first \ref MAC_OnBeaconReceived call.
 *          Default 0x00 (skip all cells) before any beacon is received.
 *
 * \retval  \ref CellEligibilityMask_t Uplink mask.
 */
CellEligibilityMask_t MAC_GetCellEligibilityMask_Uplink(void);

/*!
 * \brief   Return the downlink CellEligibilityMask last written by the MAC.
 *
 * \retval  \ref CellEligibilityMask_t Downlink mask.
 */
CellEligibilityMask_t MAC_GetCellEligibilityMask_Downlink(void);

/*!
 * \brief   Return the phase_tx_flag last written by the MAC.
 *
 * \details C3: always 1. C1: always 0. C2: not pre-written (reactive model).
 *
 * \retval  \ref PhaseTxFlag_t Current flag value.
 */
PhaseTxFlag_t MAC_GetPhaseTxFlag(void);

/*!
 * \brief   Return whether the MAC has received a valid sync epoch in the
 *          current Sync Phase occurrence.
 *
 * \details C2: true after a Tier 1 packet (error below
 *          \ref SYNC_PARTICIPATE_THRESHOLD_MS) is received; reset to false
 *          at Sync phase entry. C1 and C3: always false (C1 never relays,
 *          C3 originates).
 *
 * \retval  bool true if epoch received this phase; false otherwise.
 */
bool MAC_GetEpochReceivedThisPhase(void);

/*!
 * \brief   Return the node's current mesh hop count.
 *
 * \details Set to \c peer.hop_count + 1 on first valid beacon reception.
 *          Default 0 before any beacon is received. Used by the TDMA Machine
 *          for guard-time look-ahead in \ref DIRECTION_CELL_SKIP phases.
 *
 * \retval  uint8_t Hop count (0 = no beacon received yet).
 */
uint8_t MAC_GetHopCount(void);

/*!
 * \brief   Return the remaining BeaconTxBudget.
 *
 * \details C2: counts down from \ref BEACON_K_TX_CELLS on each Tx; reset to K
 *          when a structural beacon change is received. C1 and C3: always 0
 *          (C1 never transmits beacons, C3 bypasses the budget). Used by the
 *          TDMA Machine for guard-time look-ahead in Mesh_Beacon phases.
 *
 * \retval  uint8_t Remaining Tx budget.
 */
uint8_t MAC_GetBeaconTxBudget(void);

/*!
 * \brief   Return the remaining sync TX budget for the current Sync Phase
 *          occurrence.
 *
 * \details C2: counts down from \ref SYNC_TX_BUDGET on each sync relay TX;
 *          reset to \ref SYNC_TX_BUDGET at Sync phase entry. C3: counts
 *          down from \ref SYNC_TX_BUDGET on each sync TX; reset at phase
 *          entry. C1: always 0 (never relays). Used by the TDMA Machine
 *          for state-aware guard-time look-ahead in Sync phases.
 *
 * \retval  uint8_t Remaining sync TX budget.
 */
uint8_t MAC_GetSyncTxBudget(void);

/*!
 * \brief   Return the Sync Phase Epoch captured for TX relay.
 *
 * \details C3: the nominal start of the Sync Phase occurrence, from the
 *          schedule (not an RTC reading), set at Sync Phase entry.
 *          C2: \c ms_since_midnight_sync_phase received in Cell 0 (Tier 1 only).
 *          Written into the outgoing \c SyncPayload_t.ms_since_midnight_sync_phase.
 *
 * \retval  uint32_t ms_since_midnight_sync_phase for the current occurrence.
 */
uint32_t MAC_GetSyncPhaseEpochMs(void);

/*!
 * \brief   Return the instant of the last received Sync packet, RTC ms of the
 *          day.
 *
 * \details C1 and C2: the stamp of the latest Sync packet of any tier, or the
 *          RTC reading after the set when it was Packet 1: the instant the
 *          silence timer counts from. The Guard Time Resolver measures the
 *          drift the guard has to absorb from it (issue #36). C3: always 0, it
 *          has no estimate and its guard is the cap.
 *
 * \retval  uint32_t RTC ms of the day of the last Sync packet.
 */
uint32_t MAC_GetLastSyncMs(void);

/*!
 * \brief   Return the BCD date captured at Sync Phase entry.
 *
 * \details Populated by \c get_rtc_snapshot at phase entry (C3) or by the
 *          received SyncPayload date fields (C2 relay).
 *
 * \param[out] day   BCD day 01–31.
 * \param[out] month BCD month 01–12.
 * \param[out] year  BCD year 00–99.
 */
void MAC_GetSyncPhaseDate(uint8_t *day, uint8_t *month, uint8_t *year);

#endif /* MAC_STATE_MACHINE_H */
