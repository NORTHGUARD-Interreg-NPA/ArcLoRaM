/*!
 * \file      core_state_glue.c
 *
 * \brief     Target glue of the liveness record (#101); see core_state_glue.h.
 *
 * \author    Simon R.C. Langlais ( Celium )
 */
#include "core_state_glue.h"
#include "core_cp.h"
#include "arclog.h"
#include "shared_mem.h"
#if defined(CORE_CM4)
#  include "stm32_timer.h"
#else
#  include "day_ms.h"        /* DayMs_Diff */
#endif

#if defined(CORE_CM0PLUS)
#  define SELF_NAME   "cm0"
#  define SELF_CORE   CORE_STATE_CORE_CM0
#  define OTHER_NAME  "cm4"
#  define OTHER_CORE  CORE_STATE_CORE_CM4
#  define SEEN_ABOUT_SELF   g_core_seen_cm0     /* written by the CM4 */
#else
#  define SELF_NAME   "cm4"
#  define SELF_CORE   CORE_STATE_CORE_CM4
#  define OTHER_NAME  "cm0"
#  define OTHER_CORE  CORE_STATE_CORE_CM0
#  define SEEN_ABOUT_SELF   g_core_seen_cm4     /* written by the CM0+ */
#endif

/* The two cores look at each other at the same pace of 30 s (the CM4's timer; the CM0+ at its wakes) and
 * with the same margin, which covers the one period by which a look under-reads the silence. */
#define WATCH_PERIOD_MS   30000u
#define WATCH_MARGIN_MS   5000u

static CoreState_t s_prev;
static bool        s_prev_valid;

void CoreStateBoot_Snapshot(void)
{
    s_prev       = CORE_STATE_SELF;
    s_prev_valid = CoreState_IsValid(&s_prev);
    CoreState_Init(&CORE_STATE_SELF);
}

/* The wake promised, as ArcLog prints it: -1 when nothing was promised. */
static int due_of_ms(uint32_t wake_in_ms)
{
    return (wake_in_ms == CORE_STATE_NO_PROMISE) ? -1 : (int)wake_in_ms;
}

void CoreStateBoot_Report(void)
{
    if (!s_prev_valid) {
        return;
    }
    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "CORE_STATE",
           "core=%s st=boot cp=%s step=%s isr=%s hb=%u kept=%u seq=%u due=%d",
           SELF_NAME, CoreState_CpName(SELF_CORE, s_prev.cp), CoreState_CpName(SELF_CORE, s_prev.step),
           CoreState_CpName(SELF_CORE, s_prev.isr), (unsigned)s_prev.hb, (unsigned)s_prev.kept, (unsigned)s_prev.seq, due_of_ms(s_prev.wake_in_ms));
}

/* One overdue line, whoever made the verdict and whenever it is printed: the live look of the CM4 about the
 * CM0+, and the report at BOOT of what a live core saw of the other. `by` is the core that looked. */
static void print_overdue(const char *about, uint8_t about_core, const char *by, const CoreSeen_t *r)
{
    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "CORE_STATE",
           "core=%s st=overdue by=%s cp=%s step=%s isr=%s hb=%u kept=%u seq=%u due=%d silent=%u over=%u alive=%u crash=%u",
           about, by, CoreState_CpName(about_core, r->cp), CoreState_CpName(about_core, r->step),
           CoreState_CpName(about_core, r->isr), (unsigned)r->hb, (unsigned)r->kept, (unsigned)r->seq, due_of_ms(r->due_ms),
           (unsigned)r->silent_ms, (unsigned)r->over_ms, (unsigned)r->alive, (unsigned)r->crash);
}

void CoreSeenBoot_Report(void)
{
    if (!CoreSeen_IsOverdue(&SEEN_ABOUT_SELF)) {
        return;
    }
    print_overdue(SELF_NAME, SELF_CORE, OTHER_NAME, &SEEN_ABOUT_SELF);
    CoreSeen_Clear(&SEEN_ABOUT_SELF);
}

#if defined(CORE_CM4)

/* The CM4 looks at the CM0+ every 30 s, from a timer of its own. Its own promise to the CM0+ is the same
 * 30 s: it serves it at each look, which is what the CM0+ judges it by. On the production board, which has
 * no LED timer, that is one wake per 30 s (the NUCLEO's LED timer wakes it every 4 s anyway). */
/* While overdue, print the first look and then one in 20 (every 10 minutes): the line is 150 bytes. */
#define WATCH_REPEAT_EVERY 20u

static UTIL_TIMER_Object_t s_watch_timer;
static CoreWatch_t         s_watch;
static uint32_t            s_overdue_looks;
static volatile bool       s_look_due;

/* The timer fires in an interrupt, on a wake from Stop2, before the wake-up path has restarted the UART
 * (vcom_Resume): a line printed from here left with its first characters garbled and the host could not
 * parse it. The interrupt only says a look is due; the look is made, and printed, from the idle hook
 * (CoreWatch_PostIdle), after the UART is back. */
static void watch_cb(void *context)
{
    (void)context;
    s_look_due = true;
}

void CoreWatch_PostIdle(void)
{
    if (!s_look_due) {
        return;
    }
    s_look_due = false;
    CORE_KEPT();   /* the wake promised to the CM0+ is served */

    CoreVerdict_t v = CoreWatch_Step(&s_watch, &g_core_state_cm0, WATCH_PERIOD_MS, WATCH_MARGIN_MS);
    CoreSeen_Make(&g_core_seen_cm0, &g_core_state_cm0, &v, CoreCrash_IsValid(&g_core_crash_cm0));

    if (v.status != CORE_WATCH_OVERDUE) {
        s_overdue_looks = 0u;
        return;
    }
    if ((s_overdue_looks++ % WATCH_REPEAT_EVERY) != 0u) {
        return;
    }
    print_overdue("cm0", CORE_STATE_CORE_CM0, "cm4", &g_core_seen_cm0);
}

void CoreWatch_Start(void)
{
    CoreWatch_Init(&s_watch);
    CoreState_Promise(&CORE_STATE_SELF, WATCH_PERIOD_MS);
    UTIL_TIMER_Create(&s_watch_timer, WATCH_PERIOD_MS, UTIL_TIMER_PERIODIC, watch_cb, NULL);
    UTIL_TIMER_Start(&s_watch_timer);
}

void CoreWatchOther_Look(uint32_t now_ms)
{
    (void)now_ms;
}

void CoreWatchOther_Resync(void)
{
}

#else /* CM0+ */

void CoreWatch_Start(void)
{
}

void CoreWatch_PostIdle(void)
{
}

/* The CM0+ looks at the CM4 at its own wakes that already know the time of day: the slot task and the radio
 * interrupt. No timer of its own, so no extra wake and no further start and stop of the RTC wake-up timer
 * (#75 is about that very race). Between two slots there are 20 s on the DEV Sync profile, hours on PROD:
 * a CM4 that stops in a long gap is seen at the next slot, not before. A look at most every 5 s. */
#define LOOK_EVERY_MS   5000u

static CoreWatch_t s_other;
static uint32_t    s_last_look_ms;
static bool        s_have_last;

void CoreWatchOther_Resync(void)
{
    s_have_last = false;   /* the time of day just jumped: the elapsed time of the next look is unknown */
}

void CoreWatchOther_Look(uint32_t now_ms)
{
    if (!s_have_last) {
        s_last_look_ms = now_ms;
        s_have_last    = true;
        CoreWatch_Init(&s_other);
        return;
    }
    int32_t elapsed = DayMs_Diff(now_ms, s_last_look_ms);
    if (elapsed < 0) {
        s_last_look_ms = now_ms;   /* the clock went back: no information */
        return;
    }
    if ((uint32_t)elapsed < LOOK_EVERY_MS) {
        return;
    }
    s_last_look_ms = now_ms;

    CoreVerdict_t v = CoreWatch_Step(&s_other, &g_core_state_cm4, (uint32_t)elapsed, WATCH_MARGIN_MS);
    CoreSeen_Make(&g_core_seen_cm4, &g_core_state_cm4, &v, CoreCrash_IsValid(&g_core_crash_cm4));
}

#endif
