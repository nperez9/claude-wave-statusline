#!/usr/bin/env bash
# Remove the wave status line, leaving the rest of settings.json alone.
set -euo pipefail

SCOPE="user"
PURGE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) SCOPE="project" ;;
    --purge) PURGE=1 ;;
    -h|--help) echo "Usage: ./uninstall.sh [--project] [--purge]"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$SCOPE" = "project" ]; then SETTINGS="$PWD/.claude/settings.json"; else SETTINGS="$HOME/.claude/settings.json"; fi

SETTINGS="$SETTINGS" python3 <<'PY'
import json, os, shutil, time
path = os.environ["SETTINGS"]
if not os.path.exists(path):
    raise SystemExit("nothing to do: %s does not exist" % path)
path = os.path.realpath(path) if os.path.islink(path) else path
shutil.copy2(path, "%s.bak.%s" % (path, time.strftime("%Y%m%d-%H%M%S")))
with open(path) as fh:
    data = json.load(fh) or {}
removed = [k for k in ("statusLine", "spinnerVerbs") if data.pop(k, None) is not None]

FINISH_CMD = "python3 ~/.claude/finish-sound.py"
stop_list = data.get("hooks", {}).get("Stop")
if stop_list:
    before = len(stop_list)
    stop_list[:] = [entry for entry in stop_list
                     if not any(h.get("command") == FINISH_CMD
                                for h in entry.get("hooks", []))]
    if len(stop_list) != before:
        removed.append("Stop hook")
    if not stop_list:
        data["hooks"].pop("Stop")
    if "hooks" in data and not data["hooks"]:
        data.pop("hooks")

with open(path, "w") as fh:
    json.dump(data, fh, indent=2); fh.write("\n")
print("removed %s from %s" % (", ".join(removed) or "nothing", path))
PY

if [ "$PURGE" = "1" ]; then
  rm -f "$HOME/.claude/statusline.py" "$HOME/.claude/finish-sound.py" "$HOME/.claude/finish-sound.mp3"
  echo "deleted ~/.claude/statusline.py, finish-sound.py, finish-sound.mp3"
fi
