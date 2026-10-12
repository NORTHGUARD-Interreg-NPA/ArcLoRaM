#include "unity.h"
#include <stdint.h>
#include <string.h>
#include "core_crash.h"

/* The crash record of a core (#101): what a fault handler stores in RAM that a
 * reset does not clear, so that the next BOOT, or a debugger, can say where and
 * why the core stopped. The module is pure: the handler hands it the stacked
 * frame and the status words, the tests check what comes out. */

void setUp(void) {}
void tearDown(void) {}

/* The frame of event 4 (docs/test-tracker/node-silences.md): the thread was in
 * tiny_vsnprintf_like when the SysTick handler was taken. */
static const uint32_t FRAME[8] = {
    0x2000129Cu, 0x200012A3u, 0x0000005Fu, 0x08009414u,   /* r0 r1 r2 r3 */
    0x00000FFFu, 0x0800932Du, 0x08009336u, 0x01000000u    /* r12 lr pc xpsr */
};
static const uint32_t STK[CORE_CRASH_STK_WORDS] = {
    0xFFFFFFFEu, 1u, 1u, 0x080061D5u, 0x20007F90u, 0x08009419u, 0x100u, 0x20001294u
};

static CoreCrashInput_t cm4_input(void)
{
    static const CoreCrashStatus_t st = {
        .cfsr = 0x00000100u, .hfsr = 0x40000000u, .mmfar = 0u, .bfar = 0u,
        .icsr = 0x04437003u, .shcsr = 0x00000800u
    };
    CoreCrashInput_t in = {0};
    in.core       = CORE_CRASH_CORE_CM4;
    in.kind       = CORE_CRASH_KIND_HARD;
    in.frame_addr = 0x20007ED0u;
    in.frame      = FRAME;
    in.stk        = STK;
    in.exc_return = 0xFFFFFFF1u;
    in.status     = &st;
    return in;
}

/* ---- make and validate --------------------------------------------------- */

void test_make_copies_the_stacked_frame(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    CoreCrash_Make(&rec, &in);

    TEST_ASSERT_EQUAL_HEX32(0x2000129Cu, rec.r0);
    TEST_ASSERT_EQUAL_HEX32(0x200012A3u, rec.r1);
    TEST_ASSERT_EQUAL_HEX32(0x0000005Fu, rec.r2);
    TEST_ASSERT_EQUAL_HEX32(0x08009414u, rec.r3);
    TEST_ASSERT_EQUAL_HEX32(0x00000FFFu, rec.r12);
    TEST_ASSERT_EQUAL_HEX32(0x0800932Du, rec.lr);
    TEST_ASSERT_EQUAL_HEX32(0x08009336u, rec.pc);
    TEST_ASSERT_EQUAL_HEX32(0x01000000u, rec.xpsr);
}

void test_sp_is_the_frame_plus_its_eight_words(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    CoreCrash_Make(&rec, &in);
    TEST_ASSERT_EQUAL_HEX32(0x20007ED0u + 32u, rec.sp);
    TEST_ASSERT_EQUAL_HEX32(0xFFFFFFF1u, rec.exc_return);
}

void test_make_keeps_the_status_words_and_the_stack_words(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    CoreCrash_Make(&rec, &in);

    TEST_ASSERT_EQUAL_HEX32(0x00000100u, rec.cfsr);
    TEST_ASSERT_EQUAL_HEX32(0x40000000u, rec.hfsr);
    TEST_ASSERT_EQUAL_HEX32(0x04437003u, rec.icsr);
    TEST_ASSERT_EQUAL_HEX32(0x00000800u, rec.shcsr);
    TEST_ASSERT_EQUAL_HEX32_ARRAY(STK, rec.stk, CORE_CRASH_STK_WORDS);
    TEST_ASSERT_EQUAL_UINT8(CORE_CRASH_CORE_CM4, rec.core);
    TEST_ASSERT_EQUAL_UINT8(CORE_CRASH_KIND_HARD, rec.kind);
    TEST_ASSERT_EQUAL_UINT8(CORE_CRASH_F_FRAME | CORE_CRASH_F_STATUS | CORE_CRASH_F_STK,
                            rec.flags & (CORE_CRASH_F_FRAME | CORE_CRASH_F_STATUS | CORE_CRASH_F_STK));
}

void test_a_fresh_record_is_valid(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    CoreCrash_Make(&rec, &in);
    TEST_ASSERT_TRUE(CoreCrash_IsValid(&rec));
}

void test_ram_that_was_never_written_is_not_a_record(void)
{
    CoreCrash_t rec;
    memset(&rec, 0, sizeof rec);
    TEST_ASSERT_FALSE(CoreCrash_IsValid(&rec));
    memset(&rec, 0xA5, sizeof rec);   /* what SRAM may hold after a power-up */
    TEST_ASSERT_FALSE(CoreCrash_IsValid(&rec));
}

void test_one_flipped_bit_in_any_word_makes_the_record_invalid(void)
{
    CoreCrashInput_t in = cm4_input();
    for (unsigned w = 0; w < sizeof(CoreCrash_t) / 4u; w++) {
        CoreCrash_t rec;
        CoreCrash_Make(&rec, &in);
        ((uint32_t *)&rec)[w] ^= 0x00010000u;
        TEST_ASSERT_FALSE_MESSAGE(CoreCrash_IsValid(&rec), "a flipped bit went unseen");
    }
}

void test_clear_makes_the_record_invalid(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    CoreCrash_Make(&rec, &in);
    CoreCrash_Clear(&rec);
    TEST_ASSERT_FALSE(CoreCrash_IsValid(&rec));
}

void test_the_record_has_a_fixed_size(void)
{
    /* It lives at a fixed place in the shared region: a change of layout moves
     * every field the post-mortem reads, so it is a change of version. */
    TEST_ASSERT_EQUAL_UINT32(108u, sizeof(CoreCrash_t));
    TEST_ASSERT_EQUAL_UINT32(0u, sizeof(CoreCrash_t) % 4u);
}

/* ---- a core without fault status registers (the CM0+) -------------------- */

void test_a_cm0_record_has_no_status_and_says_so(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    in.core   = CORE_CRASH_CORE_CM0;
    in.status = NULL;
    CoreCrash_Make(&rec, &in);

    TEST_ASSERT_TRUE(CoreCrash_IsValid(&rec));
    TEST_ASSERT_EQUAL_UINT8(0, rec.flags & CORE_CRASH_F_STATUS);
    TEST_ASSERT_EQUAL_HEX32(0u, rec.cfsr);
    TEST_ASSERT_EQUAL_HEX32(0u, rec.shcsr);
    TEST_ASSERT_EQUAL_STRING("na", CoreCrash_Why(&rec));
}

/* ---- a frame that cannot be read ------------------------------------------ */

void test_an_unreadable_frame_still_leaves_a_record(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    in.frame = NULL;
    in.stk   = NULL;
    CoreCrash_Make(&rec, &in);

    TEST_ASSERT_TRUE(CoreCrash_IsValid(&rec));
    TEST_ASSERT_EQUAL_UINT8(0, rec.flags & (CORE_CRASH_F_FRAME | CORE_CRASH_F_STK));
    TEST_ASSERT_EQUAL_HEX32(0u, rec.pc);
    TEST_ASSERT_EQUAL_HEX32(0u, rec.sp);
    TEST_ASSERT_EQUAL_HEX32(0xFFFFFFF1u, rec.exc_return);
    TEST_ASSERT_EQUAL_HEX32(0x00000100u, rec.cfsr);   /* the status words do not depend on the stack */
}

/* ---- the first fault wins -------------------------------------------------- */

void test_a_second_fault_keeps_the_first_record_and_counts_itself(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    CoreCrash_Make(&rec, &in);

    CoreCrash_NoteAgain(&rec);
    CoreCrash_NoteAgain(&rec);

    TEST_ASSERT_TRUE(CoreCrash_IsValid(&rec));   /* the check word follows the count */
    TEST_ASSERT_EQUAL_HEX32(0x08009336u, rec.pc);
    TEST_ASSERT_EQUAL_UINT8(2, CoreCrash_Again(&rec));
}

void test_the_count_of_faults_stops_at_fifteen(void)
{
    CoreCrash_t rec;
    CoreCrashInput_t in = cm4_input();
    CoreCrash_Make(&rec, &in);
    for (int i = 0; i < 40; i++) CoreCrash_NoteAgain(&rec);
    TEST_ASSERT_EQUAL_UINT8(15, CoreCrash_Again(&rec));
    TEST_ASSERT_TRUE(CoreCrash_IsValid(&rec));
    TEST_ASSERT_EQUAL_UINT8(CORE_CRASH_F_FRAME | CORE_CRASH_F_STATUS | CORE_CRASH_F_STK,
                            rec.flags & 0x0Fu);   /* the count did not spill into the flags */
}

/* ---- names ---------------------------------------------------------------- */

void test_names_of_the_core_and_of_the_kind(void)
{
    TEST_ASSERT_EQUAL_STRING("cm4", CoreCrash_CoreName(CORE_CRASH_CORE_CM4));
    TEST_ASSERT_EQUAL_STRING("cm0", CoreCrash_CoreName(CORE_CRASH_CORE_CM0));
    TEST_ASSERT_EQUAL_STRING("?",   CoreCrash_CoreName(9));

    TEST_ASSERT_EQUAL_STRING("hard",  CoreCrash_KindName(CORE_CRASH_KIND_HARD));
    TEST_ASSERT_EQUAL_STRING("bus",   CoreCrash_KindName(CORE_CRASH_KIND_BUS));
    TEST_ASSERT_EQUAL_STRING("mem",   CoreCrash_KindName(CORE_CRASH_KIND_MEM));
    TEST_ASSERT_EQUAL_STRING("usage", CoreCrash_KindName(CORE_CRASH_KIND_USAGE));
    TEST_ASSERT_EQUAL_STRING("nmi",   CoreCrash_KindName(CORE_CRASH_KIND_NMI));
    TEST_ASSERT_EQUAL_STRING("?",     CoreCrash_KindName(0));
    TEST_ASSERT_EQUAL_STRING("?",     CoreCrash_KindName(99));
}

/* ---- the cause, from the CM4 status registers ------------------------------ */

static const char *why(uint32_t cfsr, uint32_t hfsr)
{
    CoreCrash_t rec;
    CoreCrashStatus_t st = { .cfsr = cfsr, .hfsr = hfsr };
    CoreCrashInput_t in = cm4_input();
    in.status = &st;
    CoreCrash_Make(&rec, &in);
    return CoreCrash_Why(&rec);
}

void test_event_4_reads_as_an_instruction_bus_error(void)
{
    /* CFSR 0x100, HFSR FORCED: the registers of the halted CM4 of event 4. */
    TEST_ASSERT_EQUAL_STRING("ibuserr", why(0x00000100u, 0x40000000u));
}

void test_each_bus_fault_bit_has_a_name(void)
{
    TEST_ASSERT_EQUAL_STRING("ibuserr",     why(1u << 8,  0));
    TEST_ASSERT_EQUAL_STRING("preciserr",   why(1u << 9,  0));
    TEST_ASSERT_EQUAL_STRING("impreciserr", why(1u << 10, 0));
    TEST_ASSERT_EQUAL_STRING("unstkerr",    why(1u << 11, 0));
    TEST_ASSERT_EQUAL_STRING("stkerr",      why(1u << 12, 0));
}

void test_each_memory_management_bit_has_a_name(void)
{
    TEST_ASSERT_EQUAL_STRING("iaccviol",  why(1u << 0, 0));
    TEST_ASSERT_EQUAL_STRING("daccviol",  why(1u << 1, 0));
    TEST_ASSERT_EQUAL_STRING("munstkerr", why(1u << 3, 0));
    TEST_ASSERT_EQUAL_STRING("mstkerr",   why(1u << 4, 0));
}

void test_each_usage_fault_bit_has_a_name(void)
{
    TEST_ASSERT_EQUAL_STRING("undefinstr", why(1u << 16, 0));
    TEST_ASSERT_EQUAL_STRING("invstate",   why(1u << 17, 0));
    TEST_ASSERT_EQUAL_STRING("invpc",      why(1u << 18, 0));
    TEST_ASSERT_EQUAL_STRING("nocp",       why(1u << 19, 0));
    TEST_ASSERT_EQUAL_STRING("unaligned",  why(1u << 24, 0));
    TEST_ASSERT_EQUAL_STRING("divbyzero",  why(1u << 25, 0));
}

void test_a_stacking_error_is_named_before_the_fault_it_hides(void)
{
    /* A bad stack pointer makes every other bit unreliable: say it first. */
    TEST_ASSERT_EQUAL_STRING("stkerr", why((1u << 12) | (1u << 8) | (1u << 16), 0));
}

void test_without_a_configurable_fault_the_hard_fault_status_speaks(void)
{
    TEST_ASSERT_EQUAL_STRING("vecttbl",  why(0, 1u << 1));
    TEST_ASSERT_EQUAL_STRING("forced",   why(0, 1u << 30));
    TEST_ASSERT_EQUAL_STRING("debugevt", why(0, 1u << 31));
    TEST_ASSERT_EQUAL_STRING("none",     why(0, 0));
}

void test_the_cfsr_bit_wins_over_forced(void)
{
    TEST_ASSERT_EQUAL_STRING("undefinstr", why(1u << 16, 1u << 30));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_make_copies_the_stacked_frame);
    RUN_TEST(test_sp_is_the_frame_plus_its_eight_words);
    RUN_TEST(test_make_keeps_the_status_words_and_the_stack_words);
    RUN_TEST(test_a_fresh_record_is_valid);
    RUN_TEST(test_ram_that_was_never_written_is_not_a_record);
    RUN_TEST(test_one_flipped_bit_in_any_word_makes_the_record_invalid);
    RUN_TEST(test_clear_makes_the_record_invalid);
    RUN_TEST(test_the_record_has_a_fixed_size);
    RUN_TEST(test_a_cm0_record_has_no_status_and_says_so);
    RUN_TEST(test_an_unreadable_frame_still_leaves_a_record);
    RUN_TEST(test_a_second_fault_keeps_the_first_record_and_counts_itself);
    RUN_TEST(test_the_count_of_faults_stops_at_fifteen);
    RUN_TEST(test_names_of_the_core_and_of_the_kind);
    RUN_TEST(test_event_4_reads_as_an_instruction_bus_error);
    RUN_TEST(test_each_bus_fault_bit_has_a_name);
    RUN_TEST(test_each_memory_management_bit_has_a_name);
    RUN_TEST(test_each_usage_fault_bit_has_a_name);
    RUN_TEST(test_a_stacking_error_is_named_before_the_fault_it_hides);
    RUN_TEST(test_without_a_configurable_fault_the_hard_fault_status_speaks);
    RUN_TEST(test_the_cfsr_bit_wins_over_forced);
    return UNITY_END();
}
