"""The Pi Node link: how bench reads the UID, flashes and resets a board wired to a Raspberry Pi (ADR-0002).

The Pi runs OpenOCD (SWD over its GPIO header) and offers the debug probe as a GDB server on the
tailnet. bench drives it with CubeIDE's `arm-none-eabi-gdb` in batch mode, from the controller: the
ELF is read locally, nothing is copied to the Pi.

bench must never do anything to a board that cannot be undone by flashing it again, and the GDB
port is as powerful as the programmer: `monitor` passes any OpenOCD command (mass erase, option
bytes), `load FILE OFFSET` writes anywhere, `shell` and `python` run on the controller. So, as in
programmer.py, bench builds every command list itself and checks it token by token against a small
grammar (validate_gdb) before GDB runs; anything else raises Refused and nothing is sent. Images are
checked to write only inside their core's half of main flash (check_image) before they are named.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Callable, Mapping

from bench.config import Remote
from bench.programmer import FLASH_REGION, UID_ADDR, UID_SIZE, ProgrammerError, Refused, Runner, check_image
from bench.winpath import to_windows

#: `Probe.board` of a Pi Node in the probe list, next to the ST-LINK boards' names.
PI_NODE = "pi-node"

REMOTE_TIMEOUT_S = 30
GDB_TIMEOUT_S = 900
CUBEIDE_BASE = Path("/mnt/c/ST")
_GDB_GLOB = ("STM32CubeIDE_*/STM32CubeIDE/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.*/"
             "tools/bin/arm-none-eabi-gdb.exe")

_SETTINGS = ("set confirm off", "set pagination off")
_TIMEOUT_RE = re.compile(r"set remotetimeout (\d{1,3})")
_MDW_UID = f"monitor mdw 0x{UID_ADDR:08X} {UID_SIZE // 4}"
_PLAIN = ("monitor reset halt", "monitor reset run", _MDW_UID, "detach")
_ELF_PATH_RE = re.compile(r"^(?:[A-Za-z]:)?[A-Za-z0-9_./+~-]*[A-Za-z0-9_+~-]\.elf$")


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def validate_gdb(commands: list[str], remote: Remote) -> None:
    """Refuse any GDB command list outside the grammar below (each command is one -ex argument).

        set confirm off | set pagination off | set remotetimeout 1-120
        target extended-remote <host>:<gdb port>      the configured endpoint, once, before the rest
        monitor reset halt | monitor reset run
        monitor mdw 0x1FFF7590 3                      the chip UID, read only
        file <path>.elf                               chosen image (checked by check_image)
        load | compare-sections                       no argument, after a file command
        detach
    """
    target = f"target extended-remote {remote.host}:{remote.gdb}"
    connected = image = False
    for c in commands:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in c):
            raise Refused(f"GDB command with a control character: {c!r}")
        if c in _SETTINGS:
            continue
        m = _TIMEOUT_RE.fullmatch(c)
        if m:
            if not 1 <= int(m[1]) <= 120:
                raise Refused(f"remotetimeout must be 1-120 s: {c}")
            continue
        if c.startswith("target "):
            if c != target:
                raise Refused(f"only the configured endpoint may be connected ({target!r}): {c!r}")
            if connected:
                raise Refused("connect once per GDB session")
            connected = True
            continue
        if not connected:
            raise Refused(f"connect first ({target!r}): {c!r} comes before it")
        if c in _PLAIN:
            continue
        if c.startswith("file "):
            path = c[len("file "):]
            if not _ELF_PATH_RE.match(path) or ".." in path.split("/"):
                raise Refused(f"the image path is not one GDB can take safely (an .elf path of letters, "
                              f"digits and _ . / + ~ -, no spaces or quotes): {path!r}")
            image = True
            continue
        if c in ("load", "compare-sections"):
            if not image:
                raise Refused(f"{c!r} follows a file command that chose the image")
            continue
        raise Refused(f"GDB command {c!r} is not allowed (bench never erases, writes option bytes or OTP, "
                      "writes memory, or runs scripts through a Pi Node)")


# ---------------------------------------------------------------------------
# Finding GDB, running it
# ---------------------------------------------------------------------------


def find_gdb(env: Mapping[str, str] = os.environ, base: Path = CUBEIDE_BASE) -> str:
    """$BENCH_GDB, else the newest CubeIDE's arm-none-eabi-gdb.exe (Windows, through WSL interop)."""
    if env.get("BENCH_GDB"):
        return env["BENCH_GDB"]
    found = sorted(base.glob(_GDB_GLOB), key=lambda p: [int(n) for n in re.findall(r"\d+", str(p))])
    if not found:
        raise ProgrammerError(f"arm-none-eabi-gdb not found under {base}: set BENCH_GDB to its path")
    return str(found[-1])


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd="/mnt/c",
                           timeout=GDB_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, f"gdb did not finish within {GDB_TIMEOUT_S} s"
    return p.returncode, (p.stdout + p.stderr).replace("\r", "")


#: What GDB or OpenOCD print when a step failed. GDB's exit code only reflects the last command,
#: and `compare-sections` exits 0 on a mismatch, so the output is read as well.
_ERROR_RE = re.compile(
    r"Connection (?:refused|timed out|reset)|Remote communication error|Target disconnected|"
    r"Remote connection closed|Ignoring packet error|Cannot access memory|MIS-MATCHED|Load failed|"
    r"Error (?:erasing|writing|flashing)|Failed to (?:erase|write)|Target not examined|Examination failed|"
    r"No such file or directory|not in executable format|You can't do that when your target|"
    r"The program is not being run|^Error\b|^Undefined command", re.IGNORECASE | re.MULTILINE)
_UID_RE = re.compile(r"0x1fff7590:\s+([0-9a-f]{8})\s+([0-9a-f]{8})\s+([0-9a-f]{8})", re.IGNORECASE)


def _problems(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines() if _ERROR_RE.search(ln)]


class PiNodeLink:
    """One Pi Node: read the UID, flash both cores, reset, through its OpenOCD GDB server."""

    def __init__(self, remote: Remote, gdb: str | None = None, runner: Runner | None = None,
                 windows: Callable[[Path], str] | None = None) -> None:
        self.remote = remote
        self._gdb = gdb
        self.runner = runner or _run
        self._windows = windows

    @property
    def gdb(self) -> str:
        if self._gdb is None:
            self._gdb = find_gdb()
        return self._gdb

    def _image_path(self, path: Path) -> str:
        if self._windows:
            return self._windows(path)
        if self.gdb.lower().endswith(".exe") and str(path).startswith("/mnt/"):
            return to_windows(path).replace("\\", "/")
        return path.as_posix()

    def _session(self, *body: str) -> str:
        r = self.remote
        commands = [*_SETTINGS, f"set remotetimeout {REMOTE_TIMEOUT_S}",
                    f"target extended-remote {r.host}:{r.gdb}", *body, "detach"]
        validate_gdb(commands, r)
        argv = [self.gdb, "-batch", "-nx"]
        for c in commands:
            argv += ["-ex", c]
        code, out = self.runner(argv)
        problems = _problems(out)
        if code != 0 or problems:
            last = [ln.strip() for ln in out.splitlines() if ln.strip()][-2:]
            raise ProgrammerError(f"{r.name} ({r.host}:{r.gdb}): " + ("; ".join(dict.fromkeys(problems[:3]))
                                  or "; ".join(last) or f"gdb exit code {code}"))
        return out

    def _uid(self, out: str) -> tuple[int, int, int]:
        m = _UID_RE.search(out)
        if not m:
            raise ProgrammerError(f"{self.remote.name}: no UID in the GDB output")
        return int(m[1], 16), int(m[2], 16), int(m[3], 16)

    def read_uid(self) -> tuple[int, int, int]:
        """Chip UID over SWD, one memory read: the core is not halted and the board does not reboot
        (unlike the ST-LINK read, which connects under reset).
        An all-zero UID is no chip's: the core is held in reset and flash reads zero (#103, or a new
        board whose firmware stops SWD, reachable only under reset). The board is then reset-halted,
        read and let run, the way a flash starts."""
        uid = self._uid(self._session(_MDW_UID))
        if uid != (0, 0, 0):
            return uid
        uid = self._uid(self._session("monitor reset halt", _MDW_UID, "monitor reset run"))
        if uid == (0, 0, 0):
            raise ProgrammerError(f"{self.remote.name}: the UID reads zero even after a reset halt")
        return uid

    def flash(self, images: dict[str, Path]) -> str:
        """Write and verify both cores' images in one session, then reset and run."""
        if set(images) != set(FLASH_REGION):
            raise Refused(f"both cores are flashed together, from one build: got {sorted(images)}")
        body = ["monitor reset halt"]
        for core in ("CM0PLUS", "CM4"):  # the CM4 starts the CM0+: write it last
            check_image(images[core], core)
            body += [f"file {self._image_path(images[core])}", "load", "compare-sections"]
        out = self._session(*body, "monitor reset run")
        verified = len(re.findall(r"matched\.", out))
        if verified < len(images):
            raise ProgrammerError(f"{self.remote.name}: only {verified} section(s) verified; "
                                  "both images must verify")
        return out

    def reset(self) -> str:
        return self._session("monitor reset run")

    def log_reachable(self, timeout: float = 2.0) -> bool:
        """Whether the read-only UART log port answers; the GDB port is never probed (a connection
        to it is a debug session)."""
        try:
            socket.create_connection((self.remote.host, self.remote.log), timeout=timeout).close()
        except OSError:
            return False
        return True
