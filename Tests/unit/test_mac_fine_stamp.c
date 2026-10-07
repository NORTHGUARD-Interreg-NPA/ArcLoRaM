#include "unity.h"
#include <string.h>
#include "mac_state_machine.h"
#include "tdma_table.h"
#include "guard_time_resolver.h"
#include "arclog_capture.h"
#include "sync_stamp.h"

/*
 * The MAC takes the SyncStamp in RTC ticks (issue #82) and judges the Sync
 * error in microseconds. Built for the C1 and the C2 MAC (same behaviour).
 * The table is the bring-up one: a Sync cell every 3000 ms, which is exactly
 * 12288 ticks, so an expected arrival of k cells is k x 12288 ticks and a
 * stamp n ticks later is n x 244.140625 us late.
 */

#define CELL_TICKS  12288u

static int      s_rtc_sets;
static uint32_t s_snapshot_ms;
static int      s_samples;
static int32_t  s_last_sample;

static void stub_rtc_set(uint32_t target_ms, uint8_t d, uint8_t mo, uint8_t y)
{
    (void)d; (void)mo; (void)y;
    s_snapshot_ms = target_ms;
    s_rtc_sets++;
}
static void stub_snapshot(uint32_t *ms, uint8_t *d, uint8_t *mo, uint8_t *y)
{
    *ms = s_snapshot_ms; *d = 1u; *mo = 1u; *y = 0x24u;
}
static void stub_sample(int32_t err_us) { s_samples++; s_last_sample = err_us; }

static int     s_aligns;
static int32_t s_last_align;
static void stub_align(int32_t err_us) { s_aligns++; s_last_align = err_us; }

static const MAC_Hooks_t k_hooks = {
    .rtc_set = stub_rtc_set, .get_rtc_snapshot = stub_snapshot, .sync_sample = stub_sample,
    .rtc_align_subsecond = stub_align,
};

static SyncPayload_t pkt(uint8_t cell)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_cell_index = cell;
    p.day = 1u; p.month = 1u; p.year = 0x24u;
    return p;
}

/* A cold node to CLOCK_WARM: Packet 1 (the set) then two good ones, exactly on time. */
static void warm(void)
{
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    p = pkt(0u); MAC_OnSyncPacketReceivedTicks(&p, 0u);
    p = pkt(1u); MAC_OnSyncPacketReceivedTicks(&p, 1u * CELL_TICKS);
    p = pkt(2u); MAC_OnSyncPacketReceivedTicks(&p, 2u * CELL_TICKS);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
}

void setUp(void)
{
    s_rtc_sets = 0; s_samples = 0; s_last_sample = 0; s_snapshot_ms = 0u;
    s_aligns = 0; s_last_align = 0;
    MAC_Init(&k_hooks);
    ArcLog_CaptureReset();
}
void tearDown(void) {}

/* 12 ticks late = 2929.69 us: the log carries it to the us, and err in ms is the rounded value. */
void test_sync_rx_logs_the_error_in_us_beside_the_ms(void)
{
    warm();
    ArcLog_CaptureReset();
    SyncPayload_t p = pkt(3u);
    MAC_OnSyncPacketReceivedTicks(&p, 3u * CELL_TICKS + 12u);
    TEST_ASSERT_ARCLOG("err=3 erru=2930");
}

void test_an_early_stamp_is_a_negative_error(void)
{
    warm();
    ArcLog_CaptureReset();
    SyncPayload_t p = pkt(3u);
    MAC_OnSyncPacketReceivedTicks(&p, 3u * CELL_TICKS - 82u);        /* -20 019.5 us */
    TEST_ASSERT_ARCLOG("err=-20 erru=-20020");
}

/* 32 ticks is 7812.5 us: under 8 ms, Tier 1. 33 ticks is 8056.6 us: Tier 2.
 * The whole-ms stamp could not tell these apart. */
void test_tier1_ends_between_32_and_33_ticks(void)
{
    warm();
    ArcLog_CaptureReset();
    SyncPayload_t p = pkt(3u);
    MAC_OnSyncPacketReceivedTicks(&p, 3u * CELL_TICKS + 32u);
    TEST_ASSERT_ARCLOG("erru=7813 clk=WARM act=t1");
    p = pkt(4u);
    MAC_OnSyncPacketReceivedTicks(&p, 4u * CELL_TICKS + 33u);
    TEST_ASSERT_ARCLOG("erru=8057 clk=WARM act=t2");
}

/* 409 ticks is 99.85 ms (Tier 2), 410 ticks is 100.098 ms (Tier 3: the resync threshold). */
void test_tier3_begins_between_409_and_410_ticks(void)
{
    warm();
    ArcLog_CaptureReset();
    SyncPayload_t p = pkt(3u);
    MAC_OnSyncPacketReceivedTicks(&p, 3u * CELL_TICKS + 409u);
    TEST_ASSERT_ARCLOG("act=t2");
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    p = pkt(4u);
    MAC_OnSyncPacketReceivedTicks(&p, 4u * CELL_TICKS + 410u);
    TEST_ASSERT_ARCLOG("act=t3");
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
}

/* The acquisition check is the same 8 ms bound. */
void test_acquisition_accepts_32_ticks_and_rejects_33(void)
{
    SyncPayload_t p = pkt(0u);
    MAC_OnSyncPacketReceivedTicks(&p, 0u);                           /* the set */
    p = pkt(1u);
    MAC_OnSyncPacketReceivedTicks(&p, 1u * CELL_TICKS + 32u);
    TEST_ASSERT_ARCLOG("act=good");
    p = pkt(2u);
    MAC_OnSyncPacketReceivedTicks(&p, 2u * CELL_TICKS + 33u);
    TEST_ASSERT_ARCLOG("act=bad");
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
}

/* The drift estimator gets the error in us, for the packets that are samples. */
void test_the_sample_hook_gets_the_error_in_us(void)
{
    warm();
    s_samples = 0;
    SyncPayload_t p = pkt(3u);
    MAC_OnSyncPacketReceivedTicks(&p, 3u * CELL_TICKS + 12u);
    TEST_ASSERT_EQUAL_INT(1, s_samples);
    TEST_ASSERT_EQUAL_INT32(2930, s_last_sample);
    p = pkt(4u);
    MAC_OnSyncPacketReceivedTicks(&p, 4u * CELL_TICKS - 82u);        /* Tier 2, early */
    TEST_ASSERT_EQUAL_INT(2, s_samples);
    TEST_ASSERT_EQUAL_INT32(-20020, s_last_sample);
}

/* The error crosses midnight the short way: a cell after midnight in a phase that began before it. */
void test_the_error_across_midnight(void)
{
    warm();
    ArcLog_CaptureReset();
    SyncPayload_t p = pkt(3u);
    p.ms_since_midnight_sync_phase = 86400000u - 4500u;              /* cell 3 is due at +4500 ms into the next day */
    MAC_OnSyncPacketReceivedTicks(&p, SyncStamp_MsToTicks(4500u) + 12u);
    TEST_ASSERT_ARCLOG("err=3 erru=2930");
}

/* The whole-ms entry point stays, as a wrapper: a packet 4 ms late reads 4. */
void test_the_ms_entry_point_still_works(void)
{
    warm();
    ArcLog_CaptureReset();
    SyncPayload_t p = pkt(3u);
    MAC_OnSyncPacketReceived(&p, 9004u);
    TEST_ASSERT_ARCLOG("err=4 erru=");
    TEST_ASSERT_ARCLOG("act=t1");
}

/* The phase is corrected from SYNC_CORRECT_THRESHOLD_MS (1 ms), and below it the
 * clock is left alone so as not to chase the noise of the stamp. 4 ticks late is
 * 976.6 us (977): no correction. 5 ticks is 1220.7 us (1221): corrected, with the
 * error in us, and the same early. Both are still under the 8 ms of Tier 1. */
void test_the_correction_starts_between_4_and_5_ticks(void)
{
    warm();
    s_aligns = 0;
    SyncPayload_t p = pkt(3u);
    MAC_OnSyncPacketReceivedTicks(&p, 3u * CELL_TICKS + 4u);
    TEST_ASSERT_EQUAL_INT(0, s_aligns);
    p = pkt(4u);
    MAC_OnSyncPacketReceivedTicks(&p, 4u * CELL_TICKS + 5u);
    TEST_ASSERT_EQUAL_INT(1, s_aligns);
    TEST_ASSERT_EQUAL_INT32(1221, s_last_align);
    p = pkt(5u);
    MAC_OnSyncPacketReceivedTicks(&p, 5u * CELL_TICKS - 5u);
    TEST_ASSERT_EQUAL_INT(2, s_aligns);
    TEST_ASSERT_EQUAL_INT32(-1221, s_last_align);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_the_correction_starts_between_4_and_5_ticks);
    RUN_TEST(test_sync_rx_logs_the_error_in_us_beside_the_ms);
    RUN_TEST(test_an_early_stamp_is_a_negative_error);
    RUN_TEST(test_tier1_ends_between_32_and_33_ticks);
    RUN_TEST(test_tier3_begins_between_409_and_410_ticks);
    RUN_TEST(test_acquisition_accepts_32_ticks_and_rejects_33);
    RUN_TEST(test_the_sample_hook_gets_the_error_in_us);
    RUN_TEST(test_the_error_across_midnight);
    RUN_TEST(test_the_ms_entry_point_still_works);
    return UNITY_END();
}
