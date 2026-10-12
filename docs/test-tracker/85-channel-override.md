# Issue #85: board positions, the radio channel of a run, and the device-time merge

Purpose: acceptance. Issue: #85 (board positions per UID, and a merge of node logs by device time).

The issue grew two bench parts on 2026-10-12 (Simon: no separate issue): a radio channel per run, leased, and a smoke check window per board.
Everything else is host-level: a test per criterion, below.
The one bench criterion is the channel of a run.

## Scenarios

| Scenario | Proves or measures |
|---|---|
| `tools/bench/scenarios/channel-override.toml` (a template: node 0 is the board under test, `--as <Node ID>`) | A lone C3 built with `FREQ_DEFAULT_HZ=867300000u` sends its Sync on 867.3 MHz (`SYNC_TX freq=867300000`, then `TX_DONE`) and never on 868.3 MHz; no `TX_DENIED` |

## Hardware

Checked against `bench boards` on 2026-10-12 at 18:46 UTC.

| Item | Needed | Today |
|---|---|---|
| One Pi Node board as C3 (nuna-node-09, Node ID 7, was used) | yes | connected, free |

Hands on the bench: none.
A second session on 868.3 MHz is not needed for the verdict: the run takes the channel lease and the air check, and `forbid` covers the rest.

## Criteria

- [x] Host: the positions are read, validated and keyed by UID; a coordinate out of range, a duplicate UID and an unknown key are refused. `tools/bench/tests/test_positions.py` (a latitude or longitude out of range, a bool, NaN, a UID twice in any letter case, an unknown key or table, a missing coordinate).
- [x] Host: link distances and expected neighbours from a set of positions, with a worked example checked by hand. `test_positions.py`: one thousandth of a degree of latitude is 111.19 m, a degree of longitude at 60 north is 55 597 m, Pythagoras for an altitude, free space 91.22 dB at 1 km and 868.3 MHz, the expected neighbours of a set of places.
- [x] Host: `arclog merge --by device` on a recorded C3 + C2 capture (the eight packets above, kept as a test fixture) puts the TX_DONE and the RX_DONE of every packet within 5 ms, where the host order gives 69 to 216 ms, and marks the lines before the lock. `tools/arclog/tests/test_devicetime.py` and `test_cli.py` on the eight packets of 2026-10-04 (`tools/arclog/tests/data/c3-c2-20261004/`): every locked packet's TX_DONE and RX_DONE within 5 ms, where the host order gives up to 216 ms; the lines before the lock are marked `~`.
- [x] Host: a jump at `RTC_SET` is bridged by its `d=`, and a reboot ends the node's locked interval. `test_devicetime.py`: a jump adds to the lines before it, two jumps add up, a jump across the midnight wrap of `d` is bridged, a reboot or a drop out of WARM ends the interval, a reboot of the C3 starts a new axis.
- [x] `bench map` shows the placement when it is known and says nothing when it is not. `tools/bench/tests/test_viz.py` (`bench map`; `bench boards` prints the same).
- [x] The time axes are documented in the arclog README and `CONTEXT.md`. `CONTEXT.md` ("Time Axes"), `tools/arclog/README.md` (merge).
- [x] Host: the channel plan is read from `channel_plan.h`; `FREQ_DEFAULT_HZ` moves the default channel, the discovery channel and the fallback together, and a build with a channel outside the plan or off the raster does not compile; a scenario with one is refused (exit 3). `Tests/unit/test_channel_plan.c` (default, override, six values the build refuses), `tools/bench/tests/test_channels.py`, `bench scenario check`.
- [x] Host: two sessions on the same channel, or closer than 1 MHz, are refused with who holds what; channels 1 MHz apart coexist; a board another session holds, on a close channel, stops a command (`--share-air` goes on); an idle board is only said. `test_channels.py`, `test_claim_channel.py`, `test_lease.py`.
- [x] Host: the smoke check gives each flashed board its own window from the end of its flash. `tools/arclog/tests/test_expect.py`.
- [x] Host: the radio and the Compliance Engine's request take one power (`RADIO_TX_POWER_DBM`); more than 14 dBm builds on 869.5 MHz only (up to the board's 22 dBm), the engine grants it there (10 % duty cycle, up to 27 dBm) and refuses it on every other channel, and a scenario with it elsewhere is refused (exit 3). `Tests/unit/test_channel_plan.c` (the power of the radio and of the request is one number; five channel/power pairs the build refuses), `Tests/unit/test_compliance_engine.c` (22 dBm granted at 869.5 MHz and refused at 868.3 MHz, 27 dBm the limit, 10 % duty cycle of its own), `tools/bench/tests/test_channels.py` and `test_claim_channel.py` (`scenario check` exits 3).
- [x] Host: a run report has a Links table: per direction between two boards, the packets heard of sent, their RSSI and SNR, the SNR margin to the SF12 limit and, from the places, the distance, the expected RSSI and SNR and the loss over free space. `tools/arclog/tests/test_links.py` (sent, heard, RSSI and SNR from the recorded capture; a C3 is no receiver), `tools/bench/tests/test_links.py` (margin, distance, expected figures, loss over free space, a run folder), `tools/bench/tests/test_run.py` (the section in `report.md`).
- [ ] Bench: a run on 867.3 MHz sends its Sync on 867.3 MHz (`SYNC_TX freq=867300000`), none on 868.3 MHz, while another session works on 868.3 MHz. Met on its own by `channel-override.toml` (run of 18:47 below, PASS); not yet with another session on 868.3 MHz at the same time: that run had the bench to itself. The only run during such a session is the link run of 16:24 (`spot-20261012-node4-link.md`), whose `forbid` of a Sync on 868.3 MHz held but whose verdict was a FAIL for another expectation. To tick: a PASS run of `channel-override.toml` while another session's run is on 868.3 MHz.

## Runs

| Date (UTC) | Scenario | Build ID | Verdict | Record | Notes |
|---|---|---|---|---|---|
| 2026-10-12 18:47 | `channel-override.toml --as 7` | `3e12e0f-d86c82c-o24d622` | PASS | `tools/arclog/runs/20261012T184739Z-3e12e0f-d86c82c-o24d622/` | The C3 on nuna-node-09 armed at +17.4 s; `SYNC_TX freq=867300000` at +17.8 s and `TX_DONE` at +18.1 s; no `SYNC_TX` on 868.3 MHz. The run said `channel 867.3 MHz` and, from the shared capture, that nuna-node-04 (idle, not held, a C3 from an earlier run) was on 867.3 MHz too. Nobody else held a board at that time; the same override ran while a session worked on 868.3 MHz in the link run of 16:24, where the `forbid` of a Sync on 868.3 MHz held (`spot-20261012-node4-link.md`). |

## Notes

- The positions file is per machine (`~/.config/bench/positions.toml`, `$BENCH_POSITIONS`) and holds no coordinate of the repo: the tests use made-up coordinates.
- The firmware change is `Common/Protocol/channel_plan.h` (the channel plan and `FREQ_DEFAULT_HZ`), `freq_resolver.h` (the fallback and the discovery channel follow it) and the CM4 phase defaults in a USER CODE region of `app_subghz_phy.c`; both cores built with and without the override.
- The plan is 865.1 to 868.5 MHz on a 100 kHz raster, two sessions 1 MHz apart. The 1 MHz comes from the Semtech SX1261/2 figure of 88 dB of blocking immunity at a 1 MHz offset (a summary page); no figure was found for a closer offset, and the adjacent-channel leakage of the bench boards was not measured. If a run on a neighbouring channel ever disturbs another, this is the number to measure.
