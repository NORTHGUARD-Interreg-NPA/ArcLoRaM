/*!
 * \file      mac_state_machine_c2.c
 *
 * \brief     MAC State Machine — C2 (relay, mesh + cluster) implementation.
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
#include "mac_state_machine.h"
#include "date_bcd.h"
#include "sync_stamp.h"     /* SyncStamp_ErrorUs: the stamp in ticks, the error in us */
#include "tdma_table.h"
#include "guard_time_resolver.h"
#include "arclog.h"
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/* =========================================================================
 * Module state
 * ========================================================================= */

static MacState_t            s_mac_state;
static ClockState_t          s_clock_state;
static uint8_t               s_sync_consecutive;   /* consecutive good packets since P1 */
static uint32_t              s_last_sync_received_ms; /* wall-clock of last sync packet */
static uint32_t              s_sync_phase_epoch_ms; /* epoch for TX relay (from C3) */
static uint8_t               s_sync_phase_day;
static uint8_t               s_sync_phase_month;
static uint8_t               s_sync_phase_year;
static bool                  s_epoch_received_this_phase;
static uint8_t               s_hop_count;
static uint16_t              s_route_cost;
static uint8_t               s_beacon_tx_budget;
static uint8_t               s_sync_tx_remaining;
static CellEligibilityMask_t s_cell_elig_ul;
static CellEligibilityMask_t s_cell_elig_dl;
static PhaseTxFlag_t         s_phase_tx_flag;
static uint8_t               s_last_phase_idx;
static uint8_t               s_first_beacon;
static MAC_Hooks_t           s_hooks;

/* =========================================================================
 * Internal helpers
 * ========================================================================= */

static uint32_t sync_per_cell_ms_for_phase(uint8_t phase_idx)
{
    const Phase_t *p = TdmaTable_GetPhase(phase_idx);
    return (p != NULL && p->type == PHASE_TYPE_SYNC)
           ? (p->slot_active_ms + p->gap_after_slot_ms) : 0u;
}

static uint32_t u32_abs_diff(uint32_t a, uint32_t b)
{
    return (a >= b) ? (a - b) : (b - a);
}

/* Single transition points: every ClockState / MacState change is logged. */
static void set_clock_state(ClockState_t to, const char *why)
{
    if (to == s_clock_state) return;
    ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_L, "CLK", "from=%s to=%s why=%s",
           ArcLog_ClockName(s_clock_state), ArcLog_ClockName(to), why);
    s_clock_state = to;
}

static void set_mac_state(MacState_t to, const char *why)
{
    if (to == s_mac_state) return;
    ARCLOG(ARCLOG_MOD_MAC, VLEVEL_L, "MAC_ST", "from=%s to=%s why=%s",
           ArcLog_MacStateName(s_mac_state), ArcLog_MacStateName(to), why);
    s_mac_state = to;
}

/* One line per received Sync packet: the raw datum for drift analysis.
 * act = set (COLD RTC set) | good / bad (ACQUIRING check) | t1 / t2 / t3 (WARM tier). */
static void log_sync_rx(const SyncPayload_t *p, uint32_t stamp_ms,
                        uint32_t expected_ms, int32_t err_us, const char *act)
{
    ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_M, "SYNC_RX",
           "ph=%u ce=%u ep=%u st=%u exp=%u err=%d erru=%d clk=%s act=%s",
           (unsigned)p->sync_phase_index, (unsigned)p->sync_cell_index,
           (unsigned)p->ms_since_midnight_sync_phase,
           (unsigned)stamp_ms, (unsigned)expected_ms,
           (int)SyncStamp_UsToMs(err_us), (int)err_us,
           ArcLog_ClockName(s_clock_state), act);
}

/* Set the RTC so that the new domain reads the target time at the SyncStamp
 * instant. The hook runs from RxDone, about one airtime after the stamp, so
 * the time elapsed since the stamp is carried over; without it the new
 * domain would lag the sender by the airtime, while every later
 * stamp-to-expected comparison would still read zero error. Returns the
 * elapsed ms (0 when the stamp is older than SYNC_STAMP_MAX_AGE_MS, which
 * means it is unusable).
 *
 * The packet's date is the date of the phase epoch. The target time is the
 * epoch plus the cells before it plus the carry; when that sum reaches a
 * day, the target is on the next day and the date moves with it (issue #28).
 * The sum is taken here, before it is reduced to the day domain. */
static uint32_t rtc_set_at_stamp(const SyncPayload_t *p, uint32_t per_cell,
                                 uint32_t stamp_ms)
{
    uint32_t age_ms = 0u;
    if (s_hooks.get_rtc_snapshot != NULL) {
        uint32_t now_ms = 0u;
        uint8_t  d, mo, y;
        s_hooks.get_rtc_snapshot(&now_ms, &d, &mo, &y);
        int32_t age = DayMs_Diff(now_ms, stamp_ms);
        age_ms = (age >= 0 && (uint32_t)age <= SYNC_STAMP_MAX_AGE_MS) ? (uint32_t)age : 0u;
    }
    if (s_hooks.rtc_set != NULL) {
        uint32_t target_ms = p->ms_since_midnight_sync_phase
                             + (uint32_t)p->sync_cell_index * per_cell
                             + age_ms;
        uint8_t day = p->day, month = p->month, year = p->year;
        if (target_ms >= MS_PER_DAY) {
            DateBcd_AddDays(&day, &month, &year, 1);
        }
        s_hooks.rtc_set(target_ms % MS_PER_DAY, day, month, year);
    }
    return age_ms;
}

static void trigger_sync_lost(const char *why)
{
    set_clock_state(CLOCK_COLD, why);
    set_mac_state(MAC_STATE_SCANNING, why);
    s_sync_consecutive = 0u;
    s_epoch_received_this_phase = false;
    if (s_hooks.sync_lost != NULL) s_hooks.sync_lost();
}

/* =========================================================================
 * Public API
 * ========================================================================= */

void MAC_Init(const MAC_Hooks_t *hooks)
{
    s_mac_state                  = MAC_STATE_SCANNING;
    s_clock_state                = CLOCK_COLD;
    s_sync_consecutive           = 0u;
    s_last_sync_received_ms      = 0u;
    s_sync_phase_epoch_ms        = 0u;
    s_sync_phase_day             = 0u;
    s_sync_phase_month           = 0u;
    s_sync_phase_year            = 0u;
    s_epoch_received_this_phase  = false;
    s_hop_count                  = 0u;
    s_route_cost                 = 0u;
    s_beacon_tx_budget           = 0u;
    s_sync_tx_remaining          = SYNC_TX_BUDGET;
    s_cell_elig_ul               = 0x00u;
    s_cell_elig_dl               = 0x00u;
    s_phase_tx_flag              = 0u;
    s_last_phase_idx             = 0xFFu;
    s_first_beacon               = 1u;
    if (hooks != NULL) {
        s_hooks = *hooks;
    } else {
        s_hooks = (MAC_Hooks_t){0};
    }
    ARCLOG(ARCLOG_MOD_MAC, VLEVEL_L, "MAC_INIT", "cls=C2 st=%s clk=%s",
           ArcLog_MacStateName(s_mac_state), ArcLog_ClockName(s_clock_state));
}

SlotDecision_t MAC_OnSlotOpportunity(const FrameCursor_t *cursor,
                                      const Phase_t       *phase,
                                      uint32_t             slot_start_ms)
{
    (void)slot_start_ms;
    if (s_mac_state == MAC_STATE_SCANNING) {
        return SLOT_RX;
    }

    /* Detect phase entry */
    if (cursor->phase_index != s_last_phase_idx) {
        s_last_phase_idx            = cursor->phase_index;
        s_epoch_received_this_phase = false;
        s_sync_tx_remaining         = SYNC_TX_BUDGET;
    }

    switch (phase->direction_mode) {

    case DIRECTION_MAC_PHASE:
        /* Sync phase: cell 0 always RX; cells 1+ TX only if epoch received */
        if (cursor->cell_index == 0u) {
            return SLOT_RX;
        }
        return s_epoch_received_this_phase ? SLOT_TX : SLOT_RX;

    case DIRECTION_CELL_SKIP: {
        uint8_t residue = (uint8_t)(cursor->cell_index % 3u);
        if (!((s_cell_elig_ul >> residue) & 1u)) {
            return SLOT_SKIP;
        }
        return (residue == (s_hop_count % 3u)) ? SLOT_TX : SLOT_RX;
    }

    case DIRECTION_MAC_CELL: {
        if (phase->type == PHASE_TYPE_SYNC) {
            /* Sync reactive model: cell 0 always RX; cells 1+ TX only if
             * epoch received in cell 0 this occurrence, gated by
             * SYNC_TX_BUDGET to limit radio-on time. */
            if (cursor->cell_index == 0u) {
                return SLOT_RX;
            }
            if (!s_epoch_received_this_phase) {
                return SLOT_RX;
            }
            if (s_sync_tx_remaining == 0u) {
                return SLOT_RX;
            }
            s_sync_tx_remaining--;
            return SLOT_TX;
        }
        /* Mesh_Beacon: Tx in the next K cells after beacon reception
         * (budget counts down from K; no absolute cell-index constraint) */
        if (s_beacon_tx_budget > 0u) {
            s_beacon_tx_budget--;
            return SLOT_TX;
        }
        return SLOT_RX;
    }

    default:
        return SLOT_RX;
    }
}

void MAC_OnSyncPacketReceivedTicks(const SyncPayload_t *payload,
                                  uint32_t             stamp_ticks)
{
    /* The stamp is kept in RTC ticks; the milliseconds below (the silence
     * timer, the age carry of a set, the Tier 2 hook) are its floor. The
     * error is taken in microseconds from the ticks, against the exact
     * schedule ms, and every tier is judged on it (issue #82). */
    uint32_t stamp_ms = RtcTicks_ToMs(stamp_ticks);

    //duration of a cell in the sync phase, used to compute expected arrival time of the packet
    uint32_t per_cell = sync_per_cell_ms_for_phase(payload->sync_phase_index);

    /* A phase index that is not a Sync phase cannot anchor the FrameCursor
     * (TdmaMachine_BootstrapFromSync would have no phase to start from):
     * corrupt or foreign packet. It is not evidence of sync either. */
    if (per_cell == 0u) {
        ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_L, "SYNC_REJ", "ph=%u ce=%u",
               (unsigned)payload->sync_phase_index,
               (unsigned)payload->sync_cell_index);
        return;
    }

    /* Any received sync packet (any tier) resets the silence timer. */
    s_last_sync_received_ms = stamp_ms;

    /* ---- Packet 1: cold RTC set ----------------------------------------- */
    if (s_clock_state == CLOCK_COLD) {
        uint32_t target_ms = (payload->ms_since_midnight_sync_phase
                              + (uint32_t)payload->sync_cell_index * per_cell)
                             % MS_PER_DAY; //the sender claimed nominal start of cell time

        /* The new RTC domain reads target_ms at the stamp instant. */
        (void)rtc_set_at_stamp(payload, per_cell, stamp_ms);

        uint32_t rtc_now = 0u;
        if (s_hooks.get_rtc_snapshot != NULL) {
            uint8_t d, mo, y;
            s_hooks.get_rtc_snapshot(&rtc_now, &d, &mo, &y);  /* new domain */
        }
        log_sync_rx(payload, stamp_ms, target_ms,
                    SyncStamp_ErrorUs(stamp_ticks, target_ms), "set");
        s_sync_consecutive = 0u;
        set_clock_state(CLOCK_ACQUIRING, "rtc_set");
        s_last_sync_received_ms = rtc_now;  /* new RTC domain after re-anchor */

        if (s_hooks.sync_bootstrapped != NULL) {
            s_hooks.sync_bootstrapped(payload->sync_phase_index,
                                       payload->sync_cell_index,
                                       target_ms);
        }
        return;
    }

    /* ---- Packets 2+: consecutive lock check ----------------------------- */
    if (s_clock_state == CLOCK_ACQUIRING) {
        /* Packet 1 put the RTC on the sender's timeline, so every packet,
         * from any later Sync phase occurrence, is checked against its own
         * epoch (the same expected arrival as CLOCK_WARM). */
        uint32_t expected_arrival = (payload->ms_since_midnight_sync_phase
                                     + (uint32_t)payload->sync_cell_index * per_cell)
                                    % MS_PER_DAY;
        int32_t  err_us      = SyncStamp_ErrorUs(stamp_ticks, expected_arrival);
        uint32_t clock_error = (uint32_t)((err_us < 0) ? -err_us : err_us);   /* us */
        bool     good        = (clock_error < SYNC_PARTICIPATE_THRESHOLD_MS * 1000u);

        log_sync_rx(payload, stamp_ms, expected_arrival, err_us, good ? "good" : "bad");
        if (good) {
            if (s_hooks.sync_sample != NULL) {
                s_hooks.sync_sample(err_us);
            }
            s_sync_consecutive++;
            if (s_sync_consecutive >= 2u) {
                set_clock_state(CLOCK_WARM, "lock");
                set_mac_state(MAC_STATE_SYNCHRONIZED, "lock");
                if (s_hooks.sync_locked != NULL) s_hooks.sync_locked();
            }
        } else {
            /* This packet disagrees with the RTC set from Packet 1, and
             * either may be the wrong one: re-acquire from the next packet
             * instead of judging every later packet against a Packet 1
             * that may itself be off. */
            trigger_sync_lost("acq_bad");
        }
        return;
    }

    /* ---- CLOCK_WARM: three-tier per-occurrence dispatch ----------------- */
    if (s_clock_state == CLOCK_WARM) {
        uint32_t expected_arrival = (payload->ms_since_midnight_sync_phase
                                     + (uint32_t)payload->sync_cell_index * per_cell)
                                    % MS_PER_DAY;
        int32_t  err_us = SyncStamp_ErrorUs(stamp_ticks, expected_arrival);
        uint32_t error  = (uint32_t)((err_us < 0) ? -err_us : err_us);        /* us */

        log_sync_rx(payload, stamp_ms, expected_arrival, err_us,
                    (error < SYNC_PARTICIPATE_THRESHOLD_MS * 1000u) ? "t1"
                    : (error < SYNC_RESYNC_THRESHOLD_MS * 1000u)    ? "t2" : "t3");

        if (error < SYNC_RESYNC_THRESHOLD_MS * 1000u && s_hooks.sync_sample != NULL) {
            s_hooks.sync_sample(err_us);
        }

        /* Phase correction, from SYNC_CORRECT_THRESHOLD_MS up to the resync
         * threshold: SSR-only, after the sample, which must see the error
         * before the shift it causes. Below it the clock is left alone, so as
         * not to chase the noise of the stamp; the offset that remains is
         * bounded by that threshold and is part of the Rx guard. */
        if (error >= SYNC_CORRECT_THRESHOLD_MS * 1000u
            && error <  SYNC_RESYNC_THRESHOLD_MS * 1000u
            && s_hooks.rtc_align_subsecond != NULL) {
            s_hooks.rtc_align_subsecond(err_us);
        }

        if (error < SYNC_PARTICIPATE_THRESHOLD_MS * 1000u) {
            /* Tier 1: participate — store epoch for cells 1+ relay */
            s_sync_phase_epoch_ms       = payload->ms_since_midnight_sync_phase;
            s_sync_phase_day            = payload->day;
            s_sync_phase_month          = payload->month;
            s_sync_phase_year           = payload->year;
            s_epoch_received_this_phase = true;

        } else if (error < SYNC_RESYNC_THRESHOLD_MS * 1000u) {
            /* Tier 2: corrected above; do not relay this occurrence. */

        } else {
            /* Tier 3: drift ≥ MAX_GUARD_TIME_MS — full re-anchor via rtc_set */
            (void)rtc_set_at_stamp(payload, per_cell, stamp_ms);
            trigger_sync_lost("tier3");
        }
    }
}

void MAC_OnSyncPacketReceived(const SyncPayload_t *payload,
                              uint32_t             stamp_ms)
{
    MAC_OnSyncPacketReceivedTicks(payload, SyncStamp_MsToTicks(stamp_ms));
}

void MAC_OnBeaconReceived(const BeaconPayload_t *beacon)
{
    if (beacon == NULL) return;

    uint8_t  new_hop  = (uint8_t)(beacon->hop_count + 1u);
    uint16_t new_cost = beacon->route_cost;

    int structural_change =
        s_first_beacon ||
        (new_hop != s_hop_count) ||
        (u32_abs_diff((uint32_t)new_cost, (uint32_t)s_route_cost) > ROUTE_COST_CHANGE_THRESHOLD);

    if (structural_change) {
        s_beacon_tx_budget = BEACON_K_TX_CELLS;
        s_first_beacon     = 0u;
    }

    s_hop_count  = new_hop;
    s_route_cost = new_cost;

    s_cell_elig_ul = (uint8_t)((1u << (s_hop_count % 3u))
                              | (1u << ((s_hop_count + 1u) % 3u)));
    s_cell_elig_dl = (uint8_t)((1u << ((s_hop_count + 2u) % 3u))
                              | (1u << ((s_hop_count + 1u) % 3u)));

    if (s_mac_state == MAC_STATE_SYNCHRONIZED) {
        set_mac_state(MAC_STATE_PAIRED, "beacon");
    }
}

void MAC_OnExternalSyncAcquired(const FrameEpoch_t *epoch)
{
    (void)epoch;
}

void MAC_CheckSyncTimeout(uint32_t rtc_now_ms)
{
    if (s_clock_state == CLOCK_WARM || s_clock_state == CLOCK_ACQUIRING) {
        if (DayMs_Diff(rtc_now_ms, s_last_sync_received_ms)
                >= (int32_t)SYNC_SILENCE_TIMEOUT_MS) {
            ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_L, "SYNC_SILENCE", "last=%u now=%u",
                   (unsigned)s_last_sync_received_ms, (unsigned)rtc_now_ms);
            trigger_sync_lost("silence");
        }
    }
}

void MAC_OnCursorSuspect(void)
{
    /* The TDMA cursor no longer matches the RTC, so the schedule position is
     * unknown: drop to re-acquisition. The RTC is left untouched; the next
     * received Sync packet sets it through the CLOCK_COLD path. */
    if (s_clock_state == CLOCK_COLD) return;
    trigger_sync_lost("suspect");
}

MacState_t            MAC_GetState(void)                        { return s_mac_state;           }
ClockState_t          MAC_GetClockState(void)                   { return s_clock_state;         }
CellEligibilityMask_t MAC_GetCellEligibilityMask_Uplink(void)   { return s_cell_elig_ul;        }
CellEligibilityMask_t MAC_GetCellEligibilityMask_Downlink(void) { return s_cell_elig_dl;        }
PhaseTxFlag_t         MAC_GetPhaseTxFlag(void)                  { return s_phase_tx_flag;       }
bool                  MAC_GetEpochReceivedThisPhase(void)        { return s_epoch_received_this_phase; }
uint8_t               MAC_GetHopCount(void)                      { return s_hop_count; }
uint8_t               MAC_GetBeaconTxBudget(void)                 { return s_beacon_tx_budget; }
uint8_t               MAC_GetSyncTxBudget(void)                   { return s_sync_tx_remaining; }
uint32_t              MAC_GetSyncPhaseEpochMs(void)             { return s_sync_phase_epoch_ms; }
uint32_t              MAC_GetLastSyncMs(void)                   { return s_last_sync_received_ms; }
void MAC_GetSyncPhaseDate(uint8_t *day, uint8_t *month, uint8_t *year)
{
    if (day)   *day   = s_sync_phase_day;
    if (month) *month = s_sync_phase_month;
    if (year)  *year  = s_sync_phase_year;
}
