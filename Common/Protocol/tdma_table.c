/*!
 * \file      tdma_table.c
 *
 * \brief     TDMA Table implementation — Sync-only stub table for initial
 *            synchronisation integration testing.
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
#include "tdma_table.h"
#include "sync_profile.h"   /* SYNC_CELL_GAP_MS, SYNC_TX_BUDGET */

/* =========================================================================
 * Two-Sync-phase frame
 *
 * Two Sync Phases, each with ten cells. C3 transmits in the first
 * SYNC_TX_BUDGET cells and skips the rest, which spaces its packets out; a
 * cold C1/C2 needs three packets (one RTC set, two good) to go from
 * CLOCK_COLD to CLOCK_WARM, so with one packet per phase it takes three
 * phases. The cell gap and the budget come from the Sync profile
 * (sync_profile.h): how often the network synchronises is a compile-time
 * choice between a product schedule, a frequent one for development inside
 * the duty-cycle budget, and the bring-up table of the host tests.
 * The second Sync phase exercises the multi-Sync-phase advance path:
 * after the last cell of Sync0 the cursor moves to Sync1 (same frame),
 * not to the first phase of the next frame.
 *
 * Replace with the full frame layout once all Phase Types are finalised.
 * ========================================================================= */
static const Phase_t s_sync_phase_0 = {
    .type             = PHASE_TYPE_SYNC,
    .participant_mask = PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3,
    .direction_mode   = DIRECTION_MAC_CELL,
    .cell_count       = SYNC_PHASE_CELLS,
    .slot_count       = 1u,
    .slot_active_ms   = 2500u,    /* SF12/BW125 Sync packet airtime (conservative) */
    .gap_after_slot_ms = SYNC_CELL_GAP_MS,  /* Inter-cell guard gap */
    .header           = { .duration_ms = 0u },
    .footer           = { .duration_ms = 0u },
};

static const Phase_t s_sync_phase_1 = {
    .type             = PHASE_TYPE_SYNC,
    .participant_mask = PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3,
    .direction_mode   = DIRECTION_MAC_CELL,
    .cell_count       = SYNC_PHASE_CELLS,
    .slot_count       = 1u,
    .slot_active_ms   = 2500u,    /* SF12/BW125 Sync packet airtime (conservative) */
    .gap_after_slot_ms = SYNC_CELL_GAP_MS,  /* Inter-cell guard gap */
    .header           = { .duration_ms = 0u },
    .footer           = { .duration_ms = 0u },
};

static const Phase_t * const s_table[] = {
    &s_sync_phase_0,
    &s_sync_phase_1,
};

#define TABLE_SIZE  ( (uint8_t)( sizeof(s_table) / sizeof(s_table[0]) ) )

/* Phase start offsets, computed by TdmaTable_Init at boot. */
static uint32_t s_phase_start_ms[TABLE_SIZE];

/* =========================================================================
 * Accessor implementations
 * ========================================================================= */

void TdmaTable_Init( void )
{
    s_phase_start_ms[0] = 0u;
    for ( uint8_t i = 1u; i < TABLE_SIZE; i++ ) {
        s_phase_start_ms[i] = s_phase_start_ms[i - 1u]
            + TdmaTable_PhaseDuration_ms( s_table[i - 1u] );
    }
}

const Phase_t *TdmaTable_GetPhase( uint8_t phase_index )
{
    if ( phase_index >= TABLE_SIZE ) {
        return NULL;
    }
    return s_table[phase_index];
}

uint8_t TdmaTable_PhaseCount( void )
{
    return TABLE_SIZE;
}

uint32_t TdmaTable_PhaseStartOffset_ms( uint8_t phase_index )
{
    if ( phase_index >= TABLE_SIZE ) {
        return 0u;
    }
    return s_phase_start_ms[phase_index];
}
