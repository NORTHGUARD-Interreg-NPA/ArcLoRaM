/*!
 * \file      guard_policy.h
 *
 * \brief     The Rx guard from the drift estimate (issue #36).
 *
 * \details   Pure, integer arithmetic only, no HAL: the guard is a function of
 *            the estimator's output and of the time since the last Sync, so a
 *            host test covers it with a struct literal as the estimate.
 *
 *            While the estimate is not valid (acquisition, a node that has
 *            just booted) the guard is the cap of the slot grid,
 *            \ref MAX_GUARD_TIME_MS, as it has always been.
 *
 * \author    Simon R.C. Langlais ( Celium )
 */
#ifndef GUARD_POLICY_H
#define GUARD_POLICY_H

#include <stdint.h>
#include <stdbool.h>
#include "drift_estimator.h"
#include "guard_time_resolver.h"   /* MAX_GUARD_TIME_MS */

/*! The guard is this many times the clock uncertainty D(t). The #46 target is
 *  every guard under 15 ms, and with R1 (the neighbour drift target) at 5 ms
 *  this is the largest ratio that keeps it there. */
#ifndef GUARD_RATIO
#define GUARD_RATIO              3u
#endif

/*! Sigmas of the sample noise in D(t). */
#define GUARD_NOISE_SIGMAS       3u

/*! One RTC tick (1/4096 s = 244.14 us), rounded up: the quantisation of the stamp. */
#define GUARD_TICK_US            245u

/*! The offset a Sync error under the correction threshold leaves in the clock. */
#define GUARD_UNCORRECTED_US     (SYNC_CORRECT_THRESHOLD_MS * 1000u)

/*! The time from the early wake to a radio that listens. A delay of the radio,
 *  not an uncertainty of the clock, so it is added after the ratio.
 *
 *  Measured with timing-probe (issue #36 item 2, 2026-10-05, Node 2 as C2,
 *  NUCLEO Clock, CM0+ at 4 MHz, n = 4): the CPU path from the slot task to a
 *  radio armed in Rx is 4.14 to 4.50 ms (the channel 0.9 to 1.3 ms, the MAC
 *  0.09, this resolver 0.3 to 0.4, RadioSetRx 2.7), and the wake before it,
 *  alarm to slot task, under 1 ms. The two log lines of a bench build (SLOT and
 *  RX_WIN, 5.0 and 5.3 ms each) are not in it: they are not in a node that
 *  logs less. What the radio does after RadioSetRx returns (TCXO, PLL) is not
 *  visible to a CPU probe. The maximum, rounded up. */
#ifndef GUARD_RX_START_LATENCY_US
#define GUARD_RX_START_LATENCY_US  5000u
#endif

/*
 * The guard when \p clocks clocks of the same kind stand between the sender and
 * this node's window: 1 on the Sync link (the estimate is of exactly that link),
 * 2 when the sender is a peer assumed to be like this node.
 */
static inline uint32_t GuardPolicy_Compute(const DriftEstimate_t *e, uint32_t age_ms,
                                           uint32_t clocks)
{
    if (!e->valid) {
        return MAX_GUARD_TIME_MS;
    }

    /* Rate x age, rounded up: ppb x ms / 1e6 is us. The sign says which way
     * the clock drifts, the window needs the same margin either way. */
    uint32_t rate_ppb = (e->residual_ppb < 0) ? (uint32_t)(-(int64_t)e->residual_ppb)
                                              : (uint32_t)e->residual_ppb;
    uint64_t drift_us = ((uint64_t)rate_ppb * age_ms + 999999u) / 1000000u;
    /* noise_us = 0: not estimable yet. Zero would shrink the guard when the
     * estimate is least known, so the estimator's design floor stands in. */
    uint32_t sigma_us = (e->noise_us != 0u) ? e->noise_us : DRIFT_SIGMA_FLOOR_US;
    uint64_t d_us     = (uint64_t)clocks * (drift_us
                                            + (uint64_t)GUARD_NOISE_SIGMAS * sigma_us
                                            + GUARD_TICK_US
                                            + GUARD_UNCORRECTED_US);
    uint64_t g_ms     = (GUARD_RATIO * d_us + GUARD_RX_START_LATENCY_US + 999u) / 1000u;

    /* Never below the Tier 1 band, never above the slot grid: a node that
     * would need more degrades, as it does today. Compared in 64 bits, so a
     * nonsense uncertainty cannot wrap into a small guard. */
    if (g_ms < SYNC_PARTICIPATE_THRESHOLD_MS) return SYNC_PARTICIPATE_THRESHOLD_MS;
    if (g_ms > MAX_GUARD_TIME_MS)             return MAX_GUARD_TIME_MS;
    return (uint32_t)g_ms;
}

/*!
 * \brief   The guard for a slot that receives from the Sync sender.
 *
 * \param   e        The estimate after the latest Sync sample.
 * \param   age_ms   Time from the last Sync reception to the slot being woken for.
 * \return  The guard in ms: how early the Rx window opens, and how late a
 *          packet may still start in it.
 */
static inline uint32_t GuardPolicy_Ms(const DriftEstimate_t *e, uint32_t age_ms)
{
    return GuardPolicy_Compute(e, age_ms, 1u);
}

/*!
 * \brief   The guard for a slot in a phase that is not Sync: the sender is a
 *          peer with its own error, assumed like this node's, so the
 *          uncertainty doubles. The estimate is of this node against the Sync
 *          sender only, and says nothing of the peer.
 */
static inline uint32_t GuardPolicy_PeerMs(const DriftEstimate_t *e, uint32_t age_ms)
{
    return GuardPolicy_Compute(e, age_ms, 2u);
}

#endif /* GUARD_POLICY_H */
