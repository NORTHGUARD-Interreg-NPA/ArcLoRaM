/*!
 * \file      stub_tdma_table_toa.c
 *
 * \brief     TDMA Table stub whose slots carry packets of different sizes, for
 *            the per-slot expected time on air of the Rx window (issue #38).
 *
 * \details   slot_active=2500, gap=500 everywhere, so every slot is 3000 ms.
 *            Phase 0, Mesh_Uplink, a header, two slots per cell, a footer:
 *              header          4 bytes   SCHEDULED
 *              cell slot 0    51 bytes
 *              cell slot 1    10 bytes
 *              footer         20 bytes   CONTENTION
 *            Phase 1, Sync: 3 cells of one slot, 10 bytes.
 *            The sizes are all different, so a slot that reads its neighbour's
 *            entry shows.
 */
#include "tdma_table.h"

static const uint8_t s_uplink_slot_lens[] = { 51u, 10u };
static const uint8_t s_sync_slot_lens[]   = { 10u };

static const Phase_t s_uplink = {
    .type             = PHASE_TYPE_MESH_UPLINK,
    .participant_mask = PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3,
    .direction_mode   = DIRECTION_MAC_CELL,
    .cell_count       = 1u,
    .slot_count       = 2u,
    .slot_active_ms   = 2500u,
    .gap_after_slot_ms = 500u,
    .slot_max_packet_len = s_uplink_slot_lens,
    .header           = { .duration_ms = 2500u, .kind = SLOT_SCHEDULED,
                          .gap_after_ms = 500u, .max_packet_len = 4u },
    .footer           = { .duration_ms = 2500u, .kind = SLOT_CONTENTION,
                          .gap_after_ms = 500u, .max_packet_len = 20u },
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
    &s_uplink,
    &s_sync,
};

#define TABLE_SIZE  ( (uint8_t)( sizeof(s_table) / sizeof(s_table[0]) ) )

static uint32_t s_phase_start_ms[TABLE_SIZE];

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
