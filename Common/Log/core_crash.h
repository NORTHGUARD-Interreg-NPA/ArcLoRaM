/*!
 * \file      core_crash.h
 *
 * \brief     Crash record of a core: what a fault handler leaves in RAM that
 *            a reset does not clear, so that the next BOOT (or a debugger)
 *            can say where and why the core stopped.
 *
 * \details   Issue #101. The fault handlers of both cores were endless loops:
 *            a core that faulted left nothing, and a node whose log stopped
 *            could not be told from one whose core had. The handler now
 *            builds one \ref CoreCrash_t from the frame the hardware stacked
 *            (r0-r3, r12, lr, pc, xpsr), the stack pointer, the EXC_RETURN
 *            value and, on the CM4, the fault status registers; the CM0+
 *            (a Cortex-M0+, ARMv6-M) has none of those registers, so its
 *            record holds the frame only.
 *
 *            This module is pure: the handler hands it words, the host tests
 *            check what comes out. Where the record lives (the shared
 *            region, which a reset does not clear and both cores see at the
 *            same address) and how the handler gets the frame are the glue
 *            of core_fault.c.
 *
 *            Rules:
 *              - the first fault wins: a second one while the record is
 *                still valid only counts itself (\ref CoreCrash_NoteAgain),
 *                since the second is usually a fault of the handler or a
 *                consequence of the first;
 *              - a record is valid only when its magic and its check word
 *                agree: SRAM after a power-up holds anything;
 *              - the owner reports the record at its next BOOT and clears it.
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef CORE_CRASH_H
#define CORE_CRASH_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>

#define CORE_CRASH_MAGIC      0xC4A5E501u
#define CORE_CRASH_VERSION    1u
/*! Words of the stack kept above the stacked frame: return addresses to read a backtrace from. */
#define CORE_CRASH_STK_WORDS  8u

/* Which core wrote the record: the digit of the ArcLog header (0 = CM0+, 4 = CM4). */
#define CORE_CRASH_CORE_CM0   0u
#define CORE_CRASH_CORE_CM4   4u

/* Which handler took the exception. Plain numbers in macros, not an enum and
 * not unsigned: the fault entry pastes them into assembly (`movs r2, #1`). */
#define CORE_CRASH_KIND_HARD   1
#define CORE_CRASH_KIND_BUS    2
#define CORE_CRASH_KIND_MEM    3
#define CORE_CRASH_KIND_USAGE  4
#define CORE_CRASH_KIND_NMI    5

/* Flags: the low nibble says what the record holds, the high nibble counts the faults after the first. */
#define CORE_CRASH_F_FRAME     0x01u   /*!< the stacked frame was readable */
#define CORE_CRASH_F_STATUS    0x02u   /*!< the fault status words are valid (CM4) */
#define CORE_CRASH_F_STK       0x04u   /*!< the stack words above the frame were readable */
#define CORE_CRASH_AGAIN_SHIFT 4u

/*! The CM4 fault status words (SCB), read by the handler; absent on the CM0+. */
typedef struct {
    uint32_t cfsr;    /*!< 0xE000ED28: MemManage, BusFault and UsageFault status */
    uint32_t hfsr;    /*!< 0xE000ED2C: HardFault status (VECTTBL, FORCED, DEBUGEVT) */
    uint32_t mmfar;   /*!< 0xE000ED34 */
    uint32_t bfar;    /*!< 0xE000ED38 */
    uint32_t icsr;    /*!< 0xE000ED04: VECTACTIVE, RETTOBASE (nested exceptions) */
    uint32_t shcsr;   /*!< 0xE000ED24: which handlers were active (SYSTICKACT...) */
} CoreCrashStatus_t;

/*! The record, 108 bytes, word-aligned, at a fixed place of the shared region. */
typedef struct {
    uint32_t magic;
    uint8_t  version;
    uint8_t  core;
    uint8_t  kind;
    uint8_t  flags;
    uint32_t r0, r1, r2, r3, r12, lr, pc, xpsr;   /*!< the frame the hardware stacked */
    uint32_t sp;                                  /*!< the stack pointer before the exception: the frame + 8 words */
    uint32_t exc_return;                          /*!< LR on entry to the handler (0xFFFFFFF1/9/D) */
    uint32_t cfsr, hfsr, mmfar, bfar, icsr, shcsr;
    uint32_t stk[CORE_CRASH_STK_WORDS];           /*!< the words just above the frame */
    uint32_t check;                               /*!< over every word above */
} CoreCrash_t;

/*! What the handler knows. A NULL \c frame or \c stk means that memory was not readable. */
typedef struct {
    uint8_t                  core;
    uint8_t                  kind;
    uint32_t                 frame_addr;   /*!< where the hardware stacked the frame */
    const uint32_t          *frame;        /*!< r0 r1 r2 r3 r12 lr pc xpsr */
    const uint32_t          *stk;          /*!< CORE_CRASH_STK_WORDS words above the frame */
    uint32_t                 exc_return;
    const CoreCrashStatus_t *status;       /*!< NULL on the CM0+ */
} CoreCrashInput_t;

_Static_assert(sizeof(CoreCrash_t) == 108u, "CoreCrash_t is at a fixed place: a change is a new version");

static inline uint32_t CoreCrash_Check(const CoreCrash_t *rec)
{
    const uint32_t *w = (const uint32_t *)rec;
    uint32_t acc = CORE_CRASH_MAGIC;
    for (size_t i = 0; i < (sizeof(CoreCrash_t) / 4u) - 1u; i++) {
        acc = ((acc << 5) | (acc >> 27)) ^ w[i];
    }
    return acc;
}

static inline void CoreCrash_Make(CoreCrash_t *rec, const CoreCrashInput_t *in)
{
    memset(rec, 0, sizeof *rec);
    rec->magic      = CORE_CRASH_MAGIC;
    rec->version    = (uint8_t)CORE_CRASH_VERSION;
    rec->core       = in->core;
    rec->kind       = in->kind;
    rec->exc_return = in->exc_return;

    if (in->frame != NULL) {
        rec->flags |= CORE_CRASH_F_FRAME;
        rec->r0   = in->frame[0];
        rec->r1   = in->frame[1];
        rec->r2   = in->frame[2];
        rec->r3   = in->frame[3];
        rec->r12  = in->frame[4];
        rec->lr   = in->frame[5];
        rec->pc   = in->frame[6];
        rec->xpsr = in->frame[7];
        rec->sp   = in->frame_addr + 32u;
    }
    if (in->stk != NULL) {
        rec->flags |= CORE_CRASH_F_STK;
        memcpy(rec->stk, in->stk, sizeof rec->stk);
    }
    if (in->status != NULL) {
        rec->flags |= CORE_CRASH_F_STATUS;
        rec->cfsr  = in->status->cfsr;
        rec->hfsr  = in->status->hfsr;
        rec->mmfar = in->status->mmfar;
        rec->bfar  = in->status->bfar;
        rec->icsr  = in->status->icsr;
        rec->shcsr = in->status->shcsr;
    }
    rec->check = CoreCrash_Check(rec);
}

static inline bool CoreCrash_IsValid(const CoreCrash_t *rec)
{
    return rec->magic == CORE_CRASH_MAGIC
        && rec->version == CORE_CRASH_VERSION
        && rec->check == CoreCrash_Check(rec);
}

static inline void CoreCrash_Clear(CoreCrash_t *rec)
{
    memset(rec, 0, sizeof *rec);
}

/*! A fault while the record is still valid: keep the first, count this one (to 15). */
static inline void CoreCrash_NoteAgain(CoreCrash_t *rec)
{
    if ((rec->flags >> CORE_CRASH_AGAIN_SHIFT) < 15u) {
        rec->flags = (uint8_t)(rec->flags + (1u << CORE_CRASH_AGAIN_SHIFT));
    }
    rec->check = CoreCrash_Check(rec);
}

/*! Faults after the first one that the record kept. */
static inline uint8_t CoreCrash_Again(const CoreCrash_t *rec)
{
    return (uint8_t)(rec->flags >> CORE_CRASH_AGAIN_SHIFT);
}

static inline const char *CoreCrash_CoreName(uint8_t core)
{
    switch (core) {
    case CORE_CRASH_CORE_CM4: return "cm4";
    case CORE_CRASH_CORE_CM0: return "cm0";
    default:                  return "?";
    }
}

static inline const char *CoreCrash_KindName(uint8_t kind)
{
    switch (kind) {
    case CORE_CRASH_KIND_HARD:  return "hard";
    case CORE_CRASH_KIND_BUS:   return "bus";
    case CORE_CRASH_KIND_MEM:   return "mem";
    case CORE_CRASH_KIND_USAGE: return "usage";
    case CORE_CRASH_KIND_NMI:   return "nmi";
    default:                    return "?";
    }
}

/*!
 * \brief   The cause in one word, from the CM4 status words.
 *
 * \details A stacking error is named first: with a bad stack pointer the other
 *          bits and the frame are unreliable. Then the CFSR bits in the order
 *          bus, memory management, usage; then the HardFault status when no
 *          configurable fault says anything (\c forced: an escalated fault
 *          whose own handler was not enabled). "na" on a core with no status
 *          registers; "none" when the registers say nothing. The raw words
 *          are in the record and in the trace.
 */
static inline const char *CoreCrash_Why(const CoreCrash_t *rec)
{
    if (!(rec->flags & CORE_CRASH_F_STATUS)) {
        return "na";
    }
    static const struct { uint8_t bit; const char *name; } cfsr_bits[] = {
        { 12, "stkerr" }, { 11, "unstkerr" }, { 4, "mstkerr" }, { 3, "munstkerr" },
        { 8, "ibuserr" }, { 9, "preciserr" }, { 10, "impreciserr" },
        { 0, "iaccviol" }, { 1, "daccviol" },
        { 16, "undefinstr" }, { 17, "invstate" }, { 18, "invpc" }, { 19, "nocp" },
        { 24, "unaligned" }, { 25, "divbyzero" },
    };
    for (size_t i = 0; i < sizeof cfsr_bits / sizeof cfsr_bits[0]; i++) {
        if (rec->cfsr & (1u << cfsr_bits[i].bit)) {
            return cfsr_bits[i].name;
        }
    }
    if (rec->hfsr & (1u << 1))  return "vecttbl";
    if (rec->hfsr & (1u << 30)) return "forced";
    if (rec->hfsr & (1u << 31)) return "debugevt";
    return "none";
}

#endif /* CORE_CRASH_H */
