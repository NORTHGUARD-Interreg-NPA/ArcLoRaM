/*!
 * \file      subghz_phy_task.c
 *
 * \brief     SubGHz PHY task registration — initialises all CM0+ protocol
 *            machines and registers the TDMA slot task with UTIL_SEQ.
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
#include "subghz_phy_task.h"
#include "utilities_def.h"
#include "stm32_seq.h"
#include "arclog.h"          /* ARCLOG, VLEVEL_* */
#include "app_version.h"      /* APP_VERSION_* */
#include "stm32_timer.h"      /* UTIL_TIMER_GetCurrentTime */
#include "radio.h"            /* Radio */
#include "radio_def.h"        /* MODEM_LORA */
#include "radio_driver.h"     /* SUBGRF_SetDioIrqParams, SUBGRF_GetIrqStatus, IRQ_* */
#include "rtc.h"              /* hrtc */
#include "timer_if.h"         /* TIMER_IF_RtcWriteBegin / End */
#include "main.h"             /* RTC_PREDIV_S */
#include "tdma_machine.h"
#include "mac_state_machine.h"
#include "freq_resolver.h"
#include "compliance_engine.h"
#include "shared_mem.h"
#include "protocol_types.h"   /* NODE_CLASS_C* constants */
#include "tdma_table.h"       /* TdmaTable_PhaseCount */
#include "guard_time_resolver.h" /* MAX_GUARD_TIME_MS */
#include "rtc_set_plan.h"         /* RtcSetPlan_*, RtcTicks_* */
#include "sync_stamp.h"           /* SyncStamp_FromRxDone: the stamp in RTC ticks (#82) */
#include "lora_toa.h"             /* LoraToa_Us */
#include "drift_estimator.h"      /* DriftEstimator_* */
#include "rtc_calr.h"            /* RtcCalr_* */
#include "calr_policy.h"         /* CalrPolicy_Decide */
#include "probe.h"               /* PROBE_* (BENCH_PROBE only) */
#include "node_id.h"          /* NodeId_ReadUid, NodeId_FromUid */

/* =========================================================================
 * Radio IRQ timestamps
 *
 * The radio IRQ handler (stm32wlxx_it.c, USER CODE SUBGHZ_Radio_IRQn 0) calls
 * SubGhzPhyTask_OnRadioIrq() first thing, before HAL dispatch, so every stamp
 * is the RTC at IRQ entry. The Sync timestamp given to the MAC (SyncStamp) is
 * the packet's start on air: RxDone minus the airtime of the received length.
 * Both are deterministic, so it tracks the sender's TX start to within an RTC
 * tick. The PREAMBLE_DETECTED IRQ is not: at SF12 it lands one symbol
 * (32.8 ms) early or late from packet to packet (bench, 2026-09-26). It is
 * still stamped, with HEADER_VALID, as a diagnostic in RX_DONE.
 * ========================================================================= */

#ifndef RX_DONE_LATENCY_MS
/* Delay between the end of the packet on air and the RxDone IRQ stamp,
 * subtracted from the SyncStamp. The radio raises RxDone right after the
 * last symbol and the IRQ is stamped at entry, so it is well under a
 * millisecond; 0 until a common time reference (e.g. GPIO on a logic
 * analyser) measures it. */
#define RX_DONE_LATENCY_MS  0u
#endif

/* Time the radio may take to raise PREAMBLE_DETECTED after a packet starts,
 * added to a synced window's latest packet start to form the hardware Rx
 * timeout. Detection happens within the programmed preamble, so its length
 * (8 symbols x 32.768 ms at SF12/BW125 = 262 ms) is a safe upper bound. A
 * packet detected in that margin but starting too late to fit is aborted by
 * the cap. Recompute if the modem parameters change. */
#define RX_PREAMBLE_DETECT_MARGIN_MS  262u

/* Time from Radio.Send to the first preamble symbol on air: SPI commands
 * (packet params, 10-byte buffer, SetTx), TCXO start-up (RF_WAKEUP_TIME),
 * PLL lock and PA ramp-up, the radio in standby after RadioSetChannel.
 * Measured as TX_DONE start - SYNC_TX send: 4 ms on C3 and on a relaying C2,
 * every cell (bench 2026-09-27, 66 of 68 packets; the other two +6 / +7). */
#ifndef TX_RAMP_MS
#define TX_RAMP_MS  4u
#endif

/* Largest hardware Rx timeout: 24-bit count of 15.625 us steps. */
#define RX_HW_TIMEOUT_MAX_MS  (0xFFFFFFu >> 6)

static RadioEvents_t     s_radio_events;
static UTIL_TIMER_Object_t s_rx_cap_timer;   /* synced-window hard end */
static volatile uint32_t s_irq_stamp_ms;   /* RTC at entry of the latest radio IRQ */
static volatile uint32_t s_irq_stamp_ticks; /* the same, in RTC ticks (1/4096 s) */
static volatile uint32_t s_pre_stamp_ms;   /* PREAMBLE_DETECTED of the current Rx */
static volatile uint32_t s_hdr_stamp_ms;   /* HEADER_VALID of the current Rx      */
static volatile bool     s_pre_valid;
static volatile bool     s_hdr_valid;
static uint8_t           s_last_tx_len;

static uint32_t plat_radio_toa(uint8_t len);
static uint32_t plat_radio_toa_us(uint8_t len);
static uint32_t plat_rx_back_ticks(uint8_t len, uint32_t *toa_ms);

void SubGhzPhyTask_OnRadioIrq(void)
{
    /* One RTC read in ticks; the ms of the log and the diagnostics are its
     * floor (the same value GetTimerTicks gives). */
    uint32_t ticks = TIMER_IF_GetDayTicks(false);
    uint32_t stamp = RtcTicks_ToMs(ticks);
    uint16_t irq   = SUBGRF_GetIrqStatus();

    /* Keep the latest detection: a false preamble detection on noise earlier
     * in the window is superseded by the real packet's. */
    s_irq_stamp_ms    = stamp;
    s_irq_stamp_ticks = ticks;
    if ((irq & IRQ_PREAMBLE_DETECTED) != 0u) {
        s_pre_stamp_ms = stamp;
        s_pre_valid    = true;
        s_hdr_valid    = false;
    }
    if ((irq & IRQ_HEADER_VALID) != 0u) {
        s_hdr_stamp_ms = stamp;
        s_hdr_valid    = true;
    }
}

/* =========================================================================
 * Radio event callbacks - bridge radio ISR events to the MAC layer
 * ========================================================================= */

static void on_tx_done(void)
{
    uint32_t end = s_irq_stamp_ms;
    uint32_t toa = plat_radio_toa(s_last_tx_len);
    /* start = end - toa: when the radio actually began transmitting. */
    ARCLOG(ARCLOG_MOD_RADIO, VLEVEL_M, "TX_DONE", "sz=%u toa=%u end=%u start=%u",
           (unsigned)s_last_tx_len, (unsigned)toa,
           (unsigned)end, (unsigned)DayMs_Add(end, -(int32_t)toa));
}

static void on_tx_timeout(void)
{
    ARCLOG(ARCLOG_MOD_RADIO, VLEVEL_L, "TX_TIMEOUT", "");
}

static void on_rx_done(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr)
{
    PROBE_START(probe);
    uint32_t rxd_ticks = s_irq_stamp_ticks;
    uint32_t rxd   = s_irq_stamp_ms;
    uint32_t toa   = 0u;     /* ms, for the log, from the same cache */
    /* SyncStamp: packet start, in RTC ticks and in the RTC day domain (a
     * packet received just after midnight started before it): RxDone minus
     * the time on air in microseconds (991 232 us for a Sync packet, not the
     * driver's whole 991 ms) minus the Rx latency (issue #82). `stamp` is its
     * floor in ms, for the log. */
    uint32_t stamp_ticks = SyncStamp_FromRxDoneTicks(rxd_ticks, plat_rx_back_ticks((uint8_t)size, &toa));
    uint32_t stamp = RtcTicks_ToMs(stamp_ticks);
    PROBE_MARK(probe, "stamp");
    PROBE_LOG(probe, "rx_stamp");

    UTIL_TIMER_Stop(&s_rx_cap_timer);
    ARCLOG(ARCLOG_MOD_RADIO, VLEVEL_M, "RX_DONE",
           "sz=%u rssi=%d snr=%d pre=%u hdr=%u rxd=%u toa=%u st=%u",
           (unsigned)size, (int)rssi, (int)snr,
           (unsigned)(s_pre_valid ? s_pre_stamp_ms : 0u),
           (unsigned)(s_hdr_valid ? s_hdr_stamp_ms : 0u),
           (unsigned)rxd, (unsigned)toa, (unsigned)stamp);

    if (size == (uint16_t)sizeof(SyncPayload_t)) {
        MAC_OnSyncPacketReceivedTicks((const SyncPayload_t *)payload, stamp_ticks);
    } else if (size == (uint16_t)sizeof(BeaconPayload_t)) {
        MAC_OnBeaconReceived((const BeaconPayload_t *)payload);
    }
    TdmaMachine_OnRxEnd();
}

static void on_rx_timeout(void)
{
    UTIL_TIMER_Stop(&s_rx_cap_timer);
    ARCLOG(ARCLOG_MOD_RADIO, VLEVEL_H, "RX_TIMEOUT", "pre=%u", (unsigned)s_pre_valid);
    TdmaMachine_OnRxEnd();
}

static void on_rx_error(void)
{
    UTIL_TIMER_Stop(&s_rx_cap_timer);
    ARCLOG(ARCLOG_MOD_RADIO, VLEVEL_M, "RX_ERROR", "pre=%u hdr=%u",
           (unsigned)s_pre_valid, (unsigned)s_hdr_valid);
    TdmaMachine_OnRxEnd();
}

/* =========================================================================
 * Platform callbacks for TdmaMachine_Init
 * ========================================================================= */

/* GetTimerTicks (timer_if.c) returns:
 *   (h*3600 + m*60 + s)*1000 + (PREDIV_S - SSR)*1000/(PREDIV_S+1)
 * UTIL_TIMER_GetCurrentTime is a thin wrapper around GetTimerTicks (identity
 * Tick2ms conversion). Both return ms-since-midnight. */
static uint32_t plat_get_rtc_ms(void)
{
    return (uint32_t)UTIL_TIMER_GetCurrentTime();
}

/* ProgramAlarmA — converts an absolute ms-since-midnight value back to
 * RTC register fields and arms Alarm A.
 *
 * abs_ms is in the same epoch as plat_get_rtc_ms() (ms since midnight,
 * wraps at 86 400 000 ms).  The inverse of GetTimerTicks:
 *
 *   target_s   = (abs_ms / 1000) % 86400
 *   target_ssr = PREDIV_S - target_ms * (PREDIV_S+1) / 1000
 *
 * Alarm A fires when the RTC calendar matches hours/minutes/seconds/subseconds.
 * Date/weekday is masked out — only the time fields are compared.
 *
 * The UTIL_TIMER infrastructure drives the WakeUp Timer (WUT); Alarm A is
 * fully available for the TDMA Machine.
 */
static void plat_program_alarm_a(uint32_t abs_ms)
{
    uint32_t target_s   = (abs_ms / 1000u) % 86400u;
    uint32_t target_ms  = abs_ms % 1000u;
    uint32_t target_ssr = RTC_PREDIV_S
                        - (target_ms * (RTC_PREDIV_S + 1u)) / 1000u;

    RTC_AlarmTypeDef alarm;
    alarm.AlarmTime.Hours          = (uint8_t)(target_s / 3600u);
    alarm.AlarmTime.Minutes        = (uint8_t)((target_s % 3600u) / 60u);
    alarm.AlarmTime.Seconds        = (uint8_t)(target_s % 60u);
    alarm.AlarmTime.SubSeconds     = target_ssr;
    alarm.AlarmTime.DayLightSaving = RTC_DAYLIGHTSAVING_NONE;
    alarm.AlarmTime.StoreOperation = RTC_STOREOPERATION_RESET;
    alarm.AlarmMask                = RTC_ALARMMASK_DATEWEEKDAY;
    alarm.AlarmSubSecondMask       = RTC_ALARMSUBSECONDMASK_NONE;
    alarm.AlarmDateWeekDaySel      = RTC_ALARMDATEWEEKDAYSEL_DATE;
    alarm.AlarmDateWeekDay         = 1u;  /* masked out by RTC_ALARMMASK_DATEWEEKDAY */
    alarm.Alarm                    = RTC_ALARM_A;

    hrtc.IsEnabled.RtcFeatures = UINT32_MAX;
    HAL_RTC_SetAlarm_IT(&hrtc, &alarm, RTC_FORMAT_BIN);
}

static void     plat_radio_set_channel(uint32_t hz)            { Radio.SetChannel(hz);              }

static void plat_radio_send(const uint8_t *b, uint8_t l)
{
    s_last_tx_len = l;
    Radio.Send((uint8_t *)b, l);
}

/* Start a single-mode Rx with the radio's own window timer (steps of
 * 15.625 us; 0 = no timeout). Radio.Rx() is bypassed: it can only bound the
 * window with the driver's software RxTimeoutTimer, which is stopped by
 * RxDone / errors only and so cuts a packet that is still arriving. The
 * hardware timer instead stops on preamble detection, so a packet that
 * started in time is always received in full. Preamble rather than header
 * (SetRxConfig's default): it is the earliest proof of a packet, and the
 * Sync packet will move to implicit header (issue #39 keeps the tradeoff).
 * It is re-applied on every start rather than trusted to survive the radio's
 * sleep between slots. The IRQ mask adds PREAMBLE_DETECTED and HEADER_VALID so the IRQ
 * handler can stamp them; RadioIrqProcess handles both harmlessly (its
 * preamble branch only acts in Rx duty-cycle mode, never used here). */
static void radio_rx_start(uint32_t hw_timeout_steps)
{
    const uint16_t mask = IRQ_RX_DONE | IRQ_RX_TX_TIMEOUT | IRQ_CRC_ERROR
                        | IRQ_HEADER_ERROR | IRQ_PREAMBLE_DETECTED | IRQ_HEADER_VALID;
    s_pre_valid = false;
    s_hdr_valid = false;
    SUBGRF_SetDioIrqParams(mask, mask, IRQ_RADIO_NONE, IRQ_RADIO_NONE);
    SUBGRF_SetStopRxTimerOnPreambleDetect(true);
    SUBGRF_SetSwitch(RFO_LP, RFSWITCH_RX);  /* PA selection is ignored for Rx */
    SUBGRF_SetRx(hw_timeout_steps);
}

/* Synced window: the hardware timeout is the latest packet start plus the
 * time the radio needs to detect its preamble; the cap then aborts any
 * reception still running at the slot's hard end (TdmaPlatform_t). */
static void plat_radio_set_rx(uint32_t start_window_ms, uint32_t cap_ms)
{
    uint32_t tmo_ms = start_window_ms + RX_PREAMBLE_DETECT_MARGIN_MS;
    if (tmo_ms > RX_HW_TIMEOUT_MAX_MS) {
        tmo_ms = RX_HW_TIMEOUT_MAX_MS;
    }
    radio_rx_start(tmo_ms << 6);  /* 64 steps of 15.625 us per ms */
    UTIL_TIMER_StartWithPeriod(&s_rx_cap_timer, cap_ms);
}

/* Scanning: listen until a reception ends; TdmaMachine_OnRxEnd re-arms. */
static void plat_radio_scan(void)
{
    radio_rx_start(0u);
}

/* The slot's hard end passed with a reception still running: a false
 * preamble detection, or a packet that started too late to end in time. */
static void on_rx_cap(void *context)
{
    (void)context;
    if (SUBGRF_GetOperatingMode() != MODE_RX) return;
    ARCLOG(ARCLOG_MOD_RADIO, VLEVEL_L, "RX_CAP", "pre=%u hdr=%u",
           (unsigned)(s_pre_valid ? s_pre_stamp_ms : 0u),
           (unsigned)(s_hdr_valid ? s_hdr_stamp_ms : 0u));
    TdmaMachine_OnRxEnd();  /* puts the radio to sleep */
}

/* Block until the RTC reaches abs_ms: the fire instant of a transmission.
 * The node woke at most MAX_GUARD_TIME_MS early (a Tx lead, or a guard when
 * the slot was predicted Rx), so a target further away than that is not a
 * fire instant and returns at once, as does one already passed. The RTC
 * reads in 1/4096 s ticks, so the wait ends within one tick of the target.
 * Midnight-safe. */
static void plat_wait_until_ms(uint32_t abs_ms)
{
    for (;;) {
        uint32_t left = (abs_ms % MS_PER_DAY + MS_PER_DAY - plat_get_rtc_ms()) % MS_PER_DAY;
        if (left == 0u || left > MAX_GUARD_TIME_MS) {
            return;
        }
    }
}

static void plat_cancel_alarm_a(void)
{
    hrtc.IsEnabled.RtcFeatures = UINT32_MAX;
    HAL_RTC_DeactivateAlarm(&hrtc, RTC_ALARM_A);
}
static void     plat_radio_sleep(void)                         { Radio.Sleep();                     }

/* Time on air of a len-byte packet with the modem configuration set in
 * SubGhzPhyTask_Init (SF12 / BW125 / CR4-5 / 8-symbol preamble / explicit
 * header / CRC on). The driver's formula is the reference for duty-cycle
 * accounting and for deriving TX start (TX_DONE) and the SyncStamp
 * (RxDone − ToA). Keep the parameters in step with Radio.SetTxConfig/SetRxConfig. */
/* Time on air in microseconds, from the formula and the modem configuration
 * of SubGhzPhyTask_Init (SF12, BW125, CR4/5, 8 preamble symbols, explicit
 * header, CRC): keep the two in step. */
static uint32_t plat_radio_toa_us(uint8_t len)
{
    return LoraToa_Us(len, 12u, 125000u, 1u, 8u, true, false);
}

/* Time on air plus the Rx latency, in ticks, for the length received. The
 * modem configuration is fixed, so it is computed once per length: the
 * 64-bit formula and conversions cost 550 us per packet at 4 MHz when done
 * in the interrupt (bench probe, 2026-10-04, issue #82). */
static uint32_t plat_rx_back_ticks(uint8_t len, uint32_t *toa_ms)
{
    static uint8_t  s_len;
    static uint32_t s_back;
    static uint32_t s_toa_ms;
    if (s_back == 0u || s_len != len) {
        uint32_t toa_us = plat_radio_toa_us(len);
        s_back   = SyncStamp_UsToTicks(toa_us) + SyncStamp_UsToTicks(RX_DONE_LATENCY_MS * 1000u);
        s_toa_ms = (toa_us + 500u) / 1000u;       /* 991 for a Sync packet, as the driver gave */
        s_len    = len;
    }
    *toa_ms = s_toa_ms;
    return s_back;
}

static uint32_t plat_radio_toa(uint8_t len)
{
    return Radio.TimeOnAir(MODEM_LORA,
                           0u,     /* BW 125 kHz  */
                           12u,    /* SF12        */
                           1u,     /* CR 4/5      */
                           8u,     /* preamble symbols */
                           false,  /* variable length (explicit header) */
                           len,
                           true);  /* CRC on */
}

/* =========================================================================
 * MAC State Machine hooks
 * ========================================================================= */

/* Signed change of the RTC reading (ms, rounded) a SHIFTR of shift_ticks
 * makes: + for an advance (ADD1S=1), - for a delay. Reported to the
 * monotonic counter, which excludes it (TIMER_IF_RtcWriteEnd). */
static int32_t shift_ticks_to_ms(uint32_t shift_ticks, bool advance)
{
    int32_t ms = (int32_t)((shift_ticks * 1000u + (RTC_PREDIV_S + 1u) / 2u)
                           / (RTC_PREDIV_S + 1u));
    return advance ? ms : -ms;
}

/* =========================================================================
 * Drift estimator and RTC smooth calibration (issue #34)
 *
 * C1 and C2 estimate their RTC's rate offset against the Sync sender and
 * cancel it with the RTC_CALR smooth calibration. C3 is the time reference
 * and calibrates against no one (an external reference is #35): none of this
 * is built for it.
 *
 * Every call below runs in the context of the Sync packet processing (the
 * radio ISR, like the RTC writes it follows) and shares the estimator with
 * nothing else. Times are the monotonic ms, never the day-wrapping RTC ms.
 * The estimator is told every RTC write that moves the error (a shift, a
 * calendar set, a calibration) so the fit sees the crystal alone.
 * ========================================================================= */

#if (NODE_CLASS != NODE_CLASS_C3)
#define DRIFT_ACTIVE 1
#endif

#ifndef BENCH_CALR_OFF
/* Build Override: 1 keeps the estimator and the DRIFT log but never writes
 * RTC_CALR (the control stretch of the #34 Test Record). */
#define BENCH_CALR_OFF 0
#endif

/* Build Override BENCH_CALR_BOOT_PULSES=<N>: write the net pulses N of
 * RTC_CALR at boot (logged CALR res=preset), where the register would
 * otherwise be read as it is. The register survives a re-flash and a reset, so
 * a run that must start from a known setting says so: 0 starts uncalibrated;
 * +8 detunes the RTC by +7.6 ppm, as a wrong static calibration (#23) would,
 * so a pair that is well matched by luck still has a rate to cancel.
 * Not defined in a normal build, which keeps what it reads (res=boot). */

#ifdef DRIFT_ACTIVE

static RtcCalr_t s_calr;      /* the setting held by RTC->CALR */
static int32_t   s_cal0_ppb;  /* the baseline: the setting the node started from (ADR-0019) */

/* Logged with the baseline (cal0) and the trim, applied minus baseline, as
 * they stand after the event: the runtime trim stays apart from the baseline
 * calibration in every replay. */
static void calr_log(int32_t req_ppb, RtcCalr_t c, const char *res)
{
    ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_L, "CALR", "req=%d calp=%u calm=%u res=%s cal0=%d trim=%d",
           (int)req_ppb, (unsigned)c.calp, (unsigned)c.calm, res,
           (int)s_cal0_ppb, (int)(RtcCalr_ToPpb(s_calr) - s_cal0_ppb));
}

/* Write the setting, 32 s window. A recalibration still pending (RECALPF) is
 * not waited for (the HAL would spin up to its timeout in the ISR): the next
 * sample tries again. Smooth calibration does not step the day clock, so no
 * RtcWriteBegin/End; the write-protect rule is the HAL's. */
static const char *calr_write(RtcCalr_t c)
{
    if (READ_BIT(RTC->ICSR, RTC_ICSR_RECALPF) != 0u) {
        return "busy";
    }
    hrtc.IsEnabled.RtcFeatures = UINT32_MAX;
    PROBE_START(probe);
    HAL_StatusTypeDef st = HAL_RTCEx_SetSmoothCalib(
        &hrtc, RTC_SMOOTHCALIB_PERIOD_32SEC,
        c.calp ? RTC_SMOOTHCALIB_PLUSPULSES_SET : RTC_SMOOTHCALIB_PLUSPULSES_RESET,
        c.calm);
    PROBE_MARK(probe, "setsmoothcalib");
    PROBE_LOG(probe, "calr_write");
    return (st == HAL_OK) ? "ok" : (st == HAL_BUSY) ? "busy" : "fail";
}

/* Boot: the estimator starts from what the register holds (it survives a
 * reset of the MCU; persisting the estimate itself is Phase 2). A register
 * that selects a short window is not ours: clear it. */
static void drift_init(void)
{
    PROBE_START(probe);
    DriftEstimator_Init();
    RtcCalr_t c;
    if (RtcCalr_FromReg(RTC->CALR, &c)) {
        s_calr = c;
        s_cal0_ppb = RtcCalr_ToPpb(c);
        calr_log(RtcCalr_ToPpb(c), c, "boot");
    } else {
        RtcCalr_t none = { 0u, 0u };
        const char *res = calr_write(none);
        if (res[0] == 'o') s_calr = none;
        s_cal0_ppb = RtcCalr_ToPpb(s_calr);
        calr_log(0, none, res);
    }
#ifdef BENCH_CALR_BOOT_PULSES
    {
        /* Logged also when the register already holds it (no write then). */
        RtcCalr_t   preset = RtcCalr_FromPulses(BENCH_CALR_BOOT_PULSES);
        const char *res    = "preset";
        if (RtcCalr_Pulses(s_calr) != RtcCalr_Pulses(preset)) {
            res = calr_write(preset);
            if (res[0] == 'o') { s_calr = preset; res = "preset"; }
        }
        s_cal0_ppb = RtcCalr_ToPpb(s_calr);   /* the preset is the baseline of the run */
        calr_log(RtcCalr_ToPpb(preset), preset, res);
    }
#endif
    DriftEstimator_OnCalr(TIMER_IF_GetMonotonicMs(), RtcCalr_ToPpb(s_calr));
    PROBE_MARK(probe, "init_with_log");
    PROBE_LOG(probe, "drift_init");
}

/* Sync sample hook (MAC_Hooks_t::sync_sample): feed the estimator, log its
 * state, and write a new setting when the policy says so. */
static void mac_hook_sync_sample(int32_t err_us)
{
    if (DriftEstimator_AddSampleUs(TIMER_IF_GetMonotonicMs(), err_us) != DRIFT_SAMPLE_ACCEPTED) {
        return;
    }
    DriftEstimate_t e = DriftEstimator_Get();
    ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_M, "DRIFT", "n=%u base=%u rate=%d resid=%d noise=%u ok=%u trim=%d",
           (unsigned)e.n, (unsigned)e.baseline_s, (int)e.rate_ppb, (int)e.residual_ppb,
           (unsigned)e.noise_us, (unsigned)e.valid,
           (int)(RtcCalr_ToPpb(s_calr) - s_cal0_ppb));
#if !BENCH_CALR_OFF
    CalrDecision_t d = CalrPolicy_Decide(&e, s_calr);
    if (d.write) {
        const char *res = calr_write(d.calr);
        if (res[0] == 'o') {
            s_calr = d.calr;
            DriftEstimator_OnCalr(TIMER_IF_GetMonotonicMs(), RtcCalr_ToPpb(d.calr));
        }
        calr_log(d.req_ppb, d.calr, res);
    }
#endif
}

static inline void drift_on_shift(int32_t shift_ticks)
{
    DriftEstimator_OnShift(TIMER_IF_GetMonotonicMs(), shift_ticks);
}

static inline void drift_on_rtc_set(void)
{
    DriftEstimator_OnRtcSet(TIMER_IF_GetMonotonicMs());
}

#else  /* C3 */

static inline void drift_init(void) {}
static inline void drift_on_shift(int32_t shift_ticks) { (void)shift_ticks; }
static inline void drift_on_rtc_set(void) {}

#endif /* DRIFT_ACTIVE */

/* Time the calendar stands still in mac_hook_rtc_set, from the tick edge
 * read to its restart (RM0453, calendar initialization): the INIT set right
 * after the edge, INITF within 2 RTCCLK (0-61 us), the TR/DR writes, then
 * the restart 4 RTCCLK (122 us) after INIT is cleared. ~180 +/- 40 us. */
#define RTC_SET_LOSS_US  180u

/* Old-clock reading of the last snapshot: the instant a Packet 1 / Tier 3
 * target refers to (the MAC reads the snapshot, then asks for the set). */
static uint32_t s_anchor_ticks;
static bool     s_anchor_valid;

/* Write time and date in one init-mode session, entered right after the
 * caller's tick edge. Register writes only: the HAL path (SetTime then
 * SetDate, two sessions) took ~1.3 ms at 4 MHz and stopped the calendar
 * twice. WeekDay is left 0, as HAL_RTC_SetDate with an unset field did. */
static bool rtc_write_calendar(uint32_t tr, uint32_t dr)
{
    __HAL_RTC_WRITEPROTECTION_DISABLE(&hrtc);
    SET_BIT(RTC->ICSR, RTC_ICSR_INIT);
    uint32_t spins = 0u;
    while (READ_BIT(RTC->ICSR, RTC_ICSR_INITF) == 0u) {
        if (++spins > 10000u) {          /* INITF comes within 2 RTCCLK */
            CLEAR_BIT(RTC->ICSR, RTC_ICSR_INIT);
            __HAL_RTC_WRITEPROTECTION_ENABLE(&hrtc);
            return false;
        }
    }
    WRITE_REG(RTC->TR, tr & RTC_TR_RESERVED_MASK);
    WRITE_REG(RTC->DR, dr & RTC_DR_RESERVED_MASK);
    CLEAR_BIT(RTC->CR, RTC_CR_BKP);      /* StoreOperation RESET, as before */
    /* Clears INIT first (the calendar restarts 4 RTCCLK later), then the
     * BYPSHAD workaround of errata 2.9.6. */
    (void)RTC_ExitInitMode(&hrtc);
    __HAL_RTC_WRITEPROTECTION_ENABLE(&hrtc);
    return true;
}

static uint32_t bcd2(uint32_t v) { return ((v / 10u) << 4) | (v % 10u); }

/* Packet 1 / Tier 3 hook: the clock reads target_ms at the instant of the
 * last snapshot, i.e. target_ms - floor_ms(snapshot) + t at any old-clock
 * time t (rtc_set_plan.h). The plan (64-bit maths) is made first; then the
 * old clock is read exactly on a tick edge, the calendar is written at once,
 * and a SHIFTR applies the sub-second plus the edge-exact time since the
 * snapshot plus the restart loss (#55). */
static void mac_hook_rtc_set(uint32_t target_ms,
                              uint8_t  day, uint8_t month, uint8_t year)
{
    /* The monotonic counter (UTIL_TIMER, HAL_GetTick) must not count this
     * jump: bracket the write. Begin also says whether a shift is pending. */
    bool shift_free = TIMER_IF_RtcWriteBegin();

    uint32_t now_ticks = TIMER_IF_GetDayTicks(false);
    uint32_t anchor    = s_anchor_ticks;
    if (!s_anchor_valid
        || RtcTicks_Elapsed(anchor, now_ticks) > RTC_SET_ANCHOR_MAX_TICKS) {
        anchor = now_ticks;              /* no fresh snapshot: target is "now" */
    }
    s_anchor_valid = false;

    RtcSetPlan_t plan = RtcSetPlan_Make(target_ms, anchor, RTC_SET_LOSS_US);
    uint32_t tr = (bcd2(plan.seconds / 3600u) << RTC_TR_HU_Pos)
                | (bcd2((plan.seconds % 3600u) / 60u) << RTC_TR_MNU_Pos)
                | (bcd2(plan.seconds % 60u) << RTC_TR_SU_Pos);
    uint32_t dr = ((uint32_t)year << RTC_DR_YU_Pos) | ((uint32_t)month << RTC_DR_MU_Pos)
                | ((uint32_t)day << RTC_DR_DU_Pos);

    /* Timing-critical from the edge to the calendar restart. */
    uint32_t edge_ticks = TIMER_IF_GetDayTicks(true);
    bool     written    = rtc_write_calendar(tr, dr);
    int32_t  shift      = RtcSetPlan_ShiftTicks(&plan, anchor, edge_ticks);

    const char *res      = written ? "none" : "fail";
    int32_t     shift_ms = 0;
    if (written && shift != 0) {
        hrtc.IsEnabled.RtcFeatures = UINT32_MAX;
        res = "busy";   /* a previous shift is still pending (SHPF) */
        if (shift_free) {
            uint32_t ticks = (uint32_t)(shift > 0 ? shift : -shift);
            /* Advance: ADD1S adds a second, SUBFS takes back (1 s - ticks).
             * Delay: SUBFS adds ticks to the SSR down-counter (RM0453 RTC_SHIFTR,
             * AN4759 2.6). Either way never a whole second: no date change. */
            HAL_StatusTypeDef st = (shift > 0)
                ? HAL_RTCEx_SetSynchroShift(&hrtc, RTC_SHIFTADD1S_SET, RTC_TICKS_PER_S - ticks)
                : HAL_RTCEx_SetSynchroShift(&hrtc, RTC_SHIFTADD1S_RESET, ticks);
            res = "fail";
            if (st == HAL_OK) {
                res      = "ok";
                shift_ms = shift_ticks_to_ms(ticks, shift > 0);
            }
        }
    }
    TIMER_IF_RtcWriteEnd(written, shift_ms);
    if (written) {
        drift_on_rtc_set();   /* the error jumps: a new segment for the estimator */
    }

    /* d = jump applied to the local clock (new - old domain), in ms;
     * adv = the SHIFTR in ticks (+ advance, - delay). */
    uint32_t old_ms = RtcTicks_ToMs(edge_ticks);
    ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_L, "RTC_SET",
           "old=%u new=%u d=%d date=%02x%02x%02x shift=%s adv=%d",
           (unsigned)old_ms, (unsigned)target_ms,
           (int)DayMs_Diff(target_ms, old_ms),
           (unsigned)year, (unsigned)month, (unsigned)day, res, (int)shift);
}

/* Phase correction hook: apply an SSR-only correction when CLOCK_WARM and
 * SYNC_CORRECT_THRESHOLD_MS <= error < SYNC_RESYNC_THRESHOLD_MS (= MAX_GUARD_TIME_MS).
 * The error is in microseconds (SYNC_RX erru), so a correction near 1 ms is not
 * lost to a whole-ms rounding; the shift is the nearest RTC tick (244 us).
 * Direction: err_us > 0 → RTC fast → delay (ADD1S=0).
 *            err_us < 0 → RTC slow → advance (ADD1S=1, SSR set to SUBFS). */
static void mac_hook_rtc_align_sub(int32_t err_us)
{
    uint32_t error_abs   = (err_us < 0) ? (uint32_t)(-err_us) : (uint32_t)(err_us);
    uint32_t shift_ticks = SyncStamp_UsToTicks(error_abs);

    if (shift_ticks == 0u) return;

    /* Bracket the write for the monotonic counter. A shift still pending
     * (SHPF) is not waited for: the HAL would block this callback until the
     * hardware absorbs it; the next Sync packet corrects instead. */
    const char *res      = "busy";
    int32_t     shift_ms = 0;
    int32_t     applied_ticks = 0;   /* + advance, - delay: what the estimator unwraps */
    if (TIMER_IF_RtcWriteBegin()) {
        HAL_StatusTypeDef st;
        hrtc.IsEnabled.RtcFeatures = UINT32_MAX;
        if (err_us > 0) {
            /* RTC fast → delay: SUBFS added to SSR prescaler counter */
            st = HAL_RTCEx_SetSynchroShift(&hrtc, RTC_SHIFTADD1S_RESET, shift_ticks);
        } else {
            /* RTC slow → advance: ADD1S=1 sets SSR = SUBFS after +1 calendar second.
             * Advance = 1 − SUBFS/(PREDIV_S+1) = shift_ticks/(PREDIV_S+1)  (AN4759 §2.6) */
            st = HAL_RTCEx_SetSynchroShift(&hrtc, RTC_SHIFTADD1S_SET,
                                            (RTC_PREDIV_S + 1u) - shift_ticks);
        }
        res = "fail";
        if (st == HAL_OK) {
            res      = "ok";
            shift_ms = shift_ticks_to_ms(shift_ticks, err_us < 0);
            applied_ticks = (err_us > 0) ? -(int32_t)shift_ticks : (int32_t)shift_ticks;
        }
    }
    TIMER_IF_RtcWriteEnd(false, shift_ms);
    if (applied_ticks != 0) {
        drift_on_shift(applied_ticks);
    }

    /* err > 0: local clock was ahead and is delayed; ticks of 1/(PREDIV_S+1) s;
     * err is the error in ms, rounded (the correction itself is in ticks). */
    ARCLOG(ARCLOG_MOD_SYNC, VLEVEL_M, "RTC_SHIFT", "err=%d ticks=%u res=%s",
           (int)SyncStamp_UsToMs(err_us), (unsigned)shift_ticks, res);
}

/* Atomic RTC snapshot hook: called at Sync Phase entry (C3/C2 TX epoch
 * capture) and right after mac_hook_rtc_set (C2/C1 Packet 1 path) to read
 * back the new RTC domain. HAL_RTC_GetTime must precede HAL_RTC_GetDate -
 * it unlocks the calendar shadow registers (see sys_app.c SystemApp_Init). */
static void mac_hook_get_rtc_snapshot(uint32_t *ms,
                                       uint8_t  *day, uint8_t *month, uint8_t *year)
{
    RTC_TimeTypeDef t = {0};
    RTC_DateTypeDef d = {0};

    hrtc.IsEnabled.RtcFeatures = UINT32_MAX;
    HAL_RTC_GetTime(&hrtc, &t, RTC_FORMAT_BCD);
    HAL_RTC_GetDate(&hrtc, &d, RTC_FORMAT_BCD);

    /* One register read gives both the ms the MAC sees and the tick an
     * RTC set right after refers to (the anchor of mac_hook_rtc_set). */
    s_anchor_ticks = TIMER_IF_GetDayTicks(false);
    s_anchor_valid = true;
    *ms    = RtcTicks_ToMs(s_anchor_ticks);
    *day   = d.Date;
    *month = d.Month;
    *year  = d.Year;
}

/* Packet 1 hook: re-anchor the TDMA Machine's FrameCursor and alarm chain
 * to the cell the Sync packet was actually received in. Uses the
 * schedule-derived nominal cell start (not a hardware readback) as the
 * alarm base so all nodes wake at the same absolute slot boundary. */
static void mac_hook_sync_bootstrapped(uint8_t  sync_phase_idx,
                                        uint8_t  sync_cell_idx,
                                        uint32_t nominal_start_ms)
{
    ARCLOG(ARCLOG_MOD_TDMA, VLEVEL_M, "BOOTSTRAP", "ph=%u ce=%u nom=%u",
           (unsigned)sync_phase_idx, (unsigned)sync_cell_idx,
           (unsigned)nominal_start_ms);
    TdmaMachine_BootstrapFromSync(sync_phase_idx, sync_cell_idx, nominal_start_ms);
}

/* ClockState transitions are logged by the MAC itself (CLK events). */

static const MAC_Hooks_t s_mac_hooks = {
    .rtc_set             = mac_hook_rtc_set,
    .rtc_align_subsecond = mac_hook_rtc_align_sub,
    .get_rtc_snapshot    = mac_hook_get_rtc_snapshot,
    .sync_bootstrapped   = mac_hook_sync_bootstrapped,
    .sync_locked         = NULL,
    .sync_lost           = NULL,
#ifdef DRIFT_ACTIVE
    .sync_sample         = mac_hook_sync_sample,
#endif
};

/* =========================================================================
 * HAL Alarm A callback — bridges RTC ISR to sequencer
 *
 * HAL_RTC_AlarmAEventCallback is a weak symbol in the HAL; this definition
 * overrides it.  Called from RTC_LSECSS_IRQHandler → HAL_RTC_AlarmIRQHandler.
 * ========================================================================= */

void HAL_RTC_AlarmAEventCallback(RTC_HandleTypeDef *hrtc_arg)
{
    (void)hrtc_arg;
    UTIL_SEQ_SetTask(1u << CFG_SEQ_Task_TdmaSlotWake, CFG_SEQ_Prio_0);
}

/* =========================================================================
 * Public API
 * ========================================================================= */

void SubGhzPhyTask_Init(void)
{
    /* 0. Zero the inter-core SRAM2 shared region before any machine touches it.
     *    CM4 writes its fields (g_freq_resolver_state, g_alarm_b_request) after
     *    this point, before the first TDMA slot fires. */
    SharedMem_Init();

    uint32_t uid[NODE_UID_WORDS];
    NodeId_ReadUid(uid);
    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "BOOT", "cls=C%u id=%u uid=%08x%08x%08x fw=%u.%u.%u build=%s sync=%s",
           (unsigned)NODE_CLASS, (unsigned)NodeId_FromUid(uid),
           (unsigned)uid[0], (unsigned)uid[1], (unsigned)uid[2],
           (unsigned)APP_VERSION_MAIN, (unsigned)APP_VERSION_SUB1,
           (unsigned)APP_VERSION_SUB2, BENCH_BUILD_ID, SYNC_PROFILE_NAME);

    /* 1. Radio — must be initialised before any UTIL_TIMER usage.
     *    TxTimeoutTimer and RxTimeoutTimer inside the radio driver are created
     *    here; without this call their Callback fields stay NULL (BSS zero),
     *    and the first Radio.Rx() would enqueue a NULL-callback timer →
     *    HardFault at UTIL_TIMER_IRQ_Handler. */
    s_radio_events.TxDone           = on_tx_done;
    s_radio_events.TxTimeout        = on_tx_timeout;
    s_radio_events.RxDone           = on_rx_done;
    s_radio_events.RxTimeout        = on_rx_timeout;
    s_radio_events.RxError          = on_rx_error;
    s_radio_events.FhssChangeChannel = NULL;
    s_radio_events.CadDone          = NULL;
    Radio.Init(&s_radio_events);

    /* Configure modem: LoRa SF12 / BW125 / CR4-5 / 14 dBm / CRC on.
     * bandwidth index 0 = 125 kHz, datarate = SF, coderate 1 = CR 4/5. */
    Radio.SetTxConfig(MODEM_LORA,
                      14,    /* power dBm  */
                      0u,    /* fdev (FSK only) */
                      0u,    /* bandwidth index: 0 = 125 kHz */
                      12u,   /* datarate: SF12 */
                      1u,    /* coderate: CR 4/5 */
                      8u,    /* preamble symbols */
                      false, /* fixed length */
                      true,  /* CRC on */
                      false, /* FHSS off */
                      0u,    /* hop period */
                      false, /* IQ not inverted */
                      3000u);/* TX timeout ms */

    Radio.SetRxConfig(MODEM_LORA,
                      0u,    /* bandwidth index: 0 = 125 kHz */
                      12u,   /* datarate: SF12 */
                      1u,    /* coderate: CR 4/5 */
                      0u,    /* bandwidthAfc (FSK only) */
                      8u,    /* preamble symbols */
                      0u,    /* symbol timeout: off, the window timer bounds Rx */
                      false, /* fixed length */
                      0u,    /* payload length (variable) */
                      true,  /* CRC on */
                      false, /* FHSS off */
                      0u,    /* hop period */
                      false, /* IQ not inverted */
                      false);/* single RX */

    UTIL_TIMER_Create(&s_rx_cap_timer, 0xFFFFFFFFu, UTIL_TIMER_ONESHOT,
                      on_rx_cap, NULL);

    /* 2. Compliance Engine — write results to g_compliance_status in SRAM2
     *    so CM4 can read duty-cycle state on every wake. */
    ComplianceEngine_Init(&g_compliance_status, HAL_GetTick);

    /* 3. Frequency Resolver — reads g_freq_resolver_state from SRAM2.
     *    CM4 populates all phases with default 868.3 MHz CELL_FREQ_STATIC at boot. */
    FrequencyResolver_Init(&g_freq_resolver_state);

    /* 4. MAC State Machine */
    MAC_Init(&s_mac_hooks);   /* logs MAC_INIT */
    drift_init();             /* C1/C2: estimator from the RTC_CALR in the register */

#if defined(BENCH_RTC_START_S) && (NODE_CLASS == NODE_CLASS_C3)
    /* Bench only: start the SyncAnchor's clock at BENCH_RTC_START_S seconds
     * after midnight instead of the CubeMX boot value 00:00:00, so a run
     * reaches the network's midnight soon after boot (issue #54). E.g.
     * -DBENCH_RTC_START_S=86100 starts at 23:55:00. Every other node takes
     * its time from C3. Logged as RTC_SET; the date is kept. */
    {
        uint32_t now_ms;
        uint8_t  day, month, year;
        mac_hook_get_rtc_snapshot(&now_ms, &day, &month, &year);
        mac_hook_rtc_set(((uint32_t)(BENCH_RTC_START_S) % 86400u) * 1000u,
                         day, month, year);
    }
#endif

    /* 5. TDMA Machine */
    static const TdmaPlatform_t plat = {
        .GetRtcMs        = plat_get_rtc_ms,
        .ProgramAlarmA   = plat_program_alarm_a,
        .CancelAlarmA    = plat_cancel_alarm_a,
        .RadioSetChannel = plat_radio_set_channel,
        .RadioSend       = plat_radio_send,
        .RadioSetRx      = plat_radio_set_rx,
        .RadioScan       = plat_radio_scan,
        .RadioSleep      = plat_radio_sleep,
        .RadioTimeOnAir  = plat_radio_toa,
        .WaitUntilMs     = plat_wait_until_ms,
        .tx_ramp_ms      = TX_RAMP_MS,
    };
    TdmaMachine_Init(&plat);

    /* 6. Register the slot task. C1/C2 boot cold and scan (no alarm chain
     *    until the first Sync packet); C3 runs its first slot now. */
    UTIL_SEQ_RegTask(1u << CFG_SEQ_Task_TdmaSlotWake, 0u, TdmaMachine_SlotTask);
    if (TdmaMachine_Start()) {
        UTIL_SEQ_SetTask(1u << CFG_SEQ_Task_TdmaSlotWake, CFG_SEQ_Prio_0);
    }
    ARCLOG(ARCLOG_MOD_SYS, VLEVEL_L, "INIT_DONE", "phases=%u",
           (unsigned)TdmaTable_PhaseCount());
}
