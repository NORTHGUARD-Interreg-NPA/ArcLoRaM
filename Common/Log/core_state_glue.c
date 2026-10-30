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
#endif

#if defined(CORE_CM0PLUS)
#  define SELF_NAME  "cm0"
#  define SELF_CORE  CORE_STATE_CORE_CM0
#else
#  define SELF_NAME  "cm4"
#  define SELF_CORE  CORE_STATE_CORE_CM4
#endif

static CoreState_t s_prev;
static bool        s_prev_valid;

void CoreStateBoot_Snapshot(void)
{
    s_prev       = CORE_STATE_SELF;
    s_prev_valid = CoreState_IsValid(&s_prev);
    CoreState_Init(&CORE_STATE_SELF);
}

/* The wake promised, as ArcLog prints it: -1 when nothing was promised. */
static int due_of(const CoreState_t *s)
{
    return (s->wake_in_ms == CORE_STATE_NO_PROMISE) ? -1 : (int)s->wake_in_ms;
}

void CoreStateBoot_Report(void)
{
    if (!s_prev_valid) {
        return;
    }
    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "CORE_STATE",
           "core=%s st=boot cp=%s step=%s hb=%u kept=%u seq=%u due=%d",
           SELF_NAME, CoreState_CpName(SELF_CORE, s_prev.cp), CoreState_CpName(SELF_CORE, s_prev.step),
           (unsigned)s_prev.hb, (unsigned)s_prev.kept, (unsigned)s_prev.seq, due_of(&s_prev));
}

#if defined(CORE_CM4)

/* The CM4 looks at the CM0+ every 30 s. A CM4 that is not the C2's survivor of anything still has the
 * timer: it costs a wake every 30 s in the field (the NUCLEO's LED timer wakes it every 4 s anyway).
 * The margin covers the one period by which a look under-reads the silence. */
#define WATCH_PERIOD_MS   30000u
#define WATCH_MARGIN_MS   5000u
/* While overdue, print the first look and then one in 20 (every 10 minutes): the line is 120 bytes. */
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
    CoreVerdict_t v = CoreWatch_Step(&s_watch, &g_core_state_cm0, WATCH_PERIOD_MS, WATCH_MARGIN_MS);

    if (v.status != CORE_WATCH_OVERDUE) {
        s_overdue_looks = 0u;
        return;
    }
    if ((s_overdue_looks++ % WATCH_REPEAT_EVERY) != 0u) {
        return;
    }
    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "CORE_STATE",
           "core=cm0 st=overdue cp=%s step=%s hb=%u kept=%u seq=%u due=%d silent=%u over=%u alive=%u",
           CoreState_CpName(CORE_STATE_CORE_CM0, g_core_state_cm0.cp),
           CoreState_CpName(CORE_STATE_CORE_CM0, g_core_state_cm0.step),
           (unsigned)g_core_state_cm0.hb, (unsigned)g_core_state_cm0.kept, (unsigned)g_core_state_cm0.seq,
           due_of(&g_core_state_cm0), (unsigned)v.silent_ms, (unsigned)v.over_ms, (unsigned)v.alive);
}

void CoreWatch_Start(void)
{
    CoreWatch_Init(&s_watch);
    UTIL_TIMER_Create(&s_watch_timer, WATCH_PERIOD_MS, UTIL_TIMER_PERIODIC, watch_cb, NULL);
    UTIL_TIMER_Start(&s_watch_timer);
}

#else

void CoreWatch_Start(void)
{
}

void CoreWatch_PostIdle(void)
{
}

#endif
