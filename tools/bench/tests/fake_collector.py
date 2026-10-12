"""A fake log collector for the host tests: a local http.server that answers like the real one.

What it copies from the real collector (read from its root page and from its answers on 2026-10-08):
- it stores lines in the order they arrive, and gives each an id `<node number>:<generation>:<offset>`;
- `/logs` answers its newest `limit` lines of a time window (`since`, `until`) when there is no `after`, and,
  with `after` = a cursor, the oldest `limit` lines stored after it, per node; both are sorted by the Pi's time;
- `truncated`, and `next` = the cursor to ask next (the highest id returned, or where the store ends);
- `limit` has a ceiling (`cap`), whatever the client asks;
- a line recovered after a reconnect is stored when it is recovered: later than the lines that came live, with an
  older Pi time, so its id is above theirs;
- `/nodes` says per node what is connected, the oldest line, the stored size.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc


@dataclass
class Stored:
    id: str
    offset: int
    when: datetime          # the Pi's time
    source: str
    line: str


def node_number(name: str) -> int:
    return int(name.rsplit("-", 1)[1])


def parse_time(text: str) -> datetime:
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=PARIS)


class FakeCollector:
    def __init__(self, nodes: list[str], cap: int = 10000, next_is_last_by_time: bool = False) -> None:
        self.cap = cap
        self.next_is_last_by_time = next_is_last_by_time
        self.names = list(nodes)
        self.generation = 1
        self.store: dict[str, list[Stored]] = {n: [] for n in nodes}
        self.offset: dict[str, int] = {n: 0 for n in nodes}
        self.status: dict[str, dict] = {n: {"connected": True, "connected_since": "2026-10-01T00:00:00+00:00",
                                            "pi_ntp": "synced", "clock_lag_s": 0.05, "silent_for_s": 3,
                                            "last_error": None, "recovered_from_replay": 0,
                                            "max_bytes": 104857600} for n in nodes}
        self.requests: list[str] = []
        self.fail_logs_after: int | None = None   # answer 500 to /logs once this many have been served
        self._logs_served = 0
        self._server: ThreadingHTTPServer | None = None

    # -- what the Pis send ------------------------------------------------------------------------------------

    def add(self, name: str, when: datetime, line: str, source: str = "uart") -> None:
        """Store a line as it arrives now (after everything stored before)."""
        self.offset[name] += len(line) + 100
        number = node_number(name)
        self.store[name].append(Stored(f"{number}:{self.generation}:{self.offset[name]}", self.offset[name],
                                       when.astimezone(PARIS), source, line))

    def add_all(self, name: str, lines: list[tuple[datetime, str]]) -> None:
        for when, text in lines:
            self.add(name, when, text)

    def drop_oldest(self, name: str, count: int) -> None:
        """The collector's retention: the oldest lines are gone."""
        del self.store[name][:count]

    def reset(self, name: str) -> None:
        """The storage starts again from nothing (a restart that lost it): its offsets start over."""
        self.store[name] = []
        self.offset[name] = 0

    # -- the server -------------------------------------------------------------------------------------------

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                fake.requests.append(self.path)
                url = urlparse(self.path)
                params = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == "/nodes":
                    body, code = fake.nodes(), 200
                elif url.path == "/logs":
                    fake._logs_served += 1
                    if fake.fail_logs_after is not None and fake._logs_served > fake.fail_logs_after:
                        body, code = {"error": "boom"}, 500
                    else:
                        body, code = fake.logs(params), 200
                else:
                    body, code = {"error": "not found"}, 404
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # -- the answers ------------------------------------------------------------------------------------------

    def nodes(self) -> dict:
        out = {}
        for name in self.names:
            lines = self.store[name]
            entry = dict(self.status[name])
            whens = [s.when for s in lines]
            entry["oldest_line_at"] = min(whens).isoformat() if whens else None
            entry["last_line_at"] = max(whens).isoformat() if whens else None
            entry["stored_bytes"] = self.offset[name]
            out[name] = entry
        return out

    def logs(self, params: dict[str, str]) -> dict:
        names = [self.resolve(n) for n in params["nodes"].split(",")]
        assert len(names) == 1, "the fake answers one node per call"
        name = names[0]
        limit = min(int(params.get("limit", 10000)), self.cap)
        excluded = set(params.get("exclude", "").split(","))
        rows = [s for s in self.store[name] if s.source not in excluded]
        cursor = params.get("after")
        if cursor and cursor.count(":") == 2:
            generation, offset = (int(x) for x in cursor.split(":")[1:])
            rows = [s for s in rows if s.offset > offset]            # strictly after, in arrival order
            chosen = rows[:limit]                                    # the OLDEST first
            truncated = len(rows) > limit
        else:
            if "since" in params:
                rows = [s for s in rows if s.when >= parse_time(params["since"])]
            if cursor:
                rows = [s for s in rows if s.when > parse_time(cursor)]
            if "until" in params:
                rows = [s for s in rows if s.when < parse_time(params["until"])]  # exclusive, as the real one
            rows.sort(key=lambda s: (s.when, s.offset))
            chosen = rows[-limit:] if limit else []                  # the NEWEST limit
            truncated = len(rows) > limit
        chosen = sorted(chosen, key=lambda s: (s.when, s.offset))
        if self.next_is_last_by_time and chosen:
            high = chosen[-1].id
        elif chosen:
            high = max(chosen, key=lambda s: s.offset).id
        else:
            high = f"{node_number(name)}:{self.generation}:{self.offset[name]}"
        return {"since": params.get("since"), "until": params.get("until"), "nodes": [name], "no_data_nodes": [],
                "truncated": truncated, "count": len(chosen), "next": high,
                "lines": [{"ts": s.when.isoformat(timespec="microseconds"), "node": name, "line": s.line,
                           "source": s.source, "pi_time": s.when.replace(tzinfo=None).isoformat(sep=" "),
                           "received": s.when.astimezone(UTC).isoformat(), "lag_s": 0.1, "id": s.id}
                          for s in chosen]}

    def resolve(self, text: str) -> str:
        if text in self.store:
            return text
        for name in self.names:
            if node_number(name) == int(text):
                return name
        raise KeyError(text)


# ---------------------------------------------------------------------------
# A trace to put in it
# ---------------------------------------------------------------------------


def dev_time(t: datetime) -> str:
    return t.strftime("%y%m%dT%H%M%S.") + f"{t.microsecond // 100:04d}"


class Trace:
    """The device lines of one node as the firmware prints them, with each core's sequence number."""

    def __init__(self, uid: str = "0026001a3232501420383543") -> None:
        self.uid = uid
        self.seq = {"0": 0, "4": 0}
        self.rows: list[tuple[datetime, str]] = []

    def emit(self, t: datetime, core: str, mod: str, lvl: str, event: str, body: str = "") -> None:
        seq = 0 if event == "BOOT" else (self.seq[core] + 1) % 256
        self.seq[core] = seq
        self.rows.append((t, f"{dev_time(t)} {core}{mod} {lvl} #{seq:02x} {event}" + (f" {body}" if body else "")))

    def boot(self, t: datetime, build: str, cls: str = "C2", node_id: int = 2) -> None:
        self.emit(t, "4", "S", "A", "BOOT", f"build={build}")
        self.emit(t + timedelta(milliseconds=100), "4", "X", "M", "CORE_SYNC", "stage=linked")
        self.emit(t + timedelta(milliseconds=300), "0", "S", "A", "BOOT",
                  f"cls={cls} id={node_id} uid={self.uid} fw=1.5.0 build={build} sync=DEV")

    def slot(self, t: datetime, n: int, with_window: bool = True) -> None:
        """One 20 s slot: the lines a node logs per slot in a soak."""
        self.emit(t, "0", "T", "H", "SLOT", f"ph=1 ty=SYNC ce={n % 1000} sl=0 pos=CELL dec=RX wake=0 nom={n * 20032}")
        if with_window:
            self.emit(t + timedelta(milliseconds=80), "0", "T", "H", "RX_WIN",
                      f"last={n * 20032 + 1640} cap={n * 20032 + 2632} g=10 win=18")
        self.emit(t + timedelta(seconds=1.9), "0", "R", "H", "RX_TIMEOUT", "pre=0")
        if n % 64 == 0:
            self.emit(t + timedelta(seconds=2), "0", "P", "M", "STOP2_WAKES", f"n={n}")
        self.emit(t + timedelta(seconds=10), "0", "R", "M", "TX_DONE",
                  f"sz=10 toa=992 end={n * 20032 + 10000} start={n * 20032 + 9008}")


def soak(start: datetime, hours: float, build: str, cls: str = "C2", node_id: int = 2, boot_after_s: float = 10,
         with_window: bool = True) -> list[tuple[datetime, str]]:
    """A node that boots `boot_after_s` after `start` and then logs a slot every 20 s for `hours`."""
    trace = Trace()
    t0 = start + timedelta(seconds=boot_after_s)
    trace.boot(t0, build, cls, node_id)
    for n in range(int(hours * 180)):
        trace.slot(t0 + timedelta(seconds=20 * (n + 1)), n + 1, with_window)
    return trace.rows
