#include "unity.h"
#include <stdbool.h>
#include <stdint.h>
#include "lora_toa.h"

/*
 * LoRa time on air in microseconds, from the formula of the SX126x datasheet /
 * Semtech AN1200.13 (issue #82). Not measured: the expected values are worked
 * by hand from the formula and agree with the Semtech LoRa calculator.
 *
 *   Tsym = 2^SF / BW
 *   n_payload = 8 + max(ceil((8 PL - 4 SF + 28 + 16 CRC - 20 IH) / (4 (SF - 2 DE))) x (CR + 4), 0)
 *   ToA = (n_preamble + 4.25 + n_payload) x Tsym
 */

void setUp(void) {}
void tearDown(void) {}

/* The Sync packet: SF12, BW125, CR4/5, 8 preamble symbols, explicit header,
 * CRC, 10 bytes. Tsym 32.768 ms (DE = 1); n_payload = 8 + ceil(76/40) x 5 = 18;
 * (12.25 + 18) x 32.768 = 991.232 ms. The driver's whole-ms answer was 991. */
void test_the_sync_packet_is_991232_us(void)
{
    TEST_ASSERT_EQUAL_UINT32(991232u, LoraToa_Us(10u, 12u, 125000u, 1u, 8u, true, false));
}

/* SF7, BW125: Tsym 1.024 ms, no low data rate optimisation;
 * n_payload = 8 + ceil(96/28) x 5 = 28; (12.25 + 28) x 1.024 = 41.216 ms. */
void test_sf7_ten_bytes_is_41216_us(void)
{
    TEST_ASSERT_EQUAL_UINT32(41216u, LoraToa_Us(10u, 7u, 125000u, 1u, 8u, true, false));
}

/* SF12, 51 bytes: n_payload = 8 + ceil(388 / 40) x 5 = 63; 75.25 x 32.768 = 2465.792 ms. */
void test_sf12_fifty_one_bytes_is_2465792_us(void)
{
    TEST_ASSERT_EQUAL_UINT32(2465792u, LoraToa_Us(51u, 12u, 125000u, 1u, 8u, true, false));
}

/* The payload term cannot go negative: a 1-byte SF12 packet has the 8 fixed symbols. */
void test_a_tiny_packet_has_the_fixed_payload_symbols(void)
{
    /* numerator 8 - 48 + 28 + 16 = 4 -> ceil(4/40) = 1 -> 5 -> n_payload 13; (12.25 + 13) x 32.768 */
    TEST_ASSERT_EQUAL_UINT32(827392u, LoraToa_Us(1u, 12u, 125000u, 1u, 8u, true, false));
}

/* An implicit header saves 20 bits, no CRC saves 16: SF7, 10 bytes:
 * numerator 80 - 28 + 28 = 80 -> ceil(80/28) = 3 -> 15 -> n_payload 23; 35.25 x 1.024 = 36.096 ms. */
void test_implicit_header_and_no_crc(void)
{
    TEST_ASSERT_EQUAL_UINT32(36096u, LoraToa_Us(10u, 7u, 125000u, 1u, 8u, false, true));
}

/* A longer coding rate costs symbols: CR4/8 (4) at SF7, 10 bytes: ceil(96/28) = 4 x 8 = 32,
 * n_payload 40; 52.25 x 1.024 = 53.504 ms. */
void test_coding_rate_4_8(void)
{
    TEST_ASSERT_EQUAL_UINT32(53504u, LoraToa_Us(10u, 7u, 125000u, 4u, 8u, true, false));
}

/* The driver's whole-ms value and ours agree to the ms (991.232 -> 991). */
void test_the_us_value_rounds_to_the_drivers_ms(void)
{
    TEST_ASSERT_EQUAL_UINT32(991u, (LoraToa_Us(10u, 12u, 125000u, 1u, 8u, true, false) + 500u) / 1000u);
}

/*
 * The SF12 staircase (issues #57, #90). At SF12 / BW125 (DE = 1), CR4/5, 8 preamble
 * symbols, CRC, the payload term is 8 + 5 b symbols with b = ceil((8 PL - 4 - 20 IH) / 40),
 * so the airtime is a staircase in steps of 5 payload bytes:
 *   ToA = (12.25 + 8 + 5 b) x 32.768 ms = 663.552 ms + 163.840 ms x b.
 * Implicit header (IH = 1) shifts the staircase by 3 bytes: it saves a step only when
 * PL mod 5 is 1, 2 or 3, and nothing for the 10-byte Sync packet or a 40-byte packet.
 */
#define SF12_ToA_US(b)  (663552u + 163840u * (b))

static uint32_t sf12_toa(uint8_t payload_len, bool implicit)
{
    return LoraToa_Us(payload_len, 12u, 125000u, 1u, 8u, true, implicit);
}

static void assert_sf12_steps(uint8_t first, uint8_t last, uint32_t blocks, bool implicit)
{
    for (uint8_t pl = first; pl <= last; pl++) {
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(SF12_ToA_US(blocks), sf12_toa(pl, implicit), "payload length off its step");
    }
}

void test_sf12_explicit_steps_are_five_bytes(void)
{
    assert_sf12_steps(1u, 5u, 1u, false);       /* 8 PL - 4 = 4 .. 36 */
    assert_sf12_steps(6u, 10u, 2u, false);      /* the Sync packet is at the top of this step */
    assert_sf12_steps(11u, 15u, 3u, false);
    assert_sf12_steps(16u, 20u, 4u, false);
    assert_sf12_steps(36u, 40u, 8u, false);     /* 40 bytes: 1974.272 ms */
    assert_sf12_steps(41u, 45u, 9u, false);
}

void test_sf12_implicit_steps_are_shifted_by_three_bytes(void)
{
    assert_sf12_steps(4u, 8u, 1u, true);        /* 8 PL - 24 = 8 .. 40 */
    assert_sf12_steps(9u, 13u, 2u, true);       /* the Sync packet's 991.232 ms now reaches 13 bytes */
    assert_sf12_steps(14u, 18u, 3u, true);
    assert_sf12_steps(39u, 43u, 8u, true);      /* 40 to 43 bytes cost 1974.272 ms */
    assert_sf12_steps(44u, 48u, 9u, true);
}

/* The implicit header saves nothing for the Sync packet, and a whole step for 11 to 13 bytes. */
void test_implicit_header_saves_a_step_only_off_the_top_of_a_step(void)
{
    TEST_ASSERT_EQUAL_UINT32(sf12_toa(10u, false), sf12_toa(10u, true));
    TEST_ASSERT_EQUAL_UINT32(sf12_toa(40u, false), sf12_toa(40u, true));
    for (uint8_t pl = 11u; pl <= 13u; pl++) {
        TEST_ASSERT_EQUAL_UINT32(163840u, sf12_toa(pl, false) - sf12_toa(pl, true));
    }
    for (uint8_t pl = 41u; pl <= 43u; pl++) {
        TEST_ASSERT_EQUAL_UINT32(163840u, sf12_toa(pl, false) - sf12_toa(pl, true));
    }
    for (uint8_t pl = 14u; pl <= 15u; pl++) {
        TEST_ASSERT_EQUAL_UINT32(0u, sf12_toa(pl, false) - sf12_toa(pl, true));
    }
}

/* The largest payload that fits the 2500 ms slot of the shipped table (#90). */
void test_the_2500_ms_slot_holds_55_bytes_explicit_and_58_implicit(void)
{
    uint8_t explicit_max = 0u, implicit_max = 0u;
    for (uint16_t pl = 1u; pl <= 255u; pl++) {
        if (sf12_toa((uint8_t)pl, false) <= 2500000u) explicit_max = (uint8_t)pl;
        if (sf12_toa((uint8_t)pl, true)  <= 2500000u) implicit_max = (uint8_t)pl;
    }
    TEST_ASSERT_EQUAL_UINT8(55u, explicit_max);
    TEST_ASSERT_EQUAL_UINT8(58u, implicit_max);
    TEST_ASSERT_EQUAL_UINT32(1974272u, sf12_toa(40u, false));   /* 40 bytes alone are well inside */
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_the_sync_packet_is_991232_us);
    RUN_TEST(test_sf7_ten_bytes_is_41216_us);
    RUN_TEST(test_sf12_fifty_one_bytes_is_2465792_us);
    RUN_TEST(test_a_tiny_packet_has_the_fixed_payload_symbols);
    RUN_TEST(test_implicit_header_and_no_crc);
    RUN_TEST(test_coding_rate_4_8);
    RUN_TEST(test_the_us_value_rounds_to_the_drivers_ms);
    RUN_TEST(test_sf12_explicit_steps_are_five_bytes);
    RUN_TEST(test_sf12_implicit_steps_are_shifted_by_three_bytes);
    RUN_TEST(test_implicit_header_saves_a_step_only_off_the_top_of_a_step);
    RUN_TEST(test_the_2500_ms_slot_holds_55_bytes_explicit_and_58_implicit);
    return UNITY_END();
}
