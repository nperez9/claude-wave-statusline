#!/usr/bin/env python3
"""Claude Code status line: a colour-cycling block wave + a metrics panel.

Reads the status line JSON payload on stdin and prints a multi-line block, side
by side, ordered by WAVE_SIDE. The wave is sized to the panel so both sides
share a height. The wave marches and its colours cycle; the panel never moves,
because it leads and its width is fixed.

Whichever side leads must be exactly as wide as it claims, or the other side
drifts row to row. That is why empty wave cells carry a track glyph instead of
a space: runs of trailing spaces do not survive to the terminal, and a leading
wave built from them takes the panel's left edge with it.
"""
import json
import math
import os
import re
import sys
import time
import unicodedata

# --- knobs -------------------------------------------------------------------
# Every option below is safe to edit in place; there is no config file.
SECTIONS = ("model", "env", "context", "cost")   # also: "tokens", "limits"
GAP = 6                       # columns between the two blocks
PANEL_WIDTH = 64              # fixed, so the box never resizes with its content
WAVE_SIDE = "right"           # "right" = panel leads, "left" = wave leads
WAVE_COLS = 26                # width of the waveform
WAVE_TRACK = "░"              # empty-cell glyph; see the note above on spaces
WAVE_ROWS = None              # None = match the panel height exactly
WAVE_MOTION = True            # False = frozen crest, colours still sweep
FREEZE_PHASE = 0.15           # which crest to hold when frozen
IDLE_SHOW_TRACK = False       # True = keep the dim empty frame visible at rest
ACTIVE_WINDOW = 5.0           # mtime fallback window, if the transcript is unreadable
TAIL_BYTES = 1 << 20          # how far back to look for the last message entry
IDLE_PERIOD = 14.0            # seconds per cycle when idle
ACTIVE_PERIOD = 6.0           # seconds per cycle while working

# --- waveform ----------------------------------------------------------------
BLOCKS = " ▁▂▃▄▅▆▇█"          # 0..8 eighths of a cell
FLOOR = 0.10                  # shortest column, as a fraction of full height


def amplitudes(cols, phase, active):
    """Two traveling sines, so the crest never looks like a repeating tile."""
    swing = 0.42 if active else 0.34
    l1, l2 = max(2.0, cols / 1.6), max(2.0, cols / 3.7)
    raw = [0.5
           + swing * math.sin(2 * math.pi * (x / l1 - phase))
           + 0.16 * math.sin(2 * math.pi * (x / l2 + 1.7 * phase))
           for x in range(cols)]
    lo, hi = min(raw), max(raw)
    if hi - lo < 1e-6:
        return [0.5] * cols
    # stretch so the tallest column reaches the top row and the shortest
    # still shows a stub -- otherwise dead rows sit above the crest
    return [FLOOR + (v - lo) / (hi - lo) * (1.0 - FLOOR) for v in raw]


def wave_rows(cols, rows, phase, active):
    """Column bars rising from the baseline, in eighth-of-a-cell steps.

    When frozen, neither the phase nor the active state may reach the geometry
    -- `active` feeds the swing, so letting it through would reshape the bars
    the moment work starts. Only the colours are allowed to react.
    """
    if WAVE_MOTION:
        amps = amplitudes(cols, phase, active)
    else:
        amps = amplitudes(cols, FREEZE_PHASE, False)
    grid = []
    for y in range(rows):
        below = (rows - 1 - y) * 8          # eighths already filled beneath us
        line = []
        for a in amps:
            level = int(round(a * rows * 8)) - below
            line.append(BLOCKS[max(0, min(8, level))])
        grid.append(line)
    return grid, amps


# --- colour ------------------------------------------------------------------
# The wave runs the panel's own palette as a spectrum, bottom to top, so the
# two halves read as one system: blue -> violet -> cyan -> green -> gold -> rose.
SPECTRUM = ((122, 162, 247), (176, 150, 244), (116, 208, 200),
            (152, 224, 140), (226, 192, 110), (238, 138, 158))
HOT_RAMP = SPECTRUM
IDLE_RAMP = tuple(tuple(int(c * 0.6) for c in stop) for stop in SPECTRUM)
QUANT = 12

ANSI_RE = re.compile(r"\x1b\[[\d;]*m")
# A blank track is only safe on the trailing block; leading, its trailing
# spaces are exactly what used to drag the panel's left edge around.
TRACK_GLYPH = WAVE_TRACK if WAVE_TRACK.strip() or WAVE_SIDE == "right" else "░"
RESET = "\x1b[0m"
BORDER = "\x1b[38;2;74;92;112m"
TRACK = "\x1b[38;2;48;58;72m"
DIM = "\x1b[38;2;108;118;134m"
LBL = "\x1b[38;2;130;142;158m"
VAL = "\x1b[38;2;198;208;220m"
ACC = "\x1b[38;2;116;208;200m"
OK = "\x1b[38;2;168;240;150m"
WARN = "\x1b[38;2;232;186;96m"
BAD = "\x1b[38;2;226;120;110m"
BLUE = "\x1b[38;2;122;162;247m"      # fresh input, elapsed time
VIOLET = "\x1b[38;2;176;150;244m"    # cached / plan
AMBER = "\x1b[38;2;226;170;92m"      # cache writes
GREEN = "\x1b[38;2;152;224;140m"     # output, additions
GOLD = "\x1b[38;2;226;192;110m"      # money
ROSE = "\x1b[38;2;238;138;158m"      # deletions
TEAL = "\x1b[38;2;108;196;192m"      # window size


def ramp_at(ramp, p):
    p = p % 1.0
    n = len(ramp)
    i = int(p * n)
    f = p * n - i
    a, b = ramp[i % n], ramp[(i + 1) % n]
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def paint(grid, amps, ramp, phase):
    """Tall columns glow hot; the whole ramp drifts with the phase.

    Empty cells get a dim track glyph rather than a space. Runs of trailing
    spaces do not survive the trip to the terminal intact, and the panel's
    left edge was riding on them -- so every cell here carries a glyph and the
    only whitespace left on a line is interior.
    """
    rows = len(grid)
    painted = []
    for y, line in enumerate(grid):
        buf, last = [], None
        for x, ch in enumerate(line):
            if ch == " ":
                if last != "track":
                    buf.append(TRACK)
                    last = "track"
                buf.append(TRACK_GLYPH)
                continue
            p = (0.82 * (1 - y / max(1, rows))
                 + 0.18 * (x / max(1, len(line)))
                 - phase) % 1.0
            band = int(p * QUANT)
            if band != last:
                buf.append("\x1b[38;2;%d;%d;%dm" % ramp_at(ramp, band / QUANT))
                last = band
            buf.append(ch)
        buf.append(RESET)
        painted.append("".join(buf))
    return painted


def compress(line):
    """Strip SGR codes that cannot change what is drawn.

    Claude Code's multi-line splitter cumulatively prepends every ANSI code from
    earlier lines onto each later line, so one redundant code costs bytes on
    every line below it. Only foreground colours are used here, so a reset whose
    entire run is whitespace is invisible and can go; so can any code that
    repeats the currently active one.
    """
    toks, pos = [], 0
    for m in ANSI_RE.finditer(line):
        if m.start() > pos:
            toks.append(("t", line[pos:m.start()]))
        toks.append(("c", m.group(0)))
        pos = m.end()
    if pos < len(line):
        toks.append(("t", line[pos:]))

    out, active = [], None
    for i, (kind, val) in enumerate(toks):
        if kind == "t":
            out.append(val)
            continue
        if val == active:
            continue
        if val == RESET:
            nxt = next((j for j in range(i + 1, len(toks)) if toks[j][0] == "c"), None)
            if nxt is not None:
                between = "".join(v for k, v in toks[i + 1:nxt] if k == "t")
                if between.strip() == "":
                    continue
        out.append(val)
        active = val
    res = "".join(out)
    return res if res.endswith(RESET) else res + RESET


# --- width-aware text --------------------------------------------------------
def vwidth(s):
    s = ANSI_RE.sub("", s)
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def fit(s, width):
    """Pad or clip to an exact visible width, keeping colour codes intact."""
    w = vwidth(s)
    if w <= width:
        return s + " " * (width - w)
    out, used = [], 0
    for m in re.finditer(r"\x1b\[[\d;]*m|.", s, re.S):
        tok = m.group(0)
        if tok.startswith("\x1b"):
            out.append(tok)
            continue
        cw = 2 if unicodedata.east_asian_width(tok) in ("W", "F") else 1
        if used + cw > width - 1:
            out.append("…")
            used += 1
            break
        out.append(tok)
        used += cw
    return "".join(out) + " " * (width - used)


# --- metrics -----------------------------------------------------------------
def human(n):
    if n is None:
        return "—"
    n = float(n)
    if n >= 1_000_000:
        return "%.2fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fk" % (n / 1_000)
    return "%d" % n


def tone_for(frac):
    return OK if frac < 0.6 else (WARN if frac < 0.85 else BAD)


def bar(frac, width=12):
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "%s%s%s%s%s" % (tone_for(frac), "█" * filled, DIM,
                           "░" * (width - filled), RESET)


LABEL_W = 8                   # left label column
CELL_L = 6                    # sub-label inside a cell
CELL_V = 8                    # value inside a cell, right-aligned
CELL_SEP = 2                  # blanks between cells
CELLS = 3                     # cells per data row
GRID_W = CELLS * (CELL_L + CELL_V) + (CELLS - 1) * CELL_SEP


def row(label, body, tone=None):
    return "%s%-*s%s%s" % (tone or LBL, LABEL_W, label, RESET, body)


def cell(sub, value, tone=None):
    """One grid cell: sub-label left, value right-aligned so digits stack.

    Padding is measured on visible width, so a value that carries its own
    colours (``+412/-96`` in two of them) still lands on the column.
    """
    pad = " " * max(0, CELL_V - vwidth(value))
    tinted = value if tone is None and ANSI_RE.search(value) \
        else (tone or VAL) + value + RESET
    return "%s%-*s%s%s%s" % (LBL, CELL_L, sub, RESET, pad, tinted)


def grid(label, *cells, tone=None):
    """A data row on the fixed column grid; missing cells stay blank."""
    filled = list(cells) + [" " * (CELL_L + CELL_V)] * (CELLS - len(cells))
    return row(label, (" " * CELL_SEP).join(filled[:CELLS]), tone)


def short_path(p, home):
    if not p:
        return "—"
    if p.startswith(home):
        p = "~" + p[len(home):]
    parts = p.split(os.sep)
    if len(parts) > 3:
        parts = [parts[0], "…"] + parts[-2:]
    return os.sep.join(parts)


def build_panel(d, active):
    """Fixed set of rows, every time -- missing data renders as a dash, so the
    box height and the block's vertical alignment never shift."""
    home = os.path.expanduser("~")
    ctx = d.get("context_window") or {}
    use = d.get("current_usage") or ctx.get("current_usage") or {}
    cost = d.get("cost") or {}
    ws = d.get("workspace") or {}
    rows, seps = [], []

    if "model" in SECTIONS:
        bits = ["%s%s%s" % (ACC, (d.get("model") or {}).get("display_name", "—"), RESET)]
        eff = (d.get("effort") or {}).get("level")
        if eff:
            bits.append("%s%s%s" % (VIOLET, eff, RESET))
        if (d.get("thinking") or {}).get("enabled"):
            bits.append("%sthinking%s" % (DIM, RESET))
        if d.get("fast_mode"):
            bits.append("%sfast%s" % (WARN, RESET))
        bits.append("%s%s%s" % (OK, "working", RESET) if active
                    else "%s%s%s" % (DIM, "idle", RESET))
        rows.append((" %s·%s " % (DIM, RESET)).join(bits))

    if "env" in SECTIONS:
        repo = ws.get("repo") or {}
        where = "%s/%s" % (repo.get("owner"), repo.get("name")) if repo \
            else short_path(ws.get("current_dir"), home)
        line = "%s%s%s" % (VAL, where, RESET)
        branch = (d.get("worktree") or {}).get("branch") or ws.get("git_worktree")
        if branch:
            line += " %s%s%s" % (ACC, branch, RESET)
        pr = d.get("pr")
        if pr:
            mark = "!" if pr.get("kind") == "mr" else "#"
            st = {"changes_requested": "changes req", "approved": "approved",
                  "pending": "review pending", "draft": "draft"}.get(
                      pr.get("review_state"), "open")
            tone = OK if st == "approved" else (WARN if st == "changes requested" else DIM)
            line += " %s%s%s%s %s%s%s" % (ACC, mark, pr.get("number"), RESET, tone, st, RESET)
        rows.append(line)

    if "context" in SECTIONS or "tokens" in SECTIONS:
        seps.append(len(rows))

    frac = 0.0
    if "context" in SECTIONS:
        used = ctx.get("used_percentage")
        total = ctx.get("total_input_tokens") or 0
        size = ctx.get("context_window_size") or 0
        frac = (used / 100.0) if used is not None else (total / size if size else 0.0)
        pct = used if used is not None else frac * 100
        rows.append(grid("ctx",
                         cell("used", "%.0f%%" % pct, tone_for(frac)),
                         cell("tokens", human(total), ACC),
                         cell("window", human(size),
                              WARN if d.get("exceeds_200k_tokens") else TEAL),
                         tone=ACC))
        rows.append(row("", bar(frac, GRID_W)))

    if "tokens" in SECTIONS:
        read = use.get("cache_read_input_tokens") or 0
        write = use.get("cache_creation_input_tokens") or 0
        total_in = ctx.get("total_input_tokens") or 0
        hit = (read / total_in * 100) if total_in else 0.0
        rows.append(grid("input",
                         cell("fresh", human(use.get("input_tokens")), BLUE),
                         cell("cached", human(read), VIOLET),
                         cell("new", human(write), AMBER),
                         tone=BLUE))
        rows.append(grid("output",
                         cell("turn", human(ctx.get("total_output_tokens")), GREEN),
                         cell("hit", "%.0f%%" % hit,
                              OK if hit >= 70 else (WARN if hit >= 30 else DIM)),
                         tone=GREEN))

    if "cost" in SECTIONS:
        mins = (cost.get("total_duration_ms") or 0) / 60000.0
        rows.append(grid("session",
                         cell("spent", "$%.2f" % (cost.get("total_cost_usd") or 0.0), GOLD),
                         cell("time", "%.0fm" % mins, BLUE),
                         cell("lines", "%s+%d%s/%s-%d%s" % (
                             GREEN, cost.get("total_lines_added") or 0, RESET,
                             ROSE, cost.get("total_lines_removed") or 0, RESET)),
                         tone=GOLD))

    if "limits" in SECTIONS:
        rl = d.get("rate_limits") or {}
        parts = []
        for key, tag in (("five_hour", "5h"), ("seven_day", "7d")):
            win = rl.get(key)
            if not win:
                continue
            pct = win.get("used_percentage") or 0
            left = max(0, int((win.get("resets_at") or 0) - time.time()))
            when = "%dh%02dm" % (left // 3600, (left % 3600) // 60) if left >= 3600 \
                else "%dm" % (left // 60)
            parts.append(cell(tag, "%.0f%% %s" % (pct, when), tone_for(pct / 100.0)))
        rows.append(grid("plan", *parts, tone=VIOLET) if parts
                    else grid("plan", cell("", "—"), tone=VIOLET))

    return rows, seps


def draw_box(rows, seps, title, width):
    inner = width - 4
    head = " %s " % title if title else ""
    left = "─" * 2
    fill = "─" * max(0, width - 2 - len(left) - vwidth(head))
    out = ["%s╭%s%s%s%s%s%s" % (BORDER, left, ACC, head, BORDER, fill, "╮" + RESET)]
    for i, line in enumerate(rows):
        if i in seps:
            out.append("%s├%s┤%s" % (BORDER, "─" * (width - 2), RESET))
        out.append("%s│%s %s %s│%s" % (BORDER, RESET, fit(line, inner), BORDER, RESET))
    out.append("%s╰%s╯%s" % (BORDER, "─" * (width - 2), RESET))
    return out


# --- "is Claude working?" -----------------------------------------------------
def is_working(path):
    """True while Claude owes a reply, None if it cannot be determined.

    File mtime is useless here: the transcript is flushed in batches, so it can
    sit a minute stale mid-turn. What is reliable is the last entry carrying a
    message. If that message is the user's, Claude has not answered yet. If it
    is Claude's, the turn is over -- unless it ends in a tool_use, in which case
    a tool is still running.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    step, pos, buf = 65536, size, b""
    try:
        with open(path, "rb") as fh:
            while pos > 0 and len(buf) < TAIL_BYTES:
                back = min(step, pos)
                pos -= back
                fh.seek(pos)
                buf = fh.read(back) + buf
                lines = buf.split(b"\n")
                for raw in reversed(lines if pos == 0 else lines[1:]):
                    if not raw.strip():
                        continue
                    try:
                        msg = (json.loads(raw) or {}).get("message")
                    except Exception:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("role") == "user":
                        return True
                    if msg.get("role") == "assistant":
                        body = msg.get("content")
                        return isinstance(body, list) and any(
                            isinstance(c, dict) and c.get("type") == "tool_use" for c in body)
                step *= 2
    except OSError:
        return None
    return None


# --- render ------------------------------------------------------------------
def main():
    try:
        d = json.loads(sys.stdin.read() or "{}")
    except Exception:
        d = {}

    now = time.time()
    tp = d.get("transcript_path")
    active = is_working(tp) if tp else None
    if active is None and tp:
        try:
            active = (now - os.path.getmtime(tp)) < ACTIVE_WINDOW
        except OSError:
            active = False
    active = bool(active)

    period = ACTIVE_PERIOD if active else IDLE_PERIOD
    phase = (now % period) / period
    ramp = HOT_RAMP if active else IDLE_RAMP

    body, seps = build_panel(d, active)
    box = draw_box(body, seps, d.get("session_name") or "claude", PANEL_WIDTH)

    if not (active or IDLE_SHOW_TRACK):
        sys.stdout.write("\n".join(compress(l) for l in box))
        return
    grid, amps = wave_rows(WAVE_COLS, WAVE_ROWS or len(box), phase, active)
    wave = paint(grid, amps, ramp, phase)

    pad = " " * GAP
    height = max(len(box), len(wave))
    wave_top = height - len(wave)          # bottom-aligned: bars sit on the floor
    blank = TRACK + TRACK_GLYPH * WAVE_COLS + RESET
    out = []
    for i in range(height):
        w = fit(wave[i - wave_top], WAVE_COLS) if i >= wave_top else blank
        if WAVE_SIDE == "left":
            out.append(w + pad + box[i] if i < len(box) else w)
        else:
            out.append((box[i] if i < len(box) else " " * PANEL_WIDTH) + pad + w)
    sys.stdout.write("\n".join(compress(l) for l in out))


if __name__ == "__main__":
    main()
