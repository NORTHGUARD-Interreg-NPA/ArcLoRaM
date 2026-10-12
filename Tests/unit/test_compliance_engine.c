#include "unity.h"
#include "compliance_engine.h"
#include "shared_mem.h"
#include <string.h>

/* ------- test fixtures ---------------------------------------------------- */

static ComplianceStatus_t s_status;
static uint32_t           s_tick;

static uint32_t get_tick(void) { return s_tick; }

void setUp(void)
{
    memset(&s_status, 0, sizeof(s_status));
    s_tick = 0u;
    ComplianceEngine_Init(&s_status, get_tick);
}

void tearDown(void) {}

/* ------- helpers ---------------------------------------------------------- */

/* In-band frequency: 868.1 MHz (standard EU LoRa channel) */
#define FREQ_868_HZ     868100000u
/* Out-of-band frequency: 915 MHz (US ISM, outside ETSI 863–870 band) */
#define FREQ_915_HZ     915000000u
/* Max credit for 1 % DC over 1 h = 36 000 ms */
#define MAX_CREDIT_MS   36000u
/* ETSI 863–870 MHz max TX power */
#define MAX_POWER_DBM   14

/* ------- GRANTED ---------------------------------------------------------- */

void test_granted_when_credit_available(void)
{
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, 100u, 10));
}

void test_credit_deducted_after_granted(void)
{
    /* Exhaust all credit in one call; zero time passes — no regeneration. */
    ComplianceEngine_RequestChannel(FREQ_868_HZ, MAX_CREDIT_MS, 10);

    /* Any further TX must be RESTRICTED. */
    TEST_ASSERT_EQUAL(COMPLIANCE_RESTRICTED,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, 1u, 10));
}

/* ------- RESTRICTED ------------------------------------------------------- */

void test_restricted_when_no_credit(void)
{
    ComplianceEngine_RequestChannel(FREQ_868_HZ, MAX_CREDIT_MS, 10);

    TEST_ASSERT_EQUAL(COMPLIANCE_RESTRICTED,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, 1u, 10));
}

void test_restricted_wait_ms_nonzero(void)
{
    ComplianceEngine_RequestChannel(FREQ_868_HZ, MAX_CREDIT_MS, 10);
    ComplianceEngine_RequestChannel(FREQ_868_HZ, 1u, 10);

    TEST_ASSERT_GREATER_THAN(0u, s_status.wait_ms);
}

void test_restricted_wait_ms_reflects_duty_cycle(void)
{
    /*
     * Exhaust all credit.  Deficit for the next call = 10 ms.
     * With 1 % DC (duty_cycle_x10 = 10):
     *   wait_ms = ceil(10 × 1000 / 10) = 1 000 ms.
     */
    ComplianceEngine_RequestChannel(FREQ_868_HZ, MAX_CREDIT_MS, 10);
    ComplianceEngine_RequestChannel(FREQ_868_HZ, 10u, 10);

    TEST_ASSERT_EQUAL(1000u, s_status.wait_ms);
}

/* ------- POWER_TOO_HIGH --------------------------------------------------- */

void test_power_too_high(void)
{
    TEST_ASSERT_EQUAL(COMPLIANCE_POWER_TOO_HIGH,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, 100u, MAX_POWER_DBM + 1));
}

void test_power_at_limit_is_granted(void)
{
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, 100u, MAX_POWER_DBM));
}

/* ------- the 10 % / 500 mW sub-band (ETSI sub-band P, 869.4 - 869.65 MHz) ------------------ */

#define FREQ_869_5_HZ   869500000u
/* Max credit for 10 % DC over 1 h = 360 000 ms */
#define MAX_CREDIT_P_MS 360000u

void test_the_high_power_channel_grants_the_maximum_power_of_the_board(void)
{
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED, ComplianceEngine_RequestChannel(FREQ_869_5_HZ, 1000u, 22));
}

void test_the_same_power_is_refused_on_the_one_percent_channels(void)
{
    TEST_ASSERT_EQUAL(COMPLIANCE_POWER_TOO_HIGH, ComplianceEngine_RequestChannel(FREQ_868_HZ, 1000u, 22));
    TEST_ASSERT_EQUAL(COMPLIANCE_POWER_TOO_HIGH, ComplianceEngine_RequestChannel(868300000u, 1000u, 15));
}

void test_the_high_power_channel_stops_at_500_mw(void)
{
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED, ComplianceEngine_RequestChannel(FREQ_869_5_HZ, 1000u, 27));
    TEST_ASSERT_EQUAL(COMPLIANCE_POWER_TOO_HIGH, ComplianceEngine_RequestChannel(FREQ_869_5_HZ, 1000u, 28));
}

void test_the_high_power_channel_has_ten_percent_of_duty_cycle_of_its_own(void)
{
    /* 360 s of credit an hour; a deficit of 1 000 ms waits ceil(1000 x 1000 / 100) = 10 000 ms. */
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED, ComplianceEngine_RequestChannel(FREQ_869_5_HZ, MAX_CREDIT_P_MS, 22));
    TEST_ASSERT_EQUAL(COMPLIANCE_RESTRICTED, ComplianceEngine_RequestChannel(FREQ_869_5_HZ, 1000u, 22));
    TEST_ASSERT_EQUAL(10000u, s_status.wait_ms);
    /* The one percent band keeps its own credit. */
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED, ComplianceEngine_RequestChannel(FREQ_868_HZ, MAX_CREDIT_MS, 14));
}

/* ------- BAND_UNKNOWN ----------------------------------------------------- */

void test_band_unknown_returns_correct_result(void)
{
    TEST_ASSERT_EQUAL(COMPLIANCE_BAND_UNKNOWN,
        ComplianceEngine_RequestChannel(FREQ_915_HZ, 100u, 10));
}

void test_band_unknown_increments_count(void)
{
    ComplianceEngine_RequestChannel(FREQ_915_HZ, 100u, 10);
    ComplianceEngine_RequestChannel(FREQ_915_HZ, 100u, 10);

    TEST_ASSERT_EQUAL(2u, s_status.band_unknown_count);
}

/* ------- ComplianceStatus updates ---------------------------------------- */

void test_status_updated_on_granted(void)
{
    ComplianceEngine_RequestChannel(FREQ_868_HZ, 100u, 10);

    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED, s_status.last_result);
    TEST_ASSERT_EQUAL(FREQ_868_HZ,        s_status.last_freq_hz);
}

void test_status_updated_on_restricted(void)
{
    ComplianceEngine_RequestChannel(FREQ_868_HZ, MAX_CREDIT_MS, 10);
    ComplianceEngine_RequestChannel(FREQ_868_HZ, 10u, 10);

    TEST_ASSERT_EQUAL(COMPLIANCE_RESTRICTED, s_status.last_result);
    TEST_ASSERT_EQUAL(FREQ_868_HZ,           s_status.last_freq_hz);
    TEST_ASSERT_GREATER_THAN(0u,             s_status.wait_ms);
}

/* ------- ReportTxDone ----------------------------------------------------- */

void test_report_tx_done_refunds_credit(void)
{
    /*
     * Use expected=200 ms, then report actual=100 ms.  Refund = 100 ms.
     * Without the refund a subsequent 100 ms call would be RESTRICTED
     * (credit = 36000 − 200 = 35800; then 35800 + 0 regenerated = 35800).
     * With the refund credit recovers to 35900 ms → 100 ms granted.
     *
     * Exhaust (MAX_CREDIT_MS − 200) first so the post-refund headroom is
     * exactly 100 ms, making the assertion unambiguous.
     */
    ComplianceEngine_RequestChannel(FREQ_868_HZ, MAX_CREDIT_MS - 200u, 10);
    ComplianceEngine_RequestChannel(FREQ_868_HZ, 200u, 10);
    /* Credit is now 0.  Refund 100 ms. */
    ComplianceEngine_ReportTxDone(FREQ_868_HZ, 100u);

    /* Exactly 100 ms headroom: GRANTED */
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, 100u, 10));
    /* Nothing left: RESTRICTED */
    TEST_ASSERT_EQUAL(COMPLIANCE_RESTRICTED,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, 1u, 10));
}

void test_status_updated_with_production_values(void)
{
    /* 868.3 MHz Sync slot, TX_POWER_DBM=14, slot_active_ms=2500 */
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED,
        ComplianceEngine_RequestChannel(868300000u, 2500u, 14));
    TEST_ASSERT_EQUAL(868300000u,         s_status.last_freq_hz);
    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED, s_status.last_result);
}

void test_credit_capped_at_max(void)
{
    /*
     * Take 200 ms, refund 200 ms while tick is still 0.
     * Credit must return to exactly MAX_CREDIT_MS, not beyond.
     * A request for MAX_CREDIT_MS must succeed; MAX_CREDIT_MS + 1 must fail.
     */
    ComplianceEngine_RequestChannel(FREQ_868_HZ, 200u, 10);
    ComplianceEngine_ReportTxDone(FREQ_868_HZ, 0u);

    TEST_ASSERT_EQUAL(COMPLIANCE_GRANTED,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, MAX_CREDIT_MS, 10));
    TEST_ASSERT_EQUAL(COMPLIANCE_RESTRICTED,
        ComplianceEngine_RequestChannel(FREQ_868_HZ, 1u, 10));
}

/* ------- main ------------------------------------------------------------- */

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_granted_when_credit_available);
    RUN_TEST(test_credit_deducted_after_granted);
    RUN_TEST(test_restricted_when_no_credit);
    RUN_TEST(test_restricted_wait_ms_nonzero);
    RUN_TEST(test_restricted_wait_ms_reflects_duty_cycle);
    RUN_TEST(test_power_too_high);
    RUN_TEST(test_power_at_limit_is_granted);
    RUN_TEST(test_the_high_power_channel_grants_the_maximum_power_of_the_board);
    RUN_TEST(test_the_same_power_is_refused_on_the_one_percent_channels);
    RUN_TEST(test_the_high_power_channel_stops_at_500_mw);
    RUN_TEST(test_the_high_power_channel_has_ten_percent_of_duty_cycle_of_its_own);
    RUN_TEST(test_band_unknown_returns_correct_result);
    RUN_TEST(test_band_unknown_increments_count);
    RUN_TEST(test_status_updated_on_granted);
    RUN_TEST(test_status_updated_on_restricted);
    RUN_TEST(test_report_tx_done_refunds_credit);
    RUN_TEST(test_credit_capped_at_max);
    RUN_TEST(test_status_updated_with_production_values);
    return UNITY_END();
}
