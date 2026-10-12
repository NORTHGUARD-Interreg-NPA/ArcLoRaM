"""Radio channels of a run: the plan, the channel a run is on, its lease, and who is on the air (issue #85).

A board lease keeps two sessions off one board; it says nothing about the air. Two runs on disjoint boards
still hear each other on one channel, a C2 locks to whichever C3 it hears first, and an idle board keeps
sending its last image's Sync. So a run has a channel of its own:

- The plan (range, raster, spacing, default) is read from the firmware's own header,
  Common/Protocol/channel_plan.h: there is no second copy to keep in step.
- The channel of a run is its Build Override FREQ_DEFAULT_HZ, else the default. A value outside the plan is
  refused before anything is built. Its power is RADIO_TX_POWER_DBM, else 14 dBm; more than that is allowed
  on the high-power channel (869.5 MHz, 10 % duty cycle, 500 mW) only, as in the firmware's own #error.
- The channel is leased: exclusive on its own slot, shared on every plan slot closer than the spacing
  (ADR-0003 leases, ids `channel-<kHz>`). Two sessions therefore never share a channel and never sit closer
  than the spacing. Taking is never blocking; the refusal names who holds what.
- A session on code from before this, or a board left running its last image, takes no lease. The shared
  capture shows them anyway: a board's last SYNC_TX says which channel it is on, and `air_conflicts` names
  the boards, outside the run, that are on a channel closer than the spacing.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping

from arclog.model import Kind, parse_capture_line
from bench.lease import Held, Leases

class AirBusy(RuntimeError):
    """A command stopped because its channel is shared with a run of another session."""


PLAN_HEADER = Path("Common/Protocol/channel_plan.h")  #: relative to the repo root
OVERRIDE = "FREQ_DEFAULT_HZ"

#: A board whose last Sync is older than this is not counted as on the air (two DEV phases and a margin).
AIR_WINDOW = timedelta(minutes=20)
_TAIL_BYTES = 256 * 1024

_CONSTANTS = ("CHANNEL_MIN_HZ", "CHANNEL_MAX_HZ", "CHANNEL_RASTER_HZ", "CHANNEL_SPACING_HZ", "CHANNEL_DEFAULT_HZ",
              "CHANNEL_HIGH_POWER_HZ", "RADIO_TX_POWER_DEFAULT_DBM", "RADIO_TX_POWER_LIMIT_DBM",
              "RADIO_TX_POWER_MAX_DBM", "RADIO_TX_POWER_MIN_DBM")
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(" + "|".join(_CONSTANTS) + r")\s+\(?(-?\d+)[uUlL]*\)?\s*(?:/\*.*)?$", re.M)
_HZ_RE = re.compile(r"^\s*(\d+)(?:[uU][lL]{0,2}|[lL]{1,2}[uU]?)?\s*$")
_ID_RE = re.compile(r"^channel-(\d+)$")


@dataclass(frozen=True)
class ChannelPlan:
    min_hz: int
    max_hz: int
    raster_hz: int
    spacing_hz: int
    default_hz: int
    high_power_hz: int
    power_default_dbm: int
    power_limit_dbm: int
    power_max_dbm: int
    power_min_dbm: int

    def channels(self) -> list[int]:
        """Every channel of the plan, ascending: the raster from min to max, and the high-power channel."""
        return [*range(self.min_hz, self.max_hz + 1, self.raster_hz), self.high_power_hz]


def parse_plan(text: str) -> ChannelPlan:
    found = {m[1]: int(m[2]) for m in _DEFINE_RE.finditer(text)}
    missing = [c for c in _CONSTANTS if c not in found]
    if missing:
        raise ValueError(f"{PLAN_HEADER}: no #define of {', '.join(missing)}")
    return ChannelPlan(found["CHANNEL_MIN_HZ"], found["CHANNEL_MAX_HZ"], found["CHANNEL_RASTER_HZ"],
                       found["CHANNEL_SPACING_HZ"], found["CHANNEL_DEFAULT_HZ"], found["CHANNEL_HIGH_POWER_HZ"],
                       found["RADIO_TX_POWER_DEFAULT_DBM"], found["RADIO_TX_POWER_LIMIT_DBM"],
                       found["RADIO_TX_POWER_MAX_DBM"], found["RADIO_TX_POWER_MIN_DBM"])


def load_plan(repo: Path) -> ChannelPlan:
    return parse_plan((repo / PLAN_HEADER).read_text(encoding="utf-8"))


def mhz(hz: int) -> str:
    text = f"{hz / 1e6:.6f}".rstrip("0")
    return f"{text}0 MHz" if text.endswith(".") else f"{text} MHz"


def parse_hz(text: str) -> int:
    """A frequency as a Build Override writes it: decimal Hz with an optional C suffix (`867300000u`)."""
    m = _HZ_RE.match(text)
    if not m:
        raise ValueError(f"{text!r} is not a frequency in Hz (a decimal integer, as in 867300000u)")
    return int(m[1])


def channel_of(overrides: Mapping[str, str], plan: ChannelPlan) -> int:
    """The channel a build with these overrides is on; a channel outside the plan is refused."""
    if OVERRIDE not in overrides:
        return plan.default_hz
    hz = parse_hz(overrides[OVERRIDE])
    if hz == plan.high_power_hz:
        return hz
    if not plan.min_hz <= hz <= plan.max_hz:
        raise ValueError(f"{OVERRIDE}={overrides[OVERRIDE]}: {mhz(hz)} is outside the channel plan "
                         f"({mhz(plan.min_hz)} to {mhz(plan.max_hz)}, or {mhz(plan.high_power_hz)}; channel_plan.h)")
    if hz % plan.raster_hz:
        raise ValueError(f"{OVERRIDE}={overrides[OVERRIDE]}: {mhz(hz)} is not a multiple of "
                         f"{plan.raster_hz // 1000} kHz (channel_plan.h)")
    return hz


POWER_OVERRIDE = "RADIO_TX_POWER_DBM"
_DBM_RE = re.compile(r"^\s*\(?\s*(-?\d+)\s*\)?\s*$")


def power_of(overrides: Mapping[str, str], channel_hz: int, plan: ChannelPlan) -> int:
    """The power (dBm) a build with these overrides transmits at on this channel. More than the 25 mW limit
    is refused off the high-power channel, as the firmware's own header refuses it."""
    if POWER_OVERRIDE not in overrides:
        return plan.power_default_dbm
    m = _DBM_RE.match(overrides[POWER_OVERRIDE])
    if not m:
        raise ValueError(f"{POWER_OVERRIDE}={overrides[POWER_OVERRIDE]!r} is not a power in dBm (a whole number)")
    dbm = int(m[1])
    if not plan.power_min_dbm <= dbm <= plan.power_max_dbm:
        raise ValueError(f"{POWER_OVERRIDE}={dbm}: the board does {plan.power_min_dbm} to {plan.power_max_dbm} dBm "
                         f"(channel_plan.h)")
    if dbm > plan.power_limit_dbm and channel_hz != plan.high_power_hz:
        raise ValueError(f"{POWER_OVERRIDE}={dbm}: more than {plan.power_limit_dbm} dBm (25 mW) is allowed on "
                         f"{mhz(plan.high_power_hz)} only (10 % duty cycle, 500 mW sub-band); {mhz(channel_hz)} "
                         f"stops at {plan.power_limit_dbm} dBm")
    return dbm


# --- the lease -----------------------------------------------------------------------------------------------------

def _slot(hz: int) -> str:
    return f"channel-{hz // 1000}"


def channel_ids(hz: int, plan: ChannelPlan) -> tuple[str, list[str]]:
    """(the channel's own lease id, the ids of the plan slots closer than the spacing)."""
    return _slot(hz), [_slot(f) for f in plan.channels() if f != hz and abs(f - hz) < plan.spacing_hz]


@contextmanager
def hold_channel(leases: Leases, hz: int, plan: ChannelPlan) -> Iterator[None]:
    """Hold the channel for as long as the block runs; raises Held when another session is on it or closer
    than the spacing (see describe_busy)."""
    own, guard = channel_ids(hz, plan)
    with leases.hold(exclusive=[own], shared=guard):
        yield


def _hz_of(slot: str) -> int:
    return int(_ID_RE.match(slot)[1]) * 1000  # type: ignore[index]


def held_channels(leases: Leases, plan: ChannelPlan) -> dict[int, str]:
    """The channels another session holds now (not their guard slots), and who."""
    ids = [_slot(f) for f in plan.channels()]
    return {_hz_of(slot): who for slot, who in leases.exclusive_holders(ids).items()}


def describe_busy(busy: Mapping[str, str], plan: ChannelPlan) -> str:
    """The refusal of hold_channel in words: the channel a session is on, and the channels that are too close."""
    parts = []
    for slot, who in busy.items():
        hz = _hz_of(slot)
        if who.startswith(Leases.UNKNOWN_HOLDER):
            parts.append(f"{mhz(hz)} is closer than {plan.spacing_hz / 1e6:g} MHz to a channel "
                         f"another session is on")
        else:
            parts.append(f"{mhz(hz)} is held by {who}")
    return "; ".join(parts)


# --- who is on the air ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Transmitter:
    freq_hz: int
    last_tx: datetime


def _tail_lines(path: Path) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > _TAIL_BYTES else lines


def observe_transmitters(capture_dir: Path, nodes: list[str], now: datetime,
                         window: timedelta = AIR_WINDOW) -> dict[str, Transmitter]:
    """For each capture node that sent a Sync packet within the window: its channel and when it last sent.

    Read from the end of the shared capture files (UTC-day files), so it costs the same whatever the
    length of the day. No lease is needed to read."""
    since = now - window
    days = {since.astimezone(timezone.utc).date(), now.astimezone(timezone.utc).date()}
    out: dict[str, Transmitter] = {}
    for node in nodes:
        best: Transmitter | None = None
        for day in sorted(days):
            for text in _tail_lines(capture_dir / f"{node}-{day:%Y%m%d}.log"):
                if "SYNC_TX" not in text:
                    continue
                line = parse_capture_line(text, node=node)
                if line.kind is not Kind.ARCLOG or line.core != "0" or line.event != "SYNC_TX":
                    continue
                freq, when = line.int("freq"), line.host_time
                if freq is None or when is None or not since <= when <= now:
                    continue
                if best is None or when > best.last_tx:
                    best = Transmitter(freq, when)
        if best is not None:
            out[node] = best
    return out


def air_conflicts(channel_hz: int, on_air: Mapping[str, Transmitter], plan: ChannelPlan,
                  mine: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """The boards outside the run (`mine`) that transmit on a channel closer than the spacing to the run's."""
    return sorted(node for node, tx in on_air.items()
                  if node not in mine and abs(tx.freq_hz - channel_hz) < plan.spacing_hz)


def free_channels(plan: ChannelPlan, in_use: list[int]) -> list[int]:
    """The plan channels a megahertz or more from every channel in use: the default first, then the ordinary
    ones from the top down (the lower channels are the ones nobody uses yet), the high-power channel last
    (it is for a run that raises its power, not a place to hide)."""
    clear = [f for f in plan.channels() if all(abs(f - used) >= plan.spacing_hz for used in in_use)]
    return sorted(clear, key=lambda f: (f != plan.default_hz, f == plan.high_power_hz, -f))


@dataclass
class AirVerdict:
    refuse: list[str]   #: boards another session holds, on a channel closer than the spacing: the command stops
    warn: list[str]     #: idle boards on such a channel: the command goes on and says so


def judge_air(channel_hz: int, plan: ChannelPlan, on_air: Mapping[str, Transmitter], labels: Mapping[str, str],
              mine: set[str], held: Mapping[str, str]) -> AirVerdict:
    """What the boards outside the command do to its channel. A board held by another session is part of a
    run that would hear this one: refuse. An idle board still sends the Sync of its last image and cannot be
    silenced from here: warn."""
    refuse: list[str] = []
    warn: list[str] = []
    for node in air_conflicts(channel_hz, on_air, plan, mine):
        tx, label = on_air[node], labels.get(node, node)
        seen = f"{label} sent Sync on {mhz(tx.freq_hz)} at {tx.last_tx:%H:%M} UTC"
        near = f"within {plan.spacing_hz / 1e6:g} MHz of this command's {mhz(channel_hz)}"
        if node in held:
            refuse.append(f"{seen}, {near}, and another session holds it ({held[node]})")
        else:
            warn.append(f"{seen}, {near}: it is idle, not held by a session, and keeps sending its last image")
    return AirVerdict(refuse, warn)
