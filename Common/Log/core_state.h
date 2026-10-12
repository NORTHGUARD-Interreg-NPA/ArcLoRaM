/*!
 * \file      core_state.h
 *
 * \brief     Liveness of a core (#101): a heartbeat, the checkpoint it last
 *            passed, and the wake it promised, kept in shared RAM so that
 *            the other core can judge it.
 *
 * \details   A node whose log stops cannot tell a core that stopped from one
 *            that sleeps, nor which one, nor where. Each core now keeps a
 *            \ref CoreState_t:
 *              - \c hb, +1 at every wake (a pass of its main loop): the proof
 *                of life. A core spinning inside a task never returns to its
 *                main loop, so its heartbeat stops even if it passes through
 *                checkpoints. Any wake counts, the LED timer's of a NUCLEO
 *                included, so it says the core lives, not that it works;
 *              - \c kept, +1 each time the core serves a wake it had promised
 *                (the slot task runs): what the promise is judged by. A lost
 *                Alarm A leaves the heartbeat moving on a NUCLEO and \c kept
 *                still;
 *              - \c cp, the checkpoint it last passed (one byte, named per
 *                core), and \c step, the last one that is not plumbing (a trace
 *                call, a Stop2 entry or exit): where the code's own flow last
 *                was, which a trace call or a sleep after it would hide;
 *                \c isr, the last checkpoint passed in an interrupt (SysTick, the
 *                radio), kept apart because a thread stuck in its main loop is
 *                interrupted a thousand times a second and those would erase
 *                where it is stuck; \c seq counts them all;
 *              - \c wake_in_ms, the wake it promised when it last programmed
 *                its alarm, as a duration (the two cores share no time base
 *                that survives an RTC set); \ref CORE_STATE_NO_PROMISE when
 *                it sleeps until an interrupt (Scanning Rx has no alarm chain).
 *
 *            The survivor (the CM4 for the CM0+) calls \ref CoreWatch_Step at
 *            a fixed period: no promised wake served for longer than the promise
 *            and its margin is overdue, by how much, at which checkpoint, and
 *            whether the core is still beating. The same record is printed at
 *            the next BOOT as the state of the previous run, for a core that
 *            cannot be seen alive; it cannot tell a lost wake from a sleep by
 *            itself, only the silence the survivor measured can.
 *
 *            Pure: the platform stores the words, the host tests check the
 *            judgement. The target glue is core_fault.c.
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef CORE_STATE_H
#define CORE_STATE_H

#include <stdint.h>
#include <stdbool.h>

#define CORE_STATE_MAGIC       0xC0DE5A01u
/*! \c wake_in_ms when the core promised no wake: it sleeps until an interrupt. */
#define CORE_STATE_NO_PROMISE  0xFFFFFFFFu

/* The digit of the ArcLog header, as in core_crash.h. */
#define CORE_STATE_CORE_CM0    0u
#define CORE_STATE_CORE_CM4    4u

/* Checkpoints. One byte, and the two cores share no id. Plain numbers in macros. */
#define CP_CM0_IRQ_RADIO   1   /*!< entry of the radio interrupt */
#define CP_CM0_SLOT_IN     2   /*!< entry of the slot task */
#define CP_CM0_MAC_DONE    3   /*!< the MAC has decided the slot */
#define CP_CM0_RX_SET      4   /*!< about to set the radio to Rx */
#define CP_CM0_RX_ARMED    5   /*!< the radio listens */
#define CP_CM0_ALARM_SET   6   /*!< Alarm A is programmed (or cancelled) and the wake promised */
#define CP_CM0_LOG_IN      8   /*!< entering a trace call */
#define CP_CM0_LOG_OUT     9   /*!< the trace call returned */
#define CP_CM0_STOP_IN     10  /*!< about to enter Stop2 */
#define CP_CM0_STOP_OUT    11  /*!< woke from Stop2 */

#define CP_CM4_LOOP        32  /*!< a pass of the main loop */
#define CP_CM4_LOG_IN      33  /*!< entering a trace call (the formatter) */
#define CP_CM4_LOG_OUT     34  /*!< the trace call returned */
#define CP_CM4_SYSTICK     35  /*!< entry of the SysTick handler */
#define CP_CM4_STOP_IN     36  /*!< about to enter Stop2 */
#define CP_CM4_STOP_OUT    37  /*!< woke from Stop2 */

/*! 24 bytes, word-aligned, in the shared region. */
typedef struct {
    uint32_t magic;
    uint32_t hb;           /*!< wakes of the core */
    uint32_t kept;         /*!< promised wakes served */
    uint32_t seq;          /*!< checkpoints passed */
    uint32_t wake_in_ms;   /*!< the wake promised at the last alarm, a duration; CORE_STATE_NO_PROMISE */
    uint8_t  cp;           /*!< last checkpoint */
    uint8_t  step;         /*!< last checkpoint that is not plumbing: a trace call or a Stop2 entry or exit */
    uint8_t  isr;          /*!< last checkpoint passed in an interrupt, apart from the thread's cp and step */
    uint8_t  pad;
} CoreState_t;

_Static_assert(sizeof(CoreState_t) == 24u, "CoreState_t is at a fixed place in the shared region");

static inline void CoreState_Init(CoreState_t *s)
{
    s->magic      = CORE_STATE_MAGIC;
    s->hb         = 0u;
    s->kept       = 0u;
    s->seq        = 0u;
    s->wake_in_ms = CORE_STATE_NO_PROMISE;
    s->cp         = 0u;
    s->step       = 0u;
    s->isr        = 0u;
    s->pad        = 0u;
}

static inline bool CoreState_IsValid(const CoreState_t *s)
{
    return s->magic == CORE_STATE_MAGIC;
}

/* The hot path of every checkpoint is a few instructions on a 4 MHz core built without optimisation
 * (the bench builds Debug): inlined for certain, and no 64-bit shift (a library call on the CM0+). */
#define CORE_STATE_INLINE  static inline __attribute__((always_inline))

/*! Checkpoints that say nothing of the code's own flow: they come around a trace call or a sleep.
 *  Bits of the two masks are the ids 8 to 11 (CM0+) and 33, 34, 36, 37 (CM4). */
CORE_STATE_INLINE bool CoreState_IsPlumbing(uint8_t id)
{
    if (id < 32u) {
        return ((0x00000F00u >> id) & 1u) != 0u;
    }
    if (id < 64u) {
        return ((0x00000036u >> (id - 32u)) & 1u) != 0u;
    }
    return false;
}

CORE_STATE_INLINE void CoreState_Checkpoint(CoreState_t *s, uint8_t id)
{
    s->cp = id;
    if (!CoreState_IsPlumbing(id)) {
        s->step = id;
    }
    s->seq++;
}

/*! A checkpoint passed in an interrupt: it leaves the thread's cp and step alone. */
CORE_STATE_INLINE void CoreState_CheckpointIsr(CoreState_t *s, uint8_t id)
{
    s->isr = id;
    s->seq++;
}

CORE_STATE_INLINE void CoreState_Beat(CoreState_t *s)
{
    s->hb++;
}

CORE_STATE_INLINE void CoreState_Kept(CoreState_t *s)
{
    s->kept++;
}

CORE_STATE_INLINE void CoreState_Promise(CoreState_t *s, uint32_t wake_in_ms)
{
    s->wake_in_ms = wake_in_ms;
}

static inline const char *CoreState_CpName(uint8_t core, uint8_t id)
{
    if (id == 0u) {
        return "none";
    }
    if (core == CORE_STATE_CORE_CM0) {
        switch (id) {
        case CP_CM0_IRQ_RADIO: return "irq";
        case CP_CM0_SLOT_IN:   return "slot_in";
        case CP_CM0_MAC_DONE:  return "mac_done";
        case CP_CM0_RX_SET:    return "rx_set";
        case CP_CM0_RX_ARMED:  return "rx_armed";
        case CP_CM0_ALARM_SET: return "alarm_set";
        case CP_CM0_LOG_IN:    return "log_in";
        case CP_CM0_LOG_OUT:   return "log_out";
        case CP_CM0_STOP_IN:   return "stop_in";
        case CP_CM0_STOP_OUT:  return "stop_out";
        default:               return "?";
        }
    }
    if (core == CORE_STATE_CORE_CM4) {
        switch (id) {
        case CP_CM4_LOOP:      return "loop";
        case CP_CM4_LOG_IN:    return "log_in";
        case CP_CM4_LOG_OUT:   return "log_out";
        case CP_CM4_SYSTICK:   return "systick";
        case CP_CM4_STOP_IN:   return "stop_in";
        case CP_CM4_STOP_OUT:  return "stop_out";
        default:               return "?";
        }
    }
    return "?";
}

/* ---- the survivor ----------------------------------------------------------- */

typedef enum {
    CORE_WATCH_UNKNOWN = 0,   /*!< the state is not valid: nothing to judge */
    CORE_WATCH_ALIVE,         /*!< a promised wake was served since the last look (or it is the first look) */
    CORE_WATCH_ASLEEP,        /*!< none was, but nothing is overdue: a sleep inside its promise, or no promise */
    CORE_WATCH_OVERDUE,       /*!< no promised wake served past the promise and the margin */
} CoreWatchStatus_t;

/*! What the survivor remembers between two looks. */
typedef struct {
    uint32_t last_hb;      /*!< heartbeat at the last look */
    uint32_t last_kept;    /*!< promised wakes served at the last look */
    uint32_t silent_ms;    /*!< time with no heartbeat, as the survivor measures it */
    uint32_t unkept_ms;    /*!< time with no promised wake served */
    bool     seen;         /*!< a baseline exists: the first look only takes one */
} CoreWatch_t;

typedef struct {
    CoreWatchStatus_t status;
    bool              alive;       /*!< the heartbeat moved since the last look: the core lives, even if it serves nothing */
    uint32_t          silent_ms;   /*!< the survivor's measure of the time without a promised wake served */
    uint32_t          due_ms;      /*!< the wake the core promised, or CORE_STATE_NO_PROMISE */
    uint32_t          over_ms;     /*!< silent minus due (the margin not deducted); 0 unless overdue */
} CoreVerdict_t;

static inline void CoreWatch_Init(CoreWatch_t *w)
{
    w->last_hb   = 0u;
    w->last_kept = 0u;
    w->silent_ms = 0u;
    w->unkept_ms = 0u;
    w->seen      = false;
}

static inline uint32_t CoreWatch_Sat(uint32_t a, uint32_t b)
{
    uint32_t sum = a + b;
    return (sum < a) ? 0xFFFFFFFFu : sum;   /* saturate, never wrap */
}

/*!
 * \brief   One look at the other core, \c elapsed_ms after the previous one.
 *
 * \details The promise is judged by \c kept: a core that serves its promised wakes is fine
 *          whatever its heartbeat says, and one that does not is overdue once the time since the
 *          last one passes the promise and the margin. The heartbeat then says whether it is a dead
 *          core or a live one that lost a wake. The silences are measured by the survivor from
 *          the looks that found the counters unchanged, so they under-read by up to one period (the
 *          core may have stopped just after a look); the margin covers it. The first look only takes
 *          a baseline.
 */
static inline CoreVerdict_t CoreWatch_Step(CoreWatch_t *w, const CoreState_t *s,
                                           uint32_t elapsed_ms, uint32_t margin_ms)
{
    CoreVerdict_t v = { CORE_WATCH_UNKNOWN, false, 0u, CORE_STATE_NO_PROMISE, 0u };

    if (!CoreState_IsValid(s)) {
        return v;
    }
    v.due_ms = s->wake_in_ms;

    bool first      = !w->seen;
    bool hb_moved   = first || s->hb != w->last_hb;
    bool kept_moved = first || s->kept != w->last_kept;

    w->seen      = true;
    w->last_hb   = s->hb;
    w->last_kept = s->kept;
    w->silent_ms = hb_moved   ? 0u : CoreWatch_Sat(w->silent_ms, elapsed_ms);
    w->unkept_ms = kept_moved ? 0u : CoreWatch_Sat(w->unkept_ms, elapsed_ms);

    v.alive     = hb_moved;
    v.silent_ms = w->unkept_ms;

    if (kept_moved) {
        v.status = CORE_WATCH_ALIVE;
        return v;
    }
    if (s->wake_in_ms == CORE_STATE_NO_PROMISE) {
        v.status = CORE_WATCH_ASLEEP;
        return v;
    }
    uint64_t limit = (uint64_t)s->wake_in_ms + margin_ms;
    if ((uint64_t)w->unkept_ms > limit) {
        v.status  = CORE_WATCH_OVERDUE;
        v.over_ms = w->unkept_ms - s->wake_in_ms;
    } else {
        v.status = CORE_WATCH_ASLEEP;
    }
    return v;
}

/* ---- what the survivor saw ------------------------------------------------------ */

#define CORE_SEEN_MAGIC  0xC0DE5E01u

/*!
 * \brief   The verdict a live core made of the other, kept in the shared region so that it survives a
 *          reset and is printed at the next BOOT: the only report of a stopped CM4, which cannot print,
 *          and the proof that the CM0+ was alive when it was seen dead. 36 bytes.
 *
 * \details Written at every look; an overdue verdict fills it, any other clears it.
 */
typedef struct {
    uint32_t magic;
    uint8_t  status;       /*!< CoreWatchStatus_t of the last look: only OVERDUE is kept */
    uint8_t  alive;        /*!< the heartbeat still moved at that look */
    uint8_t  cp;           /*!< the checkpoint it was seen at */
    uint8_t  step;
    uint32_t hb;
    uint32_t kept;
    uint32_t seq;
    uint32_t silent_ms;    /*!< time with no promised wake served, as the survivor measured it */
    uint32_t over_ms;
    uint32_t due_ms;       /*!< the wake it had promised */
    uint8_t  crash;        /*!< a crash record of that core was valid at that look */
    uint8_t  isr;          /*!< the last checkpoint passed in an interrupt */
    uint8_t  pad[2];
} CoreSeen_t;

_Static_assert(sizeof(CoreSeen_t) == 36u, "CoreSeen_t is at a fixed place in the shared region");

static inline void CoreSeen_Clear(CoreSeen_t *r)
{
    uint8_t *p = (uint8_t *)r;
    for (unsigned i = 0; i < sizeof *r; i++) {
        p[i] = 0u;
    }
}

static inline void CoreSeen_Make(CoreSeen_t *r, const CoreState_t *s, const CoreVerdict_t *v, bool crash)
{
    if (v->status != CORE_WATCH_OVERDUE) {
        CoreSeen_Clear(r);
        return;
    }
    r->magic     = CORE_SEEN_MAGIC;
    r->status    = (uint8_t)v->status;
    r->alive     = v->alive ? 1u : 0u;
    r->cp        = s->cp;
    r->step      = s->step;
    r->hb        = s->hb;
    r->kept      = s->kept;
    r->seq       = s->seq;
    r->silent_ms = v->silent_ms;
    r->over_ms   = v->over_ms;
    r->due_ms    = v->due_ms;
    r->crash     = crash ? 1u : 0u;
    r->isr       = s->isr;
    r->pad[0] = r->pad[1] = 0u;
}

static inline bool CoreSeen_IsOverdue(const CoreSeen_t *r)
{
    return r->magic == CORE_SEEN_MAGIC && r->status == (uint8_t)CORE_WATCH_OVERDUE;
}

static inline const char *CoreWatch_StatusName(CoreWatchStatus_t st)
{
    switch (st) {
    case CORE_WATCH_UNKNOWN: return "unknown";
    case CORE_WATCH_ALIVE:   return "alive";
    case CORE_WATCH_ASLEEP:  return "asleep";
    case CORE_WATCH_OVERDUE: return "overdue";
    default:                 return "?";
    }
}

#endif /* CORE_STATE_H */
