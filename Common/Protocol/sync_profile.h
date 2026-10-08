/*!
 * \file      sync_profile.h
 *
 * \brief     Sync profile: how often the network synchronises, chosen at
 *            compile time (issue #45, ADR-0020).
 *
 * \details   Sync is protocol overhead, kept as low as the clocks allow, but
 *            a long Sync period makes a node slow to lock and to learn its
 *            rate, which is no way to iterate on work that is not about sync
 *            (an Uplink test assumes synchronised clocks). One parameter
 *            picks the Sync schedule:
 *
 *              SYNC_PROFILE_PROD  the product schedule: a long Sync period,
 *                                 derived from the drift bound (#45).
 *                                 9 min, from the NUCLEO Clock figures of #34
 *                                 with one lost packet tolerated (ADR-0020);
 *                                 provisional only for the Production Clock,
 *                                 which is not measured yet (#112).
 *              SYNC_PROFILE_DEV   frequent Sync for development, INSIDE the
 *                                 duty-cycle budget: a Sync phase every
 *                                 200 s, one packet per node per phase,
 *                                 0.496 % of the 1 % band. The default.
 *              SYNC_PROFILE_BRINGUP  the former default: a phase every 30 s,
 *                                 three packets per phase, 9.9 % duty cycle.
 *                                 The compliance credit is gone after
 *                                 6.1 min and the nodes are denied from
 *                                 then on. Host tests only (they pin it).
 *
 *            Select with `-D SYNC_PROFILE=SYNC_PROFILE_PROD` (the bench
 *            override of a run or the build configuration). Each constant
 *            below stays overridable on its own (`-D SYNC_TX_BUDGET=1u`),
 *            so a scenario can still vary one value. The profile is logged
 *            in the CM0+ BOOT line (`sync=`).
 *
 *            A packet is 991 ms of airtime at SF12/BW125 (10-byte payload).
 *            Duty cycle of a node = packets per phase x 0.991 s / phase
 *            period; a Sync phase is cell_count (10) x (2.5 s slot + gap).
 *
 * \author    Simon R.C. Langlais ( Celium )
 *
 */
#ifndef SYNC_PROFILE_H
#define SYNC_PROFILE_H

#define SYNC_PROFILE_BRINGUP  0
#define SYNC_PROFILE_DEV      1
#define SYNC_PROFILE_PROD     2

#ifndef SYNC_PROFILE
#  define SYNC_PROFILE  SYNC_PROFILE_DEV
#endif

#if   SYNC_PROFILE == SYNC_PROFILE_BRINGUP
#  define SYNC_PROFILE_NAME             "BRINGUP"
#  define SYNC_PROFILE_TX_BUDGET        3u        /* packets per node per Sync phase */
#  define SYNC_PROFILE_CELL_GAP_MS      500u      /* cell 3 s, phase 30 s */
#  define SYNC_PROFILE_SILENCE_MS       900000u   /* 15 min (ADR-0013) */
#elif SYNC_PROFILE == SYNC_PROFILE_DEV
#  define SYNC_PROFILE_NAME             "DEV"
#  define SYNC_PROFILE_TX_BUDGET        1u
#  define SYNC_PROFILE_CELL_GAP_MS      17500u    /* cell 20 s, phase 200 s: 0.496 % */
#  define SYNC_PROFILE_SILENCE_MS       900000u   /* 15 min = 4.5 periods */
#elif SYNC_PROFILE == SYNC_PROFILE_PROD
#  define SYNC_PROFILE_NAME             "PROD"
#  define SYNC_PROFILE_TX_BUDGET        1u
#  define SYNC_PROFILE_CELL_GAP_MS      51500u    /* cell 54 s, phase 540 s: 0.18 % (Production Clock: #112) */
#  define SYNC_PROFILE_SILENCE_MS       1620000u  /* 3 periods: one lost packet (k = 1) must not drop the node to COLD */
#else
#  error "SYNC_PROFILE must be SYNC_PROFILE_BRINGUP, SYNC_PROFILE_DEV or SYNC_PROFILE_PROD"
#endif

/*! Packets per node per Sync phase (C3 in its first cells, a relaying C2 after it has the epoch). */
#ifndef SYNC_TX_BUDGET
#  define SYNC_TX_BUDGET  SYNC_PROFILE_TX_BUDGET
#endif

/*! Gap after each Sync cell, ms (uint16 in the table). */
#ifndef SYNC_CELL_GAP_MS
#  define SYNC_CELL_GAP_MS  SYNC_PROFILE_CELL_GAP_MS
#endif

/*! Cells in a Sync phase (the table, tdma_table.c). */
#define SYNC_PHASE_CELLS  10u

/*!
 * Boot burst, an option for faster warm-up (default 1: none). The C3 sends
 * this many packets, in the first cells, of the FIRST Sync phase after it
 * boots, drawn from its full duty-cycle credit (the 36 s bucket holds 36
 * packets); every later phase sends SYNC_TX_BUDGET. Boards flashed or reset
 * together with the C3 then lock within that phase (about a minute on DEV)
 * instead of one phase per packet. A node reset alone later, and every relay,
 * keep the regular schedule. `-D SYNC_BOOT_BURST=5u`.
 *
 * Choose N so that three packets remain after the cells the node misses
 * while it boots: boards are flashed one after the other, so a node is up 8 s
 * or more after the C3 and misses cell 0 (bench, 2026-10-04: a burst of 3
 * gave it two packets, not the three a cold node needs). With cells 20 s
 * apart, N = 5 tolerates a node booting up to 40 s late.
 */
#ifndef SYNC_BOOT_BURST
#  define SYNC_BOOT_BURST  1u
#endif
#if (SYNC_BOOT_BURST < 1u) || (SYNC_BOOT_BURST > SYNC_PHASE_CELLS)
#  error "SYNC_BOOT_BURST must be between 1 and the cells of a Sync phase"
#endif

/*! Silence after which a node drops to CLOCK_COLD (ADR-0013). */
#ifndef SYNC_SILENCE_TIMEOUT_MS
#  define SYNC_SILENCE_TIMEOUT_MS  SYNC_PROFILE_SILENCE_MS
#endif

#endif /* SYNC_PROFILE_H */
