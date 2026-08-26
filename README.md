# claude-wave-statusline

A status line for [Claude Code](https://code.claude.com): a metrics panel plus a
spectrum-coloured block wave that only appears while Claude is actually working.

```
╭── my session ────────────────────────────────────────────────╮      ░░░░░░▄█▁░░░░░░░░░░░░▅▆░░░
│ Opus 5 (1M) · xhigh · thinking · working                     │      ░░░░░░███░░░░░░░░░░░░██▆░░
│ acme/backend main #41010 changes req                         │      ░░░░░████▁░░░░░░░░░░████▃░
├──────────────────────────────────────────────────────────────┤      ░░░░▄█████░░░░░░░░░░█████▂
│ ctx     used       14%  tokens  142.3k  window   1.00M       │      ░░▂▄██████░░░░░░░░░▆██████
│         ███████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░       │      ▃██████████░░░░░░░░███████
│ session spent    $2.41  time       18m  lines +412/-96       │      ███████████▄▁▃▄▂░░▆███████
╰──────────────────────────────────────────────────────────────╯      ████████████████▇▆████████
```

At rest the wave disappears and you get the panel alone, so motion means
"Claude is thinking", not "the terminal is decorated".

## Install

```sh
git clone https://github.com/YOUR-NAME/claude-wave-statusline
cd claude-wave-statusline
./install.sh
```

Claude Code picks up settings changes live, so the status line appears within a
second. No restart.

| flag | effect |
| --- | --- |
| `--project` | install into `./.claude/settings.json` (one repo) instead of `~/.claude/settings.json` (everywhere) |
| `--no-spinner` | skip the custom spinner verbs, status line only |

The installer copies `statusline.py` to `~/.claude/statusline.py` and merges two
keys into `settings.json` — `statusLine` and `spinnerVerbs`. Every other key is
left exactly as it was, and the file is backed up to `settings.json.bak.<stamp>`
first. Re-running is safe. `./uninstall.sh` removes both keys again (add
`--purge` to delete the script too).

Requirements: **python3** (3.8+, stdlib only) and a terminal with truecolor.
Works on macOS, Linux, and Windows via Git Bash.

## What it shows

| row | meaning |
| --- | --- |
| header | model · reasoning effort · thinking · `working`/`idle` |
| location | `owner/repo` (or path), branch, open PR with review state |
| `ctx` | context used %, tokens in the window, window size, and a gauge |
| `session` | cost so far, wall-clock elapsed, lines added/removed |

Each row's label is tinted to its own colour so you can find a row by hue.
Thresholds (`used`, the gauge) stay green → amber → red on purpose: there the
colour *is* the data.

Two sections ship switched off. Add them to `SECTIONS` at the top of the script:

- `"tokens"` — an `input` row (fresh / cached / cache-writes) and an `output`
  row with the cache hit rate.
- `"limits"` — a `plan` row with 5-hour and 7-day usage bars and reset countdowns.

## Knobs

All at the top of `statusline.py`; edit in place, there is no config file.

| knob | default | what it does |
| --- | --- | --- |
| `SECTIONS` | `model, env, context, cost` | which panel rows to render |
| `PANEL_WIDTH` | `64` | panel width in columns; fixed so it never reflows |
| `WAVE_SIDE` | `"right"` | `"right"` = panel first, `"left"` = wave first |
| `WAVE_COLS` | `26` | wave width |
| `WAVE_ROWS` | `None` | `None` matches the panel height automatically |
| `WAVE_MOTION` | `True` | `False` freezes the crest, colours still cycle |
| `WAVE_TRACK` | `"░"` | glyph for empty cells (see *Gotchas*) |
| `IDLE_SHOW_TRACK` | `False` | `True` keeps the dim empty frame visible at rest |
| `SPECTRUM` | 6 stops | the wave's palette, bottom to top |
| `IDLE_PERIOD` / `ACTIVE_PERIOD` | `14` / `6` | seconds per colour+motion cycle |
| `GAP` | `6` | columns between the two blocks |

## How "is Claude working?" is decided

Not by file mtime. The transcript is flushed in batches and can sit a full
minute stale mid-turn, which makes mtime useless for this.

Instead `is_working()` reads backwards from the end of the transcript for the
last entry carrying a `message`:

- that message is the **user's** → Claude owes a reply, so it is working
- it is **Claude's** and ends in a `tool_use` → a tool is still running
- it is **Claude's** plain text → the turn is over, idle

mtime is kept only as a fallback for when the transcript cannot be read.

## Gotchas worth knowing

Both of these were found the hard way and are why the code looks the way it does.

**1. Runs of trailing spaces do not survive to the terminal.** If the leading
block is padded with spaces, the block beside it drifts left, row by row, on
exactly the rows that end in whitespace. That is why empty wave cells carry a
`░` track glyph instead of a space: every row is fully inked, so its width is
real. A blank `WAVE_TRACK` is therefore refused when the wave leads — it is
allowed when the wave trails, where trailing spaces are harmless.

**2. Multi-line status lines accumulate ANSI codes.** Claude Code's line
splitter cumulatively prepends *every* SGR code from all preceding lines onto
each later line. One redundant code costs bytes on every line below it — an
uncompressed 2.6KB block ballooned to 20KB per frame. `compress()` drops codes
that repeat the active one and resets whose entire run is whitespace, and the
wave's hue is weighted toward the row so each row stays near-uniform and cheap.

Also: `refreshInterval` is clamped to `max(1, n)` **seconds**, so 1 fps is the
ceiling for timer-driven frames. Event-driven refreshes (token usage, model or
effort changes) fire on top of that with a 300ms debounce, which is why motion
looks livelier mid-turn. Frames are driven off the wall clock so the speed does
not depend on how often the command happens to be invoked.

## License

MIT
