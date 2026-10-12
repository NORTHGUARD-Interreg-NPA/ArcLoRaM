/*!
 * \file      freq_resolver.h
 *
 * \brief     Frequency Resolver — maps a FrameCursor + SlotPosition to a
 *            frequency in Hz by reading FrequencyResolverState shared memory.
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
#ifndef FREQ_RESOLVER_H
#define FREQ_RESOLVER_H

#include <stdint.h>
#include "protocol_types.h"
#include "shared_mem.h"
#include "channel_plan.h"   /* FREQ_DEFAULT_HZ: the channel of every phase */

#ifndef FREQ_FALLBACK_HZ
/*!
 * Safe fallback frequency returned when a \ref CELL_FREQ_STATIC phase has not
 * yet been populated by CM4.
 *
 * \remark The default channel CM4 writes into every phase at boot
 *         (\ref FREQ_DEFAULT_HZ, 868.3 MHz unless a Build Override moves it).
 *         The TDMA Machine must never call \c Radio.SetChannel(0); this
 *         constant ensures a valid channel is used even before the first CM4
 *         write completes.
 */
#define FREQ_FALLBACK_HZ  FREQ_DEFAULT_HZ
#endif

#ifndef SCAN_FREQ_HZ
/*!
 * Discovery channel: C1/C2 listen here continuously while \c CLOCK_COLD,
 * before the FrameCursor (and so any per-cell frequency) is known.
 *
 * \remark Must match the frequency the SyncAnchor transmits Sync packets
 *         on: the default channel CM4 writes into every phase at boot
 *         (\ref FREQ_DEFAULT_HZ).
 */
#define SCAN_FREQ_HZ  FREQ_DEFAULT_HZ
#endif

/*!
 * \brief   Inject the shared-memory region the Frequency Resolver reads from.
 *
 * \details On the real device call once at boot with the SRAM2 symbol address.
 *          In host tests call with a stack-allocated FrequencyResolverState_t.
 *          Also resets any internal resolver state.
 *
 * \param[in] state  Pointer to the FrequencyResolverState_t region. Must remain
 *                   valid for the lifetime of all subsequent GetFreq calls.
 */
void FrequencyResolver_Init(const FrequencyResolverState_t *state);

/*!
 * \brief   Return the frequency in Hz for the current slot.
 *
 * \details Header and footer positions bypass cell_mode and return their
 *          dedicated fields directly. Cell positions dispatch on cell_mode.
 *          Any field equal to 0 means the slot is absent — returns 0; the
 *          caller must not invoke Radio.SetChannel(0).
 *
 * \param[in] cursor  Live position of the TDMA Machine within the TDMA Table.
 *                    The \c slot_pos field selects header / cell / footer.
 *
 * \return  Frequency in Hz, or 0 if the slot is absent or state is not set.
 */
uint32_t FrequencyResolver_GetFreq(const FrameCursor_t *cursor);

#endif /* FREQ_RESOLVER_H */
