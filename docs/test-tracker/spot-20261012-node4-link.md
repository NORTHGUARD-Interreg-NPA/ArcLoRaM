# Link between the remote site and the default site: does nuna-node-04 hear a C3, and at what margin

Purpose: spontaneous. Issue: #85 (board positions and the radio channel; the expected link is the output of its positions).

Simon asked on 2026-10-12 whether nuna-node-04, standing at a site of its own, receives the Sync packets of a C3 on the default site where the other boards stand, and with what margin.
Nobody had measured a link between two sites before; the boards of the bench had only ever been a few metres apart.
Conditions (Simon, 2026-10-12): the default-site board is indoors, in a room; nuna-node-04 is indoors too, near a window; both are NUCLEO development boards with a cheap antenna.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/node4-link.toml` (a template: node 0 is the C3 on the default site, `--as <Node ID>`) | nuna-node-04 as C2 receives the Sync of the C3 (`RX_DONE` within 3 min of arming, ten in 21 min), on its own channel 867.3 MHz, with the default Sync profile and a boot burst; the C3's Sync is on 867.3 MHz and never on 868.3 MHz. The RSSI and SNR of every packet are read from the record's report |
| `tools/bench/scenarios/node4-link-reverse.toml` (a template: node 0 is the C2 on the default site) | the other direction, nuna-node-04 as C3: tells a dead path (nothing heard either way) from a receiver that does not work (heard in one direction only) |
| `tools/bench/scenarios/node4-link-max-power.toml` (a template, as `node4-link.toml`) | the forward link again with the board's maximum power: both boards built for 869.5 MHz (the 10 % duty cycle, 500 mW sub-band, 1.2 MHz from the desk channel) at 22 dBm, the high-power amplifier (+8 dB over `node4-link.toml`). `RADIO_TX_POWER_DBM` is refused by the build and by `bench scenario check` on any other channel; the Compliance Engine grants it there only |

## Hardware

Checked against `bench boards` on 2026-10-12 at 16:35 UTC.

| Item | Needed | Today |
|---|---|---|
| nuna-node-04 (Node ID 4) at the remote site, flashed as C2 (forward) or C3 (reverse) | yes | connected, free |
| One board at the default site, as C3 (forward) or C2 (reverse): nuna-node-09 (Node ID 7) was used | yes | connected, free; nuna-node-06 (Node ID 1) and nuna-node-08 (Node ID 6) were held by another session, on 868.3 MHz |
| The antennas of both boards | yes | a cheap antenna on each NUCLEO board (Simon); how they are connected was not checked on site |

The distance between the two sites is 306 m (from the per-machine positions file, `bench boards --json`).
Hands on the bench: none during a run.
Simon power-cycled the Pi of nuna-node-09 at 15:14 UTC after the first attempt broke its flash (see the Fleet check of `spot-20261010-onboard-board.md`).

## Expected link

Free space over 306 m at 868.3 MHz, 14 dBm as configured in `Radio.SetTxConfig` (not measured at the antenna) and no antenna gain assumed: RSSI about -67 dBm, SNR about +50 dB (noise floor -117 dBm in 125 kHz with a 6 dB receiver).
The SF12 / BW125 limits are -137 dBm and -20 dB SNR (Semtech SX1261/2 datasheet figures), so on paper the margin is about 70 dB.
That is a ceiling: it knows no building, tree or antenna.

## Criteria

- [x] The RSSI and SNR of the Sync packets nuna-node-04 receives from the default site, and its margin to the SF12 limits (-137 dBm, -20 dB). At 22 dBm on 869.5 MHz (run of 19:13): 9 of 11 packets, RSSI -105 to -104 dBm, SNR -19 to -18 dB, so 1 dB of SNR margin for the worst packet and 2 dB for the median; at 14 dBm on 867.3 MHz nothing was heard (SNR about -26 dB expected, 6 dB under the limit). The RSSI margin to -137 dBm (32 dB) is not the margin: the RSSI is mostly noise at this SNR.
- [ ] The same for the reverse direction.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-12 14:44 | `node4-link.toml` (first version: C3 nuna-node-09, 868.3 MHz) | `3e12e0f-o954ffd` | invalid (bench fault: the flash of nuna-node-09 was cut) | none: the flash never completed | The Pi's OpenOCD lost the debug port in the middle of the flash (`Failed to write memory`, `Error connecting DP: cannot read IDR`); the board kept a partial image and looped on `CORE_SYNC stage=wait_cm0`. Restarting `openocd.service` did not help (it exits at start with the same error). Simon power-cycled that Pi at 15:14, and `bench flash --node 7=C3` repaired the board at 15:55. Run on a channel shared with another session's crash tests: not repeated that way. |
| 2026-10-12 16:13 | `node4-link.toml` (C3 nuna-node-06, 867.3 MHz) | `3e12e0f-d6435f6-o24d622` | invalid (bench fault: smoke window) | `tools/arclog/runs/20261012T161342Z-3e12e0f-d6435f6-o24d622/` | First run on its own channel: the override reached the radio (`SYNC_TX freq=867300000`, `SCAN freq=867300000` on nuna-node-04). Failed the smoke check at +30.0 s with nuna-node-04's BOOT lines at about +30 s: the window counted from the first flash, not from each board's. Fixed (`arclog/expect.py`, `test_expect.py`). After it, nuna-node-06 sent four Sync packets (cells 1 to 4) while nuna-node-04 was listening and nuna-node-04 logged none. |
| 2026-10-12 16:19 | `node4-link.toml --as 7` (C3 nuna-node-09, 867.3 MHz) | `3e12e0f-d6435f6-o24d622` | invalid (bench fault: smoke window) | `tools/arclog/runs/20261012T161959Z-3e12e0f-d6435f6-o24d622/` | The same race again: BOOT of nuna-node-04 at +30.3 s against a 30 s window from the first flash. |
| 2026-10-12 16:24 | `node4-link.toml --as 7` (C3 nuna-node-09, 867.3 MHz) | `3e12e0f-d6435f6-o24d622` | FAIL (no `RX_DONE` within 3 min of arming) | `tools/arclog/runs/20261012T162405Z-3e12e0f-d6435f6-o24d622/` | Valid run, smoke check passed (nuna-node-04 armed at +36.9 s, the first run with the fixed window). nuna-node-09 sent cells 0 to 4 on 867.3 MHz (16:24:27 to 16:25:46); nuna-node-04, scanning on 867.3 MHz from 16:24:42, received cells 1 to 4 (four packets) as nothing: no `RX_DONE`, no `RX_ERROR`, no `SYNC_RX`. The measurement is "nothing heard". |
| 2026-10-12 16:29 | `node4-link-reverse.toml --as 7` (C3 nuna-node-04, C2 nuna-node-09, 867.3 MHz) | `3e12e0f-d6435f6-o24d622` | FAIL (no `RX_DONE` within 3 min of arming) | `tools/arclog/runs/20261012T162948Z-3e12e0f-d6435f6-o24d622/` | Valid run. nuna-node-04 sent cells 0 to 4 on 867.3 MHz (16:30:09 to 16:31:29); nuna-node-09, scanning from 16:30:25, had cells 1 to 4 (four packets) and logged nothing. |
| 2026-10-12 19:13 | `node4-link-max-power.toml --as 7` (C3 nuna-node-09, C2 nuna-node-04, 869.5 MHz, 22 dBm) | `09d6e4b-d4f949e-oe675d3` | FAIL (9 of the 10 `RX_DONE` asked for in 21 min), valid: a measurement | `tools/arclog/runs/20261012T191303Z-09d6e4b-d4f949e-oe675d3/` | **nuna-node-04 hears the C3.** First `RX_DONE` at +40.8 s, then at +60.9, +100.9 (cell 3 at +80.9 missed), and one per 200 s phase from +220.9 to +1220.9 s. Links table (`bench.links.run_links` on the record): nuna-node-09 -> nuna-node-04, 9 heard of 11 sent after nuna-node-04's boot; RSSI -105 / -105 / -104 dBm, SNR -19 / -18 / -18 dB (min / median / max); SNR margin to -20 dB +1 dB for the worst packet, +2 for the median; distance 306 m; free space would give -59 dBm and +58 dB SNR; the place costs about 64 dB over free space. The Compliance Engine granted 22 dBm (no `TX_DENIED`). The smoke check passed (nuna-node-04 armed at +34.7 s). The 10th packet was one phase short of the 21 min: the count was a sample size, not a pass mark. |

## Findings

1. **At 14 dBm on 867.3 MHz nothing was heard in either direction; at 22 dBm on 869.5 MHz nuna-node-04 hears the C3.**
   12 packets sent while the receiver listened at 14 dBm (4 from nuna-node-06 and 4 from nuna-node-09 to nuna-node-04, 4 from nuna-node-04 to nuna-node-09), none heard.
   At 22 dBm, 9 of 11 (run of 19:13, Links table above).
2. **The two results agree with one link budget, with no need for another cause.** The place (indoor to indoor, a window, a cheap antenna on each board) costs about 64 dB over free space: measured signal about -123 dBm (RSSI -105 + SNR -18) against -59 dBm in free space at 22 dBm.
   The same link at 14 dBm gives about -131 dBm, an SNR near -26 dB, which is 6 dB under the SF12 limit of -20 dB: nothing can be decoded, which is what the three 14 dBm runs show.
   The 8 dB of the high-power amplifier are what brings the link from 6 dB under the limit to 1 to 2 dB over it.
3. **The link is marginal even at the maximum power.** The worst packet was 1 dB above the SF12 limit; 2 of 11 packets were not heard. Anything that costs another 2 dB (a person in the room, rain, a closed window) loses most of them.
   The in-channel power at the remote receiver was about -105 dBm, noise and interference (the thermal floor of the radio is about -117 dBm in 125 kHz with a 6 dB receiver): the band is noisy there, or the cheap antenna lets the receiver hear more of the neighbourhood than of the signal.
   What would help, in order of cost: a proper 868 MHz antenna at both ends (the dev boards' cheap antennas are a few dB down), the remote board at the window or outside, then the default-site board at a window. SF12 is already the longest range of the modem.
4. **The RF matching hypothesis is not tested.** The channel and the power changed together (867.3 MHz at 14 dBm, then 869.5 MHz at 22 dBm), and the first result is already explained by the power. A mismatch at 867.3 MHz or 869.5 MHz (both 1.0 to 1.2 MHz from 868.3 MHz) would cost a few dB of both transmit power and sensitivity.
   A close-range control settles it: two boards a metre apart, the same C3 and C2, the RSSI compared on 868.3, 867.3 and 869.5 MHz at the same power (14 dBm on the first two). It needs two free boards at the default site (the other session holds two of the three today).
5. **Before today nuna-node-04 and nuna-node-09 had never received anything on a Pi** (no `RX_DONE`, `RX_ERROR` or `RX_TIMEOUT` in their captures of 2026-10-10 and 12): only nuna-node-08, a C2 in another session's runs, receives, at -38 to -28 dBm from the others (a few metres, 868.3 MHz). The board that is now nuna-node-04 received 358 packets on 2026-10-02 and 03 as `com10`, at RSSI about 0 dBm (boards side by side): its receiver worked on 868.3 MHz, on a desk.
6. **The capture holds a reading at desk distance.** On 2026-10-12, 13:23 to 13:49 UTC, the packets of nuna-node-04 reached nuna-node-08 at -38 to -28 dBm, SNR 4 to 5 (868.3 MHz): both radios work at a few metres, and the capture at 14:50 showed none reaching a default-site board after 13:49, which fits a move of nuna-node-04 at that time (weak evidence: the listeners were locked and open only short windows; not noted by `bench note`; unverified).
7. **A bench bug found and fixed on the way**: the smoke check window ran from the start of the first flash for every board (30 s), so the second board of a run, flashed about 15 s later, had about 15 s to boot (it needs 15 s). Reproduced twice on the bench, then red/green in `test_expect.py`, then green on the bench (runs of 16:24 and 19:13).
8. **Two more found on the way**: `arclog` named a Pi node's capture `nuna-node-04-20261012.log` the node `nuna` (merge and report put every Pi node in one stream); fixed with a test. And the Compliance Engine was asked for a nominal 0 dBm while the radio ran at 14: it is now asked for the real power.
9. **Strays**: when this record was written, nuna-node-09 runs as a C3 and nuna-node-04 as a C2 on 869.5 MHz at 22 dBm (build `09d6e4b-d4f949e-oe675d3`): nuna-node-09 sends one Sync per 200 s, and so does nuna-node-04 once it is warm. It is a legal channel for that power, and 1.2 MHz from the desk channel; `bench boards` shows them as `on air 869.5 MHz`. Reflash them (`bench flash --node 7=C3 --node 4=C3`) to put them back on 868.3 MHz at 14 dBm.
