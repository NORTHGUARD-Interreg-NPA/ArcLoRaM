/*!
 * \file      core_cp.h
 *
 * \brief     The macros that mark a checkpoint, a heartbeat and a promised wake
 *            in the code of either core (#101); see core_state.h.
 *
 * \details   Each is a few stores to the state of the calling core in the shared
 *            region. In host tests they are empty, so the logic modules that
 *            carry checkpoints (tdma_machine.c) are unchanged for them.
 *            With a Build Override that forces a loop at a checkpoint
 *            (BENCH_LOOP_CM0, BENCH_LOOP_CM4) a checkpoint also asks
 *            core_fault.c whether this is the one; no other build contains it.
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef CORE_CP_H
#define CORE_CP_H

#if defined(HOST_TEST)

#  define CORE_CP(id)          ((void)0)
#  define CORE_BEAT()          ((void)0)
#  define CORE_KEPT()          ((void)0)
#  define CORE_PROMISE(ms)     ((void)0)
#  define CORE_CP_LOG_IN()     ((void)0)
#  define CORE_CP_LOG_OUT()    ((void)0)

#else /* target */

#  include <stdint.h>
#  include "core_state.h"
#  include "core_fault.h"
#  include "shared_mem.h"

#  if defined(CORE_CM0PLUS)
#    define CORE_STATE_SELF      g_core_state_cm0
#    define CORE_CP_LOG_IN()     CORE_CP(CP_CM0_LOG_IN)
#    define CORE_CP_LOG_OUT()    CORE_CP(CP_CM0_LOG_OUT)
#    define CORE_BEAT()          CoreState_Beat(&CORE_STATE_SELF)
#  else
#    define CORE_STATE_SELF      g_core_state_cm4
#    define CORE_CP_LOG_IN()     CORE_CP(CP_CM4_LOG_IN)
#    define CORE_CP_LOG_OUT()    CORE_CP(CP_CM4_LOG_OUT)
/* g_cm4_heartbeat is the counter CONTEXT.md and shared_mem.h document: the twin of the state's hb. */
#    define CORE_BEAT()          do { CoreState_Beat(&CORE_STATE_SELF); g_cm4_heartbeat++; } while (0)
#  endif

#  define CORE_CP(id)                                                         \
    do {                                                                      \
        CoreState_Checkpoint(&CORE_STATE_SELF, (uint8_t)(id));                \
        CoreFault_BenchAt((uint8_t)(id));                                     \
    } while (0)

/*! The core served a wake it had promised (its slot task runs): the survivor judges the promise by this. */
#  define CORE_KEPT()          CoreState_Kept(&CORE_STATE_SELF)

/*! The core will wake within \c ms of now (an alarm is programmed), or promises nothing (CORE_STATE_NO_PROMISE). */
#  define CORE_PROMISE(ms)     CoreState_Promise(&CORE_STATE_SELF, (ms))

#endif

#endif /* CORE_CP_H */
