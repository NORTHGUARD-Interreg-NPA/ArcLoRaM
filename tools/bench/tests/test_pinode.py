"""The Pi Node link: GDB to a Pi's OpenOCD, behind a command allowlist.

The GDB transcripts below are written from GDB's and OpenOCD's documented output, not recorded
from a board yet: the spike on a real Pi Node replaces them with recordings (ADR-0002).
"""

import socket
import threading

import pytest
from test_programmer import write_elf

from bench.config import Remote
from bench.pinode import PI_NODE, PiNodeLink, find_gdb, validate_gdb
from bench.programmer import ProgrammerError, Refused

REMOTE = Remote(name="nuna-node-01", host="nuna-node-01", gdb=3333, log=4000)
TARGET = "target extended-remote nuna-node-01:3333"
SETS = ["set confirm off", "set pagination off", "set remotetimeout 30"]
UID_LINE = "0x1fff7590: 0014008f 32325014 20383543"


# --- the guard ---------------------------------------------------------------------


@pytest.mark.parametrize("commands", [
    [*SETS, TARGET, "monitor mdw 0x1FFF7590 3", "detach"],
    [*SETS, TARGET, "monitor reset run", "detach"],
    [TARGET, "monitor reset halt", "file C:/b/cm0.elf", "load", "compare-sections",
     "file /mnt/c/b/cm4.elf", "load", "compare-sections", "monitor reset run", "detach"],
])
def test_allowed_sequences(commands):
    validate_gdb(commands, REMOTE)


@pytest.mark.parametrize("bad", [
    # Irreversible or bricking: erase, option bytes (RDP 2 is permanent), OTP, memory writes.
    "monitor stm32l4x mass_erase 0",
    "monitor stm32l4x option_write 0 0x20 0xAA",
    "monitor stm32l4x lock 0",
    "monitor stm32l4x unlock 0",
    "monitor flash erase_sector 0 0 last",
    "monitor flash write_image erase C:/b/x.elf",
    "monitor program C:/b/x.elf",
    "monitor mww 0x1FFF7000 0x12345678",
    "monitor mdw 0x1FFF7000 3",           # only the chip UID may be read
    "monitor mdw 0x1FFF7590 4",
    "monitor reset init",
    "monitor shutdown",
    "monitor",
    "set var *(int*)0x1FFF7000 = 1",
    "print *(int*)0x1FFF7000 = 1",
    "restore C:/b/x.bin binary 0x1FFF7000",
    "x/4x 0x1FFF7590",
    # The controller's own shell and scripts.
    "shell id",
    "python print(1)",
    "source C:/b/x.gdb",
    "define evil",
    # An image is loaded with no argument: GDB's `load FILE OFFSET` could write anywhere.
    "load C:/b/x.elf",
    "load C:/b/x.elf 0x1000",
    "file C:/b/x.bin",
    "file C:/b/x.hex",
    "file C:/b/a b.elf",
    'file "C:/b/x.elf"',
    "file C:/b/x.elf; shell id",
    "file C:/b/x.elf\nshell id",
    "file ../x.elf",
    # Only the configured endpoint.
    "target extended-remote evil:3333",
    "target extended-remote nuna-node-01:3334",
    "target remote nuna-node-01:3333",
    "target extended-remote nuna-node-01:3333; shell id",
    "kill",
    "continue",
    "set confirm on",
    "set remotetimeout 0",
    "set remotetimeout 9999",
    "echo hi\\n",
])
def test_refused_commands(bad):
    with pytest.raises(Refused):
        validate_gdb([TARGET, bad], REMOTE)


def test_the_connection_comes_first_and_only_once():
    with pytest.raises(Refused, match="connect"):
        validate_gdb(["monitor reset run", TARGET], REMOTE)
    with pytest.raises(Refused, match="once"):
        validate_gdb([TARGET, "detach", TARGET], REMOTE)


def test_load_and_compare_follow_an_image_chosen_with_file():
    with pytest.raises(Refused, match="file"):
        validate_gdb([TARGET, "load"], REMOTE)
    with pytest.raises(Refused, match="file"):
        validate_gdb([TARGET, "compare-sections"], REMOTE)


# --- operations ----------------------------------------------------------------------


class Gdb:
    """A GDB that records its command line and answers with a canned transcript."""

    def __init__(self, out="", code=0):
        self.out, self.code, self.calls = out, code, []

    def __call__(self, argv):
        self.calls.append(argv)
        return self.code, self.out

    @property
    def commands(self):
        argv = self.calls[-1]
        return [argv[i + 1] for i, a in enumerate(argv) if a == "-ex"]


def link(gdb):
    return PiNodeLink(REMOTE, gdb="arm-none-eabi-gdb.exe", runner=gdb, windows=lambda p: p.as_posix())


def images(tmp_path):
    return {"CM0PLUS": write_elf(tmp_path, "cm0.elf", [(0x08020000, 0x100, 0x100)]),
            "CM4": write_elf(tmp_path, "cm4.elf", [(0x08000000, 0x100, 0x100)])}


def flash_out(matched=2, extra=""):
    sections = "\n".join(f"Section .text, range 0x8000000 -- 0x8000100: matched." for _ in range(matched))
    return f"Loading section .isr_vector, size 0x100 lma 0x8000000\nStart address 0x8000000, load size 256\n{sections}\n{extra}"


def test_the_uid_is_read_with_one_memory_read_and_no_halt():
    gdb = Gdb(f"{UID_LINE}\n")
    assert link(gdb).read_uid() == (0x0014008F, 0x32325014, 0x20383543)
    assert gdb.calls[0][:3] == ["arm-none-eabi-gdb.exe", "-batch", "-nx"]
    assert gdb.commands == [*SETS, TARGET, "monitor mdw 0x1FFF7590 3", "detach"]


class SeqGdb(Gdb):
    """A GDB that answers each session with the next canned transcript."""

    def __init__(self, *outs):
        super().__init__()
        self.outs = list(outs)

    def __call__(self, argv):
        self.calls.append(argv)
        return 0, self.outs.pop(0)

    def commands_of(self, i):
        return [self.calls[i][j + 1] for j, a in enumerate(self.calls[i]) if a == "-ex"]


ZERO_UID_LINE = "0x1fff7590: 00000000 00000000 00000000"


def test_a_zero_uid_is_read_again_after_a_reset_halt_and_the_board_is_let_run():
    gdb = SeqGdb(f"{ZERO_UID_LINE}\n", f"{UID_LINE}\n")
    assert link(gdb).read_uid() == (0x0014008F, 0x32325014, 0x20383543)
    assert gdb.commands_of(0) == [*SETS, TARGET, "monitor mdw 0x1FFF7590 3", "detach"]
    assert gdb.commands_of(1) == [*SETS, TARGET, "monitor reset halt", "monitor mdw 0x1FFF7590 3",
                                  "monitor reset run", "detach"]


def test_a_uid_that_stays_zero_after_a_reset_halt_is_an_error():
    gdb = SeqGdb(f"{ZERO_UID_LINE}\n", f"{ZERO_UID_LINE}\n")
    with pytest.raises(ProgrammerError, match="zero"):
        link(gdb).read_uid()


def test_a_real_uid_is_read_once_and_the_board_is_not_reset():
    gdb = SeqGdb(f"{UID_LINE}\n")
    link(gdb).read_uid()
    assert len(gdb.calls) == 1


def test_a_reply_without_the_uid_is_an_error():
    with pytest.raises(ProgrammerError, match="UID"):
        link(Gdb("0x1fff7590: \n")).read_uid()


def test_both_cores_are_written_the_cm0plus_first_and_the_cm4_last(tmp_path):
    gdb = Gdb(flash_out())
    link(gdb).flash(images(tmp_path))
    assert gdb.commands == [
        *SETS, TARGET, "monitor reset halt",
        f"file {tmp_path.as_posix()}/cm0.elf", "load", "compare-sections",
        f"file {tmp_path.as_posix()}/cm4.elf", "load", "compare-sections",
        "monitor reset run", "detach"]


def test_an_image_outside_its_cores_flash_is_refused_before_gdb_runs(tmp_path):
    swapped = {"CM0PLUS": write_elf(tmp_path, "a.elf", [(0x08000000, 0x100, 0x100)]),
               "CM4": write_elf(tmp_path, "b.elf", [(0x08020000, 0x100, 0x100)])}
    gdb = Gdb(flash_out())
    with pytest.raises(Refused, match="outside"):
        link(gdb).flash(swapped)
    assert gdb.calls == []


def test_both_cores_are_flashed_together(tmp_path):
    gdb = Gdb(flash_out())
    with pytest.raises(Refused, match="both cores"):
        link(gdb).flash({"CM4": images(tmp_path)["CM4"]})
    assert gdb.calls == []


def test_a_path_gdb_could_misread_is_refused(tmp_path):
    spaced = tmp_path / "with space"
    spaced.mkdir()
    gdb = Gdb(flash_out())
    with pytest.raises(Refused, match="path"):
        link(gdb).flash(images(spaced))
    assert gdb.calls == []


def test_a_section_that_does_not_match_fails_the_flash_whatever_the_exit_code(tmp_path):
    out = flash_out(matched=1, extra="Section .text, range 0x8020000 -- 0x8020100: MIS-MATCHED!\n")
    with pytest.raises(ProgrammerError, match="MIS-MATCHED"):
        link(Gdb(out, code=0)).flash(images(tmp_path))


def test_a_flash_that_verified_less_than_both_images_fails(tmp_path):
    with pytest.raises(ProgrammerError, match="verified"):
        link(Gdb(flash_out(matched=1))).flash(images(tmp_path))


@pytest.mark.parametrize("out", [
    "nuna-node-01:3333: Connection refused.\n",
    "nuna-node-01:3333: Connection timed out.\n",
    "Remote communication error.  Target disconnected.: Connection reset by peer.\n",
    "Cannot access memory at address 0x1fff7590\n",
    "Target not examined yet\n",
    "Error erasing flash with vFlashErase packet\n",
])
def test_gdb_errors_are_reported_even_with_exit_code_zero(out):
    with pytest.raises(ProgrammerError, match="nuna-node-01"):
        link(Gdb(out)).reset()


def test_a_failing_gdb_is_an_error_with_its_first_message():
    with pytest.raises(ProgrammerError, match="Connection refused"):
        link(Gdb("nuna-node-01:3333: Connection refused.\nThe program is not being run.\n", code=1)).reset()


def test_reset_is_a_reset_and_run_without_flashing():
    gdb = Gdb("")
    link(gdb).reset()
    assert gdb.commands == [*SETS, TARGET, "monitor reset run", "detach"]


# --- finding things ---------------------------------------------------------------------


def test_reachable_means_the_read_only_log_port_answers():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    accepting = threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True)
    accepting.start()
    assert PiNodeLink(Remote("up", "127.0.0.1", log=port), gdb="g").log_reachable(timeout=2) is True
    accepting.join(timeout=5)       # a socket closed while accept() still blocks on it keeps listening
    srv.close()
    assert PiNodeLink(Remote("down", "127.0.0.1", log=port), gdb="g").log_reachable(timeout=0.5) is False


def test_the_programmer_exe_is_found_newest_first_or_named_by_the_environment(tmp_path):
    def exe(version):
        p = tmp_path / f"STM32CubeIDE_{version}/STM32CubeIDE/plugins/com.st.stm32cube.ide.mcu.externaltools." \
                       f"gnu-tools-for-stm32.14.3.rel1.win32_1.0.100.x/tools/bin/arm-none-eabi-gdb.exe"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"")
        return str(p)

    exe("2.0.0")
    newest = exe("2.1.1")
    assert find_gdb(env={}, base=tmp_path) == newest
    assert find_gdb(env={"BENCH_GDB": "/x/gdb"}, base=tmp_path) == "/x/gdb"
    with pytest.raises(ProgrammerError, match="BENCH_GDB"):
        find_gdb(env={}, base=tmp_path / "empty")


def test_a_pi_node_is_marked_in_the_probe_list():
    assert PI_NODE == "pi-node"
