# Compliance Engine near the limit: the slot-sized request and the unrecorded overrun

Purpose: spontaneous. Issue: #122 "The engine never charges an airtime overrun".

A host-only check made while investigating #56 "Per-class duty-cycle verification of the TDMA table".
It drives the real `compliance_engine.c` the way the TDMA Machine does (`RequestChannel`, then `ReportTxDone`) with a node that sits at the limit.
It answers two questions: what the 2500 ms `slot_active_ms` request costs a node near the 1 % limit, and what the engine does when a packet takes longer than its request.
The first is input to #90 "Slot duration derived per phase from its packet, not one worst-case constant", the second is the defect of #122.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| The host program below (`gcc`, `-DHOST_TEST`, the real `compliance_engine.c`), not a bench scenario | Grants, first grant and granted airtime of one node on a drained 1 % bucket, for a request equal to the slot (2500 ms) and to the real airtime, and for a packet longer than its request |
| `Tests/unit/test_lora_toa.c` (host) | The SF12 / BW125 airtime staircase and the implicit-header shift that #90 and #57 "Header mode: implicit vs explicit" lean on |

## Hardware

None: a host program.

Hands on the bench: none.

## Procedure

The bucket is drained at t = 0 by one request of 36000 ms.
The node then makes one request every `period` ms, and each granted request is followed by `ReportTxDone(actual)`.
The allowance of a window is 1 % of its length (bucket drained at the start).

```c
#include <stdio.h>
#include <stdint.h>
#include "compliance_engine.h"

#define F 868300000u

static uint32_t now;
static uint32_t tick(void) { return now; }

static void run(const char *name, uint32_t expected, uint32_t actual, uint32_t period, int tries)
{
    ComplianceStatus_t st;
    int granted = 0, first = -1;

    now = 0;
    ComplianceEngine_Init(&st, tick);
    ComplianceEngine_RequestChannel(F, 36000u, 14);                 /* drain the bucket */
    for (int i = 1; i <= tries; i++) {
        now = (uint32_t)i * period;
        if (ComplianceEngine_RequestChannel(F, expected, 14) == COMPLIANCE_GRANTED) {
            ComplianceEngine_ReportTxDone(F, actual);
            if (first < 0) first = i;
            granted++;
        }
    }
    printf("%-26s expected %4u actual %4u every %6u ms x %3d: granted %3d, first at %3u s, airtime %3u s\n",
           name, expected, actual, period, tries, granted,
           first < 0 ? 0u : (unsigned)((uint32_t)first * period / 1000u), (unsigned)(granted * actual / 1000u));
}

int main(void)
{
    run("slot request (1 % pace)",  2500u,  991u,  99100u, 100);
    run("real-ToA request",          991u,  991u,  99100u, 100);
    run("overrun, slot request",    2500u, 9000u, 100000u, 200);
    run("overrun, honest request",  9000u, 9000u, 100000u, 200);
    return 0;
}
```

```sh
gcc -Wall -Wextra -DHOST_TEST -ICM0PLUS/SubGHz_Phy/Logic -ICommon/SharedMemory -ICommon/Protocol \
    -o near_limit near_limit.c CM0PLUS/SubGHz_Phy/Logic/compliance_engine.c && ./near_limit
```

## Results

Measured on `main` at `231c21c`, 2026-10-12.

| Case | Request | Real airtime | Pace | Granted | First grant | Airtime granted |
|---|---|---|---|---|---|---|
| Slot-sized request | 2500 ms | 991 ms | every 99.1 s (1 %), 100 tries | 98 | 297 s | 97 s |
| Real-ToA request | 991 ms | 991 ms | every 99.1 s (1 %), 100 tries | 100 | 99 s | 99 s |
| Overrun, slot-sized request | 2500 ms | 9000 ms | every 100 s (9 %), 200 tries | 80 | 300 s | 720 s |
| Overrun, honest request | 9000 ms | 9000 ms | every 100 s (9 %), 200 tries | 22 | 900 s | 198 s |

The window of the last two rows is 20000 s, so the allowance is about 200 s of airtime.

## Findings

- **The slot-sized request costs a reserve, not rate.**
  At the 1 % pace the node keeps being granted either way.
  What the 2500 ms request costs is the credit that must stay in the bucket for a request to pass: about 1.5 s (2500 - 991 ms) of the 36 s hourly allowance can never be spent, and a drained node waits about 150 s longer for its first grant (297 s against 99 s on this send grid).
  It matters only near the limit, where a constrained deployment (#51 "Deployment table: dense Sync schedule for fast pairing") will run.
  Input to #90: the compliance request should be the airtime of the slot's largest packet, with no timing margin.
- **An overrun is never charged.**
  `ReportTxDone` refunds when the real airtime is below the request and does nothing when it is above.
  With a packet of 9000 ms and a request of 2500 ms the engine grants 720 s of airtime in a window that allows about 200 s: 3.6 times the allowance.
  The honest request keeps to 198 s.
  This is the defect of #122.
- **It cannot fire today.**
  The only packet is the 10-byte Sync packet (991 ms), under the 2500 ms request.
  The protocol's largest packet is 40 bytes of payload plus a header (decision of 2026-10-12, header size open): 40 bytes alone are 1974 ms at SF12 / BW125, and the 2500 ms slot holds a payload of up to 55 bytes explicit, 58 implicit.
  So 40 application bytes fit only if the protocol header is 15 bytes or less (explicit) or 18 bytes or less (implicit).
  The build-time guard of #90 keeps it that way.

## The SF12 airtime staircase

From `LoraToa_Us` (the SX126x datasheet formula, host tested against the 991 ms of the Sync packet, not measured on air), SF12 / BW125, CR 4/5, 8 preamble symbols, CRC on.
The airtime is a staircase in steps of 5 payload bytes (163.84 ms), and the implicit header shifts the staircase by 3 bytes.
It saves a step only when the payload length modulo 5 is 1, 2 or 3.

| Payload | Explicit | Implicit |
|---|---|---|
| 6 to 10 bytes (the Sync packet is 10) | 991.232 ms | 991.232 ms |
| 11 to 13 bytes | 1155.072 ms | 991.232 ms |
| 14 to 15 bytes | 1155.072 ms | 1155.072 ms |
| 36 to 40 bytes | 1974.272 ms | 1974.272 ms |
| 41 to 43 bytes | 2138.112 ms | 1974.272 ms |

So the implicit header saves no airtime for the Sync packet as it is, and packet lengths are better chosen at the top of a step for the mode in use (multiples of 5 explicit, 5k + 3 implicit) than the mode chosen for the airtime.
`test_lora_toa.c` pins the steps, the shift and the 55 / 58 byte limits of the 2500 ms slot.
The figures were posted on #57 and #90.

## Criteria

- [x] The slot-sized request keeps the long-run rate at the 1 % pace (98 of 100 grants, against 100 with the real airtime), and costs a reserve and a later first grant (297 s against 99 s).
- [x] The engine grants more than the allowance when a packet is longer than its request (720 s against about 200 s); reproduced on the unfixed engine, the red run of #122.
- [x] The SF12 staircase and the implicit-header shift are pinned by a host test (`test_lora_toa.c`).

The fix of the overrun is #122, the derived slot and request are #90, the header mode is #57: none is done here.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-12 15:4x | host program above | `231c21c` | measurement | none | The four rows of Results; built with `-Wall -Wextra`, no warning. The engine is unchanged since `0a942df`. First run on `4b2ff7c` gave the same figures. |
| 2026-10-12 15:5x | `test_lora_toa` (host, with the whole suite) | `74c0c05` | PASS | none | 11 of 11 in `test_lora_toa`, 38 of 38 in the host suite: the staircase, the implicit shift and the 55 / 58 byte limits. |
