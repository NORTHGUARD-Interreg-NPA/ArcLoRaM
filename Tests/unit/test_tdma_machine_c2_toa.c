#include "unity.h"
#include "tdma_machine.h"
#include "mac_state_machine.h"
#include "freq_resolver.h"
#include "compliance_engine.h"
#include "shared_mem.h"
#include "tdma_table.h"
#include "arclog_capture.h"
#include "stub_drift_estimator.h"
#include <string.h>
#include <stdint.h>

/*
 * The expected packet of a slot in its Rx window (issue #38). The window closes
 * at the latest instant a packet can start and still end by slot end +
 * MAX_GUARD_TIME_MS, so it depends on the time on air of the largest packet the
 * slot can carry, which the TDMA Table declares per slot. The table is a
 * Mesh_Uplink phase (a header, two slots per cell, a footer) followed by a Sync
 * phase; see stub_tdma_table_toa.c for the sizes.
 *
 * A node still acquiring (CLOCK_ACQUIRING) keeps that geometric end. Once warm
 * it closes one guard after the nominal start whatever the packet, except in a
 * contention slot, so these tests run a C2 that has heard one Sync packet.
 */

/* =========================================================================
 * Platform stubs
 * ========================================================================= */

static uint32_t s_rtc_ms;
static uint32_t s_alarm_programmed;
static uint32_t s_rx_start_window_ms;
static uint32_t s_rx_cap_ms;
static int      s_radio_set_rx_calls;

static uint32_t stub_GetRtcMs(void)                          { return s_rtc_ms;                      }
static void     stub_ProgramAlarmA(uint32_t t)               { s_alarm_programmed = t;               }
static void     stub_CancelAlarmA(void)                      {                                       }
static void     stub_RadioSetChannel(uint32_t f)             { (void)f;                              }
static void     stub_RadioSend(const uint8_t *b, uint8_t l)  { (void)b; (void)l;                     }
static void     stub_RadioSetRx(uint32_t win, uint32_t cap)
{
    s_rx_start_window_ms = win;
    s_rx_cap_ms          = cap;
    s_radio_set_rx_calls++;
}
static void     stub_RadioScan(void)                         {                                       }
static void     stub_RadioSleep(void)                        {                                       }
static void     stub_WaitUntilMs(uint32_t t)                 { s_rtc_ms = t % MS_PER_DAY;            }

/* Time on air in ms of the sizes the table uses: SF12, BW125, CR4/5, 8 preamble
 * symbols, explicit header, CRC (the formula of test_lora_toa.c, to the ms). */
static uint32_t stub_RadioTimeOnAir(uint8_t len)
{
    switch (len) {
    case 4u:  return  827u;
    case 10u: return  991u;
    case 20u: return 1319u;
    case 51u: return 2466u;
    default:  return    0u;
    }
}

static const TdmaPlatform_t k_platform = {
    .GetRtcMs        = stub_GetRtcMs,
    .ProgramAlarmA   = stub_ProgramAlarmA,
    .CancelAlarmA    = stub_CancelAlarmA,
    .RadioSetChannel = stub_RadioSetChannel,
    .RadioSend       = stub_RadioSend,
    .RadioSetRx      = stub_RadioSetRx,
    .RadioScan       = stub_RadioScan,
    .RadioSleep      = stub_RadioSleep,
    .RadioTimeOnAir  = stub_RadioTimeOnAir,
    .WaitUntilMs     = stub_WaitUntilMs,
};

/* =========================================================================
 * Fixtures
 * ========================================================================= */

static FrequencyResolverState_t s_freq_state;
static ComplianceStatus_t       s_comp_status;
static uint32_t comp_get_tick(void) { return s_rtc_ms; }

static uint32_t s_mac_snapshot_ms;
static void stub_mac_rtc_set(uint32_t ms, uint8_t d, uint8_t mo, uint8_t y)
{
    (void)d; (void)mo; (void)y;
    s_mac_snapshot_ms = ms;
}
static void stub_mac_get_rtc_snapshot(uint32_t *ms, uint8_t *d, uint8_t *mo, uint8_t *y)
{
    *ms = s_mac_snapshot_ms; *d = 0x01u; *mo = 0x01u; *y = 0x24u;
}
static MAC_Hooks_t s_mac_hooks = {
    .rtc_set          = stub_mac_rtc_set,
    .get_rtc_snapshot = stub_mac_get_rtc_snapshot,
};

#define T0          TX_LEAD_MS   /* the first slot starts one Tx lead after the start */
#define SYNC_PHASE  1u           /* the Sync phase of the table */

void setUp(void)
{
    memset(&s_freq_state,  0, sizeof(s_freq_state));
    memset(&s_comp_status, 0, sizeof(s_comp_status));
    s_rtc_ms = 0u;
    s_alarm_programmed   = 0u;
    s_rx_start_window_ms = 0u;
    s_rx_cap_ms          = 0u;
    s_radio_set_rx_calls = 0;
    s_mac_snapshot_ms    = 0u;
    for (int i = 0; i < 2; i++) {
        s_freq_state.phases[i].cell_mode         = CELL_FREQ_STATIC;
        s_freq_state.phases[i].cell_freq_or_seed = 868100000u;
    }
    FrequencyResolver_Init(&s_freq_state);
    ComplianceEngine_Init(&s_comp_status, comp_get_tick);
    MAC_Init(&s_mac_hooks);
    TdmaMachine_Init(&k_platform);
    StubDriftEstimator_Reset();   /* no valid estimate: the guard is MAX_GUARD_TIME_MS */
    ArcLog_CaptureReset();
}
void tearDown(void) {}

/* One Sync packet: CLOCK_COLD -> CLOCK_ACQUIRING. The MAC is still Scanning,
 * so every slot is Rx. Then the chain runs from {0,0,0}, as C3 does at boot. */
static void start_chain(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index = SYNC_PHASE;
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());
    TEST_ASSERT_TRUE(TdmaMachine_Start());
}

/* Go to the wake the last slot task programmed, and run the next slot. */
static void step_slot(void)
{
    s_rtc_ms = s_alarm_programmed % MS_PER_DAY;
    TdmaMachine_SlotTask();
}

/* =========================================================================
 * Tests
 * ========================================================================= */

/* The header slot (4 bytes, 827 ms) is the first slot, at T0 = 20, woken at 0:
 * the latest packet start is 20 + 2500 + 100 - 827 = 1793 from the wake. The
 * Sync packet's 991 ms would give 1629. */
void test_the_header_window_ends_for_the_header_packet(void)
{
    start_chain();
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(1, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(1793u, s_rx_start_window_ms);
}

/* Each later slot starts 3000 ms after the one before and is woken one max
 * guard (100 ms) early, so its window is measured from 2920, 5920, 8920. */

/* Cell slot 0 (51 bytes, 2466 ms) starts at 3020: the latest packet start is
 * 3020 + 2500 + 100 - 2466 = 3154, so 234 ms from the wake at 2920. */
void test_a_big_cell_slot_window_closes_early_for_its_long_packet(void)
{
    start_chain();
    TdmaMachine_SlotTask();                       /* the header */
    step_slot();                                  /* cell 0, slot 0 */
    TEST_ASSERT_EQUAL(2, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(234u, s_rx_start_window_ms);
}

/* Cell slot 1 (10 bytes, 991 ms) of the same cell starts at 6020: the latest
 * start is 6020 + 2500 + 100 - 991 = 7629, so 1709 ms from the wake at 5920. */
void test_the_next_slot_of_the_cell_uses_its_own_packet(void)
{
    start_chain();
    TdmaMachine_SlotTask();                       /* the header */
    step_slot();                                  /* cell 0, slot 0 */
    step_slot();                                  /* cell 0, slot 1 */
    TEST_ASSERT_EQUAL(3, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(1709u, s_rx_start_window_ms);
}

/* The footer (20 bytes, 1319 ms) starts at 9020: the latest start is
 * 9020 + 2500 + 100 - 1319 = 10301, so 1381 ms from the wake at 8920. */
void test_the_footer_window_ends_for_the_footer_packet(void)
{
    start_chain();
    TdmaMachine_SlotTask();                       /* the header */
    step_slot();                                  /* cell 0, slot 0 */
    step_slot();                                  /* cell 0, slot 1 */
    step_slot();                                  /* the footer */
    TEST_ASSERT_EQUAL(4, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(1381u, s_rx_start_window_ms);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_the_header_window_ends_for_the_header_packet);
    RUN_TEST(test_a_big_cell_slot_window_closes_early_for_its_long_packet);
    RUN_TEST(test_the_next_slot_of_the_cell_uses_its_own_packet);
    RUN_TEST(test_the_footer_window_ends_for_the_footer_packet);
    return UNITY_END();
}
