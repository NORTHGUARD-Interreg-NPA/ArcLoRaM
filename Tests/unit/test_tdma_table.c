#include "unity.h"
#include "tdma_table.h"
#include <stdio.h>

void setUp(void)    { TdmaTable_Init(); }
void tearDown(void) {}

/* --- PhaseCount --------------------------------------------------------- */

void test_table_has_two_phases(void)
{
    TEST_ASSERT_EQUAL(2u, TdmaTable_PhaseCount());
}

/* --- GetPhase ------------------------------------------------------------ */

void test_get_phase_zero_is_not_null(void)
{
    TEST_ASSERT_NOT_NULL(TdmaTable_GetPhase(0u));
}

void test_get_phase_zero_type_is_sync(void)
{
    const Phase_t *p = TdmaTable_GetPhase(0u);
    TEST_ASSERT_EQUAL(PHASE_TYPE_SYNC, p->type);
}

void test_get_phase_zero_participant_mask_is_c1_and_c2_and_c3(void)
{
    /* bit1=C1, bit2=C2, bit3=C3 → 0x07 */
    const Phase_t *p = TdmaTable_GetPhase(0u);
    TEST_ASSERT_EQUAL_HEX8(PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3, p->participant_mask);
}

void test_get_phase_zero_direction_mode_is_mac_cell(void)
{
    const Phase_t *p = TdmaTable_GetPhase(0u);
    TEST_ASSERT_EQUAL(DIRECTION_MAC_CELL, p->direction_mode);
}

void test_get_phase_one_type_is_sync(void)
{
    const Phase_t *p = TdmaTable_GetPhase(1u);
    TEST_ASSERT_EQUAL(PHASE_TYPE_SYNC, p->type);
}

void test_get_phase_one_participant_mask_is_c1_and_c2_and_c3(void)
{
    const Phase_t *p = TdmaTable_GetPhase(1u);
    TEST_ASSERT_EQUAL_HEX8(PARTICIPANT_C1 | PARTICIPANT_C2 | PARTICIPANT_C3, p->participant_mask);
}

void test_get_phase_one_direction_mode_is_mac_cell(void)
{
    const Phase_t *p = TdmaTable_GetPhase(1u);
    TEST_ASSERT_EQUAL(DIRECTION_MAC_CELL, p->direction_mode);
}

void test_get_phase_out_of_bounds_returns_null(void)
{
    TEST_ASSERT_NULL(TdmaTable_GetPhase(2u));
    TEST_ASSERT_NULL(TdmaTable_GetPhase(255u));
}

/* --- PhaseStartOffset --------------------------------------------------- */

void test_phase_start_offset_zero_is_zero(void)
{
    TEST_ASSERT_EQUAL(0u, TdmaTable_PhaseStartOffset_ms(0u));
}

void test_phase_start_offset_one_is_30000(void)
{
    /* per_cell = 1 * (2500 + 500) = 3000; 10 cells → 30000 ms */
    TEST_ASSERT_EQUAL(30000u, TdmaTable_PhaseStartOffset_ms(1u));
}

void test_phase_start_offset_out_of_bounds_returns_zero(void)
{
    TEST_ASSERT_EQUAL(0u, TdmaTable_PhaseStartOffset_ms(2u));
}

/* --- Sync cell spacing constraint ---------------------------------------- */

/* HAL_RTCEx_SetSynchroShift busy-polls SHPF until the previous shift is
 * absorbed at the next RTC second boundary (1 Hz). If two sync receptions
 * triggering Tier 2 correction fall within the same RTC second, the second
 * call blocks the UTIL_SEQ task loop for up to 1 s, causing the node to
 * miss its next TDMA slot.
 *
 * To prevent this, every PHASE_TYPE_SYNC phase must have:
 *   per_cell = slot_count * (slot_active_ms + gap_after_slot_ms) >= 1000 ms
 *
 * See ADR-0009 "Sync cell spacing constraint for HAL_RTCEx_SetSynchroShift".
 */
void test_sync_phase_per_cell_ge_1000ms(void)
{
    uint8_t count = TdmaTable_PhaseCount();
    for (uint8_t i = 0u; i < count; i++) {
        const Phase_t *p = TdmaTable_GetPhase(i);
        TEST_ASSERT_NOT_NULL(p);
        if (p->type != PHASE_TYPE_SYNC) continue;

        uint32_t per_cell = (uint32_t)p->slot_count
                          * (p->slot_active_ms + p->gap_after_slot_ms);
        char msg[64];
        snprintf(msg, sizeof(msg), "phase %u: per_cell=%u < 1000", i, per_cell);
        TEST_ASSERT_MESSAGE(per_cell >= 1000u, msg);
    }
}

/* --- Largest packet per slot (issue #38) ---------------------------------- */

/* The Rx window of a slot closes at the latest start of the largest packet the
 * slot can carry, so the table says, per slot, how long that packet is. */
static const uint8_t k_two_slot_lens[] = { 51u, 4u };

void test_a_cell_slot_reports_the_packet_length_declared_for_its_index(void)
{
    Phase_t p = { .slot_count = 2u, .slot_max_packet_len = k_two_slot_lens };

    TEST_ASSERT_EQUAL_UINT8(51u, TdmaTable_MaxPacketLen(&p, SLOT_POS_CELL, 0u));
    TEST_ASSERT_EQUAL_UINT8(4u,  TdmaTable_MaxPacketLen(&p, SLOT_POS_CELL, 1u));
}

void test_the_header_slot_reports_its_own_packet_length(void)
{
    Phase_t p = { .slot_count = 2u, .slot_max_packet_len = k_two_slot_lens,
                  .header = { .duration_ms = 2500u, .max_packet_len = 10u } };

    TEST_ASSERT_EQUAL_UINT8(10u, TdmaTable_MaxPacketLen(&p, SLOT_POS_HEADER, 0u));
}

void test_the_footer_slot_reports_its_own_packet_length(void)
{
    Phase_t p = { .slot_count = 2u, .slot_max_packet_len = k_two_slot_lens,
                  .header = { .duration_ms = 2500u, .max_packet_len = 10u },
                  .footer = { .duration_ms = 2500u, .max_packet_len = 20u } };

    TEST_ASSERT_EQUAL_UINT8(20u, TdmaTable_MaxPacketLen(&p, SLOT_POS_FOOTER, 0u));
}

/* An undeclared slot reads 0 rather than reading through a missing array: a
 * field node must not fault on a table mistake (the host check below catches it
 * before it ships). */
void test_a_phase_with_no_declared_lengths_reads_zero(void)
{
    Phase_t p = { .slot_count = 2u };

    TEST_ASSERT_EQUAL_UINT8(0u, TdmaTable_MaxPacketLen(&p, SLOT_POS_CELL, 0u));
}

void test_a_slot_index_past_slot_count_reads_zero(void)
{
    static const uint8_t lens[] = { 51u, 4u, 99u };
    Phase_t p = { .slot_count = 2u, .slot_max_packet_len = lens };

    TEST_ASSERT_EQUAL_UINT8(0u, TdmaTable_MaxPacketLen(&p, SLOT_POS_CELL, 2u));
}

/* The Sync slots expect exactly the packet they carried before the length came
 * from the table: the 10-byte SyncPayload_t. */
void test_every_sync_slot_of_the_table_expects_the_sync_packet(void)
{
    for (uint8_t i = 0u; i < TdmaTable_PhaseCount(); i++) {
        const Phase_t *p = TdmaTable_GetPhase(i);
        if (p->type != PHASE_TYPE_SYNC) continue;

        for (uint8_t s = 0u; s < p->slot_count; s++) {
            char msg[48];
            snprintf(msg, sizeof(msg), "phase %u slot %u", i, s);
            TEST_ASSERT_EQUAL_UINT8_MESSAGE(10u,
                TdmaTable_MaxPacketLen(p, SLOT_POS_CELL, s), msg);
        }
    }
}

/* A slot left at 0 would get the time on air of an empty packet and a window
 * far too wide, so a new phase that forgets its lengths must not build green. */
void test_no_declared_slot_of_the_table_is_left_without_a_packet_length(void)
{
    for (uint8_t i = 0u; i < TdmaTable_PhaseCount(); i++) {
        const Phase_t *p = TdmaTable_GetPhase(i);
        char msg[48];

        for (uint8_t s = 0u; s < p->slot_count; s++) {
            snprintf(msg, sizeof(msg), "phase %u cell slot %u", i, s);
            TEST_ASSERT_NOT_EQUAL_MESSAGE(0u,
                TdmaTable_MaxPacketLen(p, SLOT_POS_CELL, s), msg);
        }
        if (p->header.duration_ms > 0u) {
            snprintf(msg, sizeof(msg), "phase %u header", i);
            TEST_ASSERT_NOT_EQUAL_MESSAGE(0u,
                TdmaTable_MaxPacketLen(p, SLOT_POS_HEADER, 0u), msg);
        }
        if (p->footer.duration_ms > 0u) {
            snprintf(msg, sizeof(msg), "phase %u footer", i);
            TEST_ASSERT_NOT_EQUAL_MESSAGE(0u,
                TdmaTable_MaxPacketLen(p, SLOT_POS_FOOTER, 0u), msg);
        }
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_table_has_two_phases);
    RUN_TEST(test_get_phase_zero_is_not_null);
    RUN_TEST(test_get_phase_zero_type_is_sync);
    RUN_TEST(test_get_phase_zero_participant_mask_is_c1_and_c2_and_c3);
    RUN_TEST(test_get_phase_zero_direction_mode_is_mac_cell);
    RUN_TEST(test_get_phase_one_type_is_sync);
    RUN_TEST(test_get_phase_one_participant_mask_is_c1_and_c2_and_c3);
    RUN_TEST(test_get_phase_one_direction_mode_is_mac_cell);
    RUN_TEST(test_get_phase_out_of_bounds_returns_null);
    RUN_TEST(test_phase_start_offset_zero_is_zero);
    RUN_TEST(test_phase_start_offset_one_is_30000);
    RUN_TEST(test_phase_start_offset_out_of_bounds_returns_zero);
    RUN_TEST(test_sync_phase_per_cell_ge_1000ms);
    RUN_TEST(test_a_cell_slot_reports_the_packet_length_declared_for_its_index);
    RUN_TEST(test_the_header_slot_reports_its_own_packet_length);
    RUN_TEST(test_the_footer_slot_reports_its_own_packet_length);
    RUN_TEST(test_a_phase_with_no_declared_lengths_reads_zero);
    RUN_TEST(test_a_slot_index_past_slot_count_reads_zero);
    RUN_TEST(test_every_sync_slot_of_the_table_expects_the_sync_packet);
    RUN_TEST(test_no_declared_slot_of_the_table_is_left_without_a_packet_length);
    return UNITY_END();
}
