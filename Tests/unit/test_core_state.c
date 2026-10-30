#include "unity.h"
#include <stdint.h>
#include <string.h>
#include "core_state.h"

/* The liveness record of a core and the survivor's judgement of it (#101): a
 * heartbeat that moves at every wake, a checkpoint id that says where the core
 * last was, and the wake it promised when it last went to sleep. */

void setUp(void) {}
void tearDown(void) {}

#define MARGIN 5000u

/* ---- the record ----------------------------------------------------------- */

void test_a_fresh_state_is_empty_and_valid(void)
{
    CoreState_t s;
    memset(&s, 0xA5, sizeof s);
    CoreState_Init(&s);
    TEST_ASSERT_TRUE(CoreState_IsValid(&s));
    TEST_ASSERT_EQUAL_UINT32(0, s.hb);
    TEST_ASSERT_EQUAL_UINT32(0, s.seq);
    TEST_ASSERT_EQUAL_UINT8(0, s.cp);
    TEST_ASSERT_EQUAL_UINT32(CORE_STATE_NO_PROMISE, s.wake_in_ms);
}

void test_ram_that_was_never_written_is_not_a_state(void)
{
    CoreState_t s;
    memset(&s, 0, sizeof s);
    TEST_ASSERT_FALSE(CoreState_IsValid(&s));
    memset(&s, 0xA5, sizeof s);
    TEST_ASSERT_FALSE(CoreState_IsValid(&s));
}

void test_a_checkpoint_keeps_the_last_id_and_counts(void)
{
    CoreState_t s;
    CoreState_Init(&s);
    CoreState_Checkpoint(&s, CP_CM0_SLOT_IN);
    CoreState_Checkpoint(&s, CP_CM0_MAC_DONE);
    TEST_ASSERT_EQUAL_UINT8(CP_CM0_MAC_DONE, s.cp);
    TEST_ASSERT_EQUAL_UINT8(CP_CM0_MAC_DONE, s.step);
    TEST_ASSERT_EQUAL_UINT32(2, s.seq);
}

void test_a_trace_call_or_a_sleep_does_not_hide_where_the_flow_was(void)
{
    /* A lost wake ends "alarm_set, a trace call, Stop2 entry": the step stays the alarm. */
    CoreState_t s;
    CoreState_Init(&s);
    CoreState_Checkpoint(&s, CP_CM0_ALARM_SET);
    CoreState_Checkpoint(&s, CP_CM0_LOG_IN);
    CoreState_Checkpoint(&s, CP_CM0_LOG_OUT);
    CoreState_Checkpoint(&s, CP_CM0_STOP_IN);
    TEST_ASSERT_EQUAL_UINT8(CP_CM0_STOP_IN, s.cp);
    TEST_ASSERT_EQUAL_UINT8(CP_CM0_ALARM_SET, s.step);
    CoreState_Checkpoint(&s, CP_CM4_STOP_OUT);
    TEST_ASSERT_EQUAL_UINT8(CP_CM0_ALARM_SET, s.step);
}

void test_exactly_the_trace_and_sleep_checkpoints_are_plumbing(void)
{
    for (unsigned id = 0; id < 256; id++) {
        bool expect = id == CP_CM0_LOG_IN || id == CP_CM0_LOG_OUT || id == CP_CM0_STOP_IN || id == CP_CM0_STOP_OUT
                   || id == CP_CM4_LOG_IN || id == CP_CM4_LOG_OUT || id == CP_CM4_STOP_IN || id == CP_CM4_STOP_OUT;
        TEST_ASSERT_EQUAL_MESSAGE(expect, CoreState_IsPlumbing((uint8_t)id), "plumbing mask");
    }
}

void test_a_core_stuck_in_a_trace_call_shows_both(void)
{
    CoreState_t s;
    CoreState_Init(&s);
    CoreState_Checkpoint(&s, CP_CM4_LOOP);
    CoreState_Checkpoint(&s, CP_CM4_LOG_IN);   /* never returns */
    TEST_ASSERT_EQUAL_UINT8(CP_CM4_LOG_IN, s.cp);
    TEST_ASSERT_EQUAL_UINT8(CP_CM4_LOOP, s.step);
}

void test_a_beat_counts_wakes(void)
{
    CoreState_t s;
    CoreState_Init(&s);
    CoreState_Beat(&s);
    CoreState_Beat(&s);
    CoreState_Beat(&s);
    TEST_ASSERT_EQUAL_UINT32(3, s.hb);
}

void test_a_kept_promise_counts_apart_from_the_wakes(void)
{
    CoreState_t s;
    CoreState_Init(&s);
    CoreState_Beat(&s);
    CoreState_Beat(&s);
    CoreState_Kept(&s);
    TEST_ASSERT_EQUAL_UINT32(2, s.hb);
    TEST_ASSERT_EQUAL_UINT32(1, s.kept);
}

void test_the_counters_wrap_without_trouble(void)
{
    CoreState_t s;
    CoreState_Init(&s);
    s.hb = 0xFFFFFFFFu;
    CoreState_Beat(&s);
    TEST_ASSERT_EQUAL_UINT32(0, s.hb);
}

void test_a_promise_is_a_duration_and_cancelling_it_promises_nothing(void)
{
    CoreState_t s;
    CoreState_Init(&s);
    CoreState_Promise(&s, 20000u);
    TEST_ASSERT_EQUAL_UINT32(20000u, s.wake_in_ms);
    CoreState_Promise(&s, CORE_STATE_NO_PROMISE);
    TEST_ASSERT_EQUAL_UINT32(CORE_STATE_NO_PROMISE, s.wake_in_ms);
}

void test_the_state_has_a_fixed_size(void)
{
    TEST_ASSERT_EQUAL_UINT32(24u, sizeof(CoreState_t));
}

/* ---- names ------------------------------------------------------------------ */

void test_checkpoints_have_names_per_core(void)
{
    TEST_ASSERT_EQUAL_STRING("slot_in",   CoreState_CpName(CORE_STATE_CORE_CM0, CP_CM0_SLOT_IN));
    TEST_ASSERT_EQUAL_STRING("mac_done",  CoreState_CpName(CORE_STATE_CORE_CM0, CP_CM0_MAC_DONE));
    TEST_ASSERT_EQUAL_STRING("rx_set",    CoreState_CpName(CORE_STATE_CORE_CM0, CP_CM0_RX_SET));
    TEST_ASSERT_EQUAL_STRING("rx_armed",  CoreState_CpName(CORE_STATE_CORE_CM0, CP_CM0_RX_ARMED));
    TEST_ASSERT_EQUAL_STRING("alarm_set", CoreState_CpName(CORE_STATE_CORE_CM0, CP_CM0_ALARM_SET));
    TEST_ASSERT_EQUAL_STRING("stop_in",   CoreState_CpName(CORE_STATE_CORE_CM0, CP_CM0_STOP_IN));
    TEST_ASSERT_EQUAL_STRING("loop",      CoreState_CpName(CORE_STATE_CORE_CM4, CP_CM4_LOOP));
    TEST_ASSERT_EQUAL_STRING("systick",   CoreState_CpName(CORE_STATE_CORE_CM4, CP_CM4_SYSTICK));
    TEST_ASSERT_EQUAL_STRING("log_in",    CoreState_CpName(CORE_STATE_CORE_CM4, CP_CM4_LOG_IN));
}

void test_an_id_of_the_other_core_or_none_has_no_name(void)
{
    TEST_ASSERT_EQUAL_STRING("none", CoreState_CpName(CORE_STATE_CORE_CM0, 0));
    TEST_ASSERT_EQUAL_STRING("?",    CoreState_CpName(CORE_STATE_CORE_CM0, CP_CM4_LOOP));
    TEST_ASSERT_EQUAL_STRING("?",    CoreState_CpName(CORE_STATE_CORE_CM4, CP_CM0_SLOT_IN));
    TEST_ASSERT_EQUAL_STRING("?",    CoreState_CpName(9, CP_CM0_SLOT_IN));
}

void test_the_ids_of_the_two_cores_never_meet(void)
{
    /* One byte names the checkpoint, so the cores share no id: a record read raw is unambiguous. */
    for (uint8_t id = 1; id < 255; id++) {
        int cm0 = strcmp(CoreState_CpName(CORE_STATE_CORE_CM0, id), "?") != 0;
        int cm4 = strcmp(CoreState_CpName(CORE_STATE_CORE_CM4, id), "?") != 0;
        TEST_ASSERT_FALSE_MESSAGE(cm0 && cm4, "an id names a checkpoint of both cores");
    }
}

/* ---- the survivor's judgement -------------------------------------------------- */

static CoreVerdict_t step(CoreWatch_t *w, const CoreState_t *s, uint32_t elapsed_ms)
{
    return CoreWatch_Step(w, s, elapsed_ms, MARGIN);
}

/* One cycle of a healthy core: it wakes, serves the wake it promised, promises the next. */
static void serve(CoreState_t *s, uint32_t promise_ms)
{
    CoreState_Beat(s);
    CoreState_Kept(s);
    CoreState_Promise(s, promise_ms);
}

void test_a_core_that_keeps_its_promises_is_fine(void)
{
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    for (int i = 0; i < 10; i++) {
        serve(&s, 20000u);
        CoreVerdict_t v = step(&w, &s, 30000u);
        TEST_ASSERT_EQUAL(CORE_WATCH_ALIVE, v.status);
        TEST_ASSERT_TRUE(v.alive);
        TEST_ASSERT_EQUAL_UINT32(0, v.silent_ms);
    }
}

void test_a_silence_inside_the_promise_is_a_sleep(void)
{
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, 60000u);
    TEST_ASSERT_EQUAL(CORE_WATCH_ALIVE, step(&w, &s, 30000u).status);     /* first sight: the baseline */
    CoreVerdict_t v = step(&w, &s, 30000u);                              /* 30 s of silence of 60 + 5 */
    TEST_ASSERT_EQUAL(CORE_WATCH_ASLEEP, v.status);
    TEST_ASSERT_EQUAL_UINT32(30000u, v.silent_ms);
}

void test_a_silence_past_the_promise_and_the_margin_is_overdue_by_the_difference(void)
{
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, 20000u);
    step(&w, &s, 30000u);
    CoreVerdict_t v = step(&w, &s, 30000u);   /* 30 s silent, 20 s promised, 5 s margin */
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, v.status);
    TEST_ASSERT_EQUAL_UINT32(30000u, v.silent_ms);
    TEST_ASSERT_EQUAL_UINT32(10000u, v.over_ms);          /* silent minus promised, the margin not deducted */
    TEST_ASSERT_EQUAL_UINT32(20000u, v.due_ms);
}

void test_the_margin_is_a_grace_not_a_deadline(void)
{
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, 20000u);
    step(&w, &s, 1000u);
    TEST_ASSERT_EQUAL(CORE_WATCH_ASLEEP, step(&w, &s, 24000u).status);   /* 24 s: inside 20 + 5 */
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, step(&w, &s, 2000u).status);   /* 26 s: past it */
}

void test_a_core_that_promised_nothing_is_never_overdue(void)
{
    /* Scanning Rx: no alarm chain, the core sleeps until the radio wakes it. */
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, CORE_STATE_NO_PROMISE);
    step(&w, &s, 30000u);
    for (int i = 0; i < 100; i++) {
        CoreVerdict_t v = step(&w, &s, 30000u);
        TEST_ASSERT_EQUAL(CORE_WATCH_ASLEEP, v.status);
    }
}

void test_a_promise_served_clears_the_silence(void)
{
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, 20000u);
    step(&w, &s, 30000u);
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, step(&w, &s, 30000u).status);
    serve(&s, 20000u);
    CoreVerdict_t v = step(&w, &s, 30000u);
    TEST_ASSERT_EQUAL(CORE_WATCH_ALIVE, v.status);
    TEST_ASSERT_EQUAL_UINT32(0, v.silent_ms);
}

void test_a_live_core_that_lost_its_wake_is_overdue_and_still_beating(void)
{
    /* A NUCLEO wakes for its LED timer every few seconds, so the heartbeat moves while the
     * Alarm A chain is dead: the promise is judged by the wake that was promised. */
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, 20000u);
    step(&w, &s, 30000u);
    CoreState_Beat(&s);                       /* the LED timer */
    CoreVerdict_t v = step(&w, &s, 30000u);
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, v.status);
    TEST_ASSERT_TRUE(v.alive);
    CoreState_Beat(&s);
    v = step(&w, &s, 30000u);
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, v.status);
    TEST_ASSERT_TRUE(v.alive);
    TEST_ASSERT_EQUAL_UINT32(60000u, v.silent_ms);
}

void test_a_dead_core_is_overdue_and_not_beating(void)
{
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, 20000u);
    step(&w, &s, 30000u);
    CoreVerdict_t v = step(&w, &s, 30000u);
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, v.status);
    TEST_ASSERT_FALSE(v.alive);
}

void test_a_checkpoint_loop_does_not_hide_a_stalled_core(void)
{
    /* Neither the heartbeat nor the checkpoints are the promise: a core spinning around its
     * checkpoints serves nothing. */
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, 20000u);
    step(&w, &s, 30000u);
    for (int i = 0; i < 1000; i++) CoreState_Checkpoint(&s, CP_CM0_RX_SET);
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, step(&w, &s, 30000u).status);
}

void test_the_first_look_at_a_state_only_takes_its_measure(void)
{
    /* The survivor starts watching at some point of the other core's life: the first look is a baseline,
     * not a verdict, even if the core has served nothing since boot. */
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    CoreState_Promise(&s, 1000u);
    CoreVerdict_t v = step(&w, &s, 30000u);
    TEST_ASSERT_EQUAL(CORE_WATCH_ALIVE, v.status);
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, step(&w, &s, 30000u).status);   /* a second look finds nothing served */
}

void test_a_state_that_is_not_valid_is_not_judged(void)
{
    CoreState_t s; CoreWatch_t w;
    memset(&s, 0xA5, sizeof s);
    CoreWatch_Init(&w);
    TEST_ASSERT_EQUAL(CORE_WATCH_UNKNOWN, step(&w, &s, 30000u).status);
}

void test_the_silence_saturates_instead_of_wrapping(void)
{
    CoreState_t s; CoreWatch_t w;
    CoreState_Init(&s); CoreWatch_Init(&w);
    serve(&s, 20000u);
    step(&w, &s, 30000u);
    step(&w, &s, 0xFFFFFFF0u);
    CoreVerdict_t v = step(&w, &s, 0xFFFFFFF0u);
    TEST_ASSERT_EQUAL(CORE_WATCH_OVERDUE, v.status);
    TEST_ASSERT_EQUAL_UINT32(0xFFFFFFFFu, v.silent_ms);
}

void test_names_of_the_verdicts(void)
{
    TEST_ASSERT_EQUAL_STRING("alive",   CoreWatch_StatusName(CORE_WATCH_ALIVE));
    TEST_ASSERT_EQUAL_STRING("asleep",  CoreWatch_StatusName(CORE_WATCH_ASLEEP));
    TEST_ASSERT_EQUAL_STRING("overdue", CoreWatch_StatusName(CORE_WATCH_OVERDUE));
    TEST_ASSERT_EQUAL_STRING("unknown", CoreWatch_StatusName(CORE_WATCH_UNKNOWN));
    TEST_ASSERT_EQUAL_STRING("?",       CoreWatch_StatusName((CoreWatchStatus_t)99));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_a_fresh_state_is_empty_and_valid);
    RUN_TEST(test_ram_that_was_never_written_is_not_a_state);
    RUN_TEST(test_a_checkpoint_keeps_the_last_id_and_counts);
    RUN_TEST(test_a_trace_call_or_a_sleep_does_not_hide_where_the_flow_was);
    RUN_TEST(test_exactly_the_trace_and_sleep_checkpoints_are_plumbing);
    RUN_TEST(test_a_core_stuck_in_a_trace_call_shows_both);
    RUN_TEST(test_a_beat_counts_wakes);
    RUN_TEST(test_a_kept_promise_counts_apart_from_the_wakes);
    RUN_TEST(test_the_counters_wrap_without_trouble);
    RUN_TEST(test_a_promise_is_a_duration_and_cancelling_it_promises_nothing);
    RUN_TEST(test_the_state_has_a_fixed_size);
    RUN_TEST(test_checkpoints_have_names_per_core);
    RUN_TEST(test_an_id_of_the_other_core_or_none_has_no_name);
    RUN_TEST(test_the_ids_of_the_two_cores_never_meet);
    RUN_TEST(test_a_core_that_keeps_its_promises_is_fine);
    RUN_TEST(test_a_silence_inside_the_promise_is_a_sleep);
    RUN_TEST(test_a_silence_past_the_promise_and_the_margin_is_overdue_by_the_difference);
    RUN_TEST(test_the_margin_is_a_grace_not_a_deadline);
    RUN_TEST(test_a_core_that_promised_nothing_is_never_overdue);
    RUN_TEST(test_a_promise_served_clears_the_silence);
    RUN_TEST(test_a_live_core_that_lost_its_wake_is_overdue_and_still_beating);
    RUN_TEST(test_a_dead_core_is_overdue_and_not_beating);
    RUN_TEST(test_a_checkpoint_loop_does_not_hide_a_stalled_core);
    RUN_TEST(test_the_first_look_at_a_state_only_takes_its_measure);
    RUN_TEST(test_a_state_that_is_not_valid_is_not_judged);
    RUN_TEST(test_the_silence_saturates_instead_of_wrapping);
    RUN_TEST(test_names_of_the_verdicts);
    return UNITY_END();
}
