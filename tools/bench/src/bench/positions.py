"""positions.toml: where each board stands, keyed by its chip UID (issue #85).

A board moves between the desk and a Pi Node and gets a new Node ID or a new port, so its place belongs to
its UID. The file is per machine, like bench.toml ($BENCH_POSITIONS, else ~/.config/bench/positions.toml):
the repo is public and a site's coordinates are not for it. It is optional; a bench without it shows no place.

    [default]                  # where a board with no entry of its own stands
    place = "Main site"        # optional name
    lat = 55.0                 # degrees north, -90 to 90
    lon = 12.0                 # degrees east, -180 to 180

    [[board]]
    uid = "003400443232501420383543"
    place = "East end"         # optional
    lat = 55.0
    lon = 12.01
    alt = 20.5                 # optional, metres above sea level

From the places come the distance of every link, the free-space (or log-distance) loss a link should
have and the neighbours a board should hear, to be read next to the RSSI and SNR the run measured.
The radio constants mirror the firmware (SF12, BW125, 868.3 MHz, 14 dBm configured in subghz_phy_task.c);
the model is a first estimate: it knows no walls, no trees and no antenna gain.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

DEFAULT_FILE = Path(".config/bench/positions.toml")

EARTH_RADIUS_M = 6_371_008.8  #: IUGG mean radius
SPEED_OF_LIGHT = 299_792_458.0

#: The part of the budget kept back for fading, on top of the sensitivity, before a link counts as expected.
FADE_MARGIN_DB = 10.0

_UID_RE = re.compile(r"^[0-9a-f]{24}$")
_PLACE_KEYS = {"place", "lat", "lon", "alt"}
_BOARD_KEYS = _PLACE_KEYS | {"uid"}
_ALT_RANGE_M = (-500.0, 9000.0)


@dataclass(frozen=True)
class Place:
    lat: float
    lon: float
    alt: float | None = None
    name: str | None = None


@dataclass(frozen=True)
class Positions:
    default: Place | None = None
    boards: dict[str, Place] = field(default_factory=dict)  #: UID (24 lowercase hex) -> place

    def place_of(self, uid: str | None) -> Place | None:
        """The board's own place, else the default place, else None (also for an unknown UID)."""
        if uid is None:
            return self.default
        return self.boards.get(uid.lower(), self.default)


def _number(entry: dict, key: str, where: str, low: float, high: float, required: bool = True) -> float | None:
    if key not in entry:
        if required:
            raise ValueError(f"{where}: {key} is missing")
        return None
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{where}: {key} must be a number, got {value!r}")
    if not low <= value <= high:
        raise ValueError(f"{where}: {key} must be between {low:g} and {high:g}, got {value!r}")
    return float(value)


def _place(entry: dict, where: str, known: set[str]) -> Place:
    extra = sorted(set(entry) - known)
    if extra:
        raise ValueError(f"{where}: unknown key {', '.join(extra)} (known: {', '.join(sorted(known))})")
    name = entry.get("place")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValueError(f"{where}: place must be a name, got {name!r}")
    return Place(lat=_number(entry, "lat", where, -90.0, 90.0), lon=_number(entry, "lon", where, -180.0, 180.0),
                 alt=_number(entry, "alt", where, *_ALT_RANGE_M, required=False), name=name)


def parse_positions(text: str) -> Positions:
    data = tomllib.loads(text)
    unknown = sorted(set(data) - {"default", "board"})
    if unknown:
        raise ValueError(f"unknown table or key {', '.join(unknown)} (only [default] and [[board]] are known)")
    default = _place(data["default"], "default", _PLACE_KEYS) if "default" in data else None
    boards: dict[str, Place] = {}
    for i, entry in enumerate(data.get("board", []), 1):
        where = f"board #{i}"
        uid = entry.get("uid")
        if not isinstance(uid, str) or not _UID_RE.match(uid.lower()):
            raise ValueError(f"{where}: uid must be the 24 hex digits of the chip UID, got {uid!r}")
        uid = uid.lower()
        place = _place(entry, where, _BOARD_KEYS)
        if uid in boards:
            raise ValueError(f"{where}: uid {uid} is listed twice")
        boards[uid] = place
    return Positions(default, boards)


def load_positions(path: str | Path | None = None, env: Mapping[str, str] = os.environ,
                   home: Path | None = None) -> Positions:
    """This machine's positions. No file means no positions; a file named by $BENCH_POSITIONS must exist."""
    named = path or env.get("BENCH_POSITIONS")
    if named:
        file = Path(named)
        if not file.is_file():
            raise ValueError(f"BENCH_POSITIONS: {file} does not exist")
    else:
        file = (home or Path.home()) / DEFAULT_FILE
        if not file.is_file():
            return Positions()
    try:
        return parse_positions(file.read_text(encoding="utf-8"))
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{file}: {exc}") from exc


def link_distance_m(a: Place, b: Place) -> float:
    """Great-circle distance on the ground (haversine); with both altitudes known, the straight line."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi, dlam = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    ground = 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))
    if a.alt is None or b.alt is None:
        return ground
    return math.hypot(ground, b.alt - a.alt)


def free_space_loss_db(distance_m: float, freq_hz: float, exponent: float = 2.0) -> float:
    """Path loss of a link: free space at 1 m (20 log10(4 pi f / c)) plus 10 * exponent dB per decade.

    Exponent 2 is free space: 20 log10(d_km) + 20 log10(f_MHz) + 32.44. A distance under 1 m counts as 1 m.
    """
    at_one_metre = 20 * math.log10(4 * math.pi * freq_hz / SPEED_OF_LIGHT)
    return at_one_metre + 10 * exponent * math.log10(max(distance_m, 1.0))


@dataclass(frozen=True)
class RadioModel:
    """What the radio should do on a link. Defaults mirror the firmware's Sync link."""

    freq_hz: float = 868.3e6
    tx_power_dbm: float = 14.0          #: Radio.SetTxConfig in subghz_phy_task.c; not measured at the antenna
    antenna_gain_db: float = 0.0        #: both ends together; none assumed
    bandwidth_hz: float = 125_000.0
    noise_figure_db: float = 6.0        #: of the receiver, SX126x datasheet order of magnitude
    sensitivity_dbm: float = -137.0     #: SF12 / BW125, SX1262 datasheet
    snr_limit_db: float = -20.0         #: lowest SNR SF12 demodulates
    exponent: float = 2.0               #: 2 = free space; 2.7 to 3.5 for built-up ground

    @property
    def noise_floor_dbm(self) -> float:
        return -174.0 + 10 * math.log10(self.bandwidth_hz) + self.noise_figure_db

    def loss_db(self, distance_m: float) -> float:
        return free_space_loss_db(distance_m, self.freq_hz, self.exponent)

    def expected_rssi_dbm(self, distance_m: float) -> float:
        return self.tx_power_dbm + self.antenna_gain_db - self.loss_db(distance_m)

    def expected_snr_db(self, distance_m: float) -> float:
        return self.expected_rssi_dbm(distance_m) - self.noise_floor_dbm

    def rssi_margin_db(self, rssi_dbm: float) -> float:
        """How far above the sensitivity a measured RSSI stands: the fade the link could still take."""
        return rssi_dbm - self.sensitivity_dbm

    def snr_margin_db(self, snr_db: float) -> float:
        """How far above the SNR limit of the spreading factor a measured SNR stands."""
        return snr_db - self.snr_limit_db


@dataclass(frozen=True)
class Link:
    a: str
    b: str
    distance_m: float
    expected_rssi_dbm: float
    expected_snr_db: float

    @property
    def colocated(self) -> bool:
        return self.distance_m == 0.0


def links(positions: Positions, uids: list[str], model: RadioModel) -> list[Link]:
    """Every pair of the given boards that both have a place (their own or the default), in list order."""
    out: list[Link] = []
    for i, a in enumerate(uids):
        pa = positions.place_of(a)
        for b in uids[i + 1:]:
            pb = positions.place_of(b)
            if pa is None or pb is None:
                continue
            d = link_distance_m(pa, pb)
            out.append(Link(a, b, d, model.expected_rssi_dbm(d), model.expected_snr_db(d)))
    return out


def expected_neighbours(positions: Positions, uids: list[str], model: RadioModel,
                        fade_margin_db: float = FADE_MARGIN_DB) -> dict[str, list[str]]:
    """For each board the boards it should hear: expected RSSI above the sensitivity plus the fade margin."""
    near: dict[str, list[str]] = {u: [] for u in uids}
    for link in links(positions, uids, model):
        if link.expected_rssi_dbm >= model.sensitivity_dbm + fade_margin_db:
            near[link.a].append(link.b)
            near[link.b].append(link.a)
    return near


def unknown_boards(positions: Positions, known_uids: set[str]) -> list[str]:
    """The UIDs of positions that no board has (not in node_id.c): a typo, or a board that was never onboarded."""
    known = {u.lower() for u in known_uids}
    return [u for u in positions.boards if u not in known]
