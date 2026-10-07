#include "unity.h"
#include "guard_policy.h"

/*
 * The Rx start latency (radio wake, TCXO, PLL: issue #36 item 2, measured with
 * timing-probe) is 0 until it is measured, so this file is built with a 2 ms
 * latency (-DGUARD_RX_START_LATENCY_US=2000, Tests/unit/CMakeLists.txt) to pin
 * where it enters the guard.
 */

void setUp(void) {}
void tearDown(void) {}

static DriftEstimate_t est(bool valid, int32_t residual_ppb, uint32_t noise_us)
{
    DriftEstimate_t e = { valid, 0, residual_ppb, noise_us, 40u, 1800u };
    return e;
}

/* The latency is a delay of the radio, not an uncertainty of the clock, so it
 * is added after the ratio. The case of the large uncertainty: 4263 us, times 3
 * is 12 789 us, plus the 2000 us is 14.8 ms, so 15 ms. Inside the ratio it
 * would be 3 x 6263 = 18.8 ms, so 19 ms; without it, 13 ms. */
void test_the_rx_start_latency_is_added_after_the_ratio(void)
{
    DriftEstimate_t e = est(true, 5000, 106u);
    TEST_ASSERT_EQUAL_UINT32(15u, GuardPolicy_Ms(&e, 540000u));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_the_rx_start_latency_is_added_after_the_ratio);
    return UNITY_END();
}
