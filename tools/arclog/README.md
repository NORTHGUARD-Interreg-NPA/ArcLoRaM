# arclog

Host tool for ArcLog, the structured trace format of the ArcLoRaM firmware.
It records the serial output of each node, classifies every line, merges several nodes on one timeline and produces the sync run report for issue #21.

The line format and the event vocabulary are defined in `Common/Log/arclog.h` and documented in `CONTEXT.md` (ArcLog).

## Setup

The tool needs Python 3.10+ and [uv](https://docs.astral.sh/uv/).
It works the same on Linux, WSL and Windows.

```sh
cd tools/arclog
uv sync            # creates .venv with pyserial (and pytest for development)
uv run arclog --help
```

Without uv: `python -m pip install -e .` then run `arclog` (or `python -m arclog`).

### Serial ports

- Windows: ports are `COM3`, `COM5`, ... (Device Manager, "Ports (COM & LPT)").
- Linux: `/dev/ttyUSB0`, `/dev/ttyACM0`, ... (`ls /dev/tty{USB,ACM}*`); your user must be in the `dialout` group.
- WSL2: USB devices are not visible until attached from Windows with [usbipd-win](https://github.com/dorssel/usbipd-win):
  `usbipd list`, then `usbipd bind --busid <id>` (admin, once) and `usbipd attach --wsl --busid <id>`.
  For months-long captures, prefer running the tool natively on Windows: no attach step to repeat after replugs or reboots.

The trace UART is 9600 8N1 (`--baud` to change).

## Commands

### capture

Record one or more nodes, one serial port per node, in one process.
Every line is stored verbatim, prefixed with its host UTC receive time.
Files rotate at UTC midnight (`<node>-YYYYMMDD.log`).
Each port is read on its own and reopened automatically after a USB replug or a board reset, without holding back the others.

Next to them, `marks-YYYYMMDD.log` gets one line every 10 s of the host's monotonic clock:
the UTC time, the monotonic reading (`mono=`), and for every node `up` or `down` with the UTC time of its last change (`c3=up@2026-10-07T14:00:01.123456Z`).
A port starts `down` and goes `up` when its reader is connected, so a port that never connected shows as down since the start.
The marks are a separate file because every tool reads `<UTC>\t<line>` in the node files, and a third column would break them.
They tell what the node files cannot: a port down is a capture gap and not a quiet node, a step of UTC against `mono` is the time service correcting the host, and a stretch without marks is a host that was not running.

```sh
uv run arclog capture --port COM5 --node c3 --out runs/2026-09-25
uv run arclog capture --port COM6 --node c3 --port COM8 --node c2 --port COM9 --node c2b --out runs/2026-09-25 --level M
```

Give one `--node` per `--port`, in the same order.
With several ports, the terminal echo shows the node of each line.
The echo accepts the same filters as `view`; the files always keep everything.

### view

Show capture files, or a port live, classified and filtered.

```sh
uv run arclog view runs/2026-09-25/c2-20260925.log --mod Y,M
uv run arclog view --port COM6 --level M
uv run arclog view c2.log --event SYNC_RX,CLK,RTC_SET
```

| Filter | Meaning |
|---|---|
| `--core 0,4` | CM0+ / CM4 |
| `--mod T,M,Y,R,X,S,P` | TDMA, MAC, Sync, Radio, MbMux, System, Power |
| `--level L` | most verbose level shown: `A` < `L` < `M` < `H` |
| `--event SYNC_RX,CLK` | event names |
| `--grep text` | substring |
| `--no-legacy`, `--no-raw` | hide unconverted ST lines / unparsed lines |

Line kinds:

- **ArcLog** lines are shown with their fields.
- **LEGACY** lines have a device timestamp but free text: ST-generated traces outside CubeMX USER CODE regions, which cannot be converted.
- **RAW** lines are anything else.

Nothing is hidden unless you filter it.

Health notes are appended in the output:

- `!! N line(s) lost` when the per-core sequence number jumps (trace FIFO overflow on either core); it is reported even when the lost lines' neighbour is filtered out;
- `!! <schema problem>` when a line does not match the event table (firmware and tool out of step).

### merge

Interleave several nodes by host time.
Each received Sync packet (`SYNC_RX`) is matched to the transmission it came from by its protocol key (epoch, phase, cell), independent of the host clock.

```sh
uv run arclog merge runs/2026-09-25/*.log --mod Y,R
```

### report

Sync run report: acquisitions, drops to CLOCK_COLD, tier decisions, error statistics, drift in ppm, preamble-detection diagnostics, cross-node offsets and trace health.
Writes Markdown plus a CSV with one row per received Sync packet.

```sh
uv run arclog report runs/2026-09-25/*.log -o docs/experiments/2026-09-25-c3-c2.md --title "C3-C2 bench, run 1"
```

Daily files of the same node are concatenated automatically.

The trace health table gives each node's Node ID from its CM0+ `BOOT` line.
A board missing from the Node ID table boots with `id=0`; `view`, `merge` and `report` flag that `BOOT` line with the entry to add to `Common/Protocol/node_id.c`.

### expect

Decide a run from the capture files: exit `0` pass, `1` fail, `2` timeout (`3` for an invalid expect file).
The run starts at `--since` (the flash); `--follow` keeps reading as the files grow, until a verdict.
Every step is printed as it happens (`ok ...`, `note ...`), then `PASS`, `FAIL <reason>` or `TIMEOUT missing: ...`.

```sh
uv run arclog expect c2-sync.toml --dir runs/2026-09-29 --since 2026-09-29T10:00:00Z --follow
```

The expect file (TOML) names the nodes by their capture name and what must happen:

```toml
timeout = "8m"
build = "a1b2c3d-dirty"   # Build ID every flashed node must boot

[nodes.com9]
cls = "C2"                # optional: class the node must boot as
reboots = 0               # optional: reboots allowed (scheduled resets)

[nodes.com8]
flashed = false           # the peer, not reflashed: no arming, expectations only

[[expect]]
node = "com9"
event = "CLK"
where = { to = "WARM" }
within = "7m"             # from the run being armed

[[forbid]]
event = "TX_LATE"
```

A flashed node is armed when both cores have logged `BOOT` with the expected Build ID; lines of the old image before that are ignored, and a `BOOT` of another build before arming is waited through.
The Smoke Check is always on (`smoke = false` to disable): within `smoke_window` (30 s) each flashed node is armed and its CM4 logs `CORE_SYNC stage=linked`.
From its first `BOOT` on, a node fails the run on lost lines, a schema problem, an unexpected reboot, a CM0+ rebooting alone, a forbidden event, or a `BOOT` of another build.
A run passes once every expectation is met and `min_duration` (default: the smoke window) has elapsed.
The full format is in `src/arclog/expect.py`.

Files are followed by polling (`--poll`, 1 s), not change notifications, which arrive late for files written from Windows over the WSL share.
Deadlines use the host clock of the machine running `expect`, compared with the capture's receive times.

## Development

```sh
uv run pytest
```

`tests/test_schema.py` scans the firmware sources: every `ARCLOG()` event, module and key must be in `src/arclog/schema.py`, and no format may use a length modifier (`%lu`), which the firmware's formatter does not support.
When you add or change an event in the firmware, update `schema.py` in the same commit.
