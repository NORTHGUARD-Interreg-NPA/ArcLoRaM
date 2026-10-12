/*!
 * \file      core_fault.c
 *
 * \brief     Target glue of the crash record and the reset cause (#101); see core_fault.h.
 *
 * \author    Simon R.C. Langlais ( Celium )
 */
#include "core_fault.h"
#include "arclog.h"
#include "shared_mem.h"
#include "stm32wlxx.h"
#if defined(BENCH_FAULT_CM4) || defined(BENCH_FAULT_CM0)
#  include "stm32wlxx_hal.h"   /* HAL_GetTick */
#  include "node_id.h"         /* NodeId_ReadUid, NodeId_FromUid */
#endif

#if defined(CORE_CM0PLUS)
#  define SELF_CORE    CORE_CRASH_CORE_CM0
#  define SELF_RECORD  g_core_crash_cm0
#else
#  define SELF_CORE    CORE_CRASH_CORE_CM4
#  define SELF_RECORD  g_core_crash_cm4
#endif

/* The flag bits of reset_cause.h are those of RCC_CSR. */
_Static_assert(RESET_FLAG_RF    == RCC_CSR_RFRSTF,    "RFRSTF");
_Static_assert(RESET_FLAG_RMVF  == RCC_CSR_RMVF,      "RMVF");
_Static_assert(RESET_FLAG_RFILA == RCC_CSR_RFILARSTF, "RFILARSTF");
_Static_assert(RESET_FLAG_OBL   == RCC_CSR_OBLRSTF,   "OBLRSTF");
_Static_assert(RESET_FLAG_PIN   == RCC_CSR_PINRSTF,   "PINRSTF");
_Static_assert(RESET_FLAG_BOR   == RCC_CSR_BORRSTF,   "BORRSTF");
_Static_assert(RESET_FLAG_SFT   == RCC_CSR_SFTRSTF,   "SFTRSTF");
_Static_assert(RESET_FLAG_IWDG  == RCC_CSR_IWDGRSTF,  "IWDGRSTF");
_Static_assert(RESET_FLAG_WWDG  == RCC_CSR_WWDGRSTF,  "WWDGRSTF");
_Static_assert(RESET_FLAG_LPWR  == RCC_CSR_LPWRRSTF,  "LPWRRSTF");

/* The SRAM of the STM32WL55: 64 KB at 0x20000000, shared by both cores. A frame is
 * read only when it and the stack words after it lie inside it. */
#define SRAM_START  0x20000000u
#define SRAM_END    0x20010000u

static bool readable(uint32_t addr, uint32_t bytes)
{
    return (addr & 3u) == 0u && addr >= SRAM_START && addr <= SRAM_END - bytes;
}

#if defined(CORE_FAULT_BENCH)
static void bench_arm(void);
#endif

void CoreFault_OnException(const uint32_t *frame, uint32_t exc_return, uint32_t kind)
{
    CoreCrash_t *rec = &SELF_RECORD;

    if (CoreCrash_IsValid(rec)) {
        /* The first fault wins: this one is usually a consequence of it. */
        CoreCrash_NoteAgain(rec);
    } else {
        CoreCrashInput_t in;
        memset(&in, 0, sizeof in);
        in.core       = SELF_CORE;
        in.kind       = (uint8_t)kind;
        in.exc_return = exc_return;
        in.frame_addr = (uint32_t)(uintptr_t)frame;
        if (readable(in.frame_addr, 8u * 4u)) {
            in.frame = frame;
            if (readable(in.frame_addr + 8u * 4u, CORE_CRASH_STK_WORDS * 4u)) {
                in.stk = frame + 8;
            }
        }
#if defined(CORE_CM4)
        CoreCrashStatus_t st;
        st.cfsr  = SCB->CFSR;
        st.hfsr  = SCB->HFSR;
        st.mmfar = SCB->MMFAR;
        st.bfar  = SCB->BFAR;
        st.icsr  = SCB->ICSR;
        st.shcsr = SCB->SHCSR;
        in.status = &st;
#endif
        CoreCrash_Make(rec, &in);
    }

    /* Stop, as the handlers always did: restarting the core is #106's decision. The next reset
     * (a human's, or bench's) reports the record at BOOT. */
    for (;;) {
    }
}

void CoreFault_Init(void)
{
#if defined(CORE_CM4)
    SCB->SHCSR |= SCB_SHCSR_BUSFAULTENA_Msk | SCB_SHCSR_USGFAULTENA_Msk | SCB_SHCSR_MEMFAULTENA_Msk;
#endif
}

void CoreFault_CaptureResetCause(void)
{
#if defined(CORE_CM4)
    g_reset_flags = RCC->CSR & RESET_FLAGS_MASK;
    RCC->CSR |= RCC_CSR_RMVF;
#endif
}

uint32_t CoreFault_ResetFlags(void)
{
    return g_reset_flags;
}

const char *CoreFault_ResetName(void)
{
    return ResetCause_Name(ResetCause_Decode(g_reset_flags));
}

void CoreFault_ReportAtBoot(void)
{
    CoreCrash_t *rec = &SELF_RECORD;

#if defined(CORE_FAULT_BENCH)
    bench_arm();
#endif

    if (!CoreCrash_IsValid(rec)) {
        return;
    }
    const char *core = CoreCrash_CoreName(rec->core);

    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "CORE_CRASH",
           "core=%s kind=%s why=%s pc=%08x lr=%08x xpsr=%08x sp=%08x exc=%08x again=%u",
           core, CoreCrash_KindName(rec->kind), CoreCrash_Why(rec),
           (unsigned)rec->pc, (unsigned)rec->lr, (unsigned)rec->xpsr,
           (unsigned)rec->sp, (unsigned)rec->exc_return, (unsigned)CoreCrash_Again(rec));
    if (rec->flags & CORE_CRASH_F_STATUS) {
        ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "CORE_CRASH_SCB",
               "core=%s cfsr=%08x hfsr=%08x bfar=%08x mmfar=%08x icsr=%08x shcsr=%08x",
               core, (unsigned)rec->cfsr, (unsigned)rec->hfsr, (unsigned)rec->bfar,
               (unsigned)rec->mmfar, (unsigned)rec->icsr, (unsigned)rec->shcsr);
    }
    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "CORE_CRASH_REGS",
           "core=%s r0=%08x r1=%08x r2=%08x r3=%08x r12=%08x",
           core, (unsigned)rec->r0, (unsigned)rec->r1, (unsigned)rec->r2,
           (unsigned)rec->r3, (unsigned)rec->r12);
    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "CORE_CRASH_STK",
           "core=%s w0=%08x w1=%08x w2=%08x w3=%08x w4=%08x w5=%08x w6=%08x w7=%08x",
           core, (unsigned)rec->stk[0], (unsigned)rec->stk[1], (unsigned)rec->stk[2],
           (unsigned)rec->stk[3], (unsigned)rec->stk[4], (unsigned)rec->stk[5],
           (unsigned)rec->stk[6], (unsigned)rec->stk[7]);

    CoreCrash_Clear(rec);
}

/* =========================================================================
 * Forced faults (Build Overrides, bench only)
 * ========================================================================= */

#if defined(CORE_FAULT_BENCH)

#if defined(CORE_CM0PLUS)
#  define FORCE_KIND   BENCH_FAULT_CM0
#  define GATE_INDEX   0
#else
#  define FORCE_KIND   BENCH_FAULT_CM4
#  define GATE_INDEX   1
#endif

#ifndef BENCH_FAULT_AFTER_S
#  define BENCH_FAULT_AFTER_S  60u
#endif

#define GATE_FIRED  0xFA17ED01u

/* The time since boot comes from HAL_GetTick, which keeps counting in Stop2 on both cores: the CM0+'s is
 * the monotonic clock of the RTC, the CM4's is a free-running 16-bit LPTIM counter in ms that wraps at
 * 64000 (timer_if.c). The elapsed time is accumulated at each pass, so a pass must come at least once
 * a wrap; a longer gap on the CM4 makes the fault come later. Counting main-loop passes did not work:
 * the CM4 makes hundreds of them in the first second, before its first line leaves the UART. */
#if defined(CORE_CM4)
#  define TICK_WRAP_MS  64000u
#endif

static bool     s_armed;
static bool     s_running;      /* the clock started: at the first poll, once the timers are initialised */
static uint32_t s_last_ms;
static uint32_t s_elapsed_ms;

/* Called at BOOT: only the board named by BENCH_FAULT_NODE is faulted (a Build Override reaches every
 * flashed board of the run, the C3 as well), and a forced fault that already fired in the run before
 * this reset is not repeated. */
static void bench_arm(void)
{
#if defined(BENCH_FAULT_NODE)
    uint32_t uid[NODE_UID_WORDS];
    NodeId_ReadUid(uid);
    if (NodeId_FromUid(uid) != (uint8_t)BENCH_FAULT_NODE) {
        s_armed = false;
        return;
    }
#endif
    if (g_bench_fault_gate[GATE_INDEX] == GATE_FIRED) {
        g_bench_fault_gate[GATE_INDEX] = 0u;
        s_armed = false;
    } else {
        s_armed = true;
    }
}

static void __attribute__((noinline)) force(uint32_t kind)
{
    switch (kind) {
    case 2:
        __builtin_trap();
        break;
    case 3: {
        volatile uint32_t *nowhere = (volatile uint32_t *)0x60000000u;
        (void)*nowhere;
        break;
    }
    case 4:
#if defined(CORE_CM4)
        SCB->SHCSR &= ~SCB_SHCSR_BUSFAULTENA_Msk;
#endif
        /* fall through */
    case 1:
    default:
        ((void (*)(void))0x60000001u)();
        break;
    }
}

void CoreFault_BenchPoll(void)
{
    if (!s_armed) {
        return;
    }
    uint32_t now = HAL_GetTick();
    if (!s_running) {
        /* Not at BOOT, and not while it reads 0: the CM4's HAL_GetTick does until its timer is initialised,
         * and the first real reading must not count as time elapsed. */
        if (now != 0u) {
            s_running = true;
            s_last_ms = now;
        }
        return;
    }
    uint32_t step = now - s_last_ms;
#if defined(TICK_WRAP_MS)
    if (now < s_last_ms) {
        step = now + TICK_WRAP_MS - s_last_ms;
    }
#endif
    s_last_ms = now;
    s_elapsed_ms += step;
    if (s_elapsed_ms < BENCH_FAULT_AFTER_S * 1000u) {
        return;
    }
    s_armed = false;
    g_bench_fault_gate[GATE_INDEX] = GATE_FIRED;
    force(FORCE_KIND);
}

#endif /* CORE_FAULT_BENCH */
