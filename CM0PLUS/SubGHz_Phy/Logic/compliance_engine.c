/*!
 * \file      compliance_engine.c
 *
 * \brief     Compliance Engine implementation.
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
 */
#include "compliance_engine.h"
#include <assert.h>
#include <stddef.h>

/* =========================================================================
 * Region profile — ETSI EN 300 220, EU 868 MHz ISM
 * ========================================================================= */

typedef struct {
    uint32_t freq_min_hz;
    uint32_t freq_max_hz;
    uint8_t  duty_cycle_x10;    /* 1 % = 10, 0.1 % = 1, 10 % = 100 */
    int8_t   max_tx_power_dbm;
} Band_t;

#ifndef ETSI_WINDOW_MS
/*!
 * ETSI EN 300 220 duty-cycle observation window.
 *
 * \remark Standard value is 1 hour.  Credit accumulates at
 *         (duty_cycle_x10 / 1000) ms per real ms, capped at
 *         (ETSI_WINDOW_MS × duty_cycle_x10 / 1000).
 */
#define ETSI_WINDOW_MS  3600000u
#endif

/* The first row that holds the frequency wins: a narrower row goes before the broad one. */
static const Band_t k_bands[] = {
    {
        /* ETSI EN 300 220-2 sub-band P: 869.4 - 869.65 MHz, 10 % duty cycle, 500 mW ERP (27 dBm).
         * The only row where the board's high-power amplifier (22 dBm) may run. */
        .freq_min_hz       = 869400000u,
        .freq_max_hz       = 869650000u,
        .duty_cycle_x10    = 100u,  /* 10 % */
        .max_tx_power_dbm  = 27,
    },
    {
        /* ETSI EN 300 220-2 h1.4/h1.5/h1.8 — EU 868 MHz ISM
         * 863–870 MHz, 1 % duty cycle, 25 mW (14 dBm) */
        .freq_min_hz       = 863000000u,
        .freq_max_hz       = 870000000u,
        .duty_cycle_x10    = 10u,   /* 1.0 % */
        .max_tx_power_dbm  = 14,
    },
};

#define BAND_COUNT  ((int)(sizeof(k_bands) / sizeof(k_bands[0])))

/* =========================================================================
 * Module state
 * ========================================================================= */

static ComplianceStatus_t  *s_status;
static uint32_t           (*s_get_tick_ms)(void);
static uint32_t             s_credit_ms[BAND_COUNT];
static uint32_t             s_last_tick_ms[BAND_COUNT];
static uint32_t             s_last_expected_ms[BAND_COUNT];

/* =========================================================================
 * Internal helpers
 * ========================================================================= */

static uint32_t band_max_credit(int idx)
{
    /* ETSI_WINDOW_MS / 1000 first avoids overflow for duty_cycle_x10 ≤ 255 */
    return (ETSI_WINDOW_MS / 1000u) * k_bands[idx].duty_cycle_x10;
}

static int find_band(uint32_t freq_hz)
{
    for (int i = 0; i < BAND_COUNT; i++) {
        if (freq_hz >= k_bands[i].freq_min_hz && freq_hz <= k_bands[i].freq_max_hz) {
            return i;
        }
    }
    return -1;
}

static void refresh_credit(int idx)
{
    uint32_t now     = s_get_tick_ms();
    uint32_t elapsed = now - s_last_tick_ms[idx]; /* wraps safely: unsigned subtraction */
    uint32_t max     = band_max_credit(idx);

    /* Clamp to one observation window to avoid overflow in the multiply. */
    if (elapsed >= ETSI_WINDOW_MS) {
        s_credit_ms[idx] = max;
    } else {
        s_credit_ms[idx] += elapsed * k_bands[idx].duty_cycle_x10 / 1000u;
        if (s_credit_ms[idx] > max) {
            s_credit_ms[idx] = max;
        }
    }
    s_last_tick_ms[idx] = now;
}

/* =========================================================================
 * Public API
 * ========================================================================= */

void ComplianceEngine_Init(ComplianceStatus_t *status,
                           uint32_t (*get_tick_ms)(void))
{
    s_status      = status;
    s_get_tick_ms = get_tick_ms;

    uint32_t now = (get_tick_ms != NULL) ? get_tick_ms() : 0u;
    for (int i = 0; i < BAND_COUNT; i++) {
        s_credit_ms[i]        = band_max_credit(i);
        s_last_tick_ms[i]     = now;
        s_last_expected_ms[i] = 0u;
    }
}

ComplianceResult_t ComplianceEngine_RequestChannel(uint32_t freq_hz,
                                                    uint32_t expected_toa_ms,
                                                    int8_t   tx_power_dbm)
{
    ComplianceResult_t result;
    uint32_t           wait = 0u;

    int idx = find_band(freq_hz);

    if (idx < 0) {
#if !defined(NDEBUG) && !defined(HOST_TEST)
        assert(0); /* Fatal: freq outside all declared bands — cert risk */
#endif
        if (s_status != NULL) {
            s_status->last_freq_hz = freq_hz;
            s_status->last_result  = COMPLIANCE_BAND_UNKNOWN;
            s_status->wait_ms      = 0u;
            s_status->band_unknown_count++;
        }
        return COMPLIANCE_BAND_UNKNOWN;
    }

    refresh_credit(idx);

    if (tx_power_dbm > k_bands[idx].max_tx_power_dbm) {
        result = COMPLIANCE_POWER_TOO_HIGH;

    } else if (s_credit_ms[idx] >= expected_toa_ms) {
        s_credit_ms[idx]       -= expected_toa_ms;
        s_last_expected_ms[idx] = expected_toa_ms;
        result                  = COMPLIANCE_GRANTED;

    } else {
        uint32_t deficit = expected_toa_ms - s_credit_ms[idx];
        /* ceil(deficit × 1000 / duty_cycle_x10) */
        wait   = (deficit * 1000u + k_bands[idx].duty_cycle_x10 - 1u)
                 / k_bands[idx].duty_cycle_x10;
        result = COMPLIANCE_RESTRICTED;
    }

    if (s_status != NULL) {
        s_status->last_freq_hz = freq_hz;
        s_status->last_result  = result;
        s_status->wait_ms      = wait;
    }

    return result;
}

void ComplianceEngine_ReportTxDone(uint32_t freq_hz, uint32_t actual_toa_ms)
{
    int idx = find_band(freq_hz);
    if (idx < 0) {
        return;
    }

    if (s_last_expected_ms[idx] > actual_toa_ms) {
        uint32_t refund = s_last_expected_ms[idx] - actual_toa_ms;
        uint32_t max    = band_max_credit(idx);
        s_credit_ms[idx] += refund;
        if (s_credit_ms[idx] > max) {
            s_credit_ms[idx] = max;
        }
    }
    s_last_expected_ms[idx] = 0u;
}
