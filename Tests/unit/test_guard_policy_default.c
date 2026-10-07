#include "unity.h"
#include "guard_policy.h"

/*
 * The guard with the constants as they ship, the Rx start latency included
 * (issue #36 item 2; the other policy tests pin the latency to 0 so that the
 * hand-worked values of the formula do not move with it).
 *
 * Measured 2026-10-05 with timing-probe, Node 2 as C2, NUCLEO Clock, CM0+ at
 * 4 MHz, n = 4: the CPU path from the slot task to a radio armed in Rx, without
 * the two log lines that a bench build adds, is 4.14 to 4.50 ms (the channel
 * 0.9 to 1.3 ms, the MAC 0.09, the guard 0.3 to 0.4, RadioSetRx 2.7). The
 * default is that maximum rounded up: 5000 us.
 */

void setUp(void) {}
void tearDown(void) {}

static DriftEstimate_t est(bool valid, int32_t residual_ppb, uint32_t noise_us)
{
    DriftEstimate_t e = { valid, 0, residual_ppb, noise_us, 40u, 1800u };
    return e;
}

/* The NUCLEO pair on the DEV profile, the operating point of the bench: 72 us of
 * drift + 3 x 106 us + one tick + 1 ms uncorrected = 1635 us, times 3 is
 * 4905 us, plus the 5000 us of latency is 9905 us, so 10 ms. */
void test_the_shipped_guard_of_the_nucleo_pair_is_10_ms(void)
{
    DriftEstimate_t e = est(true, 360, 106u);
    TEST_ASSERT_EQUAL_UINT32(10u, GuardPolicy_Ms(&e, 200000u));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_the_shipped_guard_of_the_nucleo_pair_is_10_ms);
    return UNITY_END();
}
