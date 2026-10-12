/*!
 * \file      shared_mem.c
 *
 * \brief     Inter-core shared SRAM2 global instance definitions for the
 *            ArcLoRaM STM32WL55 dual-core firmware.
 *
 * \code
 *              ____  ______  _         ___   _   _  __  __
 *             / ___||  ____|| |       |_ _| | | | ||  \/  |
 *            | |    | |__   | |        | |  | | | || |\/| |
 *            | |___ |  __|  | |___    _| |_ | |_| || |  | |
 *             \____||______| \_____| |_____| \___/ |_|  |_|
 *            (C)2025-2026 Celium
 *
 * \endcode
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 * \details   All instances use the "SHARED_APP" linker section, which both
 *            the CM0+ and CM4 linker scripts map to physical address
 *            0x20009000 (the first 4 K of SRAM2 above the MbMux tables).
 *            This file is compiled for BOTH cores; each core's linker places
 *            the symbols at the same physical addresses, giving both cores a
 *            consistent view of shared state with no address-translation layer.
 *
 *            SharedMem_Init() is the sole writer of zeros to this region and
 *            must be called once by CM0+ at startup before any other access.
 *            CM4 must NOT call SharedMem_Init() — it would clobber CM4-owned
 *            fields that CM4 writes after releasing CM0+.
 */
#include "shared_mem.h"
#include <string.h>

/* =========================================================================
 * Compile-time size assertions
 * ========================================================================= */

_Static_assert(sizeof(AlarmBRequest_t)       == 12u, "AlarmBRequest_t size mismatch");
_Static_assert(sizeof(ComplianceStatus_t)    == 16u, "ComplianceStatus_t size mismatch");
_Static_assert(sizeof(EmergencyAlertSlot_t)  == 12u, "EmergencyAlertSlot_t size mismatch");
_Static_assert(sizeof(PhaseFrequency_t)       == 20u, "PhaseFrequency_t size mismatch");
_Static_assert(sizeof(CoreCrash_t)            == 108u, "CoreCrash_t size mismatch");

/* =========================================================================
 * Global instances in SRAM2 application shared region
 *
 * Section "SHARED_APP" is mapped to 0x20009000 by both linker scripts.
 * Declaration order determines the within-section layout (natural alignment):
 *   g_alarm_b_request       +0x000  12 B
 *   g_compliance_status     +0x00C  16 B
 *   g_cm4_heartbeat         +0x01C   4 B
 *   g_emergency_alert_slot  +0x020  12 B
 *   g_freq_resolver_state   +0x02C 400 B
 *   g_freq_override_tables  +0x1BC 640 B
 *   g_core_crash_cm0, g_core_crash_cm4, g_reset_flags: appended after them,
 *   NOT zeroed by SharedMem_Init (#101); new shared variables go at the end so
 *   that no offset above moves.
 * ========================================================================= */

#ifndef HOST_TEST
#define SHARED_APP  __attribute__((section("SHARED_APP")))
#else
#define SHARED_APP
#endif

AlarmBRequest_t          g_alarm_b_request      SHARED_APP;
ComplianceStatus_t       g_compliance_status    SHARED_APP;
uint32_t                 g_cm4_heartbeat        SHARED_APP;
EmergencyAlertSlot_t     g_emergency_alert_slot SHARED_APP;
FrequencyResolverState_t g_freq_resolver_state  SHARED_APP;
uint32_t g_freq_override_tables[FREQ_MAX_OVERRIDE_TABLES][FREQ_MAX_CELLS_PER_PHASE] SHARED_APP;
CoreCrash_t              g_core_crash_cm0       SHARED_APP;
CoreCrash_t              g_core_crash_cm4       SHARED_APP;
uint32_t                 g_reset_flags          SHARED_APP;
#if defined(BENCH_FAULT_CM4) || defined(BENCH_FAULT_CM0)
uint32_t                 g_bench_fault_gate[2]  SHARED_APP;
#endif

/* =========================================================================
 * SharedMem_Init
 * ========================================================================= */

void SharedMem_Init(void)
{
    memset(&g_alarm_b_request,      0, sizeof(g_alarm_b_request));
    memset(&g_compliance_status,    0, sizeof(g_compliance_status));
    g_cm4_heartbeat = 0u;
    memset(&g_emergency_alert_slot, 0, sizeof(g_emergency_alert_slot));
    memset(&g_freq_resolver_state,  0, sizeof(g_freq_resolver_state));
    memset(&g_freq_override_tables, 0, sizeof(g_freq_override_tables));
    /* g_core_crash_cm0/cm4, g_reset_flags and g_bench_fault_gate are NOT zeroed: they carry
     * what the previous run (or the CM4 a moment ago) left, for this boot to report. */
}
