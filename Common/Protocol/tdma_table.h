/*!
 * \file      tdma_table.h
 *
 * \brief     TDMA Table accessor — read-only, node-agnostic schedule
 *            descriptor for one Frame.
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
#ifndef TDMA_TABLE_H
#define TDMA_TABLE_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "protocol_types.h"

/* =========================================================================
 * Accessor interface
 *
 * The TDMA Table is a const array in flash. These functions are the
 * only interface the TDMA Machine uses — it never touches the underlying
 * array directly, keeping the table swappable at link time.
 * ========================================================================= */

/*!
 * \brief   Compute phase start offsets from the phase definitions.
 *
 * \details Call once at boot, before \c TdmaMachine_Init, so that
 *          \ref TdmaTable_PhaseStartOffset_ms returns correct values.
 *          Eliminates the need for a hand-calculated parallel array.
 */
void TdmaTable_Init( void );

/*!
 * \brief   Return a pointer to the Phase at the given index.
 *
 * \param   [in]  phase_index - Zero-based index into the TDMA Table.
 *
 * \retval  const \ref Phase_t* Pointer to the Phase descriptor, or
 *          \c NULL if \c phase_index >= \ref TdmaTable_PhaseCount().
 */
const Phase_t *TdmaTable_GetPhase( uint8_t phase_index );

/*!
 * \brief   Return the total number of phases in the current table.
 *
 * \retval  uint8_t Number of phases (always >= 1 for a valid table).
 */
uint8_t TdmaTable_PhaseCount( void );

/*!
 * \brief   Compute the total duration in milliseconds of a single phase.
 *
 * \details Sums the header (if present), all cells, and the footer
 *          (if present):
 *          - Header: \c header.duration_ms + \c header.gap_after_ms
 *          - Cells: \c cell_count * per_cell, where
 *            \c per_cell = \c slot_count * (\c slot_active_ms +
 *            \c gap_after_slot_ms)
 *          - Footer: \c footer.duration_ms + \c footer.gap_after_ms
 *
 *          Absent anchors (duration_ms == 0) contribute nothing; their
 *          gap_after_ms is ignored.
 *
 * \param   [in]  p - Phase descriptor. Must not be NULL.
 *
 * \retval  uint32_t Total phase duration in milliseconds.
 */
static inline uint32_t TdmaTable_PhaseDuration_ms( const Phase_t *p )
{
    uint32_t d = 0u;

    if ( p->header.duration_ms > 0u ) {
        d += p->header.duration_ms + p->header.gap_after_ms;
    }

    uint32_t per_cell = (uint32_t)p->slot_count
                      * ( p->slot_active_ms + p->gap_after_slot_ms );
    d += (uint32_t)p->cell_count * per_cell;

    if ( p->footer.duration_ms > 0u ) {
        d += p->footer.duration_ms + p->footer.gap_after_ms;
    }

    return d;
}

/*!
 * \brief   Return the largest packet, in bytes on air, that a slot can carry.
 *
 * \details The Rx window of a slot closes at the latest instant that packet
 *          can start and still end in time, so the TDMA Machine derives the
 *          time on air from this length instead of assuming one packet type.
 *
 * \param   [in]  p          - Phase descriptor. Must not be NULL.
 * \param   [in]  pos        - Header, cell or footer slot of the Phase.
 * \param   [in]  slot_index - Slot within the Cell (cell slots only).
 *
 * \retval  uint8_t Length in bytes on air. 0 when the slot declares none: a
 *          Phase with no \c slot_max_packet_len array, or a \c slot_index past
 *          \c slot_count. The host test of the real table forbids 0 for every
 *          slot a Phase has.
 */
static inline uint8_t TdmaTable_MaxPacketLen( const Phase_t *p,
                                              SlotPosition_t pos,
                                              uint8_t slot_index )
{
    if ( pos == SLOT_POS_HEADER ) {
        return p->header.max_packet_len;
    }
    if ( pos == SLOT_POS_FOOTER ) {
        return p->footer.max_packet_len;
    }
    if ( p->slot_max_packet_len == NULL || slot_index >= p->slot_count ) {
        return 0u;
    }
    return p->slot_max_packet_len[slot_index];
}

/*!
 * \brief   Return the precomputed millisecond offset of a Phase from the
 *          start of the Frame.
 *
 * \details Used by the TDMA Machine to jump the \ref FrameCursor_t directly
 *          to the next active Phase during long inter-phase sleep gaps,
 *          avoiding slot-by-slot iteration through excluded Phases.
 *          Offsets are computed by \ref TdmaTable_Init at boot.
 *
 * \param   [in]  phase_index - Zero-based index into the TDMA Table.
 *
 * \retval  uint32_t Millisecond offset from Frame start, or 0 if
 *          \c phase_index >= \ref TdmaTable_PhaseCount().
 */
uint32_t TdmaTable_PhaseStartOffset_ms( uint8_t phase_index );

#endif /* TDMA_TABLE_H */
