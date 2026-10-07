/*!
 * \file      guard_time_resolver.c
 *
 * \brief     Guard Time Resolver implementation.
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
#include "guard_time_resolver.h"
#include "guard_policy.h"
#include "day_ms.h"              /* DayMs_Diff */
#include "mac_state_machine.h"   /* MAC_GetClockState, MAC_GetLastSyncMs */
#include "protocol_types.h"      /* NODE_CLASS_C3 */

#ifndef NODE_CLASS
#  error "NODE_CLASS must be defined at build level (-DNODE_CLASS=NODE_CLASS_CX)"
#endif

uint32_t GuardTimeResolver_GetGuardMs(uint32_t slot_start_ms, bool sync_link)
{
#if NODE_CLASS == NODE_CLASS_C3
    (void)slot_start_ms;
    (void)sync_link;
    /* The time reference has no estimate: it is not calibrated against anyone. */
    return MAX_GUARD_TIME_MS;
#else
    /* The estimate is kept across a re-acquisition, but a clock that was just
     * set from Packet 1 is not known to be locked: the cap until it is. */
    if (MAC_GetClockState() != CLOCK_WARM) {
        return MAX_GUARD_TIME_MS;
    }
    /* The drift to absorb has grown since the last Sync packet. */
    int32_t  age    = DayMs_Diff(slot_start_ms, MAC_GetLastSyncMs());
    uint32_t age_ms = (age > 0) ? (uint32_t)age : 0u;
    DriftEstimate_t e = DriftEstimator_Get();
    return sync_link ? GuardPolicy_Ms(&e, age_ms) : GuardPolicy_PeerMs(&e, age_ms);
#endif
}
