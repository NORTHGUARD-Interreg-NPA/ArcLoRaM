#include "unity.h"
#include "mac_state_machine.h"
#include "tdma_table.h"
#include "arclog_capture.h"
#include "guard_time_resolver.h"   /* SYNC_RESYNC_THRESHOLD_MS */
#include <string.h>

/* ------- hook stubs ------------------------------------------------------- */

static int      s_rtc_set_calls;
static uint32_t s_rtc_set_target_ms;
static uint8_t  s_rtc_set_day;
static uint8_t  s_rtc_set_month;
static uint8_t  s_rtc_set_year;
static int      s_sync_locked_calls;
static int      s_sync_lost_calls;
static int      s_sync_bootstrapped_calls;
static uint32_t s_sync_bootstrapped_slot_start;
static uint8_t  s_sync_bootstrapped_phase_idx;
static uint8_t  s_sync_bootstrapped_cell_idx;

/* Snapshot stub: caller writes these before the test, hook reads them out */
static uint32_t s_snapshot_ms;
static uint8_t  s_snapshot_day;
static uint8_t  s_snapshot_month;
static uint8_t  s_snapshot_year;

static void stub_rtc_set(uint32_t target_ms,
                          uint8_t day, uint8_t month, uint8_t year)
{
    s_rtc_set_day       = day;
    s_rtc_set_month     = month;
    s_rtc_set_year      = year;
    s_rtc_set_target_ms = target_ms;
    s_rtc_set_calls++;
    /* After rtc_set, pretend RTC now reads target_ms */
    s_snapshot_ms = target_ms;
}

static void stub_get_rtc_snapshot(uint32_t *ms,
                                   uint8_t *day, uint8_t *month, uint8_t *year)
{
    *ms    = s_snapshot_ms;
    *day   = s_snapshot_day;
    *month = s_snapshot_month;
    *year  = s_snapshot_year;
}

static void stub_sync_bootstrapped(uint8_t phase_idx, uint8_t cell_idx,
                                    uint32_t rtc_now_ms)
{
    s_sync_bootstrapped_phase_idx  = phase_idx;
    s_sync_bootstrapped_cell_idx   = cell_idx;
    s_sync_bootstrapped_slot_start = rtc_now_ms;
    s_sync_bootstrapped_calls++;
}

static void stub_sync_locked(void) { s_sync_locked_calls++; }
static void stub_sync_lost(void)   { s_sync_lost_calls++;   }

static const MAC_Hooks_t k_hooks = {
    .rtc_set            = stub_rtc_set,
    .get_rtc_snapshot   = stub_get_rtc_snapshot,
    .sync_bootstrapped  = stub_sync_bootstrapped,
    .sync_locked        = stub_sync_locked,
    .sync_lost          = stub_sync_lost,
};

/* ------- helpers ---------------------------------------------------------- */

/*
 * Stub TDMA table: PHASE_TYPE_SYNC, slot_active_ms=2500, gap_after_slot_ms=500
 * Per-cell step = 3000 ms.
 */
static void make_sync_pkt(SyncPayload_t *p, uint8_t cell, uint32_t ms_midnight)
{
    memset(p, 0, sizeof(*p));
    p->sync_cell_index              = cell;
    p->sync_phase_index             = 0u;
    p->ms_since_midnight_sync_phase = ms_midnight;
    p->day   = 0x01u;
    p->month = 0x01u;
    p->year  = 0x24u;
}

/*
 * Drive MAC to CLOCK_WARM.
 * P1 at cell=0: s_snapshot_ms = 0 (phase start), s_sync_phase_ms = 0.
 * P2 at cell=1: stamp=3000, expected=3000, error=0 → consecutive=1.
 * P3 at cell=2: stamp=6000, expected=6000, error=0 → consecutive=2 → WARM.
 */
static void sync_mac(void)
{
    SyncPayload_t p;
    s_snapshot_ms = 0u;  /* after rtc_set RTC reads 0 */

    make_sync_pkt(&p, 0u, 0u);  MAC_OnSyncPacketReceived(&p, 0u);
    make_sync_pkt(&p, 1u, 0u);  MAC_OnSyncPacketReceived(&p, 3000u);
    make_sync_pkt(&p, 2u, 0u);  MAC_OnSyncPacketReceived(&p, 6000u);
}

static const Phase_t s_beacon_phase = {
    .type           = PHASE_TYPE_MESH_BEACON,
    .direction_mode = DIRECTION_MAC_CELL,
    .cell_count     = 10u,
    .slot_count     = 1u,
    .slot_active_ms = 2000u,
};

static const Phase_t s_other_phase = {
    .type           = PHASE_TYPE_MESH_UPLINK,
    .direction_mode = DIRECTION_CELL_SKIP,
    .cell_count     = 3u,
    .slot_active_ms = 2500u,
};

static const Phase_t s_sync_phase_6 = {
    .type              = PHASE_TYPE_SYNC,
    .direction_mode    = DIRECTION_MAC_CELL,
    .cell_count        = 6u,
    .slot_count        = 1u,
    .slot_active_ms    = 2500u,
    .gap_after_slot_ms = 500u,
};

void setUp(void)
{
    s_rtc_set_calls             = 0;
    s_rtc_set_target_ms         = 0u;
    s_sync_locked_calls         = 0;
    s_sync_lost_calls           = 0;
    s_sync_bootstrapped_calls   = 0;
    s_sync_bootstrapped_slot_start = 0u;
    s_sync_bootstrapped_phase_idx  = 0u;
    s_sync_bootstrapped_cell_idx   = 0u;
    s_snapshot_ms    = 0u;
    s_snapshot_day   = 0x01u;
    s_snapshot_month = 0x01u;
    s_snapshot_year  = 0x24u;
    MAC_Init(&k_hooks);
    ArcLog_CaptureReset();
}

void tearDown(void) {}

/* ------- Boot state ------------------------------------------------------- */

void test_c2_boot_state_is_scanning(void)
{
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
}

void test_c2_boot_clock_state_is_cold(void)
{
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
}

/* ------- Scanning — always RX --------------------------------------------- */

void test_c2_scanning_returns_rx_for_sync_phase(void)
{
    FrameCursor_t c = {.phase_index = 0u, .cell_index = 0u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c, TdmaTable_GetPhase(0u), 0u));
}

void test_c2_scanning_returns_rx_for_beacon_phase(void)
{
    FrameCursor_t c = {.phase_index = 5u, .cell_index = 0u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c, &s_beacon_phase, 0u));
}

/* ------- Packet 1: RTC set with binary target_ms -------------------------- */

void test_c2_sync_pkt1_transitions_acquiring(void)
{
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());
}

void test_c2_sync_pkt1_calls_rtc_set_hook(void)
{
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(1, s_rtc_set_calls);
}

void test_c2_packet1_rtc_set_called_with_target_ms_including_cell_offset(void)
{
    /* ms_since_midnight=0, per_cell=3000, K=2 → target_ms = 0 + 2×3000 = 6000 */
    SyncPayload_t p;
    make_sync_pkt(&p, 2u, 0u);
    s_snapshot_ms = 6000u;  /* after rtc_set RTC reads 6000 */
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(6000u, s_rtc_set_target_ms);
}

void test_c2_sync_bootstrapped_hook_called_with_rtc_now_as_slot_start(void)
{
    /* get_rtc_snapshot returns 6000 (= target_ms); K=2
     * sync_bootstrapped slot_start == 6000 */
    SyncPayload_t p;
    make_sync_pkt(&p, 2u, 0u);
    s_snapshot_ms = 6000u;
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(1, s_sync_bootstrapped_calls);
    TEST_ASSERT_EQUAL(6000u, s_sync_bootstrapped_slot_start);
    TEST_ASSERT_EQUAL(0u, s_sync_bootstrapped_phase_idx);
    TEST_ASSERT_EQUAL(2u, s_sync_bootstrapped_cell_idx);
}

/* ------- Two-consecutive-packet lock -------------------------------------- */

void test_c2_two_consecutive_good_packets_warm(void)
{
    /* s_sync_phase_ms=0; P2(cell=1,ts=3000,err=0), P3(cell=2,ts=6000,err=0) → WARM */
    sync_mac();
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(MAC_STATE_SYNCHRONIZED, MAC_GetState());
}

void test_c2_sync_locked_called_once(void)
{
    sync_mac();
    TEST_ASSERT_EQUAL(1, s_sync_locked_calls);
}

void test_c2_acquiring_bad_packet_drops_to_cold(void)
{
    /* P1(cell=0), P2(cell=1,ts=3000,err=0 → consecutive=1),
     * P3(cell=2,ts=6100: expected=6000, err=100 ≥ 8ms → bad): the packet
     * disagrees with the RTC set from P1, so the node re-acquires. */
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    make_sync_pkt(&p, 0u, 0u);  MAC_OnSyncPacketReceived(&p, 0u);
    make_sync_pkt(&p, 1u, 0u);  MAC_OnSyncPacketReceived(&p, 3000u);
    make_sync_pkt(&p, 2u, 0u);  MAC_OnSyncPacketReceived(&p, 6100u);   /* bad */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
    TEST_ASSERT_EQUAL(0, s_sync_locked_calls);
    TEST_ASSERT_ARCLOG("SYNC_RX ph=0 ce=2 ep=0 st=6100 exp=6000 err=100 erru=");
    TEST_ASSERT_ARCLOG("clk=ACQ act=bad");
    TEST_ASSERT_ARCLOG("CLK from=ACQ to=COLD why=acq_bad");
}

void test_c2_acquiring_bad_packet_then_next_packet_sets_rtc(void)
{
    /* After a bad packet the next one is a fresh Packet 1: RTC set again. */
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    make_sync_pkt(&p, 0u, 0u);      MAC_OnSyncPacketReceived(&p, 0u);
    make_sync_pkt(&p, 1u, 0u);      MAC_OnSyncPacketReceived(&p, 3033u);  /* bad */
    make_sync_pkt(&p, 2u, 0u);      MAC_OnSyncPacketReceived(&p, 6033u);
    TEST_ASSERT_EQUAL(2, s_rtc_set_calls);
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());
}

void test_c2_acquiring_checks_packet_against_its_own_epoch(void)
{
    /* Bench case: Packet 1 from phase occurrence ep=30000, the next packets
     * from occurrence ep=60000. Each is judged against its own epoch, not
     * against Packet 1's occurrence (which read err=30003). */
    SyncPayload_t p;
    s_snapshot_ms = 30000u;
    make_sync_pkt(&p, 0u, 30000u);  MAC_OnSyncPacketReceived(&p, 30000u);
    make_sync_pkt(&p, 0u, 60000u);  MAC_OnSyncPacketReceived(&p, 60003u);
    TEST_ASSERT_ARCLOG("SYNC_RX ph=0 ce=0 ep=60000 st=60003 exp=60000 err=3 erru=");
    TEST_ASSERT_ARCLOG("clk=ACQ act=good");
    make_sync_pkt(&p, 1u, 60000u);  MAC_OnSyncPacketReceived(&p, 63003u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
}

void test_c2_acquiring_locks_when_packet1_is_the_last_tx_cell(void)
{
    /* Packet 1 in cell 2, the sender's last Tx cell of the occurrence: no
     * later packet of that occurrence exists, so the lock must come from
     * the next occurrence. */
    SyncPayload_t p;
    s_snapshot_ms = 6000u;
    make_sync_pkt(&p, 2u, 0u);      MAC_OnSyncPacketReceived(&p, 6000u);
    make_sync_pkt(&p, 0u, 30000u);  MAC_OnSyncPacketReceived(&p, 30000u);
    make_sync_pkt(&p, 1u, 30000u);  MAC_OnSyncPacketReceived(&p, 33000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_locked_calls);
}

/* ------- SYNC_LOST -------------------------------------------------------- */

void test_c2_sync_lost_resets_to_scanning(void)
{
    sync_mac();
    /* cell=0, ms_midnight=0, expected_arrival=0, stamp=400 → error=400 ≥ SYNC_RESYNC_THRESHOLD_MS Tier 3 */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 400u);
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
}

void test_c2_sync_lost_calls_hook(void)
{
    sync_mac();
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 400u);
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

/* ------- CLOCK_WARM three-tier dispatch ----------------------------------- */

void test_c2_warm_tier1_epoch_stored_on_good_cell0(void)
{
    sync_mac();
    /* CLOCK_WARM; cell=0, ms_midnight=54000000, stamp=54000000, error=0 → Tier 1 */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 54000000u);
    MAC_OnSyncPacketReceived(&p, 54000000u);
    TEST_ASSERT_EQUAL(54000000u, MAC_GetSyncPhaseEpochMs());
}

void test_c2_warm_tier2_no_epoch_below_resync_threshold(void)
{
    sync_mac();
    /* cell=0, ms_midnight=0, stamp=50, expected=0, error=50 ≥ 8ms < SYNC_RESYNC_THRESHOLD_MS → Tier 2 */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 50u);
    /* ClockState stays WARM; epoch not stored (no relay this occurrence) */
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    /* epoch is still from the warm-up acquisition, not updated */
    TEST_ASSERT_NOT_EQUAL(0u, 1u);  /* structural: state did not collapse */
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

void test_c2_warm_tier3_resync_above_resync_threshold(void)
{
    sync_mac();
    int rtc_calls_before = s_rtc_set_calls;  /* snapshot after sync_mac P1 call */
    /* cell=0, ms_midnight=0, stamp=400, expected=0, error=400 ≥ SYNC_RESYNC_THRESHOLD_MS → Tier 3 */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 400u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_rtc_set_calls - rtc_calls_before);  /* one new rtc_set */
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

/* ------- Cell 0 always RX, cells 1+ reactive ------------------------------ */

void test_c2_sync_cell0_always_rx_when_warm(void)
{
    sync_mac();
    FrameCursor_t c = {.phase_index = 0u, .cell_index = 0u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c, TdmaTable_GetPhase(0u), 0u));
}

void test_c2_sync_cell1_tx_when_epoch_received(void)
{
    sync_mac();
    FrameCursor_t c0 = {.phase_index = 0u, .cell_index = 0u, .slot_index = 0u};
    FrameCursor_t c1 = {.phase_index = 0u, .cell_index = 1u, .slot_index = 0u};

    /* Step 1: SlotOpportunity at cell 0 → phase entry detected, epoch_received=false */
    MAC_OnSlotOpportunity(&c0, TdmaTable_GetPhase(0u), 0u);

    /* Step 2: radio receives sync packet in that RX window → Tier 1 → epoch armed */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);  /* error=0 → Tier 1, epoch stored */

    /* Step 3: SlotOpportunity at cell 1 → epoch received → SLOT_TX */
    TEST_ASSERT_EQUAL(SLOT_TX, MAC_OnSlotOpportunity(&c1, TdmaTable_GetPhase(0u), 0u));
}

void test_c2_sync_cell1_rx_when_no_epoch(void)
{
    sync_mac();
    /* No new cell-0 reception this occurrence — epoch_received = false */
    /* Force a new phase entry to reset the epoch flag */
    FrameCursor_t co = {.phase_index = 1u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&co, &s_other_phase, 0u);
    FrameCursor_t c0 = {.phase_index = 0u, .cell_index = 0u, .slot_index = 0u};
    FrameCursor_t c1 = {.phase_index = 0u, .cell_index = 1u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, TdmaTable_GetPhase(0u), 0u);  /* cell 0 = RX, no pkt received */
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c1, TdmaTable_GetPhase(0u), 0u));
}

/* ------- CellEligibilityMask for hop 0–5 ---------------------------------- */

void test_c2_cell_eligibility_all_hops(void)
{
    sync_mac();
    for (uint8_t hop = 0u; hop <= 5u; hop++) {
        BeaconPayload_t b = {.hop_count = hop, .node_id = 1u, .route_cost = 100u};
        MAC_OnBeaconReceived(&b);

        uint8_t our_hop     = (uint8_t)(hop + 1u);
        uint8_t ul_expected = (uint8_t)((1u << (our_hop % 3u)) | (1u << ((our_hop + 1u) % 3u)));
        uint8_t dl_expected = (uint8_t)((1u << ((our_hop + 2u) % 3u)) | (1u << ((our_hop + 1u) % 3u)));

        TEST_ASSERT_EQUAL_HEX8(ul_expected, MAC_GetCellEligibilityMask_Uplink());
        TEST_ASSERT_EQUAL_HEX8(dl_expected, MAC_GetCellEligibilityMask_Downlink());
    }
}

/* ------- Beacon → Paired -------------------------------------------------- */

void test_c2_beacon_transitions_to_paired(void)
{
    sync_mac();
    BeaconPayload_t b = {.hop_count = 1u, .node_id = 42u, .route_cost = 100u};
    MAC_OnBeaconReceived(&b);
    TEST_ASSERT_EQUAL(MAC_STATE_PAIRED, MAC_GetState());
}

/* ------- BeaconTxBudget --------------------------------------------------- */

void test_c2_beacon_tx_budget_set_on_first_beacon(void)
{
    sync_mac();
    BeaconPayload_t b = {.hop_count = 1u, .node_id = 42u, .route_cost = 100u};
    MAC_OnBeaconReceived(&b);

    FrameCursor_t c = {.phase_index = 5u, .cell_index = 0u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_TX, MAC_OnSlotOpportunity(&c, &s_beacon_phase, 0u));
}

void test_c2_beacon_tx_budget_decrements_on_tx(void)
{
    sync_mac();
    BeaconPayload_t b = {.hop_count = 1u, .node_id = 42u, .route_cost = 100u};
    MAC_OnBeaconReceived(&b);

    FrameCursor_t c0 = {.phase_index = 5u, .cell_index = 0u, .slot_index = 0u};
    FrameCursor_t c1 = {.phase_index = 5u, .cell_index = 1u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, &s_beacon_phase, 0u);
    MAC_OnSlotOpportunity(&c1, &s_beacon_phase, 0u);

    FrameCursor_t nc0 = {.phase_index = 6u, .cell_index = 0u, .slot_index = 0u};
    FrameCursor_t nc1 = {.phase_index = 6u, .cell_index = 1u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&nc0, &s_beacon_phase, 0u));
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&nc1, &s_beacon_phase, 0u));
}

void test_c2_beacon_tx_budget_resets_on_hop_change(void)
{
    sync_mac();
    BeaconPayload_t b = {.hop_count = 1u, .node_id = 42u, .route_cost = 100u};
    MAC_OnBeaconReceived(&b);

    FrameCursor_t c0 = {.phase_index = 5u, .cell_index = 0u};
    FrameCursor_t c1 = {.phase_index = 5u, .cell_index = 1u};
    MAC_OnSlotOpportunity(&c0, &s_beacon_phase, 0u);
    MAC_OnSlotOpportunity(&c1, &s_beacon_phase, 0u);

    BeaconPayload_t b2 = {.hop_count = 2u, .node_id = 42u, .route_cost = 100u};
    MAC_OnBeaconReceived(&b2);

    FrameCursor_t nc0 = {.phase_index = 7u, .cell_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_TX, MAC_OnSlotOpportunity(&nc0, &s_beacon_phase, 0u));
}

/* ------- Beacon Tx at cell 3+ (relative to beacon reception) -------------- */

void test_c2_beacon_tx_at_cell_3_after_beacon_received(void)
{
    sync_mac();
    BeaconPayload_t b = {.hop_count = 1u, .node_id = 42u, .route_cost = 100u};
    MAC_OnBeaconReceived(&b);  /* budget = K = 2 */

    /* Consume budget at cells 0 and 1 */
    FrameCursor_t c0 = {.phase_index = 5u, .cell_index = 0u, .slot_index = 0u};
    FrameCursor_t c1 = {.phase_index = 5u, .cell_index = 1u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, &s_beacon_phase, 0u);
    MAC_OnSlotOpportunity(&c1, &s_beacon_phase, 0u);

    /* Cell 2: budget exhausted → Rx */
    FrameCursor_t c2 = {.phase_index = 5u, .cell_index = 2u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c2, &s_beacon_phase, 0u));

    /* Receive new beacon at cell 3 with different hop → structural change → budget reset */
    BeaconPayload_t b2 = {.hop_count = 5u, .node_id = 42u, .route_cost = 100u};
    MAC_OnBeaconReceived(&b2);

    /* Cell 3: budget > 0 → Tx (old code would fail: 3 < 2 is false) */
    FrameCursor_t c3 = {.phase_index = 5u, .cell_index = 3u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_TX, MAC_OnSlotOpportunity(&c3, &s_beacon_phase, 0u));

    /* Cell 4: budget > 0 → Tx */
    FrameCursor_t c4 = {.phase_index = 5u, .cell_index = 4u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_TX, MAC_OnSlotOpportunity(&c4, &s_beacon_phase, 0u));

    /* Cell 5: budget exhausted → Rx */
    FrameCursor_t c5 = {.phase_index = 5u, .cell_index = 5u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c5, &s_beacon_phase, 0u));
}

void test_c2_beacon_tx_budget_getter(void)
{
    sync_mac();
    /* After init + sync_mac, no beacon received → budget = 0 */
    TEST_ASSERT_EQUAL(0u, MAC_GetBeaconTxBudget());

    BeaconPayload_t b = {.hop_count = 1u, .node_id = 42u, .route_cost = 100u};
    MAC_OnBeaconReceived(&b);
    TEST_ASSERT_EQUAL(BEACON_K_TX_CELLS, MAC_GetBeaconTxBudget());

    /* Consume one budget */
    FrameCursor_t c0 = {.phase_index = 5u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, &s_beacon_phase, 0u);
    TEST_ASSERT_EQUAL(BEACON_K_TX_CELLS - 1u, MAC_GetBeaconTxBudget());
}

/* ------- Sync TX Budget (issue 29 - C2 only) ------------------------------ */

void test_c2_sync_tx_budget_limits_to_3_per_occurrence(void)
{
    sync_mac();
    /* Enter a 6-cell sync phase at cell 0 */
    FrameCursor_t c0 = {.phase_index = 10u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, &s_sync_phase_6, 0u);  /* cell 0 = RX, phase entry */

    /* Receive epoch at cell 0 - Tier 1 */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);

    /* Cells 1-3: SLOT_TX (budget = 3) */
    for (uint8_t cell = 1u; cell <= 3u; cell++) {
        FrameCursor_t c = {.phase_index = 10u, .cell_index = cell, .slot_index = 0u};
        TEST_ASSERT_EQUAL(SLOT_TX, MAC_OnSlotOpportunity(&c, &s_sync_phase_6, 0u));
    }
    /* Cells 4-5: SLOT_RX (budget exhausted) */
    for (uint8_t cell = 4u; cell <= 5u; cell++) {
        FrameCursor_t c = {.phase_index = 10u, .cell_index = cell, .slot_index = 0u};
        TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c, &s_sync_phase_6, 0u));
    }
}

void test_c2_sync_tx_budget_resets_on_next_occurrence(void)
{
    sync_mac();
    /* First occurrence: exhaust budget in 6-cell phase */
    FrameCursor_t c0 = {.phase_index = 10u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, &s_sync_phase_6, 0u);

    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);

    for (uint8_t cell = 1u; cell <= 5u; cell++) {
        FrameCursor_t c = {.phase_index = 10u, .cell_index = cell, .slot_index = 0u};
        MAC_OnSlotOpportunity(&c, &s_sync_phase_6, 0u);
    }

    /* Second occurrence: enter a different phase, then re-enter sync phase */
    FrameCursor_t co = {.phase_index = 11u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&co, &s_other_phase, 0u);

    FrameCursor_t c0b = {.phase_index = 10u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0b, &s_sync_phase_6, 0u);  /* phase entry → budget reset */

    /* Receive epoch again */
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);

    /* Budget should be restored: cells 1-3 TX, cell 4 RX */
    for (uint8_t cell = 1u; cell <= 3u; cell++) {
        FrameCursor_t c = {.phase_index = 10u, .cell_index = cell, .slot_index = 0u};
        TEST_ASSERT_EQUAL(SLOT_TX, MAC_OnSlotOpportunity(&c, &s_sync_phase_6, 0u));
    }
    FrameCursor_t c4 = {.phase_index = 10u, .cell_index = 4u, .slot_index = 0u};
    TEST_ASSERT_EQUAL(SLOT_RX, MAC_OnSlotOpportunity(&c4, &s_sync_phase_6, 0u));
}

void test_c2_sync_tx_budget_getter_after_init(void)
{
    TEST_ASSERT_EQUAL(SYNC_TX_BUDGET, MAC_GetSyncTxBudget());
}

void test_c2_sync_tx_budget_getter_decrements_on_tx(void)
{
    sync_mac();
    FrameCursor_t c0 = {.phase_index = 10u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, &s_sync_phase_6, 0u);

    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);

    FrameCursor_t c1 = {.phase_index = 10u, .cell_index = 1u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c1, &s_sync_phase_6, 0u);
    TEST_ASSERT_EQUAL(SYNC_TX_BUDGET - 1u, MAC_GetSyncTxBudget());

    FrameCursor_t c2 = {.phase_index = 10u, .cell_index = 2u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c2, &s_sync_phase_6, 0u);
    TEST_ASSERT_EQUAL(SYNC_TX_BUDGET - 2u, MAC_GetSyncTxBudget());
}

void test_c2_sync_tx_budget_getter_zero_after_exhaustion(void)
{
    sync_mac();
    FrameCursor_t c0 = {.phase_index = 10u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, &s_sync_phase_6, 0u);

    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);

    for (uint8_t cell = 1u; cell <= 3u; cell++) {
        FrameCursor_t c = {.phase_index = 10u, .cell_index = cell, .slot_index = 0u};
        MAC_OnSlotOpportunity(&c, &s_sync_phase_6, 0u);
    }
    TEST_ASSERT_EQUAL(0u, MAC_GetSyncTxBudget());
}

void test_c2_sync_tx_budget_getter_resets_on_phase_entry(void)
{
    sync_mac();
    FrameCursor_t c0 = {.phase_index = 10u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, &s_sync_phase_6, 0u);

    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);

    for (uint8_t cell = 1u; cell <= 3u; cell++) {
        FrameCursor_t c = {.phase_index = 10u, .cell_index = cell, .slot_index = 0u};
        MAC_OnSlotOpportunity(&c, &s_sync_phase_6, 0u);
    }
    TEST_ASSERT_EQUAL(0u, MAC_GetSyncTxBudget());

    /* Phase entry to a different phase resets the budget */
    FrameCursor_t co = {.phase_index = 11u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&co, &s_other_phase, 0u);
    TEST_ASSERT_EQUAL(SYNC_TX_BUDGET, MAC_GetSyncTxBudget());
}

/* ------- Epoch Received flag --------------------------------------------- */

void test_c2_epoch_received_false_after_init(void)
{
    TEST_ASSERT_FALSE(MAC_GetEpochReceivedThisPhase());
}

void test_c2_epoch_received_true_after_tier1(void)
{
    sync_mac();
    /* WARM state: receive a Tier 1 sync packet (error=0 < 8ms) */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_TRUE(MAC_GetEpochReceivedThisPhase());
}

void test_c2_epoch_received_resets_on_phase_entry(void)
{
    sync_mac();

    /* First slot opportunity at phase 0 — triggers phase entry */
    FrameCursor_t c0 = {.phase_index = 0u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c0, TdmaTable_GetPhase(0u), 0u);

    /* Receive Tier 1 — epoch received = true */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_TRUE(MAC_GetEpochReceivedThisPhase());

    /* Phase entry to a different phase resets the flag */
    FrameCursor_t c1 = {.phase_index = 1u, .cell_index = 0u, .slot_index = 0u};
    MAC_OnSlotOpportunity(&c1, &s_other_phase, 0u);
    TEST_ASSERT_FALSE(MAC_GetEpochReceivedThisPhase());
}

void test_c2_hop_count_zero_after_init(void)
{
    TEST_ASSERT_EQUAL(0u, MAC_GetHopCount());
}

void test_c2_hop_count_set_after_beacon(void)
{
    sync_mac();
    BeaconPayload_t b = {.hop_count = 3u, .node_id = 7u, .route_cost = 50u};
    MAC_OnBeaconReceived(&b);
    TEST_ASSERT_EQUAL(4u, MAC_GetHopCount());
}

/* ------- Sync silence timeout (ADR-0013) -------------------------------- */

/*
 * sync_mac() drives to CLOCK_WARM with the last stamp at 6000 ms.
 * s_last_sync_received_ms = 6000 after acquisition.
 * 14 min = 840000 ms, 15 min = 900000 ms = SYNC_SILENCE_TIMEOUT_MS.
 */

void test_c2_warm_14min_silence_no_degradation(void)
{
    sync_mac();
    MAC_CheckSyncTimeout(6000u + 840000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

void test_c2_warm_15min_silence_degrades_to_cold(void)
{
    sync_mac();
    MAC_CheckSyncTimeout(6000u + 900000u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

void test_c2_acquiring_15min_silence_degrades_to_cold(void)
{
    /* P1 only — CLOCK_ACQUIRING, last_sync = 0 */
    SyncPayload_t p;
    s_snapshot_ms = 0u;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 0u);
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());

    MAC_CheckSyncTimeout(900000u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

void test_c2_cold_timeout_is_noop(void)
{
    MAC_CheckSyncTimeout(900000u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

void test_c2_sync_at_14min_resets_timer(void)
{
    sync_mac();  /* CLOCK_WARM, last_sync = 6000 */

    /* 14 min: no degradation */
    MAC_CheckSyncTimeout(6000u + 840000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());

    /* Receive Tier 1 sync at 14 min — resets timer to 846000 */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 846000u);
    MAC_OnSyncPacketReceived(&p, 846000u);  /* error=0 → Tier 1 */

    /* 15 min from original start (906000): only 1 min after reset → no degradation */
    MAC_CheckSyncTimeout(906000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

void test_c2_tier2_resets_silence_timer(void)
{
    sync_mac();  /* CLOCK_WARM, last_sync = 6000 */

    /* Tier 2 packet (error 50 ms) at 14 min — stays WARM, resets timer */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 846000u);
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

void test_c2_tier3_resets_silence_timer(void)
{
    sync_mac();  /* CLOCK_WARM, last_sync = 6000 */

    /* Tier 3 packet at 14 min — immediate degradation to COLD */
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 846000u);
    MAC_OnSyncPacketReceived(&p, 846400u);  /* error=400 ≥ SYNC_RESYNC_THRESHOLD_MS → Tier 3 */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);

    /* Re-acquire from the Tier 3 moment */
    s_snapshot_ms = 846400u;
    make_sync_pkt(&p, 0u, 846400u);
    MAC_OnSyncPacketReceived(&p, 846400u);  /* P1 → ACQUIRING */
    make_sync_pkt(&p, 1u, 846400u);  MAC_OnSyncPacketReceived(&p, 849400u);  /* P2 */
    make_sync_pkt(&p, 2u, 846400u);  MAC_OnSyncPacketReceived(&p, 852400u);  /* P3 → WARM */
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());

    /* 14 min from re-acquisition: no degradation */
    MAC_CheckSyncTimeout(852400u + 840000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());

    /* 15 min from re-acquisition: degradation */
    MAC_CheckSyncTimeout(852400u + 900000u);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(2, s_sync_lost_calls);
}

void test_c2_tier3_immediate_degradation_unchanged(void)
{
    sync_mac();
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, 0u);
    MAC_OnSyncPacketReceived(&p, 400u);  /* error=400 ≥ SYNC_RESYNC_THRESHOLD_MS → Tier 3 */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

/* ------- main ------------------------------------------------------------- */

/* ------- ArcLog events ----------------------------------------------------
 * The MAC's log lines are part of its contract: the host tool (tools/arclog)
 * classifies and correlates them, so their names and keys are asserted here.
 * ------------------------------------------------------------------------- */

void test_c2_arclog_acquisition_sequence(void)
{
    sync_mac();

    TEST_ASSERT_ARCLOG("SYNC_RX ph=0 ce=0 ep=0 st=0 exp=0 err=0 erru=");
    TEST_ASSERT_ARCLOG("clk=COLD act=set");
    TEST_ASSERT_ARCLOG("CLK from=COLD to=ACQ why=rtc_set");
    TEST_ASSERT_ARCLOG("SYNC_RX ph=0 ce=1 ep=0 st=3000 exp=3000 err=0 erru=");
    TEST_ASSERT_ARCLOG("clk=ACQ act=good");
    TEST_ASSERT_ARCLOG("CLK from=ACQ to=WARM why=lock");
    TEST_ASSERT_ARCLOG("MAC_ST from=SCAN to=SYNC why=lock");
    /* Header: core, module, verbosity letter, sequence number. */
    TEST_ASSERT_EQUAL_STRING_LEN("4Y L #", ArcLog_CaptureLine((uint32_t)ArcLog_CaptureFind("CLK from=ACQ")), 6);
}

void test_c2_arclog_warm_tiers(void)
{
    SyncPayload_t p;
    sync_mac();
    ArcLog_CaptureReset();

    make_sync_pkt(&p, 1u, 30000u);
    MAC_OnSyncPacketReceived(&p, 33004u);            /* err 4 ms  -> tier 1 */
    MAC_OnSyncPacketReceived(&p, 33050u);            /* err 50 ms -> tier 2 */
    MAC_OnSyncPacketReceived(&p, 33500u);            /* err 500   -> tier 3 */

    TEST_ASSERT_ARCLOG("exp=33000 err=4 erru=");
    TEST_ASSERT_ARCLOG("clk=WARM act=t1");
    TEST_ASSERT_ARCLOG("exp=33000 err=50 erru=");
    TEST_ASSERT_ARCLOG("clk=WARM act=t2");
    TEST_ASSERT_ARCLOG("exp=33000 err=500 erru=");
    TEST_ASSERT_ARCLOG("clk=WARM act=t3");
    TEST_ASSERT_ARCLOG("CLK from=WARM to=COLD why=tier3");
    TEST_ASSERT_ARCLOG("MAC_ST from=SYNC to=SCAN why=tier3");
}

void test_c2_arclog_signed_error(void)
{
    SyncPayload_t p;
    sync_mac();
    ArcLog_CaptureReset();

    make_sync_pkt(&p, 1u, 30000u);
    MAC_OnSyncPacketReceived(&p, 32995u);            /* 5 ms early */
    TEST_ASSERT_ARCLOG("st=32995 exp=33000 err=-5 erru=");
    TEST_ASSERT_ARCLOG("clk=WARM act=t1");
}

void test_c2_arclog_silence(void)
{
    sync_mac();
    ArcLog_CaptureReset();

    MAC_CheckSyncTimeout(6000u + SYNC_SILENCE_TIMEOUT_MS);
    TEST_ASSERT_ARCLOG("SYNC_SILENCE last=6000");
    TEST_ASSERT_ARCLOG("CLK from=WARM to=COLD why=silence");
}

/* ------- SyncStamp age carry ---------------------------------------------
 * The MAC runs at RxDone, ~one airtime after the SyncStamp. rtc_set must
 * add the time elapsed since the stamp, so that the new RTC domain reads the
 * sender's nominal cell start at the stamp instant.
 * ------------------------------------------------------------------------- */

void test_c2_cold_rtc_set_carries_time_since_stamp(void)
{
    SyncPayload_t p;
    make_sync_pkt(&p, 1u, 30000u);        /* nominal cell start 33000 */
    s_snapshot_ms = 50991u;               /* RxDone: 991 ms after the stamp */
    MAC_OnSyncPacketReceived(&p, 50000u);

    TEST_ASSERT_EQUAL(33991u, s_rtc_set_target_ms);
}

void test_c2_cold_stale_stamp_is_not_carried(void)
{
    SyncPayload_t p;
    make_sync_pkt(&p, 1u, 30000u);
    s_snapshot_ms = 50000u + SYNC_STAMP_MAX_AGE_MS + 1u;
    MAC_OnSyncPacketReceived(&p, 50000u);

    TEST_ASSERT_EQUAL(33000u, s_rtc_set_target_ms);
}

void test_c2_tier3_rtc_set_carries_time_since_stamp(void)
{
    SyncPayload_t p;
    sync_mac();
    make_sync_pkt(&p, 1u, 30000u);        /* expected arrival 33000 */
    s_snapshot_ms = 34491u;               /* RxDone 991 ms after the stamp */
    MAC_OnSyncPacketReceived(&p, 33500u); /* 500 ms late -> tier 3 */

    TEST_ASSERT_EQUAL(33991u, s_rtc_set_target_ms);
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
}

void test_c2_cold_rtc_set_carry_wraps_at_midnight(void)
{
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, MS_PER_DAY - 500u);  /* nominal 23:59:59.500 */
    s_snapshot_ms = 400u;                      /* old domain wrapped */
    MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 600u);

    TEST_ASSERT_EQUAL(500u, s_rtc_set_target_ms);  /* 1000 ms later, wrapped */
}

#define EP_BEFORE_MIDNIGHT  (MS_PER_DAY - 6000u)   /* 23:59:54.000 */

/* ------- Date of the RTC set (issue #28) -----------------------------------
 * A Sync packet's date is the date of the phase epoch. A node whose target
 * time falls on the next day must set that next day's date. */

static void make_dated_sync_pkt(SyncPayload_t *p, uint8_t cell, uint32_t ms_midnight,
                                uint8_t day, uint8_t month, uint8_t year)
{
    make_sync_pkt(p, cell, ms_midnight);
    p->day   = day;
    p->month = month;
    p->year  = year;
}

void test_c2_cold_set_in_the_epoch_day_keeps_the_date(void)
{
    SyncPayload_t p;
    make_dated_sync_pkt(&p, 2u, 43200000u, 0x14u, 0x03u, 0x26u);   /* 12:00:00 + 6 s */
    s_snapshot_ms = 43206000u;
    MAC_OnSyncPacketReceived(&p, 43206000u);

    TEST_ASSERT_EQUAL_HEX8(0x14, s_rtc_set_day);
    TEST_ASSERT_EQUAL_HEX8(0x03, s_rtc_set_month);
    TEST_ASSERT_EQUAL_HEX8(0x26, s_rtc_set_year);
}

void test_c2_cold_set_on_a_cell_after_midnight_advances_the_date(void)
{
    SyncPayload_t p;
    make_dated_sync_pkt(&p, 2u, EP_BEFORE_MIDNIGHT, 0x31u, 0x12u, 0x25u);
    s_snapshot_ms = 0u;
    MAC_OnSyncPacketReceived(&p, 0u);                /* cell 2 = 00:00:00 */

    TEST_ASSERT_EQUAL_UINT32(0u, s_rtc_set_target_ms);
    TEST_ASSERT_EQUAL_HEX8(0x01, s_rtc_set_day);
    TEST_ASSERT_EQUAL_HEX8(0x01, s_rtc_set_month);
    TEST_ASSERT_EQUAL_HEX8(0x26, s_rtc_set_year);
}

void test_c2_cold_set_on_a_cell_before_midnight_keeps_the_date(void)
{
    SyncPayload_t p;
    make_dated_sync_pkt(&p, 1u, EP_BEFORE_MIDNIGHT, 0x31u, 0x12u, 0x25u);
    s_snapshot_ms = MS_PER_DAY - 3000u;
    MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 3000u);  /* cell 1 = 23:59:57 */

    TEST_ASSERT_EQUAL_HEX8(0x31, s_rtc_set_day);
    TEST_ASSERT_EQUAL_HEX8(0x12, s_rtc_set_month);
    TEST_ASSERT_EQUAL_HEX8(0x25, s_rtc_set_year);
}

void test_c2_cold_set_whose_carry_crosses_midnight_advances_the_date(void)
{
    SyncPayload_t p;
    make_dated_sync_pkt(&p, 0u, MS_PER_DAY - 500u, 0x29u, 0x02u, 0x24u);
    s_snapshot_ms = 400u;                      /* old domain wrapped */
    MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 600u);

    TEST_ASSERT_EQUAL_UINT32(500u, s_rtc_set_target_ms);
    TEST_ASSERT_EQUAL_HEX8(0x01, s_rtc_set_day);
    TEST_ASSERT_EQUAL_HEX8(0x03, s_rtc_set_month);   /* 29 Feb 2024 + 1 day */
    TEST_ASSERT_EQUAL_HEX8(0x24, s_rtc_set_year);
}

void test_c2_tier3_set_on_a_cell_after_midnight_advances_the_date(void)
{
    sync_mac();
    SyncPayload_t p;
    make_dated_sync_pkt(&p, 2u, EP_BEFORE_MIDNIGHT, 0x31u, 0x12u, 0x25u);
    s_snapshot_ms = 500u;
    MAC_OnSyncPacketReceived(&p, 500u);   /* cell 2 due at 00:00:00, 500 ms late */

    TEST_ASSERT_EQUAL_HEX8(0x01, s_rtc_set_day);
    TEST_ASSERT_EQUAL_HEX8(0x01, s_rtc_set_month);
    TEST_ASSERT_EQUAL_HEX8(0x26, s_rtc_set_year);
}

/* ------- Cursor suspect --------------------------------------------------- */

void test_c2_cursor_suspect_drops_to_cold_without_rtc_write(void)
{
    sync_mac();
    int rtc_sets = s_rtc_set_calls;

    MAC_OnCursorSuspect();

    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(MAC_STATE_SCANNING, MAC_GetState());
    TEST_ASSERT_EQUAL(rtc_sets, s_rtc_set_calls);
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
    TEST_ASSERT_ARCLOG("CLK from=WARM to=COLD why=suspect");
}

void test_c2_cursor_suspect_in_cold_is_noop(void)
{
    MAC_OnCursorSuspect();

    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_rtc_set_calls);
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
}

/* A phase index that is not a Sync phase cannot anchor the FrameCursor:
 * the packet is dropped before it touches the RTC or the ClockState, so a
 * cold node keeps scanning instead of reaching ACQUIRING with no chain. */
void test_c2_sync_pkt_with_non_sync_phase_is_rejected(void)
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

/* ------- Midnight rollover (issue #54) ------------------------------------
 * The RTC reads ms since midnight and wraps at MS_PER_DAY. A Sync phase that
 * starts before midnight has cells after it: their expected arrival must be
 * taken modulo a day, and every comparison must be midnight-safe. */

/* P1 at cell 0 (23:59:54), P2 at cell 1 (23:59:57), P3 at cell 2 (00:00:00,
 * stamped 0 by the wrapped RTC). */
static void sync_mac_across_midnight(void)
{
    SyncPayload_t p;
    make_sync_pkt(&p, 0u, EP_BEFORE_MIDNIGHT); MAC_OnSyncPacketReceived(&p, EP_BEFORE_MIDNIGHT);
    make_sync_pkt(&p, 1u, EP_BEFORE_MIDNIGHT); MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 3000u);
    make_sync_pkt(&p, 2u, EP_BEFORE_MIDNIGHT); MAC_OnSyncPacketReceived(&p, 0u);
}

void test_c2_acquires_across_midnight(void)
{
    ArcLog_CaptureReset();
    sync_mac_across_midnight();
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0, s_sync_lost_calls);
    TEST_ASSERT_ARCLOG("SYNC_RX ph=0 ce=2 ep=86394000 st=0 exp=0 err=0 erru=");
    TEST_ASSERT_ARCLOG("clk=ACQ act=good");
}

void test_c2_warm_packet_after_midnight_is_tier1(void)
{
    sync_mac_across_midnight();
    SyncPayload_t p;
    make_sync_pkt(&p, 3u, EP_BEFORE_MIDNIGHT);
    ArcLog_CaptureReset();
    MAC_OnSyncPacketReceived(&p, 3002u);           /* cell 3 at 00:00:03, 2 ms late */
    TEST_ASSERT_TRUE(MAC_GetEpochReceivedThisPhase());
    TEST_ASSERT_ARCLOG("exp=3000 err=2 erru=");
    TEST_ASSERT_ARCLOG("clk=WARM act=t1");
}

void test_c2_warm_packet_early_before_midnight_has_negative_error(void)
{
    sync_mac_across_midnight();
    SyncPayload_t p;
    make_sync_pkt(&p, 2u, EP_BEFORE_MIDNIGHT);
    ArcLog_CaptureReset();
    MAC_OnSyncPacketReceived(&p, MS_PER_DAY - 3u);  /* cell 2 due at 00:00:00, 3 ms early */
    TEST_ASSERT_TRUE(MAC_GetEpochReceivedThisPhase());
    TEST_ASSERT_ARCLOG("exp=0 err=-3 erru=");
    TEST_ASSERT_ARCLOG("clk=WARM act=t1");
}

void test_c2_packet1_after_midnight_bootstraps_in_day_domain(void)
{
    SyncPayload_t p;
    make_sync_pkt(&p, 2u, EP_BEFORE_MIDNIGHT);    /* cell 2 = 00:00:00 */
    MAC_OnSyncPacketReceived(&p, 123456u);         /* old, unrelated RTC */
    TEST_ASSERT_EQUAL(CLOCK_ACQUIRING, MAC_GetClockState());
    TEST_ASSERT_EQUAL(0u, s_sync_bootstrapped_slot_start);
}

void test_c2_silence_timeout_spans_midnight(void)
{
    /* Last packet at 23:59:00; 15 min of silence ends at 00:14:00. */
    SyncPayload_t p;
    uint32_t ep = MS_PER_DAY - 66000u;
    make_sync_pkt(&p, 0u, ep); MAC_OnSyncPacketReceived(&p, ep);
    make_sync_pkt(&p, 1u, ep); MAC_OnSyncPacketReceived(&p, ep + 3000u);
    make_sync_pkt(&p, 2u, ep); MAC_OnSyncPacketReceived(&p, ep + 6000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    uint32_t last = ep + 6000u;                    /* 23:59:00 */

    MAC_CheckSyncTimeout((last + 840000u) % MS_PER_DAY);   /* 00:13:00 */
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
    MAC_CheckSyncTimeout((last + 900000u) % MS_PER_DAY);   /* 00:14:00 */
    TEST_ASSERT_EQUAL(CLOCK_COLD, MAC_GetClockState());
    TEST_ASSERT_EQUAL(1, s_sync_lost_calls);
}

static int32_t s_align_err_us;
static int     s_align_calls;
static void stub_rtc_align_subsecond(int32_t err_us)
{
    s_align_err_us = err_us;
    s_align_calls++;
}

/* The shift hook gets the error in microseconds, as logged in SYNC_RX erru, not
 * a pair of whole-ms times: a correction near 1 ms is not lost to the rounding.
 * After midnight it is the short way round the day, not a day-wrapped number.
 * A packet 50 ms late is stamped at 3050 ms = 12 493 ticks (the first tick not
 * before it), and (12 493 x 1000 - 3000 x 4096) x 125 / 512 = 50 049 us. */
void test_c2_tier2_after_midnight_passes_the_error_in_us(void)
{
    MAC_Hooks_t hooks = k_hooks;
    hooks.rtc_align_subsecond = stub_rtc_align_subsecond;
    MAC_Init(&hooks);
    s_align_calls = 0;
    sync_mac_across_midnight();

    SyncPayload_t p;
    make_sync_pkt(&p, 3u, EP_BEFORE_MIDNIGHT);    /* due at 00:00:03 */
    MAC_OnSyncPacketReceived(&p, 3050u);           /* 50 ms late: Tier 2 */
    TEST_ASSERT_EQUAL(1, s_align_calls);
    TEST_ASSERT_EQUAL_INT32(50049, s_align_err_us);
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
void test_c2_sample_hook_not_called_for_packet1_called_for_good_acquiring(void)
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

void test_c2_sample_hook_not_called_for_a_bad_acquiring_packet(void)
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
void test_c2_sample_hook_called_for_tier1_and_tier2_not_tier3(void)
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
void test_c2_sample_hook_runs_before_the_tier2_shift(void)
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

/* ------- the bands of CLOCK_WARM (issue #36) ------------------------------ */

/* Two thresholds, apart: SYNC_CORRECT_THRESHOLD_MS (1 ms) is where the phase is
 * corrected, SYNC_PARTICIPATE_THRESHOLD_MS (8 ms) is where a node stops being
 * relayed. Between them a packet is corrected and still relays. */
static void warm_with_align_hook(void)
{
    MAC_Hooks_t hooks = k_hooks;
    hooks.rtc_align_subsecond = stub_rtc_align_subsecond;
    MAC_Init(&hooks);
    s_align_calls = 0;
    s_snapshot_ms = 0u;
    SyncPayload_t p;
    sample_pkt(&p, 0u); MAC_OnSyncPacketReceived(&p, 0u);
    sample_pkt(&p, 1u); MAC_OnSyncPacketReceived(&p, 3000u);
    sample_pkt(&p, 2u); MAC_OnSyncPacketReceived(&p, 6000u);
    TEST_ASSERT_EQUAL(CLOCK_WARM, MAC_GetClockState());
}

void test_c2_a_5_ms_error_is_corrected_and_still_relays(void)
{
    warm_with_align_hook();
    SyncPayload_t p;
    sample_pkt(&p, 3u);
    MAC_OnSyncPacketReceived(&p, 9005u);                  /* 5 ms late */
    TEST_ASSERT_EQUAL(1, s_align_calls);
    TEST_ASSERT_INT32_WITHIN(250, 5000, s_align_err_us);
    TEST_ASSERT_TRUE(MAC_GetEpochReceivedThisPhase());    /* it relays */
}

void test_c2_no_sample_hook_is_fine(void)
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
    RUN_TEST(test_c2_boot_state_is_scanning);
    RUN_TEST(test_c2_boot_clock_state_is_cold);
    RUN_TEST(test_c2_scanning_returns_rx_for_sync_phase);
    RUN_TEST(test_c2_scanning_returns_rx_for_beacon_phase);
    RUN_TEST(test_c2_sync_pkt1_transitions_acquiring);
    RUN_TEST(test_c2_sync_pkt1_calls_rtc_set_hook);
    RUN_TEST(test_c2_packet1_rtc_set_called_with_target_ms_including_cell_offset);
    RUN_TEST(test_c2_sync_bootstrapped_hook_called_with_rtc_now_as_slot_start);
    RUN_TEST(test_c2_two_consecutive_good_packets_warm);
    RUN_TEST(test_c2_sync_locked_called_once);
    RUN_TEST(test_c2_acquiring_bad_packet_drops_to_cold);
    RUN_TEST(test_c2_acquiring_bad_packet_then_next_packet_sets_rtc);
    RUN_TEST(test_c2_acquiring_checks_packet_against_its_own_epoch);
    RUN_TEST(test_c2_acquiring_locks_when_packet1_is_the_last_tx_cell);
    RUN_TEST(test_c2_sync_lost_resets_to_scanning);
    RUN_TEST(test_c2_sync_lost_calls_hook);
    RUN_TEST(test_c2_warm_tier1_epoch_stored_on_good_cell0);
    RUN_TEST(test_c2_warm_tier2_no_epoch_below_resync_threshold);
    RUN_TEST(test_c2_warm_tier3_resync_above_resync_threshold);
    RUN_TEST(test_c2_sync_cell0_always_rx_when_warm);
    RUN_TEST(test_c2_sync_cell1_tx_when_epoch_received);
    RUN_TEST(test_c2_sync_cell1_rx_when_no_epoch);
    RUN_TEST(test_c2_cell_eligibility_all_hops);
    RUN_TEST(test_c2_beacon_transitions_to_paired);
    RUN_TEST(test_c2_beacon_tx_budget_set_on_first_beacon);
    RUN_TEST(test_c2_beacon_tx_budget_decrements_on_tx);
    RUN_TEST(test_c2_beacon_tx_budget_resets_on_hop_change);

/* ------- Beacon Tx at cell 3+ ------------------------------------------- */

    RUN_TEST(test_c2_beacon_tx_at_cell_3_after_beacon_received);
    RUN_TEST(test_c2_beacon_tx_budget_getter);

/* ------- Sync TX Budget (issue 29 - C2 only) ------------------------------ */

    RUN_TEST(test_c2_sync_tx_budget_limits_to_3_per_occurrence);
    RUN_TEST(test_c2_sync_tx_budget_resets_on_next_occurrence);
    RUN_TEST(test_c2_sync_tx_budget_getter_after_init);
    RUN_TEST(test_c2_sync_tx_budget_getter_decrements_on_tx);
    RUN_TEST(test_c2_sync_tx_budget_getter_zero_after_exhaustion);
    RUN_TEST(test_c2_sync_tx_budget_getter_resets_on_phase_entry);

/* ------- Epoch Received flag --------------------------------------------- */

    RUN_TEST(test_c2_epoch_received_false_after_init);
    RUN_TEST(test_c2_epoch_received_true_after_tier1);
    RUN_TEST(test_c2_epoch_received_resets_on_phase_entry);
    RUN_TEST(test_c2_hop_count_zero_after_init);
    RUN_TEST(test_c2_hop_count_set_after_beacon);

/* ------- Sync silence timeout (ADR-0013) -------------------------------- */

    RUN_TEST(test_c2_warm_14min_silence_no_degradation);
    RUN_TEST(test_c2_warm_15min_silence_degrades_to_cold);
    RUN_TEST(test_c2_acquiring_15min_silence_degrades_to_cold);
    RUN_TEST(test_c2_cold_timeout_is_noop);
    RUN_TEST(test_c2_sync_at_14min_resets_timer);
    RUN_TEST(test_c2_tier2_resets_silence_timer);
    RUN_TEST(test_c2_tier3_resets_silence_timer);
    RUN_TEST(test_c2_tier3_immediate_degradation_unchanged);
    RUN_TEST(test_c2_arclog_acquisition_sequence);
    RUN_TEST(test_c2_arclog_warm_tiers);
    RUN_TEST(test_c2_arclog_signed_error);
    RUN_TEST(test_c2_arclog_silence);
    RUN_TEST(test_c2_cursor_suspect_drops_to_cold_without_rtc_write);
    RUN_TEST(test_c2_cursor_suspect_in_cold_is_noop);
    RUN_TEST(test_c2_cold_rtc_set_carries_time_since_stamp);
    RUN_TEST(test_c2_cold_stale_stamp_is_not_carried);
    RUN_TEST(test_c2_tier3_rtc_set_carries_time_since_stamp);
    RUN_TEST(test_c2_cold_rtc_set_carry_wraps_at_midnight);
    RUN_TEST(test_c2_sync_pkt_with_non_sync_phase_is_rejected);
    RUN_TEST(test_c2_acquires_across_midnight);
    RUN_TEST(test_c2_warm_packet_after_midnight_is_tier1);
    RUN_TEST(test_c2_warm_packet_early_before_midnight_has_negative_error);
    RUN_TEST(test_c2_packet1_after_midnight_bootstraps_in_day_domain);
    RUN_TEST(test_c2_silence_timeout_spans_midnight);
    RUN_TEST(test_c2_tier2_after_midnight_passes_the_error_in_us);
    RUN_TEST(test_c2_cold_set_in_the_epoch_day_keeps_the_date);
    RUN_TEST(test_c2_cold_set_on_a_cell_after_midnight_advances_the_date);
    RUN_TEST(test_c2_cold_set_on_a_cell_before_midnight_keeps_the_date);
    RUN_TEST(test_c2_cold_set_whose_carry_crosses_midnight_advances_the_date);
    RUN_TEST(test_c2_tier3_set_on_a_cell_after_midnight_advances_the_date);
    RUN_TEST(test_c2_sample_hook_not_called_for_packet1_called_for_good_acquiring);
    RUN_TEST(test_c2_sample_hook_not_called_for_a_bad_acquiring_packet);
    RUN_TEST(test_c2_sample_hook_called_for_tier1_and_tier2_not_tier3);
    RUN_TEST(test_c2_sample_hook_runs_before_the_tier2_shift);
    RUN_TEST(test_c2_a_5_ms_error_is_corrected_and_still_relays);
    RUN_TEST(test_c2_no_sample_hook_is_fine);
    return UNITY_END();
}
