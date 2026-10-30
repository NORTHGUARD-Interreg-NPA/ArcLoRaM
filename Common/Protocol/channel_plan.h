/*!
 * \file      channel_plan.h
 *
 * \brief     The radio channel plan: which frequency the phases use, chosen at
 *            compile time (issue #85).
 *
 * \details   Today the network uses ONE channel, for every phase: the Frequency
 *            Resolver's static phases (CM4 writes them at boot), the discovery
 *            channel a cold node listens on and the fallback of an unpopulated
 *            phase all take \ref FREQ_DEFAULT_HZ. The name is not about Sync: when
 *            phases get channels of their own, each takes its value from the
 *            protocol and this stays the default of the ones that do not.
 *
 *            Several sessions share the bench, and a run that raises its power or
 *            starts a network of its own must not land on the desk of another.
 *            A run therefore takes a channel of its own with the Build Override
 *            `-D FREQ_DEFAULT_HZ=867300000u` (all boards of the run get it; the
 *            Build ID carries it) and `bench` leases the channel (tools/bench).
 *
 *            The plan:
 *
 *              - 100 kHz raster (868.3 MHz, 867.3 MHz, ...).
 *              - From CHANNEL_MIN_HZ to CHANNEL_MAX_HZ: a 125 kHz channel centred
 *                there stays inside 865.0 to 868.6 MHz, the ETSI EN 300 220
 *                sub-bands L (865-868 MHz) and M (868-868.6 MHz): 1 % duty cycle,
 *                25 mW ERP, a row of the Compliance Engine. The sub-bands around
 *                them differ: 863-865 MHz and 868.7-869.2 MHz allow 0.1 % and are
 *                out of the plan until the engine learns them; 869.4-869.65 MHz allows
 *                10 % and 500 mW, has a row of its own in the engine and one channel
 *                in the plan (\ref CHANNEL_HIGH_POWER_HZ, below).
 *              - Channels of two sessions are CHANNEL_SPACING_HZ apart. The
 *                Semtech SX1261/2 summary gives 88 dB of blocking immunity at a
 *                1 MHz offset; the widest gap on the bench, a desk node at about
 *                -28 dBm next to a far link assumed at -90 dBm, is 62 dB. No figure
 *                was found for a closer offset, so nothing closer is claimed:
 *                865.3, 866.3, 867.3 and 868.3 MHz are four channels.
 *
 *            Power follows the channel. The 25 mW (14 dBm) of the sub-bands above is the
 *            limit of every channel of the plan but one: 869.5 MHz, the middle of
 *            ETSI sub-band P (869.4 to 869.65 MHz), 10 % duty cycle and 500 mW ERP. There
 *            the NUCLEO board's high-power amplifier may run at its maximum, 22 dBm
 *            (that is 160 mW before the antenna; the antenna gain must stay under 5 dB for
 *            the 27 dBm ERP). \ref RADIO_TX_POWER_DBM sets the power of the radio and of
 *            the request to the Compliance Engine alike, and a build that asks for more than
 *            14 dBm anywhere else does not compile. The Compliance Engine knows both rows.
 *
 *            `tools/bench/src/bench/channels.py` reads these constants from this
 *            file: there is no second copy to keep in step.
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef CHANNEL_PLAN_H
#define CHANNEL_PLAN_H

/*! Lowest channel centre of the plan, Hz (865.0 MHz band edge + 100 kHz). */
#define CHANNEL_MIN_HZ      865100000u
/*! Highest channel centre of the plan, Hz (868.6 MHz band edge - 100 kHz). */
#define CHANNEL_MAX_HZ      868500000u
/*! Channels are multiples of this, Hz. */
#define CHANNEL_RASTER_HZ   100000u
/*! Least distance between the channels of two sessions at the same time, Hz. */
#define CHANNEL_SPACING_HZ  1000000u
/*! The channel of the network when nothing is overridden: the only one used so far, Hz. */
#define CHANNEL_DEFAULT_HZ  868300000u
/*! The one channel outside the range above: the middle of the 10 % duty cycle, 500 mW sub-band P, Hz.
 *  A 125 kHz channel centred here (869.4375 to 869.5625 MHz) stays inside 869.4 to 869.65 MHz. */
#define CHANNEL_HIGH_POWER_HZ  869500000u

/*! Power of the radio when nothing is overridden, dBm: the 25 mW ERP limit of the one percent channels. */
#define RADIO_TX_POWER_DEFAULT_DBM  14
/*! The most any channel of the plan but the high-power one allows, dBm (25 mW ERP). */
#define RADIO_TX_POWER_LIMIT_DBM    14
/*! The most the board can do: the high-power amplifier of the NUCLEO-WL55JC1 (RBI_GetRFOMaxPowerConfig), dBm. */
#define RADIO_TX_POWER_MAX_DBM      22
/*! The least the radio can do, dBm. */
#define RADIO_TX_POWER_MIN_DBM      (-9)

#ifndef FREQ_DEFAULT_HZ
/*!
 * Frequency of every phase until the protocol gives a phase its own, Hz.
 * Override with `-D FREQ_DEFAULT_HZ=867300000u` (a bench scenario's [overrides]).
 */
#define FREQ_DEFAULT_HZ     CHANNEL_DEFAULT_HZ
#endif

#ifndef RADIO_TX_POWER_DBM
/*!
 * Power of the radio, and of the request to the Compliance Engine, dBm.
 * Override with `-D RADIO_TX_POWER_DBM=22`; above 14 dBm only on \ref CHANNEL_HIGH_POWER_HZ.
 */
#define RADIO_TX_POWER_DBM  RADIO_TX_POWER_DEFAULT_DBM
#endif

#if (FREQ_DEFAULT_HZ != CHANNEL_HIGH_POWER_HZ) \
    && ((FREQ_DEFAULT_HZ < CHANNEL_MIN_HZ) || (FREQ_DEFAULT_HZ > CHANNEL_MAX_HZ) \
        || ((FREQ_DEFAULT_HZ % CHANNEL_RASTER_HZ) != 0u))
#  error "FREQ_DEFAULT_HZ must be 869.5 MHz, or a multiple of 100 kHz from 865.1 to 868.5 MHz (channel_plan.h)"
#endif

#if (RADIO_TX_POWER_DBM > RADIO_TX_POWER_MAX_DBM) || (RADIO_TX_POWER_DBM < RADIO_TX_POWER_MIN_DBM)
#  error "RADIO_TX_POWER_DBM must be from -9 to 22 dBm, the range of the board (channel_plan.h)"
#endif

#if (RADIO_TX_POWER_DBM > RADIO_TX_POWER_LIMIT_DBM) && (FREQ_DEFAULT_HZ != CHANNEL_HIGH_POWER_HZ)
#  error "More than 14 dBm is allowed on 869.5 MHz only (500 mW sub-band); the others stop at 25 mW (channel_plan.h)"
#endif

#endif /* CHANNEL_PLAN_H */
