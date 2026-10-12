"""Board leases: a command that works on a board holds it, released by the OS when the process ends."""

import signal
import subprocess
import sys

import pytest

from bench.lease import Held, Leases


def leases(tmp_path, worktree="wt-a", command="bench run a.toml"):
    return Leases(tmp_path / "leases", worktree=worktree, command=command)


HOLDER = """
import sys, time
from bench.lease import Leases
lease = Leases(sys.argv[1], worktree="wt-other", command="bench run other.toml")
ids = lambda a: [x for x in a.split(",") if x]
cm = lease.hold(exclusive=ids(sys.argv[2]), shared=ids(sys.argv[3]))
cm.__enter__()
print("held", flush=True)
time.sleep(120)
"""


def other_session(tmp_path, exclusive, shared=""):
    """Another process (another worktree's session) holding leases until it is killed."""
    p = subprocess.Popen([sys.executable, "-c", HOLDER, str(tmp_path / "leases"), exclusive, shared],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def stop(p):
    p.send_signal(signal.SIGKILL)
    p.wait()


def test_boards_nobody_holds_are_taken_and_given_back(tmp_path):
    with leases(tmp_path).hold(exclusive=["SN1", "node-5"]):
        pass
    with leases(tmp_path).hold(exclusive=["SN1", "node-5"]):
        pass


def test_a_board_held_by_another_session_is_refused_with_who_holds_it(tmp_path):
    p = other_session(tmp_path, "SN1")
    try:
        with pytest.raises(Held) as err:
            with leases(tmp_path).hold(exclusive=["SN2", "SN1"]):
                pytest.fail("the lease must be refused")
        assert list(err.value.busy) == ["SN1"]
        assert "wt-other" in err.value.busy["SN1"] and "bench run other.toml" in err.value.busy["SN1"]
        assert f"pid {p.pid}" in err.value.busy["SN1"]
    finally:
        stop(p)


def test_a_refused_command_holds_nothing(tmp_path):
    p = other_session(tmp_path, "SN1")
    try:
        with pytest.raises(Held):
            with leases(tmp_path).hold(exclusive=["SN1", "SN2"]):
                pass
        with leases(tmp_path).hold(exclusive=["SN2"]):      # SN2 was released again
            pass
    finally:
        stop(p)


def test_a_session_that_dies_leaves_no_lease_behind(tmp_path):
    p = other_session(tmp_path, "SN1")
    stop(p)
    with leases(tmp_path).hold(exclusive=["SN1"]):
        pass


def test_watchers_share_a_board_but_exclude_a_flasher(tmp_path):
    p = other_session(tmp_path, "SN9", shared="SN3")
    try:
        with leases(tmp_path).hold(shared=["SN3"]):         # a second watcher is fine
            pass
        with pytest.raises(Held) as err:
            with leases(tmp_path).hold(exclusive=["SN3"]):  # flashing a board someone watches is not
                pass
        assert "SN3" in err.value.busy
    finally:
        stop(p)


def test_a_watched_board_cannot_be_watched_while_it_is_being_flashed(tmp_path):
    p = other_session(tmp_path, "SN1")
    try:
        with pytest.raises(Held):
            with leases(tmp_path).hold(shared=["SN1"]):
                pass
    finally:
        stop(p)


def test_holders_say_what_is_held_without_taking_it(tmp_path):
    p = other_session(tmp_path, "SN1")
    try:
        held = leases(tmp_path).holders(["SN1", "SN2"])
        assert list(held) == ["SN1"] and "wt-other" in held["SN1"]
        with leases(tmp_path).hold(exclusive=["SN2"]):      # looking at SN2 did not take it
            pass
    finally:
        stop(p)
    assert leases(tmp_path).holders(["SN1"]) == {}


def test_a_probe_takes_a_free_board_and_skips_a_held_one(tmp_path):
    from contextlib import ExitStack

    p = other_session(tmp_path, "SN1")
    try:
        with ExitStack() as stack:
            assert leases(tmp_path).try_exclusive(stack, "SN2") is True
            assert leases(tmp_path).try_exclusive(stack, "SN1") is False
            with pytest.raises(Held):                       # SN2 stays taken until the stack closes
                with leases(tmp_path).hold(exclusive=["SN2"]):
                    pass
        with leases(tmp_path).hold(exclusive=["SN2"]):
            pass
    finally:
        stop(p)


def test_a_board_id_is_a_file_name(tmp_path):
    with pytest.raises(ValueError):
        with leases(tmp_path).hold(exclusive=["../x"]):
            pass


def test_a_board_probed_by_this_command_can_be_held_by_it_too(tmp_path):
    from contextlib import ExitStack

    mine = leases(tmp_path)
    with ExitStack() as stack:
        assert mine.try_exclusive(stack, "SN2") is True
        with mine.hold(exclusive=["SN2", "SN3"], shared=["SN2"]):     # SN2 is already ours
            pass
        with pytest.raises(Held):                                      # still taken: the stack is open
            with leases(tmp_path, worktree="wt-b").hold(exclusive=["SN2"]):
                pass
    with leases(tmp_path, worktree="wt-b").hold(exclusive=["SN2", "SN3"]):
        pass
