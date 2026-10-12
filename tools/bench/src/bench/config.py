"""bench.toml: where bench looks for Pi Nodes (ADR-0002).

The files list endpoints, not boards: a board is still recognised by its UID against
Common/Protocol/node_id.c, and its class is the scenario's. There are two, same format:
tools/bench/pi-nodes.toml is the fleet shared through the repo (host names only: the repo is
public), and the per-machine file ($BENCH_CONFIG, else ~/.config/bench/bench.toml) adds nodes
or overrides one by name (to pin an address). Every worktree sees the same remotes.

    [[remote]]
    name = "nuna-node-01"     # capture node and display name (default: host)
    host = "nuna-node-01"     # MagicDNS name or tailnet address
    gdb  = 3333               # OpenOCD GDB port of the M4 (default 3333)
    log  = 4000               # UART log port (default 4000)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

DEFAULT_CONFIG = Path(".config/bench/bench.toml")
FLEET_FILE = Path("tools/bench/pi-nodes.toml")  #: relative to the repo root

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_KEYS = {"name", "host", "gdb", "log"}


@dataclass(frozen=True)
class Remote:
    """One Pi Node: a Raspberry Pi with OpenOCD (GDB) and the UART log server, on the tailnet."""

    name: str
    host: str
    gdb: int = 3333
    log: int = 4000

    @property
    def log_url(self) -> str:
        """The capture port of this node; the path is its capture node name (arclog ignores it)."""
        return f"tcp://{self.host}:{self.log}/{self.name}"


def _port(entry: dict, key: str, default: int, where: str) -> int:
    value = entry.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise ValueError(f"{where}: {key} must be a port number (1-65535), got {value!r}")
    return value


def parse_config(text: str) -> list[Remote]:
    data = tomllib.loads(text)
    unknown = sorted(set(data) - {"remote"})
    if unknown:
        raise ValueError(f"unknown table or key {', '.join(unknown)} (only [[remote]] is known)")
    remotes: list[Remote] = []
    for i, entry in enumerate(data.get("remote", []), 1):
        where = f"remote #{i}"
        extra = sorted(set(entry) - _KEYS)
        if extra:
            raise ValueError(f"{where}: unknown key {', '.join(extra)} (known: {', '.join(sorted(_KEYS))})")
        host = entry.get("host")
        if not isinstance(host, str) or not _HOST_RE.match(host):
            raise ValueError(f"{where}: host must be a host name or address, got {host!r}")
        name = entry.get("name", host)
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise ValueError(f"{where}: name must be lowercase letters, digits and '-' (it names the "
                             f"capture files), got {name!r}; set name = ... when host is an address")
        if any(r.name == name for r in remotes):
            raise ValueError(f"{where}: name {name!r} is listed twice")
        remotes.append(Remote(name, host, _port(entry, "gdb", 3333, where), _port(entry, "log", 4000, where)))
    return remotes


def _read(file: Path) -> list[Remote]:
    try:
        return parse_config(file.read_text(encoding="utf-8"))
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{file}: {exc}") from exc


def load_remotes(path: str | Path | None = None, env: Mapping[str, str] = os.environ,
                 home: Path | None = None, fleet: Path | None = None) -> list[Remote]:
    """The configured Pi Nodes: the shared fleet file first, then this machine's file on top of it
    (same name: the machine's entry wins, to pin an address; new name: appended).
    No machine file means the fleet alone; a file named by $BENCH_CONFIG must exist."""
    remotes: dict[str, Remote] = {}
    if fleet is not None and fleet.is_file():
        remotes.update({r.name: r for r in _read(fleet)})
    named = path or env.get("BENCH_CONFIG")
    if named:
        file = Path(named)
        if not file.is_file():
            raise ValueError(f"BENCH_CONFIG: {file} does not exist")
    else:
        file = (home or Path.home()) / DEFAULT_CONFIG
    if file.is_file():
        remotes.update({r.name: r for r in _read(file)})
    return list(remotes.values())
