#include "unity.h"
#include "channel_plan.h"
#include "freq_resolver.h"
#include "tdma_machine.h"

/*
 * The radio channel plan (channel_plan.h): one channel for every phase today, default 868.3 MHz, and
 * a Build Override (-DFREQ_DEFAULT_HZ=...) moves all of them at once. This file is built twice,
 * with and without the override; EXPECTED_FREQ_HZ is what that build must resolve to.
 */

#ifndef EXPECTED_FREQ_HZ
#  error "the build must say which channel it expects"
#endif
#ifndef EXPECTED_POWER_DBM
#  define EXPECTED_POWER_DBM 14      /* the default: the ETSI limit of the one percent channels */
#endif

void setUp(void) {}
void tearDown(void) {}

void test_the_default_channel_of_every_phase_is_the_expected_one(void)
{
    TEST_ASSERT_EQUAL_UINT32(EXPECTED_FREQ_HZ, FREQ_DEFAULT_HZ);
}

void test_the_discovery_channel_and_the_fallback_follow_it(void)
{
    /* The Sync anchor transmits on the default channel and a cold node listens for it there. */
    TEST_ASSERT_EQUAL_UINT32(EXPECTED_FREQ_HZ, SCAN_FREQ_HZ);
    TEST_ASSERT_EQUAL_UINT32(EXPECTED_FREQ_HZ, FREQ_FALLBACK_HZ);
}

void test_an_unpopulated_static_phase_resolves_to_the_default_channel(void)
{
    static FrequencyResolverState_t state;
    FrequencyResolver_Init(&state);
    state.phases[0].cell_mode          = CELL_FREQ_STATIC;
    state.phases[0].cell_freq_or_seed = 0u;
    FrameCursor_t c = { .phase_index = 0u, .cell_index = 0u, .slot_index = 0u, .slot_pos = SLOT_POS_CELL };
    TEST_ASSERT_EQUAL_UINT32(EXPECTED_FREQ_HZ, FrequencyResolver_GetFreq(&c));
}

void test_the_plan_is_inside_the_one_percent_sub_bands_on_a_100_khz_raster(void)
{
#if FREQ_DEFAULT_HZ != CHANNEL_HIGH_POWER_HZ
    TEST_ASSERT_TRUE(FREQ_DEFAULT_HZ >= CHANNEL_MIN_HZ);
    TEST_ASSERT_TRUE(FREQ_DEFAULT_HZ <= CHANNEL_MAX_HZ);
    TEST_ASSERT_EQUAL_UINT32(0u, FREQ_DEFAULT_HZ % CHANNEL_RASTER_HZ);
#endif
    /* A 125 kHz channel centred on the lowest and highest channel stays inside 865.0 to 868.6 MHz. */
    TEST_ASSERT_TRUE(CHANNEL_MIN_HZ - 62500u >= 865000000u);
    TEST_ASSERT_TRUE(CHANNEL_MAX_HZ + 62500u <= 868600000u);
}

void test_two_sessions_need_channels_a_megahertz_apart(void)
{
    /* Semtech gives 88 dB of blocking immunity at 1 MHz offset for the SX126x; the widest gap
     * between a desk node (about -28 dBm at the neighbour) and a far link (assumed -90 dBm) is
     * 62 dB. Nothing closer is claimed. */
    TEST_ASSERT_EQUAL_UINT32(1000000u, CHANNEL_SPACING_HZ);
    TEST_ASSERT_EQUAL_UINT32(868300000u, CHANNEL_DEFAULT_HZ);
}

void test_the_power_of_the_radio_and_of_the_compliance_request_is_one_number(void)
{
    TEST_ASSERT_EQUAL_INT(EXPECTED_POWER_DBM, RADIO_TX_POWER_DBM);
    /* The Compliance Engine is asked for the power the radio is set to, not for a nominal 0 dBm. */
    TEST_ASSERT_EQUAL_INT(EXPECTED_POWER_DBM, TX_POWER_DBM);
}

void test_more_than_14_dbm_exists_on_the_high_power_channel_only(void)
{
    TEST_ASSERT_EQUAL_INT(14, RADIO_TX_POWER_LIMIT_DBM);
    TEST_ASSERT_EQUAL_INT(22, RADIO_TX_POWER_MAX_DBM);
    TEST_ASSERT_EQUAL_UINT32(869500000u, CHANNEL_HIGH_POWER_HZ);
#if RADIO_TX_POWER_DBM > RADIO_TX_POWER_LIMIT_DBM
    TEST_ASSERT_EQUAL_UINT32(CHANNEL_HIGH_POWER_HZ, FREQ_DEFAULT_HZ);
#endif
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_the_power_of_the_radio_and_of_the_compliance_request_is_one_number);
    RUN_TEST(test_more_than_14_dbm_exists_on_the_high_power_channel_only);
    RUN_TEST(test_the_default_channel_of_every_phase_is_the_expected_one);
    RUN_TEST(test_the_discovery_channel_and_the_fallback_follow_it);
    RUN_TEST(test_an_unpopulated_static_phase_resolves_to_the_default_channel);
    RUN_TEST(test_the_plan_is_inside_the_one_percent_sub_bands_on_a_100_khz_raster);
    RUN_TEST(test_two_sessions_need_channels_a_megahertz_apart);
    return UNITY_END();
}
