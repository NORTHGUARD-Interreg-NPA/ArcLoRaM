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

/* =========================================================================
 * Platform stubs
 * ========================================================================= */

static uint32_t s_rtc_ms;
static uint32_t s_alarm_programmed;
static int      s_alarm_calls;
static uint32_t s_channel_set;
static int      s_channel_calls;
static int      s_radio_send_calls;
static uint8_t  s_last_sent_buf[32];
static uint8_t  s_last_sent_len;
static uint32_t s_rx_start_window_ms;
static uint32_t s_rx_cap_ms;
static int      s_radio_set_rx_calls;
static int      s_radio_scan_calls;
static int      s_cancel_alarm_calls;
static uint32_t s_toa_ms;
static int      s_radio_sleep_calls;
static uint32_t s_wait_until_ms_target;
static int      s_wait_until_ms_calls;

static uint32_t stub_GetRtcMs(void)                          { return s_rtc_ms;                                      }
static void     stub_ProgramAlarmA(uint32_t t)               { s_alarm_programmed = t; s_alarm_calls++;              }
static void     stub_CancelAlarmA(void)                      { s_cancel_alarm_calls++;                               }
static void     stub_RadioSetChannel(uint32_t f)             { s_channel_set = f; s_channel_calls++;                 }
static void     stub_RadioSend(const uint8_t *b, uint8_t l)
{
    if (l <= (uint8_t)sizeof(s_last_sent_buf)) {
        memcpy(s_last_sent_buf, b, l);
        s_last_sent_len = l;
    }
    s_radio_send_calls++;
}
static void     stub_RadioSetRx(uint32_t win, uint32_t cap)
{
    s_rx_start_window_ms = win;
    s_rx_cap_ms          = cap;
    s_radio_set_rx_calls++;
}
static void     stub_RadioScan(void)                         { s_radio_scan_calls++;                                 }
static void     stub_RadioSleep(void)                        { s_radio_sleep_calls++;                                }
static uint32_t stub_RadioTimeOnAir(uint8_t len)             { (void)len; return s_toa_ms;                           }
static void     stub_WaitUntilMs(uint32_t t)                  { s_wait_until_ms_target = t; s_wait_until_ms_calls++; s_rtc_ms = t % MS_PER_DAY; }

static const TdmaPlatform_t k_platform = {
    .GetRtcMs       = stub_GetRtcMs,
    .ProgramAlarmA  = stub_ProgramAlarmA,
    .CancelAlarmA   = stub_CancelAlarmA,
    .RadioSetChannel= stub_RadioSetChannel,
    .RadioSend      = stub_RadioSend,
    .RadioSetRx     = stub_RadioSetRx,
    .RadioScan      = stub_RadioScan,
    .RadioSleep     = stub_RadioSleep,
    .RadioTimeOnAir = stub_RadioTimeOnAir,
    .WaitUntilMs    = stub_WaitUntilMs,
};

/* =========================================================================
 * MAC & subsystem fixtures
 * ========================================================================= */

static FrequencyResolverState_t s_freq_state;
static ComplianceStatus_t       s_comp_status;

/* Stub get_tick reuses s_rtc_ms so time advances consistently */
static uint32_t comp_get_tick(void) { return s_rtc_ms; }

/* Snapshot stub: pretend RTC reads 0 after any rtc_set */
static uint32_t s_mac_snapshot_ms;
static int      s_mac_rtc_set_calls;
static void stub_mac_rtc_set(uint32_t ms, uint8_t d, uint8_t mo, uint8_t y)
{
    (void)ms; (void)d; (void)mo; (void)y;
    s_mac_snapshot_ms = ms;
    s_mac_rtc_set_calls++;
}
static void stub_mac_get_rtc_snapshot(uint32_t *ms, uint8_t *d, uint8_t *mo, uint8_t *y)
{
    *ms = s_mac_snapshot_ms; *d = 0x01u; *mo = 0x01u; *y = 0x24u;
}

static void stub_sync_bootstrapped(uint8_t phase_idx, uint8_t cell_idx, uint32_t rtc_now_ms)
{
    TdmaMachine_BootstrapFromSync(phase_idx, cell_idx, rtc_now_ms);
}

static MAC_Hooks_t s_mac_hooks = {
    .rtc_set           = stub_mac_rtc_set,
    .get_rtc_snapshot  = stub_mac_get_rtc_snapshot,
    .sync_bootstrapped = NULL,
    .sync_locked       = NULL,
    .sync_lost         = NULL,
};

/* Stub TDMA: slot_active_ms=2500, gap[0]=500, per-slot step=3000 ms */
#define SLOT_ACTIVE_MS   2500u
#define GAP_MS            500u
#define SLOT_STEP_MS     3000u   /* SLOT_ACTIVE_MS + GAP_MS */
/* TdmaMachine_Start: the first slot starts one Tx lead after the wake. */
#define T0               TX_LEAD_MS
/* 10-byte SyncPayload at SF12/BW125/CR4-5, 8-symbol preamble (Radio.TimeOnAir) */
#define SYNC_TOA_MS       991u
/* Synced Rx window of a slot starting at 0: the latest packet start is
 * slot end + max guard - ToA; the cap is slot end + max guard. */
#define WIN_CAP_MS       (SLOT_ACTIVE_MS + MAX_GUARD_TIME_MS)
#define WIN_LAST_MS      (WIN_CAP_MS - SYNC_TOA_MS)

static void init_all(void)
{
    memset(&s_freq_state,  0, sizeof(s_freq_state));
    memset(&s_comp_status, 0, sizeof(s_comp_status));
    s_rtc_ms = 0u;
    s_alarm_programmed    = 0u;
    s_alarm_calls         = 0;
    s_channel_set         = 0u;
    s_channel_calls       = 0;
    s_radio_send_calls    = 0;
    s_rx_start_window_ms  = 0u;
    s_rx_cap_ms           = 0u;
    s_radio_set_rx_calls  = 0;
    s_radio_scan_calls    = 0;
    s_cancel_alarm_calls  = 0;
    s_toa_ms              = SYNC_TOA_MS;
    s_radio_sleep_calls   = 0;
    s_wait_until_ms_target = 0u;
    s_wait_until_ms_calls  = 0;
    memset(s_last_sent_buf, 0, sizeof(s_last_sent_buf));
    s_last_sent_len       = 0u;
    s_mac_snapshot_ms     = 0u;
    s_mac_rtc_set_calls   = 0;
    s_mac_hooks.sync_bootstrapped = NULL;  /* default: no bootstrap hook */
    StubDriftEstimator_Reset();            /* default: estimate not valid, guard = cap */
    ArcLog_CaptureReset();

    /* Static 868.1 MHz on both Sync phases (cell mode STATIC) */
    s_freq_state.phases[0].cell_mode           = CELL_FREQ_STATIC;
    s_freq_state.phases[0].cell_freq_or_seed = 868100000u;
    s_freq_state.phases[1].cell_mode           = CELL_FREQ_STATIC;
    s_freq_state.phases[1].cell_freq_or_seed = 868100000u;

    FrequencyResolver_Init(&s_freq_state);
    ComplianceEngine_Init(&s_comp_status, comp_get_tick);
    MAC_Init(&s_mac_hooks);
    TdmaMachine_Init(&k_platform);
}

/* Drive MAC to Synchronized state (CLOCK_WARM) using the stub table timings */
static void sync_mac(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index = 0u;
    s_mac_snapshot_ms  = 0u;
    p.sync_cell_index = 0u;  p.ms_since_midnight_sync_phase = 0u;
    MAC_OnSyncPacketReceived(&p, 0u);
    p.sync_cell_index = 1u;
    MAC_OnSyncPacketReceived(&p, 3000u);
    p.sync_cell_index = 2u;
    MAC_OnSyncPacketReceived(&p, 6000u);
}

/* Packet 1 only: CLOCK_COLD -> CLOCK_ACQUIRING (MAC still Scanning, every
 * slot Rx). No bootstrap hook, so the TDMA cursor stays at {0,0,0}. */
static void acquire_mac(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    MAC_OnSyncPacketReceived(&p, 0u);
}

/* Run the alarm chain from the current RTC and cursor, as C3 does at boot.
 * A cold MAC is first taken to CLOCK_ACQUIRING: the chain never runs cold. */
static void start_chain(void)
{
    if (MAC_GetClockState() == CLOCK_COLD) {
        acquire_mac();
    }
    TEST_ASSERT_TRUE(TdmaMachine_Start());
}

/* Call SlotTask after advancing simulated RTC to the last programmed alarm.
 * Like the RTC, the simulated clock wraps at midnight. */
static void step_slot(void)
{
    s_rtc_ms = s_alarm_programmed % MS_PER_DAY;
    TdmaMachine_SlotTask();
}

void setUp(void)    { init_all(); }
void tearDown(void) {}

/* =========================================================================
 * Init
 * ========================================================================= */

void test_init_sets_cursor_zero(void)
{
    FrameCursor_t c = TdmaMachine_GetCursor();
    TEST_ASSERT_EQUAL(0u, c.phase_index);
    TEST_ASSERT_EQUAL(0u, c.cell_index);
    TEST_ASSERT_EQUAL(0u, c.slot_index);
}

void test_init_sets_cursor_slot_pos_cell(void)
{
    /* Sync stub table has no header (duration_ms=0), so the cursor starts
     * at SLOT_POS_CELL, not SLOT_POS_HEADER. */
    FrameCursor_t c = TdmaMachine_GetCursor();
    TEST_ASSERT_EQUAL(SLOT_POS_CELL, c.slot_pos);
}

/* =========================================================================
 * Alarm timing — nominal TX path (MAC Synchronized, phase_tx_flag=1)
 * ========================================================================= */

void test_slot_task_programs_alarm_nominal_for_tx_slot(void)
{
    sync_mac();                       /* MAC → Synchronized */
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();
    /*
     * Cell 0 of Sync phase: always SLOT_RX for C2 (epoch not yet received).
     * DIRECTION_MAC_CELL + epoch not received → next_slot_is_rx=true → guard.
     * next_alarm = T0 + 2500 + 500 - MAX_GUARD_TIME_MS.
     */
    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

/* =========================================================================
 * Alarm timing — RX guard (MAC Scanning, CLOCK_ACQUIRING → epoch not received)
 * ========================================================================= */

void test_slot_task_rx_alarm_early_by_guard(void)
{
    /* MAC in Scanning (ACQUIRING): all decisions = RX; phase_tx_flag stays 0 */
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();
    /*
     * Decision = RX.  DIRECTION_MAC_CELL + epoch not received → next slot Rx.
     * alarm = T0 + SLOT_STEP_MS - MAX_GUARD_TIME_MS.
     */
    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

/* =========================================================================
 * Synced Rx window: open until the latest packet start that still ends by
 * slot end + MAX_GUARD_TIME_MS; the cap bounds any reception at that end.
 * ========================================================================= */

void test_rx_window_ends_at_latest_packet_start(void)
{
    s_rtc_ms = 0u;
    start_chain();
    ArcLog_CaptureReset();
    TdmaMachine_SlotTask();
    /* Slot at T0 = 20, woken at 0: last = 20 + 2500 + 100 - 991 = 1629,
     * cap = 2620 (both from now = 0) */
    TEST_ASSERT_EQUAL(1, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(T0 + WIN_LAST_MS, s_rx_start_window_ms);
    TEST_ASSERT_EQUAL(T0 + WIN_CAP_MS, s_rx_cap_ms);
    TEST_ASSERT_ARCLOG("RX_WIN last=1629 cap=2620");
}

void test_rx_window_measured_from_early_wake(void)
{
    /* Bootstrap on cell 0 at 0: next cell starts at 3000, woken at 2800. */
    acquire_mac();
    TdmaMachine_BootstrapFromSync(0u, 0u, 0u);
    step_slot();
    TEST_ASSERT_EQUAL(SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_rtc_ms);
    TEST_ASSERT_EQUAL(WIN_LAST_MS + MAX_GUARD_TIME_MS, s_rx_start_window_ms);
    TEST_ASSERT_EQUAL(WIN_CAP_MS + MAX_GUARD_TIME_MS, s_rx_cap_ms);
}

void test_rx_window_uses_max_guard_not_current_guard(void)
{
    /* The window end is a property of the slot grid (MAX_GUARD_TIME_MS),
     * independent of how early this node woke. Waking late (after nominal
     * start) shortens the window but keeps the same absolute end. */
    s_rtc_ms = 0u;
    start_chain();
    s_rtc_ms = 40u;                   /* 40 ms late, within the checkpoint */
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(T0 + WIN_LAST_MS - 40u, s_rx_start_window_ms);
    TEST_ASSERT_EQUAL(T0 + WIN_CAP_MS - 40u, s_rx_cap_ms);
}

void test_rx_window_too_late_sleeps_radio(void)
{
    /* A ToA longer than the slot plus max guard leaves no start instant. */
    s_toa_ms = T0 + WIN_CAP_MS + 1u;
    s_rtc_ms = 0u;
    start_chain();
    ArcLog_CaptureReset();
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(0, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(1, s_radio_sleep_calls);
    TEST_ASSERT_ARCLOG("RX_LATE now=0");
    TEST_ASSERT_GREATER_THAN(0, s_alarm_calls);   /* chain continues */
}

void test_rx_end_in_synced_slot_sleeps_radio(void)
{
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();
    TdmaMachine_OnRxEnd();
    TEST_ASSERT_EQUAL(1, s_radio_sleep_calls);
    TEST_ASSERT_EQUAL(0, s_radio_scan_calls);
}

/* =========================================================================
 * TX path — RadioSend called, compliance grants
 * ========================================================================= */

void test_slot_task_tx_calls_radio_send(void)
{
    /* C2 TX requires: CLOCK_WARM + epoch received at cell 0.
     * Step 1: sync_mac → WARM. Step 2: run cell 0 (RX).
     * Step 3: arm epoch. Step 4: run cell 1 → TX → RadioSend called. */
    sync_mac();
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();        /* cell 0 → SLOT_RX, no send */
    TEST_ASSERT_EQUAL(0, s_radio_send_calls);

    /* Arm epoch: simulate receiving a sync packet in the cell-0 RX window */
    SyncPayload_t arm;
    memset(&arm, 0, sizeof(arm));
    arm.sync_phase_index             = 0u;
    arm.ms_since_midnight_sync_phase = 0u;
    arm.sync_cell_index              = 0u;
    MAC_OnSyncPacketReceived(&arm, 0u);  /* error=0 → Tier 1 → epoch armed */

    step_slot();                   /* cell 1 → SLOT_TX */
    TEST_ASSERT_EQUAL(1, s_radio_send_calls);
}

/* =========================================================================
 * Compliance — RESTRICTED → RadioSend skipped, alarm still programmed
 * ========================================================================= */

void test_slot_task_compliance_skip_no_radio_send(void)
{
    sync_mac();
    /* Exhaust all compliance credit before SlotTask runs */
    ComplianceEngine_RequestChannel(868100000u, 36000u, 14);

    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();

    TEST_ASSERT_EQUAL(0, s_radio_send_calls);
}

void test_slot_task_compliance_skip_programs_alarm(void)
{
    sync_mac();
    ComplianceEngine_RequestChannel(868100000u, 36000u, 14);

    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();

    TEST_ASSERT_GREATER_THAN(0, s_alarm_calls);
}

/* =========================================================================
 * RadioSetChannel called with FrequencyResolver result
 * ========================================================================= */

void test_slot_task_calls_radio_set_channel(void)
{
    s_rtc_ms = 0u;
    start_chain();
    s_channel_calls = 0;                 /* Start sets cell 0's channel too (#70) */
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(1,           s_channel_calls);
    TEST_ASSERT_EQUAL(868100000u,  s_channel_set);
}

/* =========================================================================
 * Cursor advancement across three slots
 * ========================================================================= */

void test_cursor_advances_cell_index(void)
{
    /* Slot 0 → cell_index becomes 1 */
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(1u, TdmaMachine_GetCursor().cell_index);

    /* Slot 1 → cell_index becomes 2 */
    step_slot();
    TEST_ASSERT_EQUAL(2u, TdmaMachine_GetCursor().cell_index);
}

void test_frame_wrap_resets_cursor(void)
{
    /* Twenty SlotTask calls exhaust both 10-cell Sync phases → frame wrap.
     * 10 cells × 2 phases = 20 slots per frame. */
    s_rtc_ms = 0u; start_chain(); TdmaMachine_SlotTask();
    for (int i = 0; i < 19; i++) {
        step_slot();
    }

    FrameCursor_t c = TdmaMachine_GetCursor();
    TEST_ASSERT_EQUAL(0u, c.phase_index);
    TEST_ASSERT_EQUAL(0u, c.cell_index);
    TEST_ASSERT_EQUAL(0u, c.slot_index);
}

/* =========================================================================
 * Cursor integrity checkpoint
 * ========================================================================= */

void test_cursor_integrity_clean_on_expected_wake(void)
{
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();                /* programs alarm, stores expected wake */

    s_rtc_ms = s_alarm_programmed;         /* wake exactly on time */
    TdmaMachine_SlotTask();

    TEST_ASSERT_FALSE(TdmaMachine_IsCursorSuspect());
}

void test_cursor_suspect_on_implausible_delta(void)
{
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();                /* expected_wake = s_alarm_programmed */

    /* Arrive 4000 ms late: 4000 > 1.5 × 2500 = 3750 → suspect */
    s_rtc_ms = s_alarm_programmed + 4000u;
    TdmaMachine_SlotTask();

    TEST_ASSERT_TRUE(TdmaMachine_IsCursorSuspect());
}

/* =========================================================================
 * BootstrapFromSync
 * ========================================================================= */

void test_bootstrap_cursor_positioned_at_next_cell(void)
{
    /* Table has 10 cells per Sync phase. Bootstrap at cell=1 → advance → cell=2.
     * (cell=9 is the last cell; advancing past it moves to phase 1.) */
    TdmaMachine_BootstrapFromSync(0u, 1u, 3000u);
    TEST_ASSERT_EQUAL(2u, TdmaMachine_GetCursor().cell_index);
    TEST_ASSERT_EQUAL(0u, TdmaMachine_GetCursor().phase_index);
}

void test_bootstrap_next_alarm_accounts_for_received_cell(void)
{
    /* slot_start=3000, slot_active=2500, gap=500 → nominal=6000, guard applied */
    TdmaMachine_BootstrapFromSync(0u, 1u, 3000u);
    TEST_ASSERT_EQUAL(6000u - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

void test_bootstrap_cell0_cursor_at_cell1(void)
{
    /* Receive Packet 1 at cell 0, slot_start=0 → cursor must advance to cell 1. */
    TdmaMachine_BootstrapFromSync(0u, 0u, 0u);
    TEST_ASSERT_EQUAL(0u, TdmaMachine_GetCursor().phase_index);
    TEST_ASSERT_EQUAL(1u, TdmaMachine_GetCursor().cell_index);
}

void test_bootstrap_cell0_alarm_one_step(void)
{
    /* Receive at cell 0, slot_start=0 → nominal = SLOT_STEP_MS, guard applied */
    TdmaMachine_BootstrapFromSync(0u, 0u, 0u);
    TEST_ASSERT_EQUAL(SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

void test_bootstrap_last_cell_advances_to_sync1(void)
{
    /* Receive at cell 9 (last cell of Sync0), slot_start=27000.
     * advance_cursor moves to Sync1 (phase 1, cell 0) — same frame,
     * NOT a frame wrap to phase 0. */
    TdmaMachine_BootstrapFromSync(0u, 9u, 27000u);
    TEST_ASSERT_EQUAL(1u, TdmaMachine_GetCursor().phase_index);
    TEST_ASSERT_EQUAL(0u, TdmaMachine_GetCursor().cell_index);
}

void test_bootstrap_last_cell_alarm_one_step(void)
{
    /* Receive at cell 9, slot_start=27000.
     * nominal = 27000 + 3000 = 30000, guard applied */
    TdmaMachine_BootstrapFromSync(0u, 9u, 27000u);
    TEST_ASSERT_EQUAL(27000u + SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

void test_sync_tx_payload_fields_match_cursor(void)
{
    /* C2 can only TX at cells 1+ after receiving epoch at cell 0.
     * Sequence: sync_mac → run cell 0 (RX) → arm epoch → run cell 1 (TX). */
    sync_mac();
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();   /* cell 0 → RX, no send */

    /* Arm epoch */
    SyncPayload_t arm;
    memset(&arm, 0, sizeof(arm));
    arm.sync_phase_index             = 0u;
    arm.ms_since_midnight_sync_phase = 0u;
    arm.sync_cell_index              = 0u;
    MAC_OnSyncPacketReceived(&arm, 0u);

    step_slot();              /* cell 1 → TX */
    TEST_ASSERT_EQUAL(1, s_radio_send_calls);
    TEST_ASSERT_EQUAL(10u, s_last_sent_len);
    SyncPayload_t pkt;
    memcpy(&pkt, s_last_sent_buf, sizeof(pkt));
    TEST_ASSERT_EQUAL(0u, pkt.sync_phase_index);
    TEST_ASSERT_EQUAL(1u, pkt.sync_cell_index);  /* cursor at cell 1 when TX */
}

/* =========================================================================
 * State-aware guard: no guard when epoch received (next slot is Tx)
 * ========================================================================= */

void test_no_guard_when_epoch_received(void)
{
    sync_mac();
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();   /* cell 0 → RX, epoch not received → guard */

    /* Arm epoch: receive Tier 1 sync packet in cell-0 RX window */
    SyncPayload_t arm;
    memset(&arm, 0, sizeof(arm));
    arm.sync_phase_index             = 0u;
    arm.ms_since_midnight_sync_phase = 0u;
    arm.sync_cell_index              = 0u;
    MAC_OnSyncPacketReceived(&arm, 0u);  /* error=0 → Tier 1 → epoch armed */

    step_slot();   /* cell 1 → TX (epoch received) */
    /*
     * Alarm for cell 2: epoch received → next slot is Tx → no guard, one
     * Tx lead: alarm = T0 + 2 × SLOT_STEP_MS - TX_LEAD_MS.
     */
    TEST_ASSERT_EQUAL(T0 + 2u * SLOT_STEP_MS - TX_LEAD_MS, s_alarm_programmed);
}

/* =========================================================================
 * Next wake re-decided at Rx end: the alarm for the next cell is programmed
 * at the start of the current one, before its packet arrives. A Tier 1
 * epoch received in this cell turns the next cell into Tx, so the node wakes
 * one Tx lead early instead of one guard early.
 * ========================================================================= */

void test_rx_end_with_epoch_drops_guard_from_next_wake(void)
{
    sync_mac();
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();   /* cell 0 → RX, alarm = T0 + SLOT_STEP_MS - guard */
    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);

    SyncPayload_t arm;
    memset(&arm, 0, sizeof(arm));
    arm.ms_since_midnight_sync_phase = T0;
    MAC_OnSyncPacketReceived(&arm, T0);  /* Tier 1 → epoch armed */
    ArcLog_CaptureReset();
    s_rtc_ms = 1000u;                    /* RxDone, ~one airtime later */
    TdmaMachine_OnRxEnd();

    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS - TX_LEAD_MS, s_alarm_programmed);
    TEST_ASSERT_ARCLOG("WAKE_ADJ from=2920 to=3000");

    step_slot();              /* cell 1 → TX, fired at the nominal start */
    TEST_ASSERT_EQUAL(1, s_wait_until_ms_calls);
    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS, s_wait_until_ms_target);
    TEST_ASSERT_EQUAL(1, s_radio_send_calls);
    TEST_ASSERT_ARCLOG("SYNC_TX ph=0 ce=1 ep=20 plan=3020 send=3020");
    TEST_ASSERT_NO_ARCLOG("TX_LATE");
}

void test_rx_end_without_epoch_keeps_guarded_wake(void)
{
    sync_mac();
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();   /* cell 0 → RX, no packet */
    int calls = s_alarm_calls;
    s_rtc_ms = 1900u;         /* RX_TIMEOUT */
    TdmaMachine_OnRxEnd();

    TEST_ASSERT_EQUAL(calls, s_alarm_calls);
    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

/* =========================================================================
 * TX delayed to nominal when woke early (guard applied + MAC decides Tx)
 * ========================================================================= */

void test_tx_delayed_to_nominal_when_woke_early(void)
{
    sync_mac();
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();   /* cell 0 → RX, alarm = T0 + SLOT_STEP_MS - GUARD */

    /* Arm epoch */
    SyncPayload_t arm;
    memset(&arm, 0, sizeof(arm));
    arm.sync_phase_index             = 0u;
    arm.ms_since_midnight_sync_phase = 0u;
    arm.sync_cell_index              = 0u;
    MAC_OnSyncPacketReceived(&arm, 0u);

    step_slot();   /* cell 1: woke early (guard), MAC decides Tx */
    /*
     * WaitUntilMs must be called with the nominal slot start (T0 + 3000),
     * the platform's Tx ramp being 0. The stub advances s_rtc_ms to it.
     */
    TEST_ASSERT_EQUAL(1, s_wait_until_ms_calls);
    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS, s_wait_until_ms_target);
    TEST_ASSERT_EQUAL(1, s_radio_send_calls);
}

/* =========================================================================
 * Bootstrap applies guard time on first alarm
 * ========================================================================= */

void test_bootstrap_applies_guard_on_alarm(void)
{
    /* After receiving Packet 1 at cell 0 (slot_start=0), the next alarm must
     * subtract guard: alarm = SLOT_STEP_MS - MAX_GUARD_TIME_MS. */
    TdmaMachine_BootstrapFromSync(0u, 0u, 0u);
    TEST_ASSERT_EQUAL(SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

/* =========================================================================
 * Cursor integrity checkpoint (suspect wake)
 *
 * A wake far from the expected time (> 1.5 x slot_active_ms) means the
 * FrameCursor can no longer be trusted. The TDMA Machine must drop the MAC
 * to re-acquisition WITHOUT writing the RTC (the former zero-SyncPayload
 * injection reset the clock to 00:00:00 with an invalid date), and must
 * resume the alarm chain from the actual wake time so the next alarm is in
 * the future.
 * ========================================================================= */

void test_suspect_wake_drops_to_cold_without_rtc_write(void)
{
    sync_mac();
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    int rtc_sets_before = s_mac_rtc_set_calls;

    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();                  /* next alarm 3020 - guard = 2920 */
    s_rtc_ms = s_alarm_programmed + 4000u;   /* 4000 ms late > 3750 */
    ArcLog_CaptureReset();
    TdmaMachine_SlotTask();

    TEST_ASSERT_TRUE(TdmaMachine_IsCursorSuspect());
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
    TEST_ASSERT_EQUAL(rtc_sets_before, s_mac_rtc_set_calls);
    TEST_ASSERT_ARCLOG("SLOT_SUSPECT exp=2920 now=6920");
    TEST_ASSERT_ARCLOG("CLK from=WARM to=COLD why=suspect");
}

void test_suspect_wake_stops_chain_and_scans(void)
{
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();
    int alarms_before = s_alarm_calls;

    s_rtc_ms = s_alarm_programmed + 4000u;   /* 2920 + 4000 = 6920 */
    ArcLog_CaptureReset();
    TdmaMachine_SlotTask();

    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(alarms_before, s_alarm_calls);  /* no next alarm */
    TEST_ASSERT_EQUAL(1, s_cancel_alarm_calls);
    TEST_ASSERT_EQUAL(1, s_radio_scan_calls);
    TEST_ASSERT_EQUAL(SCAN_FREQ_HZ, s_channel_set);
    TEST_ASSERT_EQUAL(1, s_radio_set_rx_calls);       /* first slot only */
    TEST_ASSERT_ARCLOG("SCAN freq=868300000 why=lost");
}

/* =========================================================================
 * Scanning: no alarm chain while CLOCK_COLD
 *
 * A cold node has no schedule position, so it listens continuously on the
 * discovery channel and the MCU sleeps until the radio wakes it. The chain
 * starts on the first Sync packet (BootstrapFromSync) and stops whenever
 * the clock drops back to CLOCK_COLD.
 * ========================================================================= */

void test_start_cold_enters_scanning(void)
{
    ArcLog_CaptureReset();
    TEST_ASSERT_FALSE(TdmaMachine_Start());
    TEST_ASSERT_EQUAL(0, s_alarm_calls);
    TEST_ASSERT_EQUAL(1, s_radio_scan_calls);
    TEST_ASSERT_EQUAL(0, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(SCAN_FREQ_HZ, s_channel_set);
    TEST_ASSERT_ARCLOG("SCAN freq=868300000 why=boot");
}

void test_slot_task_is_inert_while_scanning(void)
{
    /* A stale alarm task (chain stopped while it was pending) must leave
     * the scanning radio alone. */
    (void)TdmaMachine_Start();
    s_rtc_ms = 5000u;
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(0, s_alarm_calls);
    TEST_ASSERT_EQUAL(1, s_channel_calls);
    TEST_ASSERT_EQUAL(1, s_radio_scan_calls);
    TEST_ASSERT_EQUAL(0, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(0, s_radio_sleep_calls);
}

void test_rx_end_while_scanning_rearms_scan(void)
{
    (void)TdmaMachine_Start();
    TdmaMachine_OnRxEnd();   /* e.g. CRC error or a non-Sync packet */
    TdmaMachine_OnRxEnd();
    TEST_ASSERT_EQUAL(3, s_radio_scan_calls);
    TEST_ASSERT_EQUAL(1, s_channel_calls);   /* channel set once on entry */
    TEST_ASSERT_EQUAL(0, s_radio_sleep_calls);
    TEST_ASSERT_EQUAL(0, s_alarm_calls);
}

void test_first_sync_packet_starts_chain(void)
{
    s_mac_hooks.sync_bootstrapped = stub_sync_bootstrapped;
    MAC_Init(&s_mac_hooks);
    (void)TdmaMachine_Start();

    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    MAC_OnSyncPacketReceived(&p, 0u);   /* Packet 1: cell 0 */
    TdmaMachine_OnRxEnd();

    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_alarm_calls);
    TEST_ASSERT_EQUAL(SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);
    TEST_ASSERT_EQUAL(1, s_radio_scan_calls);    /* not re-armed */
    TEST_ASSERT_EQUAL(1, s_radio_sleep_calls);   /* idle until cell 1 */

    step_slot();                                 /* cell 1: bounded Rx */
    TEST_ASSERT_EQUAL(1, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(868100000u, s_channel_set);
}

void test_silence_timeout_stops_chain_and_scans(void)
{
    s_rtc_ms = 0u;
    start_chain();                       /* ACQUIRING, last Sync at 0 */
    TdmaMachine_SlotTask();
    while (MAC_GetClockState() != CLOCK_COLD
           && s_rtc_ms < SYNC_SILENCE_TIMEOUT_MS + SLOT_STEP_MS) {
        ArcLog_CaptureReset();
        step_slot();
    }
    int alarms_after = s_alarm_calls;

    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_ARCLOG("CLK from=ACQ to=COLD why=silence");
    TEST_ASSERT_ARCLOG("SCAN freq=868300000 why=lost");
    TEST_ASSERT_EQUAL(1, s_cancel_alarm_calls);
    TEST_ASSERT_EQUAL(1, s_radio_scan_calls);

    step_slot();                         /* stale wake: inert */
    TEST_ASSERT_EQUAL(alarms_after, s_alarm_calls);
}

void test_tier3_on_rx_end_stops_chain_and_scans(void)
{
    sync_mac();                          /* WARM */
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();              /* cell 0 Rx window */

    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    MAC_OnSyncPacketReceived(&p, 500u);  /* 500 ms off: Tier 3 → COLD */
    ArcLog_CaptureReset();
    TdmaMachine_OnRxEnd();

    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_cancel_alarm_calls);
    TEST_ASSERT_EQUAL(1, s_radio_scan_calls);
    TEST_ASSERT_EQUAL(0, s_radio_sleep_calls);
    TEST_ASSERT_ARCLOG("SCAN freq=868300000 why=lost");
}

/* =========================================================================
 * Slot-boundary alignment: alarm uses nominal_start_ms, not hardware readback
 *
 * BootstrapFromSync must base the alarm on the schedule-derived nominal cell
 * start (target_ms), not on the hardware readback from get_rtc_snapshot.
 * The readback includes per-node execution latency (~200us of SetTime +
 * SHIFTR + register read). If the alarm used the readback, every node would
 * wake at a slightly different absolute time, drifting off the slot grid.
 * ========================================================================= */

void test_bootstrap_alarm_uses_nominal_not_readback(void)
{
    /* Simulate execution latency: rtc_set is called with target_ms=3000,
     * but get_rtc_snapshot returns 3000 + 2 (2 ms latency). */
    s_mac_hooks.sync_bootstrapped = stub_sync_bootstrapped;
    MAC_Init(&s_mac_hooks);
    TdmaMachine_Init(&k_platform);

    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.sync_cell_index              = 1u;
    p.ms_since_midnight_sync_phase = 0u;

    /* target_ms = 0 + 1 * 3000 = 3000 (nominal cell 1 start) */
    s_mac_snapshot_ms = 3002u;  /* hardware readback = target + 2ms latency */

    MAC_OnSyncPacketReceived(&p, 3000u);

    /* Alarm must be based on target_ms (3000), not readback (3002):
     * nominal_next = 3000 + 3000 = 6000, guard applied = 6000 - 5 = 5995.
     * If it used readback: 3002 + 3000 = 6002, guard = 5997 (wrong). */
    TEST_ASSERT_EQUAL(6000u - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

/* =========================================================================
 * main
 * ========================================================================= */

/* =========================================================================
 * Midnight rollover (issue #54): the RTC wraps at MS_PER_DAY; slot times,
 * alarms, windows and checks must stay in that day domain.
 * ========================================================================= */

void test_chain_runs_across_midnight(void)
{
    /* Packet 1 at cell 0 of a phase starting at 23:59:51: cells 1-5 start at
     * 23:59:54, 23:59:57, 00:00:00, 00:00:03, 00:00:06. */
    s_mac_hooks.sync_bootstrapped = stub_sync_bootstrapped;
    MAC_Init(&s_mac_hooks);
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.ms_since_midnight_sync_phase = MS_PER_DAY - 9000u;
    MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 9000u);
    TdmaMachine_OnRxEnd();
    ArcLog_CaptureReset();

    for (int i = 0; i < 5; i++) {
        TEST_ASSERT_LESS_THAN_UINT32(MS_PER_DAY, s_alarm_programmed);
        step_slot();
        /* Woken one guard early: the window always spans the same length. */
        TEST_ASSERT_EQUAL(WIN_LAST_MS + MAX_GUARD_TIME_MS, s_rx_start_window_ms);
        TEST_ASSERT_EQUAL(WIN_CAP_MS + MAX_GUARD_TIME_MS, s_rx_cap_ms);
    }
    TEST_ASSERT_NO_ARCLOG("SLOT_SUSPECT");
    TEST_ASSERT_NO_ARCLOG("RX_LATE");
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());
    TEST_ASSERT_EQUAL(6u, TdmaMachine_GetCursor().cell_index);
    TEST_ASSERT_EQUAL(9000u - MAX_GUARD_TIME_MS, s_alarm_programmed);  /* cell 6 */
}

void test_bootstrap_just_before_midnight_wakes_before_it(void)
{
    /* Packet 1 at 23:59:57.050: the next cell starts at 00:00:00.050, so
     * its guarded wake is 50 ms - guard before midnight, in the day domain. */
    acquire_mac();
    TdmaMachine_BootstrapFromSync(0u, 0u, MS_PER_DAY - 2950u);
    TEST_ASSERT_EQUAL(MS_PER_DAY - (MAX_GUARD_TIME_MS - 50u), s_alarm_programmed);
    step_slot();
    TEST_ASSERT_FALSE(TdmaMachine_IsCursorSuspect());
    TEST_ASSERT_EQUAL(3050u - MAX_GUARD_TIME_MS, s_alarm_programmed);
}

/* =========================================================================
 * Guard from the drift estimate (issue #36)
 * ========================================================================= */

/* An estimate as the estimator reports it: valid, with this residual and noise. */
static DriftEstimate_t valid_est(int32_t residual_ppb, uint32_t noise_us)
{
    DriftEstimate_t e = { true, 0, residual_ppb, noise_us, 40u, 1800u };
    return e;
}

/* CLOCK_WARM with a valid estimate: the window ends one guard after nominal,
 * not at the latest start the slot geometry allows. This estimate (360 ppb,
 * 106 us) is worth 5 ms by the formula, so the 8 ms floor of the Tier 1 band
 * sets the guard. Slot at T0 = 20, woken at 0: last = 20 + 8. The cap, the
 * hard end of the slot, does not move. */
void test_warm_window_ends_one_guard_after_nominal(void)
{
    sync_mac();
    StubDriftEstimator_Set(valid_est(360, 106u));
    s_rtc_ms = 0u;
    start_chain();
    ArcLog_CaptureReset();
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(1, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(T0 + 8u, s_rx_start_window_ms);
    TEST_ASSERT_EQUAL(T0 + WIN_CAP_MS, s_rx_cap_ms);
}

/* The early wake for an Rx slot is one guard before nominal. A valid estimate
 * with 2 ms of noise is worth 3 x (3 x 2000 + 245 + 1000) = 21.7 ms, so 22 ms
 * (a drift-free residual keeps the age out of it): cell 1 starts at 3020 and
 * the node wakes at 2998, not at 2920 as with the 100 ms cap. */
void test_the_early_wake_is_one_guard_before_nominal(void)
{
    sync_mac();
    StubDriftEstimator_Set(valid_est(0, 2000u));
    s_rtc_ms = 0u;
    start_chain();
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS - 22u, s_alarm_programmed);
}

/* The estimate survives a re-acquisition, but a node whose clock was just set
 * from Packet 1 does not know yet that it is locked: while acquiring, the guard
 * stays the cap and the window keeps the geometric end, whatever the estimate
 * says (22 ms here). */
void test_acquiring_keeps_the_maximum_guard_whatever_the_estimate(void)
{
    StubDriftEstimator_Set(valid_est(0, 2000u));
    s_rtc_ms = 0u;
    start_chain();                    /* a cold MAC is taken to CLOCK_ACQUIRING */
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(T0 + SLOT_STEP_MS - MAX_GUARD_TIME_MS, s_alarm_programmed);
    TEST_ASSERT_EQUAL(T0 + WIN_LAST_MS, s_rx_start_window_ms);
}

/* The drift grows with the time since the last Sync reception, which the MAC
 * knows. The last packet was stamped at 6000 (sync_mac); the slot starts at
 * 546 000, 540 s later. With 5 ppm of residual and 106 us of noise that is
 * 13 ms (the PROD-period case of the guard policy), so the window ends at
 * 546 000 + 13 and, woken at 545 980, stays open for 33 ms. With the age
 * ignored the guard would be the 8 ms floor, and the window 28 ms. */
void test_the_guard_grows_with_the_time_since_the_last_sync(void)
{
    sync_mac();
    StubDriftEstimator_Set(valid_est(5000, 106u));
    s_rtc_ms = 546000u - T0;
    start_chain();
    TdmaMachine_SlotTask();
    TEST_ASSERT_EQUAL(1, s_radio_set_rx_calls);
    TEST_ASSERT_EQUAL(T0 + 13u, s_rx_start_window_ms);
}

/* The trace says what each Rx window used, so that a report can give the guard
 * distribution and tell a mistimed loss from a radio one: g, the guard in ms,
 * and win, the span handed to the radio (the latest packet start, from the wake;
 * the platform adds the preamble margin). WARM, the 8 ms floor, woken at 0 for
 * a slot at 20: last = 28, win = 28. */
void test_rx_win_logs_the_guard_and_the_window_when_warm(void)
{
    sync_mac();
    StubDriftEstimator_Set(valid_est(360, 106u));
    s_rtc_ms = 0u;
    start_chain();
    ArcLog_CaptureReset();
    TdmaMachine_SlotTask();
    TEST_ASSERT_ARCLOG("RX_WIN last=28 cap=2620 g=8 win=28");
}

/* While acquiring the guard is the cap and the window the geometric one:
 * last = 20 + 2500 + 100 - 991 = 1629, win = 1629 from a wake at 0. */
void test_rx_win_logs_the_cap_as_guard_while_acquiring(void)
{
    s_rtc_ms = 0u;
    start_chain();
    ArcLog_CaptureReset();
    TdmaMachine_SlotTask();
    TEST_ASSERT_ARCLOG("RX_WIN last=1629 cap=2620 g=100 win=1629");
}

/* A wake too late for any packet to fit logs the guard too, so every Rx wake
 * has one. The ToA (20 + 2600 + 1 = 2621 ms) exceeds the slot: with the cap at
 * 2620, last = 2620 - 2621 = -1, a day-wrapped 86399999. */
void test_rx_late_logs_the_guard(void)
{
    s_toa_ms = T0 + WIN_CAP_MS + 1u;
    s_rtc_ms = 0u;
    start_chain();
    ArcLog_CaptureReset();
    TdmaMachine_SlotTask();
    TEST_ASSERT_ARCLOG("RX_LATE now=0 last=86399999 g=100");
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_sets_cursor_zero);
    RUN_TEST(test_init_sets_cursor_slot_pos_cell);
    RUN_TEST(test_slot_task_programs_alarm_nominal_for_tx_slot);
    RUN_TEST(test_slot_task_rx_alarm_early_by_guard);
    RUN_TEST(test_rx_window_ends_at_latest_packet_start);
    RUN_TEST(test_rx_window_measured_from_early_wake);
    RUN_TEST(test_rx_window_uses_max_guard_not_current_guard);
    RUN_TEST(test_rx_window_too_late_sleeps_radio);
    RUN_TEST(test_rx_end_in_synced_slot_sleeps_radio);
    RUN_TEST(test_slot_task_tx_calls_radio_send);
    RUN_TEST(test_slot_task_compliance_skip_no_radio_send);
    RUN_TEST(test_slot_task_compliance_skip_programs_alarm);
    RUN_TEST(test_slot_task_calls_radio_set_channel);
    RUN_TEST(test_cursor_advances_cell_index);
    RUN_TEST(test_frame_wrap_resets_cursor);
    RUN_TEST(test_cursor_integrity_clean_on_expected_wake);
    RUN_TEST(test_cursor_suspect_on_implausible_delta);
    RUN_TEST(test_bootstrap_cursor_positioned_at_next_cell);
    RUN_TEST(test_bootstrap_next_alarm_accounts_for_received_cell);
    RUN_TEST(test_bootstrap_cell0_cursor_at_cell1);
    RUN_TEST(test_bootstrap_cell0_alarm_one_step);
    RUN_TEST(test_bootstrap_last_cell_advances_to_sync1);
    RUN_TEST(test_bootstrap_last_cell_alarm_one_step);
    RUN_TEST(test_sync_tx_payload_fields_match_cursor);

/* ------- State-aware guard ----------------------------------------------- */

    RUN_TEST(test_no_guard_when_epoch_received);

/* ------- TX delay to nominal -------------------------------------------- */

    RUN_TEST(test_tx_delayed_to_nominal_when_woke_early);
    RUN_TEST(test_rx_end_with_epoch_drops_guard_from_next_wake);
    RUN_TEST(test_rx_end_without_epoch_keeps_guarded_wake);
/* ------- Bootstrap guard ----------------------------------------------- */

    RUN_TEST(test_bootstrap_applies_guard_on_alarm);

/* ------- Double-advance guard ------------------------------------------- */

    RUN_TEST(test_suspect_wake_drops_to_cold_without_rtc_write);
    RUN_TEST(test_suspect_wake_stops_chain_and_scans);

/* ------- Scanning ------------------------------------------------------- */

    RUN_TEST(test_start_cold_enters_scanning);
    RUN_TEST(test_slot_task_is_inert_while_scanning);
    RUN_TEST(test_rx_end_while_scanning_rearms_scan);
    RUN_TEST(test_first_sync_packet_starts_chain);
    RUN_TEST(test_silence_timeout_stops_chain_and_scans);
    RUN_TEST(test_tier3_on_rx_end_stops_chain_and_scans);

/* ------- Slot-boundary alignment ---------------------------------------- */

    RUN_TEST(test_bootstrap_alarm_uses_nominal_not_readback);
    RUN_TEST(test_chain_runs_across_midnight);
    RUN_TEST(test_bootstrap_just_before_midnight_wakes_before_it);
    RUN_TEST(test_warm_window_ends_one_guard_after_nominal);
    RUN_TEST(test_the_early_wake_is_one_guard_before_nominal);
    RUN_TEST(test_acquiring_keeps_the_maximum_guard_whatever_the_estimate);
    RUN_TEST(test_the_guard_grows_with_the_time_since_the_last_sync);
    RUN_TEST(test_rx_win_logs_the_guard_and_the_window_when_warm);
    RUN_TEST(test_rx_win_logs_the_cap_as_guard_while_acquiring);
    RUN_TEST(test_rx_late_logs_the_guard);
    return UNITY_END();
}
