<p align="center">
  <img src="assets/interreg_npa_banner.png" alt="Interreg Northern Periphery and Arctic programme banner" width="400">
</p>

# ArcLoRaM Firmware

Low-power LoRa mesh firmware for the STM32WL55 dual-core SoC.
Implements an autonomous, infrastructure-free mesh network with TDMA
scheduling, time synchronisation, and duty-cycle compliance.
Designed for long-range, low-power connectivity where no central
infrastructure is available.

Developed for [NORTHGUARD](https://www.interreg-npa.eu/) (NPA1000307), an Interreg
Northern Periphery and Arctic project building an information-driven
preparedness system for Arctic resilience, wildfire detection, and emergency
response.
The system is transferable to any scenario requiring autonomous, long-range,
low-power mesh communication without existing infrastructure.

## Architecture

### Dual-core split

The STM32WL55JCI6 runs two cores with strictly separated responsibilities:

| Core | Role | Key modules |
|------|------|-------------|
| **CM0+** | Radio and protocol | TDMA Machine, MAC State Machine, Frequency Resolver, Compliance Engine, Guard Time Resolver, SubGHz radio PHY |
| **CM4** | Application | Sensor Scheduler, Payload Assembly, Routing State, Diagnostics, MbMux signal handler |

CM0+ owns the radio hardware and all protocol timing.
CM4 handles sensor acquisition and application logic.
The two cores communicate through shared SRAM2 (single-writer ownership, no
mutexes) and IPCC/MbMux event signals (`SYNC_LOCKED`, `SYNC_LOST`,
`RX_READY`, `TX_NO_ACK`, `ACK_RECEIVED`, `RX_TIMEOUT`).

### Protocol machines

Five protocol machines run on the CM0+, and the Sensor Scheduler on the CM4, as
self-contained C modules within the `UTIL_SEQ` cooperative-task framework:

| Machine | Core | Module | Purpose |
|---------|------|--------|---------|
| TDMA Machine | CM0+ | `tdma_machine` | Slot timing via RTC Alarm A, frame cursor advancement, guard-time application |
| MAC State Machine | CM0+ | `mac_state_machine_cX` | Per-class state transitions (Scanning, Synchronized, Paired, Active), driven by radio callbacks |
| Frequency Resolver | CM0+ | `freq_resolver` | Per-slot channel selection (STATIC, HOP, OVERRIDE) from shared memory state |
| Compliance Engine | CM0+ | `compliance_engine` | Duty-cycle gating on every transmission |
| Guard Time Resolver | CM0+ | `guard_time_resolver` | Clock-drift margin for Rx window timing |
| Sensor Scheduler | CM4 | `subghz_phy_app` | Sensor acquisition scheduling via RTC Alarm B |

All CM0+ machines live in `CM0PLUS/SubGHz_Phy/Logic/`.
CM4 application code lives in `CM4/SubGHz_Phy/`.

### Node classes

The node class is a compile-time constant enabling dead-code elimination and a
shared-memory constant readable by both cores.
Each board runs the same build of its class.

| Class | Role | Sleep |
|-------|------|-------|
| **C1** | End node. Cluster-only. Sensor data producer. Communicates with its cluster master. | Stop2 |
| **C2** | Relay. Cluster master for C1 nodes. Mesh backbone participant. May also collect sensor data. | Stop2 |
| **C3** | Gateway. Mesh backbone terminus. SyncAnchor: originates Sync packets, never enters Scanning. | Active |

Node IDs are assigned from a compiled UID table (`Common/Protocol/node_id.c`)
that maps each board's 96-bit MCU unique ID to a one-byte Node ID.

### TDMA scheduling

The network operates on a repeating TDMA schedule structured as:

```
Frame > Phase > Cell > Slot
```

- **Frame**: top-level repeating period (e.g. 1 hour, 24 hours).
- **Phase**: contiguous time subdivision with a Phase Type.
- **Cell**: repeating unit within a Phase; contains one or more Slots.
- **Slot**: atomic radio access opportunity (Tx, Rx, or Sleep).

Five Phase Types cover the full network behaviour:

| Phase Type | Layer | Role |
|------------|-------|------|
| `Mesh_Beacon` | Mesh | Link detection for routing (C2/C3) |
| `Mesh_Uplink` | Mesh | Node-to-gateway sensor data |
| `Mesh_Downlink` | Mesh | Gateway-originated commands/config |
| `Cluster_Exchange` | Cluster | Bidirectional local comms between C2 master and C1 end nodes |
| `Sync` | Network | Time synchronisation propagation hop by hop |

Mesh phases operate at the backbone level (C2/C3, long-range, high SF).
Cluster phases operate at the star-subnet level (C1/C2, short-range, low SF).

### Power model

C1 and C2 nodes enter Stop2 deep sleep between active slots, waking on RTC
Alarm A (TDMA) or RTC Alarm B (sensor acquisition).
C3 gateways remain active as the sync anchor.
The alarm-chain model programs the next RTC Alarm A before returning to sleep,
so a single missed wake never permanently desynchronises the schedule.

## Repository layout

```
CM0PLUS/             CM0+ firmware (radio and protocol)
  SubGHz_Phy/Logic/      Protocol machines (TDMA, MAC, freq, compliance, guard)
  Core/                  HAL drivers, RTC, IPCC, GPIO, DMA
CM4/                 CM4 firmware (application)
  SubGHz_Phy/            Sensor scheduler, diagnostics
  MbMux/                 Radio mailbox wrapper
Common/              Shared code (both cores)
  Protocol/              TDMA table, node ID, protocol types
  SharedMemory/          Inter-core shared memory structures
  Log/                   ArcLog structured trace
  MbMux/                 Mailbox multiplexer definitions
Drivers/             STM32WLxx HAL driver and CMSIS
Middlewares/         SubGHz radio driver
Utilities/           Trace, sequencer, timer and low-power utilities
Tests/               Host-side unit tests (CMake + Unity)
assets/              Images used by this README
ArcLoRaM_Base.ioc    STM32CubeMX project configuration
```

Comments in the source cite `CONTEXT.md` (the domain glossary) and Architecture
Decision Records.
These design documents are not part of this repository.

## Building the firmware

The firmware is built and flashed via **STM32CubeIDE**.
Three build configurations exist, one per node class:

| Configuration | Node class |
|--------------|-----------|
| `Debug_C1` | End node |
| `Debug_C2` | Relay |
| `Debug_C3` | Gateway |

Open the project in STM32CubeIDE, select the configuration matching your
target board's node class, and build.
The `.ioc` file (`ArcLoRaM_Base.ioc`) is the STM32CubeMX configuration for the
STM32WL55JCI6 target.

## Running unit tests

Host-side unit tests run on your development machine with CMake and a C
compiler.
They do not require firmware hardware or a cross-compiler.

```sh
cd Tests
cmake --preset host-linux
cmake --build build
ctest --test-dir build --output-on-failure
```

Use the `host-linux` preset on Linux, and `host-mingw` on Windows with
MSYS2 UCRT64.
Tests use the [Unity](https://github.com/ThrowTheSwitch/Unity) test framework,
fetched automatically by CMake.
See `Tests/TESTING_GUIDE.md` for the full reference.
