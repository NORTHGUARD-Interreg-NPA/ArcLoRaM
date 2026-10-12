/*!
 * \file      stub_tdma_table_2sync.c
 *
 * \brief     Two-Sync-phase TDMA Table stub for bootstrap tests.
 *
 * \details   Provides a minimal two-phase frame (Sync0 + Sync1) that exposes
 *            the multi-Sync-phase edge case: after the last cell of Sync0 the
 *            alarm must point to Sync1's first cell (same frame), NOT to the
 *            first Sync phase of the next frame.
 *
 *            Key values (slot_active=2500, gap=500, cell_count=3 per phase):
 *              per_cell_ms   = 3000
 *              Sync0 occupies ms  0 – 8999  (cells at 0, 3000, 6000)
 *              Sync1 occupies ms  9000–17999 (cells at 9000, 12000, 15000)
 *              frame_duration = 18000 ms
 *
 *            Discriminating alarm values after bootstrap at last cell of Sync0
 *            (cell=2, slot_start=6000):
 *              Correct  (one step forward):     9000
 *              Wrong    (Frame Epoch + frame):  0 + 18000 = 18000
 *              Wrong    (slot_start + frame):   6000 + 18000 = 24000
 */
#include "tdma_table.h"

static const uint8_t s_sync_slot_lens[]   = { (uint8_t)sizeof( SyncPayload_t ) };

static const Phase_t s_sync0 = {
    .type             = PHASE_TYPE_SYNC,
    .participant_mask = PARTICIPANT_C2 | PARTICIPANT_C3,
    .direction_mode   = DIRECTION_MAC_CELL,
    .cell_count       = 3u,
    .slot_count       = 1u,
    .slot_active_ms   = 2500u,
    .gap_after_slot_ms = 500u,
    .slot_max_packet_len = s_sync_slot_lens,
    .header           = { .duration_ms = 0u },
    .footer           = { .duration_ms = 0u },
};

static const Phase_t s_sync1 = {
    .type             = PHASE_TYPE_SYNC,
    .participant_mask = PARTICIPANT_C2 | PARTICIPANT_C3,
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
    &s_sync0,
    &s_sync1,
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
