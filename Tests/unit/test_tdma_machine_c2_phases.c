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
 * The Rx guard outside the Sync phase (issue #36). The table is a Mesh_Beacon
 * phase (one cell, then a CONTENTION footer) followed by a Sync phase; a C2 is
 * in CLOCK_WARM with a valid drift estimate. In a phase that is not Sync the
 * sender is a peer with its own error, and a contention slot is not a packet
 * that starts at nominal.
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
static uint32_t stub_RadioTimeOnAir(uint8_t len)             { (void)len; return 991u;               }
static void     stub_WaitUntilMs(uint32_t t)                 { s_rtc_ms = t % MS_PER_DAY;            }

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

#define SLOT_STEP_MS   3000u   /* slot_active_ms 2500 + gap 500 */
#define T0             TX_LEAD_MS
#define SYNC_PHASE     1u      /* the Sync phase of the table */

/* A estimate as the estimator reports it: valid, with this residual and noise. */
static DriftEstimate_t valid_est(int32_t residual_ppb, uint32_t noise_us)
{
    DriftEstimate_t e = { true, 0, residual_ppb, noise_us, 40u, 1800u };
    return e;
}

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
    StubDriftEstimator_Reset();
    ArcLog_CaptureReset();
}
void tearDown(void) {}

/* A C2 to CLOCK_WARM through three Sync packets of the Sync phase, on time. */
static void warm_mac(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index = SYNC_PHASE;
    p.sync_cell_index = 0u; MAC_OnSyncPacketReceived(&p, 0u);
    p.sync_cell_index = 1u; MAC_OnSyncPacketReceived(&p, 3000u);
    p.sync_cell_index = 2u; MAC_OnSyncPacketReceived(&p, 6000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
}

/* The chain from {0,0,0}: the Mesh_Beacon cell. */
static void start_chain(void)
{
    TEST_ASSERT_TRUE(TdmaMachine_Start());
}

static void step_slot(void)
{
    s_rtc_ms = s_alarm_programmed % MS_PER_DAY;
    TdmaMachine_SlotTask();
}

/* =========================================================================
 * Tests
 * ========================================================================= */

/* In a phase that is not Sync the sender is a peer with its own error, assumed
 * like this node's, so the uncertainty doubles. This estimate (no drift, 2 ms of
 * noise) is 7245 us on the Sync link and 14 490 us here: times 3 is 43.5 ms, so
 * 44 ms against the 22 ms of Sync. Slot at 20, woken at 0: the window ends at
 * 20 + 44 = 64. */
void test_a_peer_phase_doubles_the_uncertainty(void)
{
    warm_mac();
    StubDriftEstimator_Set(valid_est(0, 2000u));
    start_chain();
    TdmaMachine_SlotTask();                       /* the Mesh_Beacon cell: Rx for a C2 */
    TEST_ASSERT_EQUAL(1, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(T0 + 44u, s_rx_start_window_ms);
}

/* A CONTENTION slot is not a packet that starts at nominal: it starts after
 * CSMA and backoff, so closing at nominal + guard would miss it. Its window keeps
 * the geometric end. After the beacon cell the footer slot starts at 3020 and is
 * woken the peer guard (44 ms) early, at 2976; the latest start is
 * 3020 + 2500 + 100 - 991 = 4629, so 1653 ms from the wake. Closed early it
 * would be 3020 + 44 - 2976 = 88 ms. */
void test_a_contention_slot_keeps_the_geometric_window_end(void)
{
    warm_mac();
    StubDriftEstimator_Set(valid_est(0, 2000u));
    start_chain();
    TdmaMachine_SlotTask();                       /* the beacon cell */
    step_slot();                                  /* the footer, at its programmed wake */
    TEST_ASSERT_EQUAL(2, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(1653u, s_rx_start_window_ms);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_a_peer_phase_doubles_the_uncertainty);
    RUN_TEST(test_a_contention_slot_keeps_the_geometric_window_end);
    return UNITY_END();
}
