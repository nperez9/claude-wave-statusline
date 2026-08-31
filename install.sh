#!/usr/bin/env bash
# Install the wave status line into Claude Code.
#
# Merges two keys into settings.json (statusLine, spinnerVerbs) and leaves
# every other key untouched. Safe to re-run; backs up settings first.
set -euo pipefail

SCOPE="user"
WITH_SPINNER=1
WITH_FINISH_SOUND=0
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

  --project           install into ./.claude/settings.json (this repo only)
                      instead of ~/.claude/settings.json (all your sessions)
  --no-spinner        skip the custom spinner verbs, status line only
  --with-finish-sound also install a Stop hook that plays a sound when
                      Claude finishes a turn (not on compact, not on rewind
                      -- there is no rewind hook, and compaction fires
                      PreCompact/PostCompact instead of Stop)
  -h, --help          show this

The status line script always lands at ~/.claude/statusline.py so a
project-scoped install still finds it.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) SCOPE="project" ;;
    --no-spinner) WITH_SPINNER=0 ;;
    --with-finish-sound) WITH_FINISH_SOUND=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

command -v python3 >/dev/null 2>&1 || {
  echo "error: python3 not found on PATH. The status line is a python3 script." >&2
  exit 1
}

if [ "$SCOPE" = "project" ]; then
  SETTINGS_DIR="$PWD/.claude"
else
  SETTINGS_DIR="$HOME/.claude"
fi
SETTINGS="$SETTINGS_DIR/settings.json"

mkdir -p "$HOME/.claude" "$SETTINGS_DIR"
install -m 0755 "$SRC_DIR/statusline.py" "$HOME/.claude/statusline.py"
echo "installed $HOME/.claude/statusline.py"

if [ "$WITH_FINISH_SOUND" = "1" ]; then
  install -m 0755 "$SRC_DIR/finish-sound.py" "$HOME/.claude/finish-sound.py"
  install -m 0644 "$SRC_DIR/assets/finish-sound.mp3" "$HOME/.claude/finish-sound.mp3"
  echo "installed $HOME/.claude/finish-sound.py + finish-sound.mp3"
fi

WITH_SPINNER="$WITH_SPINNER" WITH_FINISH_SOUND="$WITH_FINISH_SOUND" SETTINGS="$SETTINGS" python3 <<'PY'
import json, os, shutil, sys, time

path = os.environ["SETTINGS"]
# a symlinked settings.json should be written through, not replaced
path = os.path.realpath(path) if os.path.islink(path) else path

data = {}
if os.path.exists(path):
    shutil.copy2(path, "%s.bak.%s" % (path, time.strftime("%Y%m%d-%H%M%S")))
    try:
        with open(path) as fh:
            data = json.load(fh) or {}
    except ValueError as exc:
        sys.exit("error: %s is not valid JSON (%s). Fix it and re-run." % (path, exc))
    print("backed up %s" % path)

old = data.get("statusLine")
if old and old.get("command", "").find("statusline.py") == -1:
    print("note: replacing your existing statusLine -> %r (the backup has it)"
          % old.get("command"))

data["statusLine"] = {
    "type": "command",
    # ~ resolves on macOS, Linux, and Git Bash on Windows
    "command": "python3 ~/.claude/statusline.py",
    "padding": 0,
    "refreshInterval": 1,
}

if os.environ["WITH_SPINNER"] == "1":
    verbs = ["Mako-charging", "Limit-breaking", "Materia-fusing", "Omnislashing",
             "Buster-swinging", "Chocobo-wrangling", "Reactor-diving", "Summoning",
             "Sephiroth-dodging", "Phoenix-downing", "Save-pointing", "Airship-boarding",
             "Gil-farming", "Highwinding", "Mognet-mailing", "Cure-casting"]
    data["spinnerVerbs"] = {"mode": "append", "verbs": verbs}

if os.environ["WITH_FINISH_SOUND"] == "1":
    FINISH_CMD = "python3 ~/.claude/finish-sound.py"
    stop_list = data.setdefault("hooks", {}).setdefault("Stop", [])
    # drop any entry we installed on a previous run, so re-running is safe,
    # without touching any other Stop hooks (or other events) already there
    stop_list[:] = [entry for entry in stop_list
                     if not any(h.get("command") == FINISH_CMD
                                for h in entry.get("hooks", []))]
    stop_list.append({"hooks": [{"type": "command", "command": FINISH_CMD, "timeout": 10}]})
    print("added Stop hook: %s" % FINISH_CMD)

with open(path, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
print("updated %s" % path)
PY

echo
echo "Done. Claude Code picks up settings changes live -- no restart needed."
echo "Tweak the look by editing the knobs at the top of ~/.claude/statusline.py"
