"""capture: several ports in one process, each read and reconnected on its own."""

import contextlib
import itertools
import socket
import sys
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import arclog.capture as capture_mod
from arclog.capture import capture, serial_lines, tcp_lines
from arclog.marks import Marks, read_marks
from arclog.cli import main

T0 = datetime(2026, 9, 29, 23, 59, 59, tzinfo=timezone.utc)
BOOT = "000000T000000.9997 4S A #00 BOOT build=dev"
SYNC = "000000T000001.0000 4X M #01 CORE_SYNC stage=linked"


def fake_ports(lines_by_port):
    """A line source replaying (seconds after T0, line) per port."""
    def source(port):
        for dt, raw in lines_by_port[port]:
            yield T0 + timedelta(seconds=dt), raw
    return source


def test_each_port_goes_to_its_node_file(tmp_path):
    src = fake_ports({"COM6": [(0, BOOT), (0.5, SYNC)], "COM8": [(0.2, BOOT)]})
    seen = []
    capture([("COM6", "c3"), ("COM8", "c2")], tmp_path, source=src,
            on_line=lambda ln: seen.append((ln.node, ln.event)))

    c3 = (tmp_path / "c3-20260929.log").read_text(encoding="utf-8").splitlines()
    c2 = (tmp_path / "c2-20260929.log").read_text(encoding="utf-8").splitlines()
    assert c3 == ["2026-09-29T23:59:59.000000Z\t" + BOOT, "2026-09-29T23:59:59.500000Z\t" + SYNC]
    assert c2 == ["2026-09-29T23:59:59.200000Z\t" + BOOT]
    assert sorted(seen) == [("c2", "BOOT"), ("c3", "BOOT"), ("c3", "CORE_SYNC")]


def test_files_rotate_at_utc_midnight_per_node(tmp_path):
    src = fake_ports({"COM6": [(0, BOOT), (2, SYNC)]})
    capture([("COM6", "c3")], tmp_path, source=src)
    assert (tmp_path / "c3-20260929.log").read_text(encoding="utf-8").endswith(BOOT + "\n")
    assert (tmp_path / "c3-20260930.log").read_text(encoding="utf-8").endswith(SYNC + "\n")


def test_a_quiet_port_does_not_hold_back_the_others(tmp_path):
    src = fake_ports({"COM6": [], "COM8": [(0, BOOT)]})
    capture([("COM6", "c3"), ("COM8", "c2")], tmp_path, source=src)
    assert not (tmp_path / "c3-20260929.log").exists()
    assert (tmp_path / "c2-20260929.log").exists()


def test_a_reader_error_is_raised_to_the_caller(tmp_path):
    def src(port):
        yield T0, BOOT
        raise RuntimeError(f"{port} broke")

    with pytest.raises(RuntimeError, match="COM6 broke"):
        capture([("COM6", "c3")], tmp_path, source=src)
    assert (tmp_path / "c3-20260929.log").exists()


@pytest.mark.parametrize("ports, problem", [
    ([], "no port"),
    ([("COM6", "c3"), ("COM6", "c2")], "port given twice"),
    ([("COM6", "c3"), ("COM8", "c3")], "node name given twice"),
])
def test_invalid_port_lists_are_refused(tmp_path, ports, problem):
    with pytest.raises(ValueError, match=problem):
        capture(ports, tmp_path, source=fake_ports({}))


def test_cli_pairs_ports_and_nodes_in_order(tmp_path, monkeypatch, capsys):
    calls = {}

    def fake_capture(ports, out_dir, **kwargs):
        calls["ports"] = ports

    monkeypatch.setattr(capture_mod, "capture", fake_capture)
    assert main(["capture", "--port", "COM6", "--node", "c3", "--port", "COM8", "--node", "c2",
                 "--out", str(tmp_path)]) == 0
    assert calls["ports"] == [("COM6", "c3"), ("COM8", "c2")]


def test_cli_refuses_unpaired_ports(tmp_path, capsys):
    assert main(["capture", "--port", "COM6", "--port", "COM8", "--node", "c3",
                 "--out", str(tmp_path)]) == 2
    assert "one --node per --port" in capsys.readouterr().err


class FakeSerialModule:
    """pyserial stand-in: the port fails `failures` times, then gives `data` and goes idle."""

    class SerialException(Exception):
        pass

    def __init__(self, failures, data=b""):
        self.failures = failures
        self.data = data
        mod = self

        class Serial:
            def __init__(self, port, baud, timeout):
                if mod.failures:
                    mod.failures -= 1
                    raise mod.SerialException(f"could not open port '{port}'")
                self.in_waiting = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n):
                chunk, mod.data = mod.data, b""
                return chunk

        self.Serial = Serial


def test_an_outage_is_logged_once_and_the_recovery_once(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "serial", FakeSerialModule(5, BOOT.encode() + b"\r\n"))
    monkeypatch.setattr(capture_mod, "RECONNECT_S", 0.001)
    logs = []
    lines = [raw for _, raw in capture_mod.serial_lines("COM6", log=logs.append, duration_s=0.3)]
    assert lines == [BOOT]
    assert [m.split(" (")[0] for m in logs] == ["arclog: COM6 unavailable", "arclog: listening on COM6 @ 9600"]


# --- a Pi Node's log over TCP ---------------------------------------------------------------------

PI_LINE = "2026-10-04 20:54:03.180082 | 000102T044054.0161 0T H #20 SLOT ph=1 ty=SYNC"
SLOT = "000102T044054.0161 0T H #20 SLOT ph=1 ty=SYNC"


class Peer:
    """A TCP server standing in for a Pi Node's log server: one script (byte chunks) per connection."""

    def __init__(self, *scripts):
        import socket
        import threading
        import time

        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.url = f"tcp://127.0.0.1:{self.srv.getsockname()[1]}/nuna-node-01"

        def serve():
            for chunks in scripts:
                conn, _ = self.srv.accept()
                for chunk in chunks:
                    conn.sendall(chunk)
                    time.sleep(0.02)
                conn.close()

        threading.Thread(target=serve, daemon=True).start()

    def close(self):
        self.srv.close()


@pytest.fixture
def peer_factory(monkeypatch):
    monkeypatch.setattr(capture_mod, "RECONNECT_S", 0.01)
    made = []

    def make(*scripts):
        made.append(Peer(*scripts))
        return made[-1]

    yield make
    for p in made:
        p.close()


def test_the_pi_stamp_is_removed_and_lines_are_rebuilt_across_chunks(peer_factory):
    peer = peer_factory([(PI_LINE + "\r\n").encode(),
                         b"2026-10-04 20:54:06.180128 | 000102T044057.0161 0T H #21 SL",
                         b"OT ph=1\n",
                         b"2026-10-04 20:54:07.000000 | \n",       # a stamp with nothing after it
                         b"not stamped\n"])
    lines = [raw for _, raw in capture_mod.tcp_lines(peer.url, log=lambda m: None, duration_s=1.0)]
    assert lines == [SLOT, "000102T044057.0161 0T H #21 SLOT ph=1", "not stamped"]


def test_a_line_is_stamped_with_the_controller_clock_in_utc(peer_factory):
    peer = peer_factory([(PI_LINE + "\n").encode()])
    before = datetime.now(timezone.utc)
    [(t, raw)] = list(capture_mod.tcp_lines(peer.url, log=lambda m: None, duration_s=0.6))
    assert before <= t <= datetime.now(timezone.utc) and t.tzinfo is not None
    assert raw == SLOT


def test_a_dropped_connection_is_retried(peer_factory):
    peer = peer_factory([(PI_LINE + "\n").encode()], [b"2026-10-04 20:54:09.000000 | 000102T044100.0163 0Y M #22 SYNC_EPOCH\n"])
    logs = []
    lines = [raw for _, raw in capture_mod.tcp_lines(peer.url, log=logs.append, duration_s=1.0)]
    assert lines == [SLOT, "000102T044100.0163 0Y M #22 SYNC_EPOCH"]
    assert sum("listening on" in m for m in logs) >= 2 and any("unavailable" in m for m in logs)


def test_without_reconnect_a_closed_connection_is_an_error(peer_factory):
    peer = peer_factory([(PI_LINE + "\n").encode()])
    with pytest.raises(ConnectionError, match="closed"):
        list(capture_mod.tcp_lines(peer.url, reconnect=False, log=lambda m: None, duration_s=2.0))


def test_capture_reads_a_tcp_port_with_the_tcp_source(tmp_path, peer_factory):
    peer = peer_factory([(PI_LINE + "\n").encode()])
    capture([(peer.url, "nuna-node-01")], tmp_path, duration_s=0.8)
    [f] = list(tmp_path.glob("nuna-node-01-*.log"))
    assert f.read_text(encoding="utf-8").rstrip("\n").endswith("\t" + SLOT)


@pytest.mark.parametrize("url", ["tcp://host", "tcp://:4000/x", "tcp://h:99999/x", "tcp://h:0/x", "COM6"])
def test_a_tcp_url_needs_a_host_and_a_port(url):
    with pytest.raises(ValueError, match="tcp://"):
        capture_mod.parse_tcp_url(url)


def test_a_tcp_url_gives_host_and_port_and_ignores_the_label():
    assert capture_mod.parse_tcp_url("tcp://100.64.0.11:4000/nuna-node-01") == ("100.64.0.11", 4000)


@contextlib.contextmanager
def log_server(*connections):
    """A Pi Node's log server on loopback: for each connection the bytes it sends, then whether it closes
    (otherwise it holds the connection open until the test ends)."""
    server = socket.create_server(("127.0.0.1", 0))
    release = threading.Event()

    def serve():
        for data, close in connections:
            conn, _ = server.accept()
            conn.sendall(data)
            if not close:
                release.wait(5)
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield f"tcp://127.0.0.1:{server.getsockname()[1]}"
    finally:
        release.set()
        server.close()


def test_a_tcp_reader_reports_when_its_connection_is_up_and_down(monkeypatch):
    monkeypatch.setattr(capture_mod, "RECONNECT_S", 0.01)
    states = []
    with log_server((BOOT.encode() + b"\n", True), (SYNC.encode() + b"\n", False)) as url:
        reader = tcp_lines(url, on_state=lambda up, since=None: states.append(up), log=lambda m: None)
        lines = [raw for _, raw in itertools.islice(reader, 2)]
        reader.close()

    assert lines == [BOOT, SYNC]
    assert states == [True, False, True]


def fake_serial(monkeypatch, *opens):
    """A `serial` module whose successive opens each play a script: the bytes to read, then the device
    disappears (None: the port cannot be opened)."""
    scripts = iter(opens)

    class SerialException(OSError):
        pass

    class Serial:
        in_waiting = 0

        def __init__(self, port, baud, timeout=None):
            script = next(scripts)
            if script is None:
                raise SerialException(f"could not open {port}")
            self.data = bytearray(script)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            if not self.data:
                raise SerialException("device disconnected")
            chunk, self.data = bytes(self.data[:n]), self.data[n:]
            return chunk

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=Serial, SerialException=SerialException))


def test_a_serial_reader_reports_when_its_port_is_up_and_down(monkeypatch):
    monkeypatch.setattr(capture_mod, "RECONNECT_S", 0.0)
    fake_serial(monkeypatch, None, BOOT.encode() + b"\n", SYNC.encode() + b"\n")
    states = []
    reader = serial_lines("COM6", on_state=states.append, log=lambda m: None)
    lines = [raw for _, raw in itertools.islice(reader, 2)]
    reader.close()

    assert lines == [BOOT, SYNC]
    assert states == [False, True, False, True]


def test_capture_writes_its_marks_next_to_the_daily_files(tmp_path):
    src = fake_ports({"COM6": [(0, BOOT)], "COM8": []})
    capture([("COM6", "c3"), ("COM8", "c2")], tmp_path, source=src)

    [path] = tmp_path.glob("marks-*.log")
    [mark] = read_marks(path)
    assert sorted(mark.ports) == ["c2", "c3"]


def test_capture_marks_a_connected_tcp_port_up(tmp_path):
    with log_server((BOOT.encode() + b"\n", False)) as url:
        # A mark is due at every tick: the monotonic clock the marks read moves 10 s per reading.
        marks = Marks(tmp_path, ["c3"], monotonic=itertools.count(0, 10).__next__)
        capture([(url, "c3")], tmp_path, duration_s=1.2, marks=marks)

    [path] = tmp_path.glob("marks-*.log")
    assert any(mark.ports["c3"].up for mark in read_marks(path))


# --- a TCP link that stalls without a close ---------------------------------------------------------
# A cut cable, a Pi that lost power or a firewall that drops packets sends nothing, not even a close: only the
# silence tells. The Pi's own heartbeat comes every 31 s, so a link with no byte for 45 s is dead; the OS
# keepalive is set to find one sooner, and the reader dates the outage to when the link was last known alive.


class Clock:
    """The reader's monotonic clock, moved by the scripted reads."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ScriptedSocket:
    """A connected socket whose reads follow a script: bytes (a read), a number (the 1 s read timeout expires
    after that many seconds of the fake clock) or an exception (raised by the read)."""

    def __init__(self, clock, script):
        self.clock, self.script, self.options = clock, list(script), []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def setsockopt(self, *args):
        self.options.append(args)

    def ioctl(self, *args):
        self.options.append(args)

    def settimeout(self, seconds):
        pass

    def recv(self, size):
        item = self.script.pop(0) if self.script else 3600.0
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, (int, float)):
            self.clock.now += item
            raise socket.timeout("timed out")
        return item


def scripted_links(monkeypatch, *scripts):
    clock = Clock()
    links = iter([ScriptedSocket(clock, script) for script in scripts])
    monkeypatch.setattr(capture_mod.socket, "create_connection", lambda *a, **k: next(links))
    monkeypatch.setattr(capture_mod, "RECONNECT_S", 0.0)
    return clock


def read_scripted(clock, count, **kwargs):
    """The first `count` lines of the scripted links and the (up, since) the reader reported."""
    states = []
    reader = tcp_lines("tcp://nuna-node-01:4000/c3", monotonic=clock, log=lambda m: None,
                       on_state=lambda up, since=None: states.append((up, since)), **kwargs)
    lines = [raw for _, raw in itertools.islice(reader, count)]
    reader.close()
    return lines, states


def seconds_ago(since):
    return (datetime.now(timezone.utc) - since).total_seconds()


def test_a_link_with_no_byte_for_45_seconds_is_down_since_its_last_byte(monkeypatch):
    clock = scripted_links(monkeypatch,
                           [BOOT.encode() + b"\n"] + [10.0] * 5,  # silent for 50 s
                           [SYNC.encode() + b"\n"])

    lines, states = read_scripted(clock, 2)

    assert lines == [BOOT, SYNC]
    assert [up for up, _ in states] == [True, False, True]
    assert seconds_ago(states[1][1]) == pytest.approx(50, abs=2)  # not 8 s: nothing proved the link alive since


def test_the_pi_heartbeat_keeps_a_quiet_link_up(monkeypatch):
    heartbeat = b"[pi] uart_idle=7s ntp=synced\n"
    clock = scripted_links(monkeypatch,
                           [BOOT.encode() + b"\n", 31.4, heartbeat, 31.4, heartbeat, 31.4, SYNC.encode() + b"\n"])

    lines, states = read_scripted(clock, 4)

    assert lines == [BOOT, "[pi] uart_idle=7s ntp=synced", "[pi] uart_idle=7s ntp=synced", SYNC]
    assert [up for up, _ in states] == [True]


def test_an_os_error_after_silence_is_dated_to_when_the_keepalive_began_to_fail(monkeypatch):
    # The OS gave up on probes nobody answered: the link was alive 8 s ago, not 20 s ago.
    err = TimeoutError(10060, "the peer system failed to respond")  # not our 1 s read timeout: it has an errno
    clock = scripted_links(monkeypatch, [BOOT.encode() + b"\n", 20.0, err], [SYNC.encode() + b"\n"])

    lines, states = read_scripted(clock, 2)

    assert lines == [BOOT, SYNC]
    assert [up for up, _ in states] == [True, False, True]
    assert seconds_ago(states[1][1]) == pytest.approx(capture_mod.KEEPALIVE_DEAD_S, abs=2)


def test_a_reset_right_after_data_is_down_from_now(monkeypatch):
    clock = scripted_links(monkeypatch, [BOOT.encode() + b"\n", ConnectionResetError()], [SYNC.encode() + b"\n"])

    _, states = read_scripted(clock, 2)

    assert seconds_ago(states[1][1]) == pytest.approx(0, abs=2)


def test_a_link_that_never_connects_is_down_from_now(monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(capture_mod.socket, "create_connection", refuse)
    monkeypatch.setattr(capture_mod, "RECONNECT_S", 0.01)
    states = []
    list(tcp_lines("tcp://nuna-node-01:4000/c3", log=lambda m: None, duration_s=0.05,
                   on_state=lambda up, since=None: states.append((up, since))))

    assert {up for up, _ in states} == {False}
    assert seconds_ago(states[0][1]) == pytest.approx(0, abs=2)


@pytest.mark.parametrize("silent, stalled, lost", [
    (0.0, False, 0.0),     # a reset or a close right after data: alive until now
    (2.9, False, 0.0),     # not yet idle for the keepalive
    (6.0, False, 6.0),     # the probes began at 3 s: the last proof of life is the last byte
    (20.0, False, 8.0),    # 8 s of unanswered probes (3 s idle, then 5 probes 1 s apart)
    (50.0, True, 50.0),    # the reader's own rule: nothing at all, the heartbeat included
])
def test_how_long_ago_the_link_was_last_known_alive(silent, stalled, lost):
    assert capture_mod._lost_s(silent, stalled) == lost


@pytest.mark.skipif(not hasattr(socket, "TCP_KEEPIDLE"), reason="no per-socket keepalive timers here")
def test_the_keepalive_gives_up_on_a_silent_peer_after_8_seconds():
    with socket.socket() as sock:
        capture_mod._keepalive(sock)

        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
        assert [sock.getsockopt(socket.IPPROTO_TCP, name)
                for name in (socket.TCP_KEEPIDLE, socket.TCP_KEEPINTVL, socket.TCP_KEEPCNT)] == [3, 1, 5]
    assert capture_mod.KEEPALIVE_DEAD_S == 8


def test_capture_marks_a_dropped_tcp_port_down_and_up_between_two_ticks(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_mod, "RECONNECT_S", 0.01)
    with log_server((BOOT.encode() + b"\n", True), (SYNC.encode() + b"\n", False)) as url:
        # One regular mark at the start: the drop and the reconnection fall between ticks, and each is a mark.
        clock = iter([0.0] + [1.0] * 10_000)
        marks = Marks(tmp_path, ["c3"], monotonic=lambda: next(clock))
        capture([(url, "c3")], tmp_path, duration_s=1.2, marks=marks)

    [path] = tmp_path.glob("marks-*.log")
    seen = [mark.ports["c3"].up for mark in read_marks(path)]
    assert seen[0] is False and True in seen and False in seen[seen.index(True):] and seen[-1] is True
