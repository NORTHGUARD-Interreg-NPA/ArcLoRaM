#include "unity.h"
#include "mac_state_machine.h"
#include "tdma_table.h"
#include "arclog_capture.h"
#include "guard_time_resolver.h"   /* SYNC_RESYNC_THRESHOLD_MS */
#include <string.h>

/* ------- hook stubs ------------------------------------------------------- */

static int      s_rtc_set_calls;
static int      s_sync_locked_calls;
static int      s_sync_lost_calls;
static uint32_t s_snapshot_ms;
static uint8_t  s_rtc_set_day;
static uint8_t  s_rtc_set_month;
static uint8_t  s_rtc_set_year;

static void stub_rtc_set(uint32_t target_ms,
                          uint8_t day, uint8_t month, uint8_t year)
{
    s_rtc_set_day   = day;
    s_rtc_set_month = month;
    s_rtc_set_year  = year;
    s_snapshot_ms = target_ms;
    s_rtc_set_calls++;
}

static void stub_get_rtc_snapshot(uint32_t *ms,
                                   uint8_t *day, uint8_t *month, uint8_t *year)
{
    *ms = s_snapshot_ms; *day = 0x01u; *month = 0x01u; *year = 0x24u;
}

static void stub_sync_locked(void) { s_sync_locked_calls++; }
static void stub_sync_lost(void)   { s_sync_lost_calls++;   }

static const MAC_Hooks_t k_hooks = {
    .rtc_set           = stub_rtc_set,
    .get_rtc_snapshot  = stub_get_rtc_snapshot,
    .sync_bootstrapped = NULL,
    .sync_locked       = stub_sync_locked,
    .sync_lost         = stub_sync_lost,
};

/* ------- helpers ---------------------------------------------------------- */

/*
 * Stub TDMA table: PHASE_TYPE_SYNC, slot_active_ms=2500, gap_after_slot_ms=500
 * Per-cell step = 3000 ms. Drive to CLOCK_WARM.
 */
static void three_sync_packets(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    s_snapshot_ms                  = 0u;

    p.sync_cell_index = 0u;  MAC_OnSyncPacketReceived(&p, 0u);
    p.sync_cell_index = 1u;  MAC_OnSyncPacketReceived(&p, 3000u);
    p.sync_cell_index = 2u;  MAC_OnSyncPacketReceived(&p, 6000u);
}

void setUp(void)
{
    s_rtc_set_calls    = 0;
    s_sync_locked_calls = 0;
    s_sync_lost_calls  = 0;
    s_snapshot_ms      = 0u;
    MAC_Init(&k_hooks);
}

void tearDown(void) {}

/* ------- Boot state ------------------------------------------------------- */

void test_c1_boot_state_is_scanning(void)
{
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
}

void test_c1_boot_clock_state_is_cold(void)
{
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
}

/* ------- Scanning — always RX --------------------------------------------- */

void test_c1_scanning_returns_rx_for_sync_phase(void)
{
    FrameCursor_t c = {.phase_index = 0u, .cell_index = 0u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c, TdmaTable_GetPhase(0u), 0u));
}

void test_c1_scanning_returns_rx_for_any_phase(void)
{
    static const Phase_t s_cluster = {
        .type           = PHASE_TYPE_CLUSTER_EXCHANGE,
        .direction_mode = DIRECTION_CELL_SKIP,
        .cell_count     = 4u,
        .slot_active_ms = 1000u,
    };
    FrameCursor_t c = {.phase_index = 1u, .cell_index = 0u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c, &s_cluster, 0u));
}

/* ------- Sync acquisition ------------------------------------------------- */

void test_c1_sync_pkt1_transitions_acquiring(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_cell_index = 0u;
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());
}

void test_c1_sync_pkt1_calls_rtc_set_hook(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_cell_index = 0u;
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(1, s_rtc_set_calls);
}

void test_c1_sync_pkt3_valid_transitions_warm(void)
{
    three_sync_packets();
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
}

void test_c1_sync_pkt3_valid_transitions_synchronized(void)
{
    three_sync_packets();
    TEST_ASSERT_EQUAL(MAC_STATE_SYNCHRONIZED, MAC_GetState());
}

void test_c1_sync_locked_called_once(void)
{
    three_sync_packets();
    TEST_ASSERT_EQUAL(1, s_sync_locked_calls);
}

void test_c1_acquiring_bad_packet_drops_to_cold(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    s_snapshot_ms                  = 0u;
    p.sync_cell_index = 0u;  MAC_OnSyncPacketReceived(&p, 0u);
    p.sync_cell_index = 1u;  MAC_OnSyncPacketReceived(&p, 3000u);
    /* cell=2, expected=6000, stamp=6100 → error=100 ≥ 8ms → re-acquire */
    p.sync_cell_index = 2u;  MAC_OnSyncPacketReceived(&p, 6100u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_locked_calls);
}

void test_c1_acquiring_checks_packet_against_its_own_epoch(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index = 0u;
    s_snapshot_ms      = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    p.sync_cell_index = 1u;  MAC_OnSyncPacketReceived(&p, 3000u);   /* Packet 1 */
    p.ms_since_midnight_sync_phase = 30000u;
    p.sync_cell_index = 0u;  MAC_OnSyncPacketReceived(&p, 30002u);
    p.sync_cell_index = 1u;  MAC_OnSyncPacketReceived(&p, 33002u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
}

/* ------- SYNC_LOST (Tier 3: error >= MAX_GUARD_TIME_MS) --------------------- */

void test_c1_sync_lost_resets_to_cold(void)
{
    three_sync_packets();
    /* cell=0, ms_midnight=0, expected_arrival=0, stamp=400 -> error=400 >= SYNC_RESYNC_THRESHOLD_MS -> Tier 3 */
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    MAC_OnSyncPacketReceived(&p, 400u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
}

void test_c1_sync_lost_returns_to_scanning(void)
{
    three_sync_packets();
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    MAC_OnSyncPacketReceived(&p, 400u);
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
}

void test_c1_sync_lost_calls_hook(void)
{
    three_sync_packets();
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    MAC_OnSyncPacketReceived(&p, 400u);
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

/* ------- phase_tx_flag = 0 for C1 ---------------------------------------- */

void test_c1_phase_tx_flag_always_zero(void)
{
    three_sync_packets();
    FrameCursor_t c = {.phase_index = 0u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c, TdmaTable_GetPhase(0u), 0u);
    TEST_ASSERT_EQUAL(0u, MAC_GetPhaseTxFlag());

    FrameCursor_t c2 = {.phase_index = 1u, .cell_index = 0u, .slot_index = 0u};
    static const Phase_t s_other = {
        .type = PHASE_TYPE_MESH_BEACON, .direction_mode = DIRECTION_MAC_CELL, .cell_count = 3u
    };
    MAC_OnSlotOpportunity(&c2, &s_other, 0u);
    MAC_OnSlotOpportunity(&c, TdmaTable_GetPhase(0u), 0u);
    TEST_ASSERT_EQUAL(0u, MAC_GetPhaseTxFlag());
}

/* ------- Beacon → Paired -------------------------------------------------- */

void test_c1_beacon_transitions_to_paired(void)
{
    three_sync_packets();
    BeaconPayload_t beacon = {.hop_count = 1u, .node_id = 10u, .route_cost = 100u};
    MAC_OnBeaconReceived(&beacon);
    TEST_ASSERT_EQUAL(MAC_STATE_PAIRED, MAC_GetState());
}

void test_c1_cell_eligibility_written_on_beacon(void)
{
    three_sync_packets();
    BeaconPayload_t beacon = {.hop_count = 1u, .node_id = 10u, .route_cost = 100u};
    MAC_OnBeaconReceived(&beacon);
    /* our hop=2: uplink = (1<<(2%3)) | (1<<(3%3)) = 0x04 | 0x01 = 0x05 */
    TEST_ASSERT_EQUAL_HEX8(0x05u, MAC_GetCellEligibilityMask_Uplink());
}

/* ------- Epoch Received flag (C1 receives but never relays) ------------- */

void test_c1_epoch_received_false_after_init(void)
{
    TEST_ASSERT_FALSE(MAC_GetEpochReceivedThisPhase());
}

void test_c1_epoch_received_true_after_tier1(void)
{
    three_sync_packets();
    /* CLOCK_WARM: receive a Tier 1 sync packet (error=0 < 8ms) */
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_TRUE(MAC_GetEpochReceivedThisPhase());
}

void test_c1_epoch_received_resets_on_phase_entry(void)
{
    three_sync_packets();
    /* Trigger phase entry at Sync phase */
    FrameCursor_t c = {.phase_index = 0u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c, TdmaTable_GetPhase(0u), 0u);

    /* Receive Tier 1 — epoch received = true */
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_TRUE(MAC_GetEpochReceivedThisPhase());

    /* Phase entry to a different phase resets the flag */
    FrameCursor_t c1 = {.phase_index = 1u, .cell_index = 0u, .slot_index = 0u};
    static const Phase_t s_other = {
        .type = PHASE_TYPE_MESH_BEACON, .direction_mode = DIRECTION_MAC_CELL, .cell_count = 3u
    };
    MAC_OnSlotOpportunity(&c1, &s_other, 0u);
    TEST_ASSERT_FALSE(MAC_GetEpochReceivedThisPhase());
}

/* ------- Sync silence timeout (ADR-0013) -------------------------------- */

/*
 * three_sync_packets() drives to CLOCK_WARM with the last stamp at 6000 ms.
 * s_last_sync_received_ms = 6000 after acquisition.
 * 14 min = 840000 ms, 15 min = 900000 ms = SYNC_SILENCE_TIMEOUT_MS.
 */

void test_c1_warm_14min_silence_no_degradation(void)
{
    three_sync_packets();
    MAC_CheckSyncTimeout(6000u + 840000u);  /* 14 min — under threshold */
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

void test_c1_warm_15min_silence_degrades_to_cold(void)
{
    three_sync_packets();
    MAC_CheckSyncTimeout(6000u + 900000u);  /* 15 min — at threshold */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

void test_c1_acquiring_15min_silence_degrades_to_cold(void)
{
    /* P1 only — CLOCK_ACQUIRING, last_sync = 0 */
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_cell_index = 0u;
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());

    MAC_CheckSyncTimeout(900000u);  /* 15 min from last_sync=0 */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

void test_c1_cold_timeout_is_noop(void)
{
    /* Boot state is CLOCK_COLD — timeout check must not fire */
    MAC_CheckSyncTimeout(900000u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

void test_c1_sync_at_14min_resets_timer(void)
{
    three_sync_packets();  /* CLOCK_WARM, last_sync = 6000 */

    /* 14 min: no degradation */
    MAC_CheckSyncTimeout(6000u + 840000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());

    /* Receive Tier 1 sync at 14 min — resets timer to 846000 */
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 846000u;
    MAC_OnSyncPacketReceived(&p, 846000u);  /* error=0 → Tier 1 */

    /* 15 min from original start (906000): only 1 min after reset → no degradation */
    MAC_CheckSyncTimeout(906000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

void test_c1_tier2_resets_silence_timer(void)
{
    three_sync_packets();  /* CLOCK_WARM, last_sync = 6000 */

    /* Tier 2 packet (error 50 ms, < 300 ms) at 14 min — stays WARM, resets timer */
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 846000u;
    MAC_OnSyncPacketReceived(&p, 846050u);  /* error=50 → Tier 2 */
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());

    /* 14 min from Tier 2 reset: no degradation */
    MAC_CheckSyncTimeout(846050u + 840000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);

    /* 15 min from Tier 2 reset: degradation */
    MAC_CheckSyncTimeout(846050u + 900000u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

void test_c1_tier3_resets_silence_timer(void)
{
    three_sync_packets();  /* CLOCK_WARM, last_sync = 6000 */

    /* Tier 3 packet at 14 min — immediate degradation to COLD, but timer reset */
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 846000u;
    MAC_OnSyncPacketReceived(&p, 846400u);  /* error=400 ≥ SYNC_RESYNC_THRESHOLD_MS → Tier 3 */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);

    /* Re-acquire from the Tier 3 moment */
    s_snapshot_ms = 846400u;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index             = 0u;
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 846400u;
    MAC_OnSyncPacketReceived(&p, 846400u);  /* P1 → ACQUIRING */
    p.sync_cell_index = 1u;  MAC_OnSyncPacketReceived(&p, 849400u);  /* P2 */
    p.sync_cell_index = 2u;  MAC_OnSyncPacketReceived(&p, 852400u);  /* P3 → WARM */
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());

    /* 14 min from re-acquisition: no degradation */
    MAC_CheckSyncTimeout(852400u + 840000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());

    /* 15 min from re-acquisition: degradation */
    MAC_CheckSyncTimeout(852400u + 900000u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(2, s_sync_lost_calls);
}

void test_c1_tier3_immediate_degradation_unchanged(void)
{
    three_sync_packets();
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_cell_index              = 0u;
    p.ms_since_midnight_sync_phase = 0u;
    MAC_OnSyncPacketReceived(&p, 400u);  /* error=400 ≥ SYNC_RESYNC_THRESHOLD_MS → Tier 3 */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

/* ------- main ------------------------------------------------------------- */

/* ------- Cursor suspect --------------------------------------------------- */

void test_c1_cursor_suspect_drops_to_cold_without_rtc_write(void)
{
    three_sync_packets();
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    int rtc_sets = s_rtc_set_calls;

    MAC_OnCursorSuspect();

    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
    TEST_ASSERT_EQUAL(rtc_sets, s_rtc_set_calls);
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

/* A phase index that is not a Sync phase cannot anchor the FrameCursor:
 * the packet is dropped before it touches the RTC or the ClockState, so a
 * cold node keeps scanning instead of reaching ACQUIRING with no chain. */
void test_c1_sync_pkt_with_non_sync_phase_is_rejected(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.sync_phase_index = 0xFFu;
    p.sync_cell_index  = 1u;
    ArcLog_CaptureReset();

    MAC_OnSyncPacketReceived(&p, 1000u);

    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_rtc_set_calls);
    TEST_ASSERT_ARCLOG("SYNC_REJ ph=255 ce=1");
}

/* ------- Midnight rollover (issue #54) ---------------------------------- */

void test_c1_acquires_and_stays_warm_across_midnight(void)
{
    /* Phase at 23:59:54: cells at 23:59:54, 23:59:57, 00:00:00, 00:00:03. */
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    p.ms_since_midnight_sync_phase = MS_PER_DAY - 6000u;
    p.sync_cell_index = 0u;  MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 6000u);
    p.sync_cell_index = 1u;  MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 3000u);
    p.sync_cell_index = 2u;  MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());

    p.sync_cell_index = 3u;  MAC_OnSyncPacketReceived(&p, 3001u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

void test_c1_silence_timeout_spans_midnight(void)
{
    SyncPayload_t p;
    memset(&p, 0, sizeof(p));
    uint32_t ep = MS_PER_DAY - 66000u;
    p.ms_since_midnight_sync_phase = ep;
    p.sync_cell_index = 0u;  MAC_OnSyncPacketReceived(&p, ep);
    p.sync_cell_index = 1u;  MAC_OnSyncPacketReceived(&p, ep + 3000u);
    p.sync_cell_index = 2u;  MAC_OnSyncPacketReceived(&p, ep + 6000u);
    uint32_t last = ep + 6000u;                    /* 23:59:00 */

    MAC_CheckSyncTimeout((last + 840000u) % MS_PER_DAY);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    MAC_CheckSyncTimeout((last + 900000u) % MS_PER_DAY);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
}

/* ------- Date of the RTC set (issue #28) -----------------------------------
 * A Sync packet's date is the date of the phase epoch. A node whose target
 * time falls on the next day must set that next day's date. */

static void make_dated_pkt(SyncPayload_t *p, uint8_t cell, uint32_t ms_midnight,
                           uint8_t day, uint8_t month, uint8_t year)
{
    memset(p, 0, sizeof(*p));
    p->sync_cell_index              = cell;
    p->ms_since_midnight_sync_phase = ms_midnight;
    p->day   = day;
    p->month = month;
    p->year  = year;
}

void test_c1_cold_set_in_the_epoch_day_keeps_the_date(void)
{
    SyncPayload_t p;
    make_dated_pkt(&p, 2u, 43200000u, 0x14u, 0x03u, 0x26u);
    s_snapshot_ms = 43206000u;
    MAC_OnSyncPacketReceived(&p, 43206000u);

    TEST_ASSERT_EQUAL_HEX8(0x14, s_rtc_set_day);
    TEST_ASSERT_EQUAL_HEX8(0x03, s_rtc_set_month);
    TEST_ASSERT_EQUAL_HEX8(0x26, s_rtc_set_year);
}

void test_c1_cold_set_on_a_cell_after_midnight_advances_the_date(void)
{
    SyncPayload_t p;
    make_dated_pkt(&p, 2u, MS_PER_DAY - 6000u, 0x31u, 0x12u, 0x25u);
    s_snapshot_ms = 0u;
    MAC_OnSyncPacketReceived(&p, 0u);                /* cell 2 = 00:00:00 */

    TEST_ASSERT_EQUAL_HEX8(0x01, s_rtc_set_day);
    TEST_ASSERT_EQUAL_HEX8(0x01, s_rtc_set_month);
    TEST_ASSERT_EQUAL_HEX8(0x26, s_rtc_set_year);
}

void test_c1_cold_set_whose_carry_crosses_midnight_advances_the_date(void)
{
    SyncPayload_t p;
    make_dated_pkt(&p, 0u, MS_PER_DAY - 500u, 0x28u, 0x02u, 0x25u);
    s_snapshot_ms = 400u;                      /* old domain wrapped */
    MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 600u);

    TEST_ASSERT_EQUAL_HEX8(0x01, s_rtc_set_day);
    TEST_ASSERT_EQUAL_HEX8(0x03, s_rtc_set_month);   /* 28 Feb 2025 + 1 day */
    TEST_ASSERT_EQUAL_HEX8(0x25, s_rtc_set_year);
}

/* ------- drift sample hook (issue #34) ------------------------------------ */

static int     s_sample_n;
static int32_t s_sample_err[16];
static int     s_sample_order_at_align;   /* samples seen when the shift hook ran */
static int     s_align_calls_seen;

static void stub_sync_sample(int32_t err_ms)
{
    if (s_sample_n < 16) s_sample_err[s_sample_n] = err_ms;
    s_sample_n++;
}

static void stub_align_for_sample(int32_t err_us)
{
    (void)err_us;
    s_sample_order_at_align = s_sample_n;
    s_align_calls_seen++;
}

static void init_with_sample_hooks(void)
{
    MAC_Hooks_t hooks = k_hooks;
    hooks.sync_sample         = stub_sync_sample;
    hooks.rtc_align_subsecond = stub_align_for_sample;
    MAC_Init(&hooks);
    s_sample_n = 0;
    s_sample_order_at_align = -1;
    s_align_calls_seen = 0;
}

static void sample_pkt(SyncPayload_t *p, uint8_t cell)
{
    memset(p, 0, sizeof(*p));
    p->sync_phase_index             = 0u;
    p->sync_cell_index              = cell;
    p->ms_since_midnight_sync_phase = 0u;
    p->day = 0x01u; p->month = 0x01u; p->year = 0x24u;
}

/* Packet 1 is the set: no sample. The good ACQUIRING packets are. */
void test_c1_sample_hook_not_called_for_packet1_called_for_good_acquiring(void)
{
    init_with_sample_hooks();
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    sample_pkt(&p, 0u); MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(0, s_sample_n);
    sample_pkt(&p, 1u); MAC_OnSyncPacketReceived(&p, 3003u);
    sample_pkt(&p, 2u); MAC_OnSyncPacketReceived(&p, 5997u);
    TEST_ASSERT_EQUAL(2, s_sample_n);
    TEST_ASSERT_INT32_WITHIN(250, 3000, s_sample_err[0]);
    TEST_ASSERT_INT32_WITHIN(250, -3000, s_sample_err[1]);
}

void test_c1_sample_hook_not_called_for_a_bad_acquiring_packet(void)
{
    init_with_sample_hooks();
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    sample_pkt(&p, 0u); MAC_OnSyncPacketReceived(&p, 0u);
    sample_pkt(&p, 1u); MAC_OnSyncPacketReceived(&p, 3000u + 8u)   /* the participate threshold */;
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sample_n);
}

/* WARM: Tier 1 and Tier 2 (either sign) are samples; Tier 3 is not. */
void test_c1_sample_hook_called_for_tier1_and_tier2_not_tier3(void)
{
    init_with_sample_hooks();
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    sample_pkt(&p, 0u); MAC_OnSyncPacketReceived(&p, 0u);
    sample_pkt(&p, 1u); MAC_OnSyncPacketReceived(&p, 3000u);
    sample_pkt(&p, 2u); MAC_OnSyncPacketReceived(&p, 6000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    s_sample_n = 0;

    sample_pkt(&p, 3u); MAC_OnSyncPacketReceived(&p, 9004u);    /* t1, +4 */
    sample_pkt(&p, 4u); MAC_OnSyncPacketReceived(&p, 12050u);   /* t2, +50 */
    sample_pkt(&p, 5u); MAC_OnSyncPacketReceived(&p, 14980u);   /* t2, -20 */
    TEST_ASSERT_EQUAL(3, s_sample_n);
    TEST_ASSERT_INT32_WITHIN(250, 4000, s_sample_err[0]);
    TEST_ASSERT_INT32_WITHIN(250, 50000, s_sample_err[1]);
    TEST_ASSERT_INT32_WITHIN(250, -20000, s_sample_err[2]);

    sample_pkt(&p, 1u);
    MAC_OnSyncPacketReceived(&p, 3000u + SYNC_RESYNC_THRESHOLD_MS);     /* t3 */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(3, s_sample_n);
}

/* The Tier 2 sample is taken before the shift it causes. */
void test_c1_sample_hook_runs_before_the_tier2_shift(void)
{
    init_with_sample_hooks();
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    sample_pkt(&p, 0u); MAC_OnSyncPacketReceived(&p, 0u);
    sample_pkt(&p, 1u); MAC_OnSyncPacketReceived(&p, 3000u);
    sample_pkt(&p, 2u); MAC_OnSyncPacketReceived(&p, 6000u);
    s_sample_n = 0;
    sample_pkt(&p, 3u); MAC_OnSyncPacketReceived(&p, 9040u);    /* t2 */
    TEST_ASSERT_EQUAL(1, s_align_calls_seen);
    TEST_ASSERT_EQUAL(1, s_sample_order_at_align);
}

void test_c1_no_sample_hook_is_fine(void)
{
    MAC_Init(&k_hooks);              /* sync_sample == NULL */
    s_sample_n = 0;
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    sample_pkt(&p, 0u); MAC_OnSyncPacketReceived(&p, 0u);
    sample_pkt(&p, 1u); MAC_OnSyncPacketReceived(&p, 3000u);
    TEST_ASSERT_EQUAL(0, s_sample_n);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_c1_boot_state_is_scanning);
    RUN_TEST(test_c1_boot_clock_state_is_cold);
    RUN_TEST(test_c1_scanning_returns_rx_for_sync_phase);
    RUN_TEST(test_c1_scanning_returns_rx_for_any_phase);
    RUN_TEST(test_c1_sync_pkt1_transitions_acquiring);
    RUN_TEST(test_c1_sync_pkt1_calls_rtc_set_hook);
    RUN_TEST(test_c1_sync_pkt3_valid_transitions_warm);
    RUN_TEST(test_c1_sync_pkt3_valid_transitions_synchronized);
    RUN_TEST(test_c1_sync_locked_called_once);
    RUN_TEST(test_c1_acquiring_bad_packet_drops_to_cold);
    RUN_TEST(test_c1_acquiring_checks_packet_against_its_own_epoch);
    RUN_TEST(test_c1_sync_lost_resets_to_cold);
    RUN_TEST(test_c1_sync_lost_returns_to_scanning);
    RUN_TEST(test_c1_sync_lost_calls_hook);
    RUN_TEST(test_c1_phase_tx_flag_always_zero);
    RUN_TEST(test_c1_beacon_transitions_to_paired);
    RUN_TEST(test_c1_cell_eligibility_written_on_beacon);

/* ------- Epoch Received flag (always false for C1) ----------------------- */

    RUN_TEST(test_c1_epoch_received_false_after_init);
    RUN_TEST(test_c1_epoch_received_true_after_tier1);
    RUN_TEST(test_c1_epoch_received_resets_on_phase_entry);

/* ------- Sync silence timeout (ADR-0013) -------------------------------- */

    RUN_TEST(test_c1_warm_14min_silence_no_degradation);
    RUN_TEST(test_c1_warm_15min_silence_degrades_to_cold);
    RUN_TEST(test_c1_acquiring_15min_silence_degrades_to_cold);
    RUN_TEST(test_c1_cold_timeout_is_noop);
    RUN_TEST(test_c1_sync_at_14min_resets_timer);
    RUN_TEST(test_c1_tier2_resets_silence_timer);
    RUN_TEST(test_c1_tier3_resets_silence_timer);
    RUN_TEST(test_c1_tier3_immediate_degradation_unchanged);
    RUN_TEST(test_c1_cursor_suspect_drops_to_cold_without_rtc_write);
    RUN_TEST(test_c1_sync_pkt_with_non_sync_phase_is_rejected);
    RUN_TEST(test_c1_acquires_and_stays_warm_across_midnight);
    RUN_TEST(test_c1_silence_timeout_spans_midnight);
    RUN_TEST(test_c1_cold_set_in_the_epoch_day_keeps_the_date);
    RUN_TEST(test_c1_cold_set_on_a_cell_after_midnight_advances_the_date);
    RUN_TEST(test_c1_cold_set_whose_carry_crosses_midnight_advances_the_date);
    RUN_TEST(test_c1_sample_hook_not_called_for_packet1_called_for_good_acquiring);
    RUN_TEST(test_c1_sample_hook_not_called_for_a_bad_acquiring_packet);
    RUN_TEST(test_c1_sample_hook_called_for_tier1_and_tier2_not_tier3);
    RUN_TEST(test_c1_sample_hook_runs_before_the_tier2_shift);
    RUN_TEST(test_c1_no_sample_hook_is_fine);
    return UNITY_END();
}
