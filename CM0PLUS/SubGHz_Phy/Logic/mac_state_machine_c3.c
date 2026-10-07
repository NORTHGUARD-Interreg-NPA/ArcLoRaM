/*!
 * \file      mac_state_machine_c3.c
 *
 * \brief     MAC State Machine — C3 (gateway / SyncAnchor) implementation.
 *
 * \details   C3 boots directly into \ref MAC_STATE_ACTIVE — it requires no
 *            Sync acquisition. It is the sole epoch authority for the network:
 *            at each Sync Phase entry it sets the epoch, the phase's nominal
 *            start, that all C2 relay nodes will forward verbatim.
 *            BeaconTxBudget is bypassed; phase_tx_flag is always 1.
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
#include "tdma_table.h"
#include "arclog.h"
#include <stddef.h>
#include <stdint.h>

/* =========================================================================
 * Module state
 * ========================================================================= */

static MacState_t            s_mac_state;
static uint8_t               s_hop_count;
static CellEligibilityMask_t s_cell_elig_ul;
static CellEligibilityMask_t s_cell_elig_dl;
static PhaseTxFlag_t         s_phase_tx_flag;
static uint8_t               s_last_phase_idx;
static uint8_t               s_sync_tx_remaining;
static bool                  s_boot_burst_pending;   /* the first Sync phase after boot sends the burst */
static uint32_t              s_sync_phase_epoch_ms;
static uint8_t               s_sync_phase_day;
static uint8_t               s_sync_phase_month;
static uint8_t               s_sync_phase_year;
static MAC_Hooks_t           s_hooks;

/* =========================================================================
 * Public API
 * ========================================================================= */

void MAC_Init(const MAC_Hooks_t *hooks)
{
    s_mac_state            = MAC_STATE_ACTIVE;
    s_hop_count            = 0u;
    s_cell_elig_ul         = 0x00u;
    s_cell_elig_dl         = 0x00u;
    s_phase_tx_flag        = 1u;
    s_last_phase_idx       = 0xFFu;
    s_sync_tx_remaining    = SYNC_TX_BUDGET;
    s_boot_burst_pending   = true;
    s_sync_phase_epoch_ms  = 0u;
    s_sync_phase_day       = 0u;
    s_sync_phase_month     = 0u;
    s_sync_phase_year      = 0u;
    if (hooks != NULL) {
        s_hooks = *hooks;
    } else {
        s_hooks = (MAC_Hooks_t){0};
    }
    ARCLOG(ARCLOG_MOD_MAC, VLEVEL_L, "MAC_INIT", "cls=C3 st=%s clk=%s",
           ArcLog_MacStateName(s_mac_state), ArcLog_ClockName(CLOCK_WARM));
}

SlotDecision_t MAC_OnSlotOpportunity(const FrameCursor_t *cursor,
                                      const Phase_t       *phase,
                                      uint32_t             slot_start_ms)
{
    /* Detect phase entry — set the epoch at Sync Phase start */
    if (cursor->phase_index != s_last_phase_idx) {
        s_last_phase_idx = cursor->phase_index;
        if (phase->type == PHASE_TYPE_SYNC) {
            s_phase_tx_flag = 1u;
            s_sync_tx_remaining = SYNC_TX_BUDGET;
            if (s_boot_burst_pending) {
                /* The first Sync phase after boot: the burst (an option, 1 =
                 * none), so that boards booted with the C3 lock within it. */
                s_boot_burst_pending = false;
                if (SYNC_BOOT_BURST > SYNC_TX_BUDGET) {
                    s_sync_tx_remaining = SYNC_BOOT_BURST;
                }
            }
            /* The epoch is the phase's nominal start from the schedule, the
             * instant cell 0's packet starts on air: an RTC reading here is
             * taken a Tx lead early plus the wake latency. The RTC gives the
             * date only. */
            uint32_t per_cell = (uint32_t)phase->slot_active_ms
                                + phase->gap_after_slot_ms;
            s_sync_phase_epoch_ms = (slot_start_ms + MS_PER_DAY
                                     - (uint32_t)cursor->cell_index * per_cell)
                                    % MS_PER_DAY;
            if (s_hooks.get_rtc_snapshot != NULL) {
                uint32_t now_ms;
                s_hooks.get_rtc_snapshot(&now_ms,
                                          &s_sync_phase_day,
                                          &s_sync_phase_month,
                                          &s_sync_phase_year);
                /* The packets carry the date of the epoch. The RTC read is
                 * on the other side of midnight when the epoch is a Tx lead
                 * after it, or when the phase is entered mid-way after it
                 * (issue #28). */
                int64_t epoch_day_pos = (int64_t)now_ms
                                        + DayMs_Diff(s_sync_phase_epoch_ms, now_ms);
                if (epoch_day_pos >= (int64_t)MS_PER_DAY) {
                    DateBcd_AddDays(&s_sync_phase_day, &s_sync_phase_month,
                                    &s_sync_phase_year, 1);
                } else if (epoch_day_pos < 0) {
                    DateBcd_AddDays(&s_sync_phase_day, &s_sync_phase_month,
                                    &s_sync_phase_year, -1);
                }
            }
            ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_M, "SYNC_EPOCH", "ph=%u ep=%u",
                   (unsigned)cursor->phase_index,
                   (unsigned)s_sync_phase_epoch_ms);
        }
    }

    switch (phase->direction_mode) {

    case DIRECTION_MAC_PHASE:
        return SLOT_TX;  /* C3: always TX every cell of Sync phase */

    case DIRECTION_MAC_CELL:
        if (phase->type == PHASE_TYPE_SYNC) {
            /* C3 originates sync in the first SYNC_TX_BUDGET cells,
             * then skips the rest (already sent enough for relay coverage). */
            if (s_sync_tx_remaining == 0u) {
                return SLOT_SKIP;
            }
            s_sync_tx_remaining--;
            return SLOT_TX;
        }
        /* Mesh_Beacon: C3 always Tx (bypasses BeaconTxBudget) */
        return SLOT_TX;

    case DIRECTION_CELL_SKIP: {
        uint8_t  eff_hop = 3u;
        uint8_t  residue = (uint8_t)(cursor->cell_index % 3u);
        uint8_t  mask    = (uint8_t)((1u << (eff_hop % 3u))
                                    | (1u << ((eff_hop + 1u) % 3u)));
        if (!((mask >> residue) & 1u)) return SLOT_SKIP;
        return (residue == (eff_hop % 3u)) ? SLOT_TX : SLOT_RX;
    }

    default:
        return SLOT_RX;
    }
}

void MAC_OnSyncPacketReceived(const SyncPayload_t *payload,
                               uint32_t             stamp_ms)
{
    (void)payload;
    (void)stamp_ms;
}

void MAC_OnSyncPacketReceivedTicks(const SyncPayload_t *payload,
                                   uint32_t             stamp_ticks)
{
    (void)payload;
    (void)stamp_ticks;
}

void MAC_OnBeaconReceived(const BeaconPayload_t *beacon)
{
    if (beacon == NULL) return;
    s_hop_count    = (uint8_t)(beacon->hop_count + 1u);
    s_cell_elig_ul = (uint8_t)((1u << (s_hop_count % 3u))
                              | (1u << ((s_hop_count + 1u) % 3u)));
    s_cell_elig_dl = (uint8_t)((1u << ((s_hop_count + 2u) % 3u))
                              | (1u << ((s_hop_count + 1u) % 3u)));
}

void MAC_OnExternalSyncAcquired(const FrameEpoch_t *epoch)
{
    (void)epoch;
}

void MAC_CheckSyncTimeout(uint32_t rtc_now_ms)
{
    (void)rtc_now_ms;
}

void MAC_OnCursorSuspect(void)
{
    /* SyncAnchor: the RTC is the time authority, there is nothing to
     * re-acquire. The TDMA Machine logs the suspect wake itself. */
}

MacState_t            MAC_GetState(void)                        { return s_mac_state;          }
ClockState_t          MAC_GetClockState(void)                   { return CLOCK_WARM;           }
CellEligibilityMask_t MAC_GetCellEligibilityMask_Uplink(void)   { return s_cell_elig_ul;       }
CellEligibilityMask_t MAC_GetCellEligibilityMask_Downlink(void) { return s_cell_elig_dl;       }
PhaseTxFlag_t         MAC_GetPhaseTxFlag(void)                  { return s_phase_tx_flag;      }
bool                  MAC_GetEpochReceivedThisPhase(void)        { return false;               }
uint8_t               MAC_GetHopCount(void)                      { return s_hop_count;        }
uint8_t               MAC_GetBeaconTxBudget(void)                 { return 0u;                }
uint8_t               MAC_GetSyncTxBudget(void)                   { return s_sync_tx_remaining; }
uint32_t              MAC_GetSyncPhaseEpochMs(void)             { return s_sync_phase_epoch_ms; }
uint32_t              MAC_GetLastSyncMs(void)                   { return 0u; }  /* the time reference receives no Sync */
void MAC_GetSyncPhaseDate(uint8_t *day, uint8_t *month, uint8_t *year)
{
    if (day)   *day   = s_sync_phase_day;
    if (month) *month = s_sync_phase_month;
    if (year)  *year  = s_sync_phase_year;
}
