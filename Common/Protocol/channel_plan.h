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
 *                25 mW ERP, the row the Compliance Engine knows. The sub-bands
 *                around them differ (863-865 MHz and 868.7-869.2 MHz allow 0.1 %,
 *                869.4-869.65 MHz allows 10 % and 500 mW) and are out of the plan
 *                until the engine learns them.
 *              - Channels of two sessions are CHANNEL_SPACING_HZ apart. The
 *                Semtech SX1261/2 summary gives 88 dB of blocking immunity at a
 *                1 MHz offset; the widest gap on the bench, a desk node at about
 *                -28 dBm next to a far link assumed at -90 dBm, is 62 dB. No figure
 *                was found for a closer offset, so nothing closer is claimed:
 *                865.3, 866.3, 867.3 and 868.3 MHz are four channels.
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

#ifndef FREQ_DEFAULT_HZ
/*!
 * Frequency of every phase until the protocol gives a phase its own, Hz.
 * Override with `-D FREQ_DEFAULT_HZ=867300000u` (a bench scenario's [overrides]).
 */
#define FREQ_DEFAULT_HZ     CHANNEL_DEFAULT_HZ
#endif

#if (FREQ_DEFAULT_HZ < CHANNEL_MIN_HZ) || (FREQ_DEFAULT_HZ > CHANNEL_MAX_HZ) \
    || ((FREQ_DEFAULT_HZ % CHANNEL_RASTER_HZ) != 0u)
#  error "FREQ_DEFAULT_HZ must be a multiple of 100 kHz from 865.1 to 868.5 MHz (channel_plan.h)"
#endif

#endif /* CHANNEL_PLAN_H */
