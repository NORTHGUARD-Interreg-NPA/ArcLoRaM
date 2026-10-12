# Link between the remote site and the default site: does nuna-node-04 hear a C3, and at what margin

Purpose: spontaneous. Issue: #85 (board positions and the radio channel; the expected link is the output of its positions).

Simon asked on 2026-10-12 whether nuna-node-04, standing at a site of its own, receives the Sync packets of a C3 on the default site where the other boards stand, and with what margin.
Nobody had measured a link between two sites before; the boards of the bench had only ever been a few metres apart.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/node4-link.toml` (a template: node 0 is the C3 on the default site, `--as <Node ID>`) | nuna-node-04 as C2 receives the Sync of the C3 (`RX_DONE` within 3 min of arming, ten in 21 min), on its own channel 867.3 MHz, with the default Sync profile and a boot burst; the C3's Sync is on 867.3 MHz and never on 868.3 MHz. The RSSI and SNR of every packet are read from the record's report |
| `tools/bench/scenarios/node4-link-reverse.toml` (a template: node 0 is the C2 on the default site) | the other direction, nuna-node-04 as C3: tells a dead path (nothing heard either way) from a receiver that does not work (heard in one direction only) |

## Hardware

Checked against `bench boards` on 2026-10-12 at 16:35 UTC.

| Item | Needed | Today |
|---|---|---|
| nuna-node-04 (Node ID 4) at the remote site, flashed as C2 (forward) or C3 (reverse) | yes | connected, free |
| One board at the default site, as C3 (forward) or C2 (reverse): nuna-node-09 (Node ID 7) was used | yes | connected, free; nuna-node-06 (Node ID 1) and nuna-node-08 (Node ID 6) were held by another session, on 868.3 MHz |
| The antennas of both boards and how they are connected | yes, but not known from the PC | not checked: nobody has looked on site |

The distance between the two sites is 306 m (from the per-machine positions file, `bench boards --json`).
Hands on the bench: none during a run.
Simon power-cycled the Pi of nuna-node-09 at 15:14 UTC after the first attempt broke its flash (see the Fleet check of `spot-20261010-onboard-board.md`).

## Expected link

Free space over 306 m at 868.3 MHz, 14 dBm as configured in `Radio.SetTxConfig` (not measured at the antenna) and no antenna gain assumed: RSSI about -67 dBm, SNR about +50 dB (noise floor -117 dBm in 125 kHz with a 6 dB receiver).
The SF12 / BW125 limits are -137 dBm and -20 dB SNR (Semtech SX1261/2 datasheet figures), so on paper the margin is about 70 dB.
That is a ceiling: it knows no building, tree or antenna.

## Criteria

- [ ] The RSSI and SNR of the Sync packets nuna-node-04 receives from the default site, and its margin to the SF12 limits (-137 dBm, -20 dB).
- [ ] The same for the reverse direction.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-12 14:44 | `node4-link.toml` (first version: C3 nuna-node-09, 868.3 MHz) | `3e12e0f-o954ffd` | invalid (bench fault: the flash of nuna-node-09 was cut) | none: the flash never completed | The Pi's OpenOCD lost the debug port in the middle of the flash (`Failed to write memory`, `Error connecting DP: cannot read IDR`); the board kept a partial image and looped on `CORE_SYNC stage=wait_cm0`. Restarting `openocd.service` did not help (it exits at start with the same error). Simon power-cycled that Pi at 15:14, and `bench flash --node 7=C3` repaired the board at 15:55. Run on a channel shared with another session's crash tests: not repeated that way. |
| 2026-10-12 16:13 | `node4-link.toml` (C3 nuna-node-06, 867.3 MHz) | `3e12e0f-d6435f6-o24d622` | invalid (bench fault: smoke window) | `tools/arclog/runs/20261012T161342Z-3e12e0f-d6435f6-o24d622/` | First run on its own channel: the override reached the radio (`SYNC_TX freq=867300000`, `SCAN freq=867300000` on nuna-node-04). Failed the smoke check at +30.0 s with nuna-node-04's BOOT lines at about +30 s: the window counted from the first flash, not from each board's. Fixed (`arclog/expect.py`, `test_expect.py`). After it, nuna-node-06 sent four Sync packets (cells 1 to 4) while nuna-node-04 was listening and nuna-node-04 logged none. |
| 2026-10-12 16:19 | `node4-link.toml --as 7` (C3 nuna-node-09, 867.3 MHz) | `3e12e0f-d6435f6-o24d622` | invalid (bench fault: smoke window) | `tools/arclog/runs/20261012T161959Z-3e12e0f-d6435f6-o24d622/` | The same race again: BOOT of nuna-node-04 at +30.3 s against a 30 s window from the first flash. |
| 2026-10-12 16:24 | `node4-link.toml --as 7` (C3 nuna-node-09, 867.3 MHz) | `3e12e0f-d6435f6-o24d622` | FAIL (no `RX_DONE` within 3 min of arming) | `tools/arclog/runs/20261012T162405Z-3e12e0f-d6435f6-o24d622/` | Valid run, smoke check passed (nuna-node-04 armed at +36.9 s, the first run with the fixed window). nuna-node-09 sent cells 0 to 4 on 867.3 MHz (16:24:27 to 16:25:46); nuna-node-04, scanning on 867.3 MHz from 16:24:42, received cells 1 to 4 (four packets) as nothing: no `RX_DONE`, no `RX_ERROR`, no `SYNC_RX`. The measurement is "nothing heard". |
| 2026-10-12 16:29 | `node4-link-reverse.toml --as 7` (C3 nuna-node-04, C2 nuna-node-09, 867.3 MHz) | `3e12e0f-d6435f6-o24d622` | FAIL (no `RX_DONE` within 3 min of arming) | `tools/arclog/runs/20261012T162948Z-3e12e0f-d6435f6-o24d622/` | Valid run. nuna-node-04 sent cells 0 to 4 on 867.3 MHz (16:30:09 to 16:31:29); nuna-node-09, scanning from 16:30:25, had cells 1 to 4 (four packets) and logged nothing. |

## Findings

1. **No packet was heard in either direction**: 12 packets sent while the receiver listened (4 from nuna-node-06 and 4 from nuna-node-09 to nuna-node-04, 4 from nuna-node-04 to nuna-node-09), SF12 / BW125, 14 dBm configured, 867.3 MHz, 306 m.
   The expected -67 dBm is 70 dB above the sensitivity, so a free-space link would be received easily: the path has at least 70 dB more loss than free space, or an antenna is missing or badly connected at one end, or reception on 867.3 MHz does not work at all.
2. **The last cause is not excluded yet.** No board has been heard receiving on 867.3 MHz: the transmissions on it are proven (`SYNC_TX freq=867300000`), the receptions are not.
   A control of 3 min with two boards a few metres apart on 867.3 MHz settles it (a C3 and a C2 of the default site, which needs two free boards there).
   The next steps, in order: that control; the remote pair on 868.3 MHz when no other session is on it; the antennas of nuna-node-04 and of the default site looked at by someone on site; only then a question of more power (the Compliance Engine caps the whole 863-870 MHz band at 14 dBm, and the radio is set to 14 dBm in `Radio.SetTxConfig`).
3. **The capture holds a reading at desk distance.** On 2026-10-12, 13:23 to 13:49 UTC, the packets of nuna-node-04 reached nuna-node-08 at -38 to -28 dBm, SNR 4 to 5 (868.3 MHz, `tools/arclog/runs/bench/nuna-node-08-20261012.log`, paired with the `TX_DONE` of nuna-node-04 by host time): both radios work at a few metres, and the capture at 14:50 showed none reaching a default-site board after 13:49, which fits a move of nuna-node-04 at that time (weak evidence: the listeners were locked and open only short windows; not noted by `bench note`; unverified).
4. **A bench bug found and fixed on the way**: the smoke check window ran from the start of the first flash for every board (30 s), so the second board of a run, flashed about 15 s later, had about 15 s to boot (it needs 15 s). Reproduced twice on the bench, then red/green in `test_expect.py`, then green on the bench (run of 16:24).
5. **Strays**: when this record was written nuna-node-04 runs as a C3 on 867.3 MHz and nuna-node-09 as a C2 scanning 867.3 MHz, both with build `3e12e0f-d6435f6-o24d622`; nuna-node-04 sends one Sync per 200 s on 867.3 MHz, 1 MHz from the desk channel (`bench boards` shows it as `on air 867.3 MHz`).
