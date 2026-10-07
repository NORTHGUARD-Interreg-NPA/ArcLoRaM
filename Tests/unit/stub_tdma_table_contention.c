/*!
 * \file      stub_tdma_table_contention.c
 *
 * \brief     Mesh_Beacon (one cell and a CONTENTION footer) + Sync TDMA Table
 *            stub, for the Rx guard outside the Sync phase (issue #36).
 *
 * \details   Key values (slot_active=2500, gap=500):
 *              phase 0, Mesh_Beacon: cell 0 at ms 0, footer slot at ms 3000
 *                (contention: its packet starts after CSMA and backoff, not at
 *                the nominal start)
 *              phase 1, Sync: 3 cells, per_cell_ms = 3000
 */
#include "tdma_table.h"

static const Phase_t s_beacon = {
    .type             = PHASE_TYPE_MESH_BEACON,
    .participant_mask = PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3,
    .direction_mode   = DIRECTION_MAC_CELL,
    .cell_count       = 1u,
    .slot_count       = 1u,
    .slot_active_ms   = 2500u,
    .gap_after_slot_ms = 500u,
    .header           = { .duration_ms = 0u },
    .footer           = { .duration_ms = 2500u, .kind = SLOT_CONTENTION, .gap_after_ms = 500u },
};

static const Phase_t s_sync = {
    .type             = PHASE_TYPE_SYNC,
    .participant_mask = PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3,
    .direction_mode   = DIRECTION_MAC_CELL,
    .cell_count       = 3u,
    .slot_count       = 1u,
    .slot_active_ms   = 2500u,
    .gap_after_slot_ms = 500u,
    .header           = { .duration_ms = 0u },
    .footer           = { .duration_ms = 0u },
};

static const Phase_t * const s_table[] = {
    &s_beacon,
    &s_sync,
};

#define TABLE_SIZE  ( (uint8_t)( sizeof(s_table) / sizeof(s_table[0]) ) )

static uint32_t s_phase_start_ms[2];

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
