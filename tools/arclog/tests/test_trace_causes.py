"""trace_causes: every firmware event of a run that the engine would fail on, listed, not only the first."""

from datetime import timedelta

from test_expect import SINCE, UID, Trace, spec

from arclog.validity import trace_causes


def at(seconds: float):
    return SINCE + timedelta(seconds=seconds)


def causes(trace, **overrides):
    return trace_causes(spec(**overrides), SINCE, trace.lines)


def test_an_unplanned_reboot_is_a_firmware_cause_naming_the_node_and_the_time():
    t = Trace()
    t.boot(1, "n2")
    t.boot(12, "n2")

    [cause] = causes(t)
    assert (cause.kind, cause.fault, cause.node, cause.start) == ("unplanned_reboot", "firmware", "n2", at(12))
    assert cause.text == "n2 unexpected reboot (1, 0 allowed) +12.0s"


def test_every_unplanned_reboot_is_listed_not_only_the_first():
    t = Trace()
    t.boot(1, "n2")
    t.boot(12, "n2")
    t.boot(30, "n2")

    assert [c.start for c in causes(t)] == [at(12), at(30)]


def test_lost_lines_are_listed_with_their_count_node_and_time():
    t = Trace()
    t.boot(1, "n2")
    t.idle(5, "n2")
    t.emit(6, "n2", "0", "INIT_DONE", skip=3, phases=3)
    t.emit(9, "n2", "0", "INIT_DONE", skip=2, phases=3)

    first, second = causes(t)
    assert (first.kind, first.fault, first.node, first.start) == ("lost_lines", "firmware", "n2", at(6))
    assert first.text == "n2 3 line(s) lost before core 0 #05 INIT_DONE +6.0s"
    assert second.text == "n2 2 line(s) lost before core 0 #08 INIT_DONE +9.0s"


def test_a_reboot_into_another_build_is_a_wrong_build_on_each_core_that_booted_it():
    t = Trace()
    t.boot(1, "n2")
    t.boot(12, "n2", build="b2")

    cm4, cm0 = causes(t)
    assert (cm4.kind, cm4.fault, cm4.node, cm4.start) == ("wrong_build", "firmware", "n2", at(12))
    assert cm4.text == "n2 core 4 booted build=b2, expected b1 +12.0s"
    assert cm0.text == "n2 core 0 booted build=b2, expected b1 +12.3s"


def test_a_cm0_rebooting_alone_is_an_unplanned_reboot():
    t = Trace()
    t.boot(1, "n2")
    t.emit(8, "n2", "0", "BOOT", cls="C2", id="2", uid=UID, fw="1.5.0", build="b1")

    [cause] = causes(t, nodes={"n2": {"reboots": 1}})
    assert (cause.kind, cause.node, cause.text) == ("unplanned_reboot", "n2", "n2 CM0+ rebooted alone +8.0s")


def test_a_board_that_never_booted_the_expected_build_is_a_wrong_build():
    t = Trace()
    t.boot(1, "n2", build="b0")  # the old image: the flash did not take

    [cause] = causes(t)
    assert (cause.kind, cause.fault, cause.node) == ("wrong_build", "firmware", "n2")
    assert cause.text == "n2 never booted build=b1 (last booted build=b0)"


def test_a_board_that_logged_no_boot_at_all_never_booted_the_expected_build():
    t = Trace()
    t.idle(5, "n2")

    [cause] = causes(t)
    assert cause.text == "n2 never booted build=b1 (no BOOT line)"


def test_a_board_that_booted_it_on_both_cores_is_not_a_wrong_build_and_a_watched_one_is_not_asked():
    t = Trace()
    t.boot(1, "n2")
    t.idle(5, "n3")

    assert causes(t, nodes={"n2": {}, "n3": {"flashed": False}}) == []
