/*!
 * \file      guard_time_resolver.h
 *
 * \brief     Guard Time Resolver — provides the RX window guard margin
 *            that absorbs accumulated RTC clock drift since the last
 *            Frame Epoch correction.
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
#ifndef GUARD_TIME_RESOLVER_H
#define GUARD_TIME_RESOLVER_H

#include <stdint.h>
#include <stdbool.h>

/* =========================================================================
 * Constants
 * ========================================================================= */

/*!
 * \brief   Maximum guard time in milliseconds.
 *
 * \details This is the sole reference for guard time in the system.
 *          It is a property of the slot grid: the most any node may wake
 *          early. Until the clock is CLOCK_WARM with a valid drift estimate,
 *          the TDMA Machine ends every Rx window at the latest packet start
 *          that still ends by slot end + MAX_GUARD_TIME_MS, and
 *          \ref GuardTimeResolver_GetGuardMs is equal to this value; the cap
 *          of the window is always slot end + MAX_GUARD_TIME_MS. The MAC State
 *          Machine uses it as the Tier-3 drift threshold: if a received Sync
 *          packet has a preamble offset error at or above this value, the node
 *          degrades to CLOCK_COLD immediately.
 *
 *          The TDMA Table gap constraint derives from this constant:
 *          every gap (\ref Phase_t.gap_after_slot_ms,
 *          \ref AnchorSlot_t.gap_after_ms) must be >= 2 * MAX_GUARD_TIME_MS
 *          to prevent Rx window overlap between adjacent slots.
 *
 *          Version 2 of the resolver (ADR-0022) computes the guard from the
 *          estimated clock drift (\ref GuardPolicy_Ms), capped at this value.
 *          If the drift would require a guard beyond this cap, the node
 *          degrades instead.
 *
 * \remark   Transient errors above this threshold may be tolerated in a
 *          future revision by splitting SYNC_RESYNC_THRESHOLD_MS into its
 *          own value. For now they are unified.
 */
#define MAX_GUARD_TIME_MS  100u  /* 3 x T_S at SF12/BW125 (ADR-0012); the SyncStamp is RxDone - ToA, so the preamble detection latency no longer needs absorbing */

/*!
 * \brief   Tier-3 drift threshold: preamble offset error at or above
 *          this value triggers immediate CLOCK_COLD degradation.
 *
 * \details Aliased to \ref MAX_GUARD_TIME_MS. The unification creates a
 *          clean invariant: "if drift exceeds max guard, the node
 *          degrades." Breaking this alias in the future would allow
 *          tolerating transient errors above the guard cap.
 */
#define SYNC_RESYNC_THRESHOLD_MS  MAX_GUARD_TIME_MS

/*!
 * \brief   Tier-1 threshold: a Sync error below this is a good packet.
 *
 * \details 0.25 x T_S at SF12/BW125. The MAC uses it for the acquisition lock
 *          check and for relay eligibility. The Rx guard never goes below it
 *          (\ref GuardPolicy_Ms): a packet that is still Tier 1 must be inside
 *          the window.
 */
#ifndef SYNC_PARTICIPATE_THRESHOLD_MS
#define SYNC_PARTICIPATE_THRESHOLD_MS  8u
#endif

/*!
 * \brief   Correction threshold: a Sync error below this is left uncorrected.
 *
 * \details Above the noise floor of the stamp (3 sigma of 106 us plus a constant
 *          of at most 0.25 ms, about 0.57 ms; NUCLEO measurement, #82) and far
 *          under the concurrent-transmission budget (2 x depth x error < 16 ms).
 *          The offset it leaves in the clock is part of the Rx guard
 *          (\ref GuardPolicy_Ms).
 */
#ifndef SYNC_CORRECT_THRESHOLD_MS
#define SYNC_CORRECT_THRESHOLD_MS  1u
#endif

/* =========================================================================
 * Public API
 * ========================================================================= */

/*!
 * \brief   The current guard for the Rx slot that starts at \p slot_start_ms,
 *          in milliseconds: how early the slot is woken, and how late after
 *          nominal a packet may still start in a synced window.
 *
 * \details Version 2 (issue #36): the guard follows the drift estimate
 *          (\ref GuardPolicy_Ms), between the Tier 1 threshold and
 *          \ref MAX_GUARD_TIME_MS. It is \ref MAX_GUARD_TIME_MS while the
 *          estimate is not valid, and always for C3, the time reference.
 *
 * \param   slot_start_ms  Nominal start of the slot, RTC day domain.
 * \param   sync_link      The slot receives from the Sync sender (a Sync phase):
 *                         the estimate is of exactly that link. Another phase
 *                         has a peer whose own error adds to it.
 *
 * \retval  uint32_t Guard time in milliseconds.
 */
uint32_t GuardTimeResolver_GetGuardMs(uint32_t slot_start_ms, bool sync_link);

#endif /* GUARD_TIME_RESOLVER_H */
