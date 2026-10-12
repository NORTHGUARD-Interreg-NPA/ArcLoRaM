/*!
 * \file      arclog.h
 *
 * \brief     ArcLog - structured, machine-parseable trace lines shared by
 *            CM0+ and CM4.
 *
 * \details   Every ArcLog line has a fixed header followed by an event name
 *            and space-separated key=value fields:
 *
 * \code
 *   260925T123456.7890 0Y M #41 SYNC_RX ph=0 ce=1 ep=45120000 st=45123004 exp=45123000 err=4 tier=1
 *   |                  |||  |  |   |      |
 *   |                  |||  |  |   |      +-- key=value fields (no spaces in values)
 *   |                  |||  |  |   +--------- EVENT (UPPER_SNAKE, see CONTEXT.md - ArcLog)
 *   |                  |||  |  +------------- per-core sequence number (hex, wraps at 0xff)
 *   |                  |||  +---------------- verbosity: A=VLEVEL_ALWAYS L M H
 *   |                  ||+------------------- module (ARCLOG_MOD_*)
 *   |                  |+-------------------- core: 0 = CM0+, 4 = CM4
 *   +------------------------------------------ RTC timestamp YYMMDDTHHMMSS.ssss
 *                                               (written by TimestampNow)
 * \endcode
 *
 *            A gap in the sequence number means lines were lost (trace FIFO
 *            full on either core). The sequence number is consumed only when
 *            the line passes the verbosity filter, so filtering never creates
 *            false gaps.
 *
 *            Format strings are handed to tiny_vsnprintf_like on target, built
 *            with TINY_PRINTF: only %c %s %d %i %u %x %X plus width and
 *            zero-pad. NO length modifier (%lu / %ld print literally and
 *            shift every following argument), no floats. int and long are
 *            both 32-bit on Cortex-M, so print uint32_t / int32_t with %u / %d
 *            and an (unsigned) / (int) cast. The host capture backend aborts
 *            on a length modifier so unit tests catch it.
 *
 *            In host unit tests (HOST_TEST) lines are captured into a ring
 *            buffer instead (Tests/stubs/arclog_capture.c), so tests can
 *            assert on the events a state machine emits.
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef ARCLOG_H
#define ARCLOG_H

#include <stdint.h>
#include <stdbool.h>

/* =========================================================================
 * Modules
 * ========================================================================= */

#define ARCLOG_MOD_TDMA  'T'  /*!< TDMA Machine: slot wakes, cursor.            */
#define ARCLOG_MOD_MAC   'M'  /*!< MAC State Machine: MacState transitions.     */
#define ARCLOG_MOD_SYNC  'Y'  /*!< Sync / clock: ClockState, RTC writes, error. */
#define ARCLOG_MOD_RADIO 'R'  /*!< Radio events and timestamps.                 */
#define ARCLOG_MOD_MBMUX 'X'  /*!< Inter-core mailbox.                          */
#define ARCLOG_MOD_SYS   'S'  /*!< System: boot, init.                          */
#define ARCLOG_MOD_POWER 'P'  /*!< Low-power modes.                             */

/* =========================================================================
 * Core identifier
 * ========================================================================= */

#if defined(CORE_CM0PLUS)
#  define ARCLOG_CORE  '0'
#else
#  define ARCLOG_CORE  '4'
#endif

/* =========================================================================
 * Backend
 * ========================================================================= */

#if defined(HOST_TEST)

#ifndef VLEVEL_ALWAYS
#  define VLEVEL_ALWAYS  0
#  define VLEVEL_L       1
#  define VLEVEL_M       2
#  define VLEVEL_H       3
#endif

/*! Host test backend: formats the line into the capture ring. */
int  ArcLog_CaptureEmit(uint32_t vlevel, const char *fmt, ...);
#define ARCLOG_BACKEND(vl, ...)   ArcLog_CaptureEmit((uint32_t)(vl), __VA_ARGS__)
#define ArcLog_Enabled(vl)        (true)

#else /* target */

#include "stm32_adv_trace.h"

#define ARCLOG_BACKEND(vl, ...) \
    UTIL_ADV_TRACE_COND_FSend((uint32_t)(vl), T_REG_OFF, TS_ON, __VA_ARGS__)
#define ArcLog_Enabled(vl) \
    ((uint32_t)(vl) <= (uint32_t)UTIL_ADV_TRACE_GetVerboseLevel())

#endif

#include "core_cp.h"   /* CORE_CP_LOG_IN / OUT: where a core stalled in its trace path (#101); empty in host tests */

/* =========================================================================
 * Emit macro
 * ========================================================================= */

/*!
 * \brief   Emit one ArcLog line.
 *
 * \param   mod    ARCLOG_MOD_* character.
 * \param   vl     VLEVEL_ALWAYS / VLEVEL_L / VLEVEL_M / VLEVEL_H.
 * \param   event  String literal, UPPER_SNAKE event name.
 * \param   fmt    String literal of key=value fields (may be "").
 *
 * \code
 *   ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_M, "CLK", "from=%s to=%s why=%s",
 *          ArcLog_ClockName(old), ArcLog_ClockName(new), "lock");
 * \endcode
 */
#define ARCLOG(mod, vl, event, fmt, ...)                                      \
    do {                                                                      \
        if (ArcLog_Enabled(vl)) {                                             \
            CORE_CP_LOG_IN();                                                 \
            (void)ARCLOG_BACKEND((vl), "%c%c %c #%02x " event " " fmt "\r\n", \
                                 ARCLOG_CORE, (mod), ArcLog_LevelChar(vl),    \
                                 (unsigned)ArcLog_NextSeq(), ##__VA_ARGS__);  \
            CORE_CP_LOG_OUT();                                                \
        }                                                                     \
    } while (0)

/* =========================================================================
 * Helpers
 * ========================================================================= */

/*! Return and post-increment the per-core line sequence number. */
uint8_t     ArcLog_NextSeq(void);

/*! Map a VLEVEL_* value to its ArcLog letter (A, L, M, H). */
char        ArcLog_LevelChar(uint32_t vlevel);

/*! Length of the timestamp written by \ref ArcLog_FormatTimestamp,
 *  trailing space included: "YYMMDDTHHMMSS.ssss ". */
#define ARCLOG_TIMESTAMP_LEN  19u

/*!
 * \brief   Format the ArcLog line timestamp from RTC calendar fields.
 *
 * \details Writes exactly \ref ARCLOG_TIMESTAMP_LEN characters (no NUL):
 *          YYMMDDTHHMMSS.ssss followed by a space. The fraction is in units of
 *          100 us derived from the sub-second register: (prediv_s - ssr) /
 *          (prediv_s + 1). An SSR above prediv_s (possible right after a
 *          SHIFTR ADD1S) is clamped to fraction 0. Called by TimestampNow on
 *          both cores.
 *
 * \param   [out] buf  At least ARCLOG_TIMESTAMP_LEN bytes.
 * \retval  Number of characters written (ARCLOG_TIMESTAMP_LEN).
 */
uint16_t ArcLog_FormatTimestamp(uint8_t *buf,
                                uint8_t year, uint8_t month, uint8_t day,
                                uint8_t hours, uint8_t minutes, uint8_t seconds,
                                uint32_t ssr, uint32_t prediv_s);

/*! Short names used as ArcLog field values. Out-of-range values give "?". */
const char *ArcLog_ClockName(uint32_t clock_state);     /* COLD ACQ WARM            */
const char *ArcLog_MacStateName(uint32_t mac_state);    /* SCAN SYNC ACTIVE PAIRED  */
const char *ArcLog_DecisionName(uint32_t decision);     /* TX RX SKIP               */
const char *ArcLog_PhaseTypeName(uint32_t phase_type);  /* BCN UL DL CLU SYNC       */
const char *ArcLog_SlotPosName(uint32_t slot_pos);      /* HDR CELL FTR             */

#endif /* ARCLOG_H */
