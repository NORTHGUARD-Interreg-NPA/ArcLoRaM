"""The bench map: which boards are connected, locally and on Pi Nodes, and whether they are free."""

import json
import re
from datetime import datetime, timezone

from bench.boards import Board
from bench.config import Remote
from bench.positions import Positions, parse_positions
from bench.viz import render_html, snapshot

UID1 = (0x0014008F, 0x32325014, 0x20383543)
UID2 = (0x0026001A, 0x32325014, 0x20383543)
NOW = datetime(2026, 10, 4, 20, 30, tzinfo=timezone.utc)


def boards():
    return [Board("003D003D3234510833353533", "COM8", UID1, "trace", 1, "0fcb4fc-o599ce4"),
            Board("004D00303333511431363730", "COM10"),                       # a probe with no UID yet
            Board("nuna-node-02", "tcp://nuna-node-02:4000/nuna-node-02", UID2, "swd", 2, remote=True)]


def snap(held=None, down=()):
    return snapshot(boards(), list(down), held or {}, "78-bench-remote-nodes", NOW)


def test_the_snapshot_separates_local_probes_from_pi_nodes():
    s = snap()
    assert [(b["kind"], b["where"]) for b in s["boards"]] == [
        ("st-link", "COM8"), ("st-link", "COM10"), ("pi-node", "nuna-node-02:4000")]
    assert s["worktree"] == "78-bench-remote-nodes" and s["generated"] == "2026-10-04T20:30:00Z"


def test_a_board_is_free_new_or_held():
    s = snap(held={"003D003D3234510833353533": "82-fine-stamps: bench run x.toml (pid 7, since 19:43:20 UTC)"})
    status = {b["where"]: b["status"] for b in s["boards"]}
    assert status == {"COM8": "held", "COM10": "new", "nuna-node-02:4000": "free"}
    assert s["boards"][0]["held_by"].startswith("82-fine-stamps")


def test_the_snapshot_is_json_and_names_the_uid_as_the_firmware_logs_it():
    s = snap()
    assert json.loads(json.dumps(s)) == s
    assert s["boards"][0]["uid"] == "0014008f3232501420383543" and s["boards"][1]["uid"] is None


def test_pi_nodes_that_do_not_answer_are_listed_apart():
    s = snap(down=[Remote("nuna-node-01", "nuna-node-01")])
    assert s["not_answering"] == [{"id": "nuna-node-01", "where": "nuna-node-01:4000"}]
    assert [b["id"] for b in s["boards"]] == ["003D003D3234510833353533", "004D00303333511431363730", "nuna-node-02"]


def test_the_page_shows_every_board_with_its_node_id_and_state():
    html = render_html(snap(held={"003D003D3234510833353533": "82-fine-stamps: bench run x.toml (pid 7)"},
                            down=[Remote("nuna-node-01", "nuna-node-01")]))
    assert "<title>Bench Map</title>" in html
    for text in ("COM8", "COM10", "nuna-node-02", "nuna-node-01", "0fcb4fc-o599ce4", "82-fine-stamps"):
        assert text in html
    assert html.count('<article class="card"') == 4
    for state in ("free", "held", "new", "not answering"):
        assert f'data-state="{state}"' in html


def test_the_page_is_self_contained_and_follows_the_viewers_theme():
    html = render_html(snap())
    assert not re.search(r"https?://", html)
    assert "prefers-color-scheme: dark" in html and ':root[data-theme="dark"]' in html
    assert "background: var(--bg)" in html


def test_text_from_boards_and_hosts_cannot_inject_markup():
    evil = [Board("n<script>x</script>", "tcp://h:4000/n", UID1, "swd", 1, '"><img src=x onerror=y>', remote=True)]
    html = render_html(snapshot(evil, [], {"n<script>x</script>": "<blink>w</blink>"}, "wt<marquee>", NOW))
    assert not any(tag in html for tag in ("<script>", "<img", "<blink>", "<marquee>"))
    assert "&lt;script&gt;" in html and "&lt;blink&gt;" in html


def test_an_empty_bench_says_so_instead_of_showing_empty_columns():
    html = render_html(snapshot([], [], {}, "main", NOW))
    assert "No board connected" in html


def test_a_board_recorded_by_another_sessions_capture_says_so():
    s = snapshot(boards(), [], {}, "wt", NOW, elsewhere={"003D003D3234510833353533": "82-fine-stamps"})
    assert [b["capture_elsewhere"] for b in s["boards"]] == ["82-fine-stamps", None, None]
    html = render_html(s)
    assert "recorded by 82-fine-stamps" in html and html.count("recorded by") == 1


def test_a_captures_output_folder_names_its_worktree():
    from bench.viz import capture_owner

    wt = r"\\wsl.localhost\Ubuntu\home\simon\Projects\Firmware\.claude\worktrees\82-fine-stamps\tools\arclog\runs\bench"
    assert capture_owner(wt) == "82-fine-stamps"
    assert capture_owner(r"\\wsl.localhost\Ubuntu\home\simon\Projects\Firmware\tools\arclog\runs\bench") == "main"
    assert capture_owner(r"C:\elsewhere\out") == r"C:\elsewhere\out"


# --- placement (issue #85): shown when it is known, nothing when it is not ---------------------------------

POS = parse_positions(f"""
[default]
place = "Main site"
lat = 55.0
lon = 12.0
[[board]]
uid = "0026001a3232501420383543"
place = "East end"
lat = 55.0
lon = 12.01
alt = 20.5
""")


def placed_snap(positions=POS, **kw):
    return snapshot(boards(), [], {}, "85-positions", NOW, positions=positions, **kw)


def test_a_board_shows_its_own_place_and_the_others_the_default():
    s = placed_snap()
    by_where = {b["where"]: b["place"] for b in s["boards"]}
    assert by_where["nuna-node-02:4000"] == {"name": "East end", "lat": 55.0, "lon": 12.01, "alt": 20.5, "own": True}
    assert by_where["COM8"] == {"name": "Main site", "lat": 55.0, "lon": 12.0, "alt": None, "own": False}
    assert by_where["COM10"] is None          # a probe with no UID yet: nothing is known of it, not even the default


def test_the_snapshot_lists_the_links_between_places_with_their_distance_and_expected_loss():
    [link] = placed_snap()["links"]
    assert (link["a"], link["b"]) == ("COM8", "nuna-node-02")         # the Pi a board sits on, not its Node ID
    assert link["distance_m"] == 637.8 or abs(link["distance_m"] - 637.8) < 1.0
    assert link["expected_rssi_dbm"] < 14 and link["expected_snr_db"] > 0


def test_boards_at_the_same_place_make_no_link():
    one_place = parse_positions('[default]\nlat = 55.0\nlon = 12.0\n')
    assert placed_snap(one_place)["links"] == []


def test_without_positions_the_snapshot_and_the_page_say_nothing_about_places():
    for s in (snap(), placed_snap(Positions())):
        assert all(b["place"] is None for b in s["boards"]) and s["links"] == []
        html = render_html(s)
        assert "Main site" not in html and "Expected links" not in html and ">at<" not in html


def test_the_page_shows_the_place_of_each_board_and_the_expected_links():
    html = render_html(placed_snap())
    assert "East end" in html and "Main site" in html and "55.01000" not in html
    assert "Expected links" in html and "638 m" in html and "COM8 - nuna-node-02" in html
    assert "free space" in html            # says what the expected figures assume


def test_a_place_name_cannot_inject_markup():
    evil = parse_positions('[default]\nplace = "<script>x</script>"\nlat = 1\nlon = 2\n')
    assert "<script>" not in render_html(placed_snap(evil))


# --- the air (issue #85): which board sends Sync on which channel, which channels a session holds -----------------

from bench.channels import Transmitter  # noqa: E402

TX_TIME = datetime(2026, 10, 4, 20, 27, tzinfo=timezone.utc)


def air_snap(**kw):
    on_air = {"nuna-node-02": Transmitter(868_300_000, TX_TIME)}
    return snapshot(boards(), [], {}, "85-positions", NOW, on_air=on_air, **kw)


def test_a_board_that_sent_sync_shows_its_channel_and_when():
    s = air_snap()
    by_where = {b["where"]: b["on_air"] for b in s["boards"]}
    assert by_where["nuna-node-02:4000"] == {"mhz": "868.3 MHz", "freq_hz": 868_300_000, "last_tx": "20:27 UTC"}
    assert by_where["COM8"] is None


def test_the_page_shows_the_channel_of_a_board_on_the_air_and_the_channels_in_use():
    html = render_html(air_snap(channels={868_300_000: "101-crash-record: bench run x.toml (pid 7)"}))
    assert "868.3 MHz" in html and "20:27 UTC" in html
    assert "Channels in use" in html and "101-crash-record" in html


def test_without_air_the_page_says_nothing_about_channels():
    html = render_html(snap())
    assert "Channels in use" not in html and "on air" not in html


def test_a_position_that_no_board_has_is_named():
    s = air_snap(unknown_positions=["0123456789abcdef01234567"])
    assert s["unknown_positions"] == ["0123456789abcdef01234567"]
    assert "0123456789abcdef01234567" in render_html(s) and "no board" in render_html(s)
