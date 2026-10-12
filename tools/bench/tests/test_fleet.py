"""The fleet: local ST-LINK boards and Pi Nodes behind the one interface bench already calls."""

from datetime import datetime, timezone

import pytest
from arclog.capture import DailyWriter

from bench.boards import capture_node, format_uid
from bench.capture import capture_command
from bench.config import Remote
from bench.fleet import Fleet
from bench.flash import discover, select
from bench.pinode import PI_NODE
from bench.programmer import Probe, ProgrammerError

SN8 = "003E002E3333511431363730"
UID1 = (0x0014008F, 0x32325014, 0x20383543)
UID5 = (0x00550055, 0x32325014, 0x20383543)
T0 = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)
N1 = Remote("nuna-node-01", "100.64.0.11")
N2 = Remote("nuna-node-02", "nuna-node-02", log=4010)


class FakeLocal:
    def __init__(self):
        self.calls = []

    def probes(self):
        return [Probe(SN8, "NUCLEO-WL55JC")]

    def read_uid(self, sn):
        self.calls.append(("uid", sn))
        return UID1

    def flash(self, sn, images):
        self.calls.append(("flash", sn))

    def reset(self, sn):
        self.calls.append(("reset", sn))


class FakeLink:
    def __init__(self, remote, up=True, uid=UID5):
        self.remote, self.up, self.uid, self.calls, self.checks = remote, up, uid, [], 0

    def log_reachable(self, timeout=2.0):
        self.checks += 1
        return self.up

    def read_uid(self):
        self.calls.append("uid")
        return self.uid

    def flash(self, images):
        self.calls.append(("flash", sorted(images)))

    def reset(self):
        self.calls.append("reset")


def fleet(*links):
    return Fleet(FakeLocal(), list(links))


# --- the probe list and ports ----------------------------------------------------------


def test_probes_are_the_local_ones_then_the_pi_nodes_that_answer():
    f = fleet(FakeLink(N1), FakeLink(N2, up=False))
    assert [(p.sn, p.board) for p in f.probes()] == [(SN8, "NUCLEO-WL55JC"), ("nuna-node-01", PI_NODE)]
    assert [r.name for r in f.down] == ["nuna-node-02"]


def test_reachability_is_checked_once_per_fleet():
    a, b = FakeLink(N1), FakeLink(N2, up=False)
    f = fleet(a, b)
    f.probes()
    f.probes()
    assert (a.checks, b.checks) == (1, 1)


def test_ports_name_the_capture_source_of_every_configured_pi_node():
    f = fleet(FakeLink(N1), FakeLink(N2, up=False))
    assert f.ports() == {"nuna-node-01": "tcp://100.64.0.11:4000/nuna-node-01",
                         "nuna-node-02": "tcp://nuna-node-02:4010/nuna-node-02"}


def test_a_tcp_port_names_its_capture_node_by_the_label_in_its_path():
    assert capture_node("COM9") == "com9"
    assert capture_node("tcp://100.64.0.11:4000/nuna-node-01") == "nuna-node-01"
    assert capture_node("tcp://nuna-node-02:4010") == "nuna-node-02"


def test_the_capture_command_lists_com_ports_by_number_then_pi_nodes():
    cmd = capture_command(r"\\x\arclog", r"C:\out", ["tcp://b:4000/b-node", "COM10", "COM9", "tcp://a:4000/a-node"])
    assert ("--port COM9 --node com9 --port COM10 --node com10 "
            "--port tcp://a:4000/a-node --node a-node --port tcp://b:4000/b-node --node b-node") in cmd


# --- calls go to the right adapter ---------------------------------------------------------


def test_calls_keyed_by_a_probe_serial_number_go_to_the_st_link_programmer():
    f = fleet(FakeLink(N1))
    assert f.read_uid(SN8) == UID1
    f.flash(SN8, {})
    f.reset(SN8)
    assert f._local.calls == [("uid", SN8), ("flash", SN8), ("reset", SN8)]


def test_calls_keyed_by_a_pi_node_name_go_to_its_link():
    link = FakeLink(N1)
    f = fleet(link)
    assert f.read_uid("nuna-node-01") == UID5
    f.flash("nuna-node-01", {"CM4": 1, "CM0PLUS": 2})
    f.reset("nuna-node-01")
    assert link.calls == ["uid", ("flash", ["CM0PLUS", "CM4"]), "reset"]
    assert f._local.calls == []


def test_a_pi_node_that_does_not_answer_is_not_touched():
    link = FakeLink(N1, up=False)
    f = fleet(link)
    with pytest.raises(ProgrammerError, match="nuna-node-01.*not answering"):
        f.flash("nuna-node-01", {})
    assert link.calls == []


# --- through discovery and flashing ------------------------------------------------------------


def boot(w, uid, nid, build="b1"):
    w.write(T0, f"000101T000000.0031 0S A #00 BOOT cls=C2 id={nid} uid={format_uid(uid)} fw=1.5.0 build={build}")
    w.close()


def test_discovery_lists_a_pi_node_like_any_board(tmp_path):
    boot(DailyWriter(tmp_path, "nuna-node-01"), UID5, 5)
    f = fleet(FakeLink(N1))
    boards = discover(f, {**{SN8: "COM8"}, **f.ports()}, {UID1: 1, UID5: 5}, tmp_path)
    local, remote = boards
    assert (local.sn, local.port, local.remote, local.node_id) == (SN8, "COM8", False, None)
    assert (remote.sn, remote.node, remote.remote, remote.node_id, remote.build) == \
        ("nuna-node-01", "nuna-node-01", True, 5, "b1")


def test_an_unknown_pi_node_board_is_read_through_its_link_when_asked(tmp_path):
    link = FakeLink(N1)
    f = fleet(link)
    boards = discover(f, f.ports(), {UID5: 5}, tmp_path, probe_unknown=True)
    [remote] = [b for b in boards if b.remote]
    assert (remote.uid, remote.uid_source, remote.node_id) == (UID5, "swd", 5)
    assert link.calls == ["uid"]


def test_a_node_that_is_down_is_named_when_a_scenario_needs_it(tmp_path):
    f = fleet(FakeLink(N2, up=False))
    boards = discover(f, f.ports(), {UID5: 5}, tmp_path)
    with pytest.raises(LookupError, match="no connected board has Node ID 5"):
        select(boards, {5: "C2"})
    assert [r.name for r in f.down] == ["nuna-node-02"]


def test_only_the_named_boards_are_read_over_swd(tmp_path):
    a, b = FakeLink(N1), FakeLink(N2)
    f = fleet(a, b)
    boards = discover(f, f.ports(), {UID5: 5}, tmp_path, probe_unknown={"nuna-node-02"})
    assert (a.calls, b.calls, f._local.calls) == ([], ["uid"], [])
    assert {x.sn: x.node_id for x in boards} == {SN8: None, "nuna-node-01": None, "nuna-node-02": 5}


def test_a_board_another_session_holds_is_not_read_over_swd(tmp_path):
    a, b = FakeLink(N1), FakeLink(N2)
    f = fleet(a, b)
    boards = discover(f, f.ports(), {UID5: 5}, tmp_path, probe_unknown=True,
                      may_probe=lambda sn: sn != "nuna-node-02")
    assert b.calls == [] and a.calls == ["uid"]
    assert {x.sn: x.node_id for x in boards}["nuna-node-02"] is None
