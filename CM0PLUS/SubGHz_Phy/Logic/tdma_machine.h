/*!
 * \file      tdma_machine.h
 *
 * \brief     TDMA Machine — alarm-chain timing engine on CM0+. Drives the
 *            slot-by-slot execution loop by integrating the TDMA Table,
 *            Frequency Resolver, MAC State Machine, and Compliance Engine.
 *            All hardware interactions are injected through \ref TdmaPlatform_t.
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
#ifndef TDMA_MACHINE_H
#define TDMA_MACHINE_H

#include <stdint.h>
#include <stdbool.h>
#include "protocol_types.h"
#include "guard_time_resolver.h"
#include "channel_plan.h"      /* RADIO_TX_POWER_DBM */

/* =========================================================================
 * Constants
 * ========================================================================= */

#ifndef TX_POWER_DBM
/*!
 * TX power passed to \ref ComplianceEngine_RequestChannel on every TX slot.
 *
 * \remark It is the power the radio is set to (\ref RADIO_TX_POWER_DBM, 14 dBm
 *         unless a Build Override raises it on the 869.5 MHz channel), so the engine
 *         judges what is really transmitted: it must not exceed the band's
 *         \c max_tx_power_dbm (14 dBm in 865 - 868.6 MHz, 27 dBm in 869.4 - 869.65 MHz).
 *         Full TX power management is deferred. Should probably lie inside the MAC State Machine eventually, but for now this is a convenient global constant.
 */
#define TX_POWER_DBM  RADIO_TX_POWER_DBM
#endif

#ifndef TX_LEAD_MS
/*!
 * How early a node wakes before the nominal start of a slot it will
 * transmit in (ADR-0016).
 *
 * \details Every scheduled packet starts on air exactly at its slot's nominal
 *          start. The lead covers the variable work before the radio fires:
 *          wake from Stop2, slot task, logs, compliance, payload build. The
 *          radio's own start-up is in the platform's Tx ramp. The node then waits for the nominal start minus the
 *          platform's \c tx_ramp_ms and fires. A slot task that reaches the
 *          fire instant late logs \c TX_LATE (and drops a Sync packet).
 *          Sender-local: receivers never use it. Bench worst case 13 ms from
 *          wake to on-air, cell 0 of the Sync phase (2026-09-26).
 *
 * \remark Must not exceed \ref MAX_GUARD_TIME_MS: gaps are sized for wakes up
 *         to that early (every gap >= 2 x \ref MAX_GUARD_TIME_MS).
 */
#define TX_LEAD_MS  20u
#endif

/* =========================================================================
 * Platform abstraction
 * ========================================================================= */

/*!
 * \brief   Struct of hardware function pointers (and the radio's Tx ramp)
 *          injected at \ref TdmaMachine_Init.
 *
 * \details All hardware interactions go through this struct, making
 *          \c tdma_machine.c completely free of HAL, radio-driver, and
 *          UTIL_* includes.  In production, construct one in
 *          \c app_subghz_phy.c pointing to real HAL wrappers.  In unit
 *          tests, construct one pointing to stubs.
 */
typedef struct {
    /*! Read the RTC time as a millisecond offset. */
    uint32_t (*GetRtcMs)(void);

    /*! Program RTC Alarm A to fire at the given absolute millisecond value. */
    void     (*ProgramAlarmA)(uint32_t abs_rtc_ms);

    /*! Disarm RTC Alarm A: the alarm chain stops (node enters scanning). */
    void     (*CancelAlarmA)(void);

    /*! Tune the radio to the specified frequency in Hz. */
    void     (*RadioSetChannel)(uint32_t freq_hz);

    /*!
     * Transmit \c len bytes from \c buf. The packet starts on air
     * \c tx_ramp_ms after the call.
     */
    void     (*RadioSend)(const uint8_t *buf, uint8_t len);

    /*!
     * Open a bounded receive window (synced slot).
     *
     * \details A packet whose preamble starts within \c start_window_ms from
     *          now is received in full: the radio's window timer stops on
     *          preamble detection, so the platform adds the preamble
     *          detection time to \c start_window_ms. Any reception still
     *          running \c cap_ms from now is aborted (false preamble
     *          detection, or a packet that started too late to fit).
     *          Both values are > 0; the TDMA Machine never opens an
     *          unbounded window here (see \c RadioScan).
     */
    void     (*RadioSetRx)(uint32_t start_window_ms, uint32_t cap_ms);

    /*!
     * Listen with no timeout until a reception ends (RxDone, CRC or header
     * error). Used while scanning; the TDMA Machine re-arms it from
     * \ref TdmaMachine_OnRxEnd after every reception.
     */
    void     (*RadioScan)(void);

    /*! Put the radio into low-power sleep mode. */
    void     (*RadioSleep)(void);

    /*!
     * Return the time-on-air in milliseconds of a \c len -byte packet with
     * the current modem configuration (driver formula, Radio.TimeOnAir).
     * Called after \c RadioSend to refund the difference between the
     * conservative pre-TX estimate and the real airtime to
     * \ref ComplianceEngine_ReportTxDone, and to place the end of each Rx
     * window (latest packet start).
     */
    uint32_t (*RadioTimeOnAir)(uint8_t len);

    /*!
     * Block until the RTC reaches the given absolute millisecond value.
     *
     * \details Holds every transmission until its fire instant, the nominal
     *          slot start minus \c tx_ramp_ms. The node woke up to
     *          \ref TX_LEAD_MS early, or up to one guard early when the slot
     *          was predicted Rx. In production, a busy-wait on \c GetRtcMs
     *          (the resolution is one RTC tick, 1/4096 s). In unit tests,
     *          advance the simulated RTC.
     *
     * \param abs_rtc_ms  Absolute RTC time to wait until (ms-since-midnight).
     */
    void     (*WaitUntilMs)(uint32_t abs_rtc_ms);

    /*!
     * Time from the \c RadioSend call to the packet's first preamble symbol
     * on air, in ms (radio wake-up, oscillator start-up, PLL lock, PA ramp).
     * A property of the radio and its driver, measured on the bench as
     * \c TX_DONE \c start minus \c SYNC_TX \c send.
     */
    uint32_t tx_ramp_ms;
} TdmaPlatform_t;

/* =========================================================================
 * Public API
 * ========================================================================= */

/*!
 * \brief   Inject platform dependencies and reset the FrameCursor to
 *          \c {0,0,0}.
 *
 * \details Call once before the UTIL_SEQ scheduler starts.  The platform
 *          struct is copied internally — the caller's struct need not remain
 *          valid after the call.  Nothing runs until \ref TdmaMachine_Start.
 *
 * \param   [in] platform - Pointer to an initialised \ref TdmaPlatform_t.
 *                          Must not be NULL.
 */
void TdmaMachine_Init(const TdmaPlatform_t *platform);

/*!
 * \brief   Start radio activity at boot, after \ref MAC_Init.
 *
 * \details The alarm chain only runs once the node is out of
 *          \c CLOCK_COLD. A C1/C2 node boots cold: it enters scanning
 *          (continuous Rx on \ref SCAN_FREQ_HZ, no alarm) and the chain is
 *          started by \ref TdmaMachine_BootstrapFromSync on the first Sync
 *          packet. C3 is never cold: its chain starts now, with the first
 *          slot starting \ref TX_LEAD_MS from now so that a first Tx slot
 *          has its full lead. It first sets cell 0's channel, so the radio's
 *          one-off image calibration is paid at boot and not in that lead.
 *
 * \retval  true   The alarm chain is running: the caller must schedule the
 *                 first \ref TdmaMachine_SlotTask now.
 * \retval  false  The node is scanning.
 */
bool TdmaMachine_Start(void);

/*!
 * \brief   Execute one slot of the TDMA alarm-chain loop.
 *
 * \details Registered as \c CFG_SEQ_Task_TdmaSlotWake; called by UTIL_SEQ
 *          on each RTC Alarm A wake.  Reads the TDMA Table, queries the
 *          Frequency Resolver and MAC State Machine, checks compliance,
 *          drives the appropriate radio action, advances the FrameCursor,
 *          and programs the next Alarm A before returning. If the node has
 *          fallen back to \c CLOCK_COLD (silence timeout, suspect cursor),
 *          it stops the chain and enters scanning instead.
 */
void TdmaMachine_SlotTask(void);

/*!
 * \brief   Notify the end of a reception (RxDone, Rx timeout, CRC or
 *          header error, or the platform's safety cap).
 *
 * \details Call after the MAC has processed the received packet, if any.
 *          While the node is \c CLOCK_COLD it (re-)arms scanning, stopping
 *          the alarm chain first if a Tier 3 re-anchor just dropped the
 *          clock. Otherwise the slot's reception is over and the radio is
 *          put to sleep until the next slot.
 */
void TdmaMachine_OnRxEnd(void);

/*!
 * \brief   Return a snapshot of the current FrameCursor position.
 *
 * \retval  \ref FrameCursor_t Live cursor (copy; not a pointer).
 */
FrameCursor_t TdmaMachine_GetCursor(void);

/*!
 * \brief   Re-anchor the FrameCursor and alarm chain after Sync acquisition.
 *
 * \details Called by the \c sync_bootstrapped MAC hook (Packet 1). Sets the
 *          cursor to the received cell, advances one step to the next cell,
 *          and programs RTC Alarm A for that next cell start. This is where
 *          the alarm chain starts on a node leaving \c CLOCK_COLD. After this
 *          call the normal \ref TdmaMachine_SlotTask loop runs from the
 *          correct position without missing any cells.
 *
 * \param   sync_phase_idx     Phase index from \c SyncPayload_t.sync_phase_index.
 * \param   sync_cell_idx      Cell index from \c SyncPayload_t.sync_cell_index.
 * \param   nominal_start_ms   Schedule-derived nominal start of the received
 *                              cell (ms-since-midnight):
 *                              \c ms_since_midnight_sync_phase +
 *                              sync_cell_idx * per_cell_ms. This is the same
 *                              value passed to \c rtc_set. Used as the base
 *                              for alarm programming so that all nodes wake at
 *                              the same absolute slot boundary, not at
 *                              \c target_ms + per-node execution latency.
 */
void TdmaMachine_BootstrapFromSync(uint8_t  sync_phase_idx,
                                    uint8_t  sync_cell_idx,
                                    uint32_t nominal_start_ms);

/*!
 * \brief   Return \c true if the RTC integrity checkpoint detected an
 *          implausible delta on the most recent \ref TdmaMachine_SlotTask
 *          entry.
 *
 * \details The flag is set when the actual RTC time deviates from the
 *          expected wake time by more than 1.5× the current
 *          \c slot_active_ms.  It is cleared on the next successful
 *          (plausible) SlotTask entry.
 *
 * \retval  bool \c true = cursor suspect; \c false = timing normal.
 */
bool TdmaMachine_IsCursorSuspect(void);

#endif /* TDMA_MACHINE_H */
