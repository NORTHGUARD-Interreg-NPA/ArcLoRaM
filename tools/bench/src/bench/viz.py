"""The bench map: which boards are connected locally (ST-LINK) and remotely (Pi Nodes), and whether they are free.

`snapshot` turns what `bench boards` found into plain data (also its `--json`), and `render_html`
draws it as one self-contained page that follows the viewer's light or dark theme. A page is a
snapshot of the moment it was made: `bench map` makes a new one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit

from bench.boards import Board, format_uid
from bench.config import Remote

TITLE = "Bench Map"


def capture_owner(out: str) -> str:
    """Who a capture's output folder belongs to: its worktree's name, 'main', or the folder as given."""
    parts = out.replace("/", "\\").rstrip("\\").split("\\")
    if "worktrees" in parts and parts.index("worktrees") + 1 < len(parts):
        return parts[parts.index("worktrees") + 1]
    return "main" if parts[-3:] == ["arclog", "runs", "bench"] else out


def snapshot(boards: list[Board], down: list[Remote], held: dict[str, str], worktree: str,
             now: datetime, elsewhere: dict[str, str] | None = None) -> dict:
    """Plain data for the map. `held` maps a board id to who holds it (see Leases.holders); `elsewhere`
    maps a board id to the session whose capture records it (a session without leases shows only there)."""
    entries = []
    for b in boards:
        status = "held" if b.sn in held else ("free" if b.node_id is not None else "new")
        entries.append({
            "id": b.sn,
            "kind": "pi-node" if b.remote else "st-link",
            "where": urlsplit(b.port).netloc if b.remote else (b.port or "no COM port"),
            "node_id": b.node_id,
            "uid": format_uid(b.uid) if b.uid else None,
            "uid_source": b.uid_source or None,
            "build": b.build,
            "status": status,
            "held_by": held.get(b.sn),
            "capture_elsewhere": (elsewhere or {}).get(b.sn),
        })
    return {
        "generated": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "worktree": worktree,
        "boards": entries,
        "not_answering": [{"id": r.name, "where": f"{r.host}:{r.log}"} for r in down],
    }


_STATE_TEXT = {"free": "free", "held": "held", "new": "new board", "not answering": "not answering"}

_CSS = """
:root {
  --bg: #f3f5f7; --panel: #ffffff; --ink: #14222e; --muted: #5a6b79; --line: #c5cfd8;
  --free: #1b7f4b; --free-bg: #e0f3e8; --held: #9a5b00; --held-bg: #fbecd0;
  --new: #2f5fb3; --new-bg: #e1eafa; --down: #b3261e; --down-bg: #fbe3e1;
  --mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
  --sans: system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0e171f; --panel: #16222c; --ink: #e4ecf2; --muted: #93a4b2; --line: #2d3d4a;
    --free: #55c88a; --free-bg: #143323; --held: #f0b44c; --held-bg: #3a2a0c;
    --new: #7ea8f2; --new-bg: #17294a; --down: #f08a82; --down-bg: #3d1816;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #0e171f; --panel: #16222c; --ink: #e4ecf2; --muted: #93a4b2; --line: #2d3d4a;
  --free: #55c88a; --free-bg: #143323; --held: #f0b44c; --held-bg: #3a2a0c;
  --new: #7ea8f2; --new-bg: #17294a; --down: #f08a82; --down-bg: #3d1816;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--ink); font: 15px/1.45 var(--sans); margin: 0;
       padding-inline: 16px; padding-block: 28px 40px; }
.wrap { max-width: 980px; margin: 0 auto; display: grid; gap: 24px; }
h1 { font: 600 1.55rem/1.2 var(--mono); letter-spacing: -0.01em; margin: 0; }
.meta { color: var(--muted); font-size: 0.9rem; margin: 6px 0 0; }
.meta b { color: var(--ink); font-weight: 600; }
.pc { justify-self: center; text-align: center; background: var(--panel); border: 1.5px solid var(--line);
      border-radius: 10px; padding: 10px 22px; min-width: 0; }
.pc .name { font: 600 1rem var(--mono); }
.pc .sub { color: var(--muted); font-size: 0.82rem; overflow-wrap: anywhere; }
.branches { position: relative; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px;
            padding-top: 28px; }
.branches::before { content: ""; position: absolute; top: 0; left: 50%; height: 14px; border-left: 2px solid var(--line); }
.branches::after { content: ""; position: absolute; top: 14px; left: 25%; right: 25%; border-top: 2px solid var(--line); }
.branch { position: relative; display: grid; gap: 12px; align-content: start; min-width: 0; }
.branch::before { content: ""; position: absolute; top: -14px; left: 50%; height: 14px; border-left: 2px solid var(--line); }
.branch h2 { font: 600 0.78rem var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted);
             margin: 0; text-align: center; }
.cards { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(min(100%, 210px), 1fr)); }
.card { background: var(--panel); border: 1.5px solid var(--line); border-radius: 10px; padding: 12px 14px;
        display: grid; gap: 6px; min-width: 0; }
.card[data-state="held"] { border-color: var(--held); }
.card[data-state="not answering"] { border-style: dashed; border-color: var(--down); }
.top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.nid { font: 700 2rem/1 var(--mono); font-variant-numeric: tabular-nums; }
.nid small { font: 600 0.7rem var(--mono); color: var(--muted); letter-spacing: 0.06em; margin-right: 4px; }
.pill { font: 600 0.72rem var(--mono); letter-spacing: 0.04em; border-radius: 999px; padding: 3px 9px; white-space: nowrap; }
[data-state="free"] .pill, .pill[data-state="free"] { color: var(--free); background: var(--free-bg); }
[data-state="held"] .pill, .pill[data-state="held"] { color: var(--held); background: var(--held-bg); }
[data-state="new"] .pill, .pill[data-state="new"] { color: var(--new); background: var(--new-bg); }
[data-state="not answering"] .pill, .pill[data-state="not answering"] { color: var(--down); background: var(--down-bg); }
dl { margin: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 2px 10px; font-size: 0.84rem; }
dt { color: var(--muted); }
dd { margin: 0; font-family: var(--mono); overflow-wrap: anywhere; }
.who { font-size: 0.82rem; color: var(--held); overflow-wrap: anywhere; }
.none { color: var(--muted); text-align: center; border: 1.5px dashed var(--line); border-radius: 10px; padding: 16px; font-size: 0.9rem; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--muted); font-size: 0.85rem; align-items: center; }
.legend span { display: inline-flex; gap: 8px; align-items: center; }
footer { color: var(--muted); font-size: 0.85rem; }
footer code { font-family: var(--mono); color: var(--ink); }
@media (max-width: 720px) {
  .branches { grid-template-columns: minmax(0, 1fr); gap: 36px; }
  .branches::after { display: none; }
  .branch + .branch::before { top: -26px; height: 26px; }
}
"""


def _card(entry: dict) -> str:
    state = entry["status"]
    nid = entry["node_id"]
    id_html = f'<span class="nid"><small>NODE</small>{escape(str(nid))}</span>' if nid is not None else \
        '<span class="nid"><small>NODE</small>?</span>'
    rows = [("on", entry["where"])]
    if entry.get("uid"):
        rows.append(("uid", f'{entry["uid"][:8]}…{entry["uid"][-4:]} ({entry["uid_source"]})'))
    if entry.get("build"):
        rows.append(("build", entry["build"]))
    rows.append(("id", entry["id"]))
    dl = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    who = f'<div class="who">held by {escape(entry["held_by"])}</div>' if entry.get("held_by") else ""
    if entry.get("capture_elsewhere"):
        who += f'<div class="who">recorded by {escape(entry["capture_elsewhere"])}: the board may be in use</div>'
    note = '<div class="who" style="color: var(--new)">UID not in node_id.c: onboard it on the first run</div>' \
        if state == "new" else ""
    return (f'<article class="card" data-state="{state}"><div class="top">{id_html}'
            f'<span class="pill">{escape(_STATE_TEXT[state])}</span></div><dl>{dl}</dl>{who}{note}</article>')


def _down_card(entry: dict) -> str:
    return (f'<article class="card" data-state="not answering"><div class="top">'
            f'<span class="nid"><small>NODE</small>?</span><span class="pill">{_STATE_TEXT["not answering"]}</span></div>'
            f'<dl><dt>on</dt><dd>{escape(entry["where"])}</dd><dt>id</dt><dd>{escape(entry["id"])}</dd></dl></article>')


def _branch(title: str, cards: list[str], empty: str) -> str:
    body = f'<div class="cards">{"".join(cards)}</div>' if cards else f'<div class="none">{escape(empty)}</div>'
    return f'<section class="branch"><h2>{escape(title)}</h2>{body}</section>'


def render_html(snap: dict, fragment: bool = False) -> str:
    """The page. `fragment` leaves out the document wrapper, for a host that adds its own."""
    boards, down = snap["boards"], snap["not_answering"]
    local = [_card(b) for b in boards if b["kind"] == "st-link"]
    remote = [_card(b) for b in boards if b["kind"] == "pi-node"] + [_down_card(d) for d in down]
    held = sum(1 for b in boards if b["status"] == "held")
    free = sum(1 for b in boards if b["status"] == "free")
    new = sum(1 for b in boards if b["status"] == "new")
    when = f'{snap["generated"][11:16]} UTC, {snap["generated"][:10]}'
    counts = (f"<b>{len(boards)}</b> connected: {free} free, {held} held, {new} new"
              + (f", <b>{len(down)}</b> not answering" if down else ""))
    if not boards and not down:
        topology = '<div class="none">No board connected. Plug in a NUCLEO, or add a Pi Node to tools/bench/pi-nodes.toml.</div>'
    else:
        topology = (f'<div class="pc"><div class="name">this PC</div><div class="sub">worktree '
                    f'{escape(snap["worktree"])}</div></div>'
                    f'<div class="branches">'
                    f'{_branch("USB · ST-LINK probes", local, "No ST-LINK probe connected")}'
                    f'{_branch("Tailnet · Pi Nodes", remote, "No Pi Node configured")}</div>')
    legend = "".join(f'<span><i class="pill" data-state="{k}">{escape(v)}</i>{escape(t)}</span>' for k, v, t in (
        ("free", "free", "ready for a run"), ("held", "held", "another session works on it"),
        ("new", "new board", "UID not in the Node ID table yet"), ("not answering", "not answering", "Pi Node unreachable")))
    head = f'<title>{TITLE}</title><style>{_CSS}</style>'
    body = (f'<div class="wrap"><header><h1>Bench map</h1><p class="meta">Snapshot {escape(when)} · {counts}</p></header>'
            f'{topology}<div class="legend">{legend}</div>'
            f'<footer>A snapshot of the moment it was made. Refresh with <code>bench map</code>. '
            f"Reading a board's trace never needs a lease.</footer></div>")
    if fragment:
        return head + body
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"{head}</head><body>{body}</body></html>")
