/*!
 * \file      stub_tdma_table_beacon.c
 *
 * \brief     Mesh_Beacon + Sync TDMA Table stub for Tx timing tests on a
 *            non-Sync phase.
 *
 * \details   The frame opens with a Mesh_Beacon phase, in which C3 always
 *            transmits, followed by a Sync phase.
 *
 *            Key values (slot_active=2500, gap=500, cell_count=3 per phase):
 *              per_cell_ms   = 3000
 *              Beacon occupies ms  0 – 8999  (cells at 0, 3000, 6000)
 *              Sync   occupies ms  9000–17999 (cells at 9000, 12000, 15000)
 *              frame_duration = 18000 ms
 */
#include "tdma_table.h"

static const uint8_t s_sync_slot_lens[]   = { (uint8_t)sizeof( SyncPayload_t ) };
static const uint8_t s_beacon_slot_lens[] = { (uint8_t)sizeof( BeaconPayload_t ) };

static const Phase_t s_beacon = {
    .type             = PHASE_TYPE_MESH_BEACON,
    .participant_mask = PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3,
    .direction_mode   = DIRECTION_MAC_CELL,
    .cell_count       = 3u,
    .slot_count       = 1u,
    .slot_active_ms   = 2500u,
    .gap_after_slot_ms = 500u,
    .slot_max_packet_len = s_beacon_slot_lens,
    .header           = { .duration_ms = 0u },
    .footer           = { .duration_ms = 0u },
};

static const Phase_t s_sync = {
    .type             = PHASE_TYPE_SYNC,
    .participant_mask = PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3,
    .direction_mode   = DIRECTION_MAC_CELL,
    .cell_count       = 3u,
    .slot_count       = 1u,
    .slot_active_ms   = 2500u,
    .gap_after_slot_ms = 500u,
    .slot_max_packet_len = s_sync_slot_lens,
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
