#include "unity.h"
#include <stdint.h>
#include "reset_cause.h"

/* The reset cause of BOOT (#101): the RCC_CSR flags the CM4 reads first thing
 * after any reset, decoded into one word. The flags that go with each kind of
 * reset are what the STM32WL reference manual and the L4 family before it
 * document (PINRSTF is also set by the other resets, since they pull NRST);
 * the bench checks them on the boards in the Test Record of #101. */

void setUp(void) {}
void tearDown(void) {}

#define PIN   RESET_FLAG_PIN
#define BOR   RESET_FLAG_BOR
#define SFT   RESET_FLAG_SFT
#define IWDG  RESET_FLAG_IWDG
#define WWDG  RESET_FLAG_WWDG
#define LPWR  RESET_FLAG_LPWR
#define OBL   RESET_FLAG_OBL

void test_the_flag_bits_are_those_of_rcc_csr(void)
{
    /* RCC_CSR of the STM32WL55 (stm32wl55xx.h): checked against the macros of
     * the device header when the glue is compiled for the target. */
    TEST_ASSERT_EQUAL_HEX32(1u << 14, RESET_FLAG_RF);
    TEST_ASSERT_EQUAL_HEX32(1u << 24, RESET_FLAG_RFILA);
    TEST_ASSERT_EQUAL_HEX32(1u << 25, RESET_FLAG_OBL);
    TEST_ASSERT_EQUAL_HEX32(1u << 26, RESET_FLAG_PIN);
    TEST_ASSERT_EQUAL_HEX32(1u << 27, RESET_FLAG_BOR);
    TEST_ASSERT_EQUAL_HEX32(1u << 28, RESET_FLAG_SFT);
    TEST_ASSERT_EQUAL_HEX32(1u << 29, RESET_FLAG_IWDG);
    TEST_ASSERT_EQUAL_HEX32(1u << 30, RESET_FLAG_WWDG);
    TEST_ASSERT_EQUAL_HEX32(1u << 31, RESET_FLAG_LPWR);
}

void test_a_power_up_sets_bor_and_pin(void)
{
    TEST_ASSERT_EQUAL(RESET_CAUSE_POWER, ResetCause_Decode(BOR | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_POWER, ResetCause_Decode(BOR));
}

void test_the_nrst_pin_alone_is_a_pin_reset(void)
{
    TEST_ASSERT_EQUAL(RESET_CAUSE_PIN, ResetCause_Decode(PIN));
}

void test_a_software_reset_is_not_taken_for_the_pin_that_comes_with_it(void)
{
    TEST_ASSERT_EQUAL(RESET_CAUSE_SOFT, ResetCause_Decode(SFT | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_SOFT, ResetCause_Decode(SFT));
}

void test_the_watchdogs_and_the_option_bytes_and_low_power(void)
{
    TEST_ASSERT_EQUAL(RESET_CAUSE_IWDG, ResetCause_Decode(IWDG | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_WWDG, ResetCause_Decode(WWDG | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_OBL,  ResetCause_Decode(OBL | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_LPWR, ResetCause_Decode(LPWR | PIN));
}

void test_no_flag_is_no_cause(void)
{
    /* The flags were cleared by an earlier boot and only a core restarted. */
    TEST_ASSERT_EQUAL(RESET_CAUSE_NONE, ResetCause_Decode(0));
}

void test_the_radio_flags_do_not_name_a_cause_of_the_mcu(void)
{
    TEST_ASSERT_EQUAL(RESET_CAUSE_NONE, ResetCause_Decode(RESET_FLAG_RF));
    TEST_ASSERT_EQUAL(RESET_CAUSE_NONE, ResetCause_Decode(RESET_FLAG_RFILA));
    TEST_ASSERT_EQUAL(RESET_CAUSE_PIN,  ResetCause_Decode(RESET_FLAG_RF | PIN));
}

void test_bits_that_are_not_reset_flags_are_ignored(void)
{
    TEST_ASSERT_EQUAL(RESET_CAUSE_NONE, ResetCause_Decode(0x00FFFFFFu & ~RESET_FLAG_RF));
    TEST_ASSERT_EQUAL(RESET_CAUSE_PIN,  ResetCause_Decode(0x00000F00u | PIN));
}

void test_when_several_resets_are_flagged_the_most_telling_one_wins(void)
{
    /* Flags accumulate until RMVF: a watchdog bite after a software reset that
     * nobody cleared is still a watchdog bite. */
    TEST_ASSERT_EQUAL(RESET_CAUSE_LPWR, ResetCause_Decode(LPWR | IWDG | SFT | BOR | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_WWDG, ResetCause_Decode(WWDG | IWDG | SFT | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_IWDG, ResetCause_Decode(IWDG | SFT | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_SOFT, ResetCause_Decode(SFT | OBL | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_OBL,  ResetCause_Decode(OBL | BOR | PIN));
    TEST_ASSERT_EQUAL(RESET_CAUSE_POWER, ResetCause_Decode(BOR | PIN));
}

void test_names(void)
{
    TEST_ASSERT_EQUAL_STRING("none",  ResetCause_Name(RESET_CAUSE_NONE));
    TEST_ASSERT_EQUAL_STRING("power", ResetCause_Name(RESET_CAUSE_POWER));
    TEST_ASSERT_EQUAL_STRING("pin",   ResetCause_Name(RESET_CAUSE_PIN));
    TEST_ASSERT_EQUAL_STRING("soft",  ResetCause_Name(RESET_CAUSE_SOFT));
    TEST_ASSERT_EQUAL_STRING("iwdg",  ResetCause_Name(RESET_CAUSE_IWDG));
    TEST_ASSERT_EQUAL_STRING("wwdg",  ResetCause_Name(RESET_CAUSE_WWDG));
    TEST_ASSERT_EQUAL_STRING("lpwr",  ResetCause_Name(RESET_CAUSE_LPWR));
    TEST_ASSERT_EQUAL_STRING("obl",   ResetCause_Name(RESET_CAUSE_OBL));
    TEST_ASSERT_EQUAL_STRING("?",     ResetCause_Name((ResetCause_t)99));
}

void test_the_mask_keeps_the_reset_flags_and_nothing_else(void)
{
    TEST_ASSERT_EQUAL_HEX32(0xFF004000u, RESET_FLAGS_MASK);   /* bit 14 and bits 24 to 31 */
    TEST_ASSERT_EQUAL_HEX32(0u, RESET_FLAGS_MASK & RESET_FLAG_RMVF);   /* the clear bit is not a flag */
    TEST_ASSERT_EQUAL_HEX32(1u << 23, RESET_FLAG_RMVF);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_the_flag_bits_are_those_of_rcc_csr);
    RUN_TEST(test_a_power_up_sets_bor_and_pin);
    RUN_TEST(test_the_nrst_pin_alone_is_a_pin_reset);
    RUN_TEST(test_a_software_reset_is_not_taken_for_the_pin_that_comes_with_it);
    RUN_TEST(test_the_watchdogs_and_the_option_bytes_and_low_power);
    RUN_TEST(test_no_flag_is_no_cause);
    RUN_TEST(test_the_radio_flags_do_not_name_a_cause_of_the_mcu);
    RUN_TEST(test_bits_that_are_not_reset_flags_are_ignored);
    RUN_TEST(test_when_several_resets_are_flagged_the_most_telling_one_wins);
    RUN_TEST(test_names);
    RUN_TEST(test_the_mask_keeps_the_reset_flags_and_nothing_else);
    return UNITY_END();
}
