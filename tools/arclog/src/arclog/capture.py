"""Serial capture: every received line is stored verbatim with its host UTC time.

One process captures any number of ports, one node per port. Files are named
<node>-<YYYYMMDD>.log (UTC date) inside the output directory and rotate at
UTC midnight, so a months-long run is a directory of daily files. Each port
is read by its own thread and reopened automatically when it disappears (USB
replug, board reset), independently of the others, which matters for
unattended runs. All lines are written by the calling thread, stamped with
the one host clock at receive time.
"""

from __future__ import annotations

import queue
import re
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, TextIO
from urllib.parse import urlsplit

from arclog.marks import Marks
from arclog.model import Line, format_host_time, parse_line

DEFAULT_BAUD = 9600
RECONNECT_S = 2.0


def serial_lines(port: str, baud: int = DEFAULT_BAUD, reconnect: bool = True,
                 log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
                 duration_s: float | None = None,
                 on_state: Callable[[bool], None] = lambda up: None,
                 ) -> Iterator[tuple[datetime, str]]:
    """Yield (host UTC time, line) from a serial port, reconnecting on errors.

    Stops after duration_s seconds when given, otherwise runs until interrupted.
    `on_state(True)` is called once the port is open, `on_state(False)` at every error.
    """
    import serial  # pyserial; imported here so offline commands do not need it

    deadline = time.monotonic() + duration_s if duration_s else None
    down = False  # an outage is logged once, not at every retry
    while True:
        try:
            with serial.Serial(port, baud, timeout=1.0) as ser:
                down = False
                on_state(True)
                log(f"arclog: listening on {port} @ {baud}")
                buf = bytearray()
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        return
                    chunk = ser.read(ser.in_waiting or 1)
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    while True:
                        nl = buf.find(b"\n")
                        if nl < 0:
                            break
                        raw = bytes(buf[:nl]).decode("ascii", errors="replace").rstrip("\r")
                        del buf[: nl + 1]
                        if raw.strip():
                            yield datetime.now(timezone.utc), raw
        except (serial.SerialException, OSError) as exc:
            on_state(False)
            if not reconnect:
                raise
            if deadline is not None and time.monotonic() >= deadline:
                return
            if not down:
                log(f"arclog: {port} unavailable ({exc}); retrying every {RECONNECT_S:.0f} s")
                down = True
            time.sleep(RECONNECT_S)


#: What a Pi Node's log server (Nuna-Systems/Pi-node) puts before every line: its own receive time,
#: in the Pi's local time with no zone. The capture drops it and stamps the line with the host UTC
#: clock like any serial line: a zone-less stamp repeats an hour at the autumn clock change.
_PI_STAMP_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)? \| ?")


def parse_tcp_url(url: str) -> tuple[str, int]:
    """'tcp://host:port[/label]' -> (host, port). The label names the node for the caller; it is ignored here."""
    parts = urlsplit(url)
    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        host = port = None
    if parts.scheme != "tcp" or not host or not port or not 1 <= port <= 65535:
        raise ValueError(f"not a tcp:// port with a host and a port (tcp://host:4000/name): {url!r}")
    return host, port


def _keepalive(sock: socket.socket) -> None:
    """Detect a link that died without a close (Wi-Fi, relay): a quiet board must not look alive."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    try:
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):  # Windows: on, idle 30 s, interval 10 s
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30_000, 10_000))
        else:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except (AttributeError, OSError):
        pass


def tcp_lines(url: str, reconnect: bool = True,
              log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
              duration_s: float | None = None,
              on_state: Callable[[bool], None] = lambda up: None) -> Iterator[tuple[datetime, str]]:
    """Yield (host UTC time, line) from a Pi Node's log server, reconnecting on errors.

    Same contract as serial_lines, `on_state` included. Lines the server sent while this reader was away are not
    replayed: the live stream keeps nothing (a recorder on the Pi is the way to not lose them).
    """
    host, port = parse_tcp_url(url)
    deadline = time.monotonic() + duration_s if duration_s else None
    down = False
    while True:
        try:
            with socket.create_connection((host, port), timeout=5.0) as sock:
                _keepalive(sock)
                sock.settimeout(1.0)
                down = False
                on_state(True)
                log(f"arclog: listening on {url}")
                buf = bytearray()
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        return
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise ConnectionError(f"{host}:{port} closed the connection")
                    buf.extend(chunk)
                    while True:
                        nl = buf.find(b"\n")
                        if nl < 0:
                            break
                        raw = _PI_STAMP_RE.sub("", bytes(buf[:nl]).decode("utf-8", errors="replace").rstrip("\r"))
                        del buf[: nl + 1]
                        if raw.strip():
                            yield datetime.now(timezone.utc), raw
        except OSError as exc:
            on_state(False)
            if not reconnect:
                raise
            if deadline is not None and time.monotonic() >= deadline:
                return
            if not down:
                log(f"arclog: {url} unavailable ({exc}); retrying every {RECONNECT_S:.0f} s")
                down = True
            time.sleep(RECONNECT_S)


class DailyWriter:
    """Append '<host UTC>\\t<line>' records to <dir>/<node>-<YYYYMMDD>.log."""

    def __init__(self, out_dir: Path, node: str) -> None:
        self.out_dir = out_dir
        self.node = node
        self._date = ""
        self._file: TextIO | None = None
        out_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, t: datetime) -> Path:
        return self.out_dir / f"{self.node}-{t.strftime('%Y%m%d')}.log"

    def write(self, t: datetime, raw: str) -> None:
        date = t.strftime("%Y%m%d")
        if date != self._date:
            self.close()
            self._file = self.path_for(t).open("a", encoding="utf-8", newline="\n")
            self._date = date
        assert self._file is not None
        self._file.write(f"{format_host_time(t)}\t{raw}\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


LineSource = Callable[[str], Iterator[tuple[datetime, str]]]
"""Yields (host UTC time, line) for a port; serial_lines in production."""


def _reader(port: str, node: str, source: LineSource, out: queue.Queue) -> None:
    try:
        for t, raw in source(port):
            out.put((node, t, raw))
    except BaseException as exc:  # reported by the writing thread
        out.put((node, None, exc))
    finally:
        out.put((node, None, None))


def capture(ports: list[tuple[str, str]], out_dir: Path, baud: int = DEFAULT_BAUD,
            on_line: Callable[[Line], None] | None = None,
            duration_s: float | None = None,
            source: LineSource | None = None,
            marks: Marks | None = None) -> None:
    """Capture (port, node) pairs until interrupted (Ctrl+C) or for duration_s seconds.

    Marks (marks-YYYYMMDD.log) are written next to the daily files. A custom `source` does not report its
    ports' state, so the marks show them down.
    """
    ports_seen = [p for p, _ in ports]
    nodes_seen = [n for _, n in ports]
    if not ports:
        raise ValueError("no port to capture")
    if len(set(ports_seen)) != len(ports_seen):
        raise ValueError(f"port given twice: {ports_seen}")
    if len(set(nodes_seen)) != len(nodes_seen):
        raise ValueError(f"node name given twice (would share a file): {nodes_seen}")
    marks = marks or Marks(out_dir, nodes_seen)
    if source is None:
        node_of = dict(ports)

        def source(port: str) -> Iterator[tuple[datetime, str]]:
            def on_state(up: bool) -> None:
                (marks.port_up if up else marks.port_down)(node_of[port])
            if port.startswith("tcp://"):
                return tcp_lines(port, duration_s=duration_s, on_state=on_state)
            return serial_lines(port, baud, duration_s=duration_s, on_state=on_state)

    lines: queue.Queue = queue.Queue()
    writers = {node: DailyWriter(out_dir, node) for _, node in ports}
    for port, node in ports:
        threading.Thread(target=_reader, args=(port, node, source, lines),
                         name=f"arclog-{port}", daemon=True).start()
    running = len(ports)
    try:
        while running:
            marks.tick()
            try:
                # A timeout keeps Ctrl+C responsive on Windows.
                node, t, item = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if t is None:
                if isinstance(item, BaseException):
                    raise item
                running -= 1
                continue
            writers[node].write(t, item)
            if on_line is not None:
                on_line(parse_line(item, node=node, host_time=t))
    except KeyboardInterrupt:
        pass
    finally:
        for w in writers.values():
            w.close()
        marks.close()
