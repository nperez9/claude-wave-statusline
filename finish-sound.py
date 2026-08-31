#!/usr/bin/env python3
"""Claude Code Stop hook: play a sound when a task actually finishes.

Wired to the "Stop" event only, which fires once per normal turn completion
and -- unlike PreCompact/PostCompact or any subagent event -- never fires for
compaction or (there is no such hook) a conversation rewind. The
hook_event_name check below is a second, explicit guard for that: this script
must stay a no-op for anything but a genuine Stop.

Best-effort by design: any failure here (missing player, unreadable file,
unknown platform) is swallowed and the hook exits 0, so a broken sound never
blocks or errors out Claude's turn.
"""
import json
import os
import shutil
import subprocess
import sys

SOUND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finish-sound.mp3")
DEVNULL = subprocess.DEVNULL


def play_windows(path):
    # System.Media.SoundPlayer only decodes WAV; the WPF MediaPlayer handles
    # mp3. Play() is async, so wait out the clip's own reported duration
    # instead of a guessed sleep -- correct for whatever file ships here.
    escaped = path.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName PresentationCore; "
        "$p = New-Object System.Windows.Media.MediaPlayer; "
        "$p.Open([Uri]::new('%s')); "
        "for ($i = 0; $i -lt 50 -and -not $p.NaturalDuration.HasTimeSpan; $i++) { Start-Sleep -Milliseconds 100 } "
        "$p.Play(); "
        "if ($p.NaturalDuration.HasTimeSpan) { Start-Sleep -Milliseconds ($p.NaturalDuration.TimeSpan.TotalMilliseconds + 200) } else { Start-Sleep -Seconds 3 } "
        "$p.Close()" % escaped
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                    timeout=15, stdout=DEVNULL, stderr=DEVNULL)


def play_macos(path):
    subprocess.run(["afplay", path], timeout=15, stdout=DEVNULL, stderr=DEVNULL)


def play_linux(path):
    for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                ["mpg123", "-q", path],
                ["cvlc", "--play-and-exit", "--quiet", path],
                ["paplay", path]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, timeout=15, stdout=DEVNULL, stderr=DEVNULL)
            return


def main():
    try:
        d = json.loads(sys.stdin.read() or "{}")
    except Exception:
        d = {}
    if d.get("hook_event_name") != "Stop":
        return
    if not os.path.exists(SOUND):
        return

    try:
        if sys.platform == "win32":
            play_windows(SOUND)
        elif sys.platform == "darwin":
            play_macos(SOUND)
        else:
            play_linux(SOUND)
    except Exception:
        pass


if __name__ == "__main__":
    main()
