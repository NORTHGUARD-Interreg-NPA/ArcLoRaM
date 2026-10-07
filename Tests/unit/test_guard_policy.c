#include "unity.h"
#include "guard_policy.h"

/*
 * The Rx guard from the drift estimate (issue #36, ADR-0012 Version 2): the
 * guard is a ratio over the clock uncertainty D(t) the estimate gives, seen
 * from the Sync sender. The expected values below are worked by hand from the
 * inputs of each test, not recomputed from the code's constants.
 */

void setUp(void) {}
void tearDown(void) {}

/* An estimate as the estimator reports it: {valid, rate, residual, noise us, n, baseline s}. */
static DriftEstimate_t est(bool valid, int32_t residual_ppb, uint32_t noise_us)
{
    DriftEstimate_t e = { valid, 0, residual_ppb, noise_us, 40u, 1800u };
    return e;
}

/* Before the estimate is valid (acquisition, a node that has just booted) the
 * guard is the cap of the slot grid, as it has always been. */
void test_an_invalid_estimate_gives_the_maximum_guard(void)
{
    DriftEstimate_t e = est(false, 360, 106u);
    TEST_ASSERT_EQUAL_UINT32(100u, GuardPolicy_Ms(&e, 200000u));
}

/* A valid estimate on the NUCLEO pair, DEV profile (a Sync every 200 s): the
 * uncertainty alone is 72 us of drift + 3 x 106 us of noise + one tick + the
 * 1 ms left uncorrected, about 1.6 ms, and 3 times that is 5 ms. A packet 6 ms
 * off is still Tier 1 (relayed), so the window has to reach it: the guard never
 * goes below the 8 ms of the Tier 1 band. */
void test_the_guard_never_goes_below_the_tier1_threshold(void)
{
    DriftEstimate_t e = est(true, 360, 106u);
    TEST_ASSERT_EQUAL_UINT32(8u, GuardPolicy_Ms(&e, 200000u));
}

/* A residual of 5 ppm (the datasheet bound of the Production Clock, used here as
 * an input, not a measurement) over the 540 s of the PROD Sync period: 2700 us of
 * drift + 3 x 106 us of noise + one tick (about 245 us) + the 1 ms left
 * uncorrected = 4263 us. Times 3 is 12.8 ms, rounded up to 13 ms. */
void test_a_large_uncertainty_sets_the_guard_above_the_floor(void)
{
    DriftEstimate_t e = est(true, 5000, 106u);
    TEST_ASSERT_EQUAL_UINT32(13u, GuardPolicy_Ms(&e, 540000u));
}

/* The sign of the residual says which way the clock drifts, not how much margin
 * the window needs: a slow clock gets the guard of a fast one. */
void test_a_slow_clock_gets_the_guard_of_a_fast_one(void)
{
    DriftEstimate_t e = est(true, -5000, 106u);
    TEST_ASSERT_EQUAL_UINT32(13u, GuardPolicy_Ms(&e, 540000u));
}

/* noise_us = 0 means the estimator cannot tell its noise yet. Zero noise would
 * shrink the guard exactly when the estimate is least known, so the estimator's
 * own design floor (400 us, drift_estimator.h) stands in for it:
 * 2700 + 3 x 400 + 245 + 1000 = 5145 us, times 3 is 15.4 ms, so 16 ms. */
void test_an_unknown_noise_uses_the_design_floor(void)
{
    DriftEstimate_t e = est(true, 5000, 0u);
    TEST_ASSERT_EQUAL_UINT32(16u, GuardPolicy_Ms(&e, 540000u));
}

/* The gaps of the slot grid are sized for 100 ms (2 x MAX_GUARD_TIME_MS): a node
 * that would need more is capped there and degrades, as it does today. Here
 * 5 ppm over 10 000 s is 50 ms of drift and the formula alone gives 155 ms. */
void test_the_guard_is_capped_at_the_slot_grid_maximum(void)
{
    DriftEstimate_t e = est(true, 5000, 106u);
    TEST_ASSERT_EQUAL_UINT32(100u, GuardPolicy_Ms(&e, 10000000u));
}

/* A nonsense uncertainty must not wrap into a small guard. With a residual of
 * 10^9 ppb the exact guard is 3 x age + 5 ms, and an age of 1 431 655 767 ms
 * makes it 4 294 967 306 = 2^32 + 10: a 32-bit result would read 10 ms. */
void test_a_huge_uncertainty_cannot_wrap_into_a_small_guard(void)
{
    DriftEstimate_t e = est(true, 1000000000, 106u);
    TEST_ASSERT_EQUAL_UINT32(100u, GuardPolicy_Ms(&e, 1431655767u));
}

/* Outside the Sync phase the sender is a peer with its own error: a clock
 * assumed like this node's (the same drift, noise, tick and uncorrected offset),
 * so the uncertainty doubles. The large case: 2 x 4263 = 8526 us, times 3 is
 * 25.6 ms, so 26 ms, against the 13 ms of the Sync link. */
void test_a_peer_link_doubles_the_uncertainty(void)
{
    DriftEstimate_t e = est(true, 5000, 106u);
    TEST_ASSERT_EQUAL_UINT32(26u, GuardPolicy_PeerMs(&e, 540000u));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_an_invalid_estimate_gives_the_maximum_guard);
    RUN_TEST(test_the_guard_never_goes_below_the_tier1_threshold);
    RUN_TEST(test_a_large_uncertainty_sets_the_guard_above_the_floor);
    RUN_TEST(test_a_slow_clock_gets_the_guard_of_a_fast_one);
    RUN_TEST(test_an_unknown_noise_uses_the_design_floor);
    RUN_TEST(test_the_guard_is_capped_at_the_slot_grid_maximum);
    RUN_TEST(test_a_huge_uncertainty_cannot_wrap_into_a_small_guard);
    RUN_TEST(test_a_peer_link_doubles_the_uncertainty);
    return UNITY_END();
}
