/*!
 * \file      core_state_glue.h
 *
 * \brief     The target side of the liveness record (#101): the previous run's state
 *            at BOOT, and the CM4's look at the CM0+; see core_state.h.
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef CORE_STATE_GLUE_H
#define CORE_STATE_GLUE_H

/*! First thing after a reset, before the first trace call or checkpoint of this run: keep the state
 *  the previous run left and start a new one. */
void CoreStateBoot_Snapshot(void);

/*! After the BOOT line: print the state the previous run left (st=boot), if it left a valid one. */
void CoreStateBoot_Report(void);

/*! CM4: look at the CM0+ every 30 s, from a timer of its own, and print CORE_STATE when it is overdue. */
void CoreWatch_Start(void);

/*! CM4: from the idle hook (UTIL_SEQ_PostIdle), after a wake from Stop2: make the look the timer asked for. */
void CoreWatch_PostIdle(void);

#endif /* CORE_STATE_GLUE_H */
