#!/usr/bin/env python3
"""Probe: the BufChange edit delta still reports what the editor did.

While a Lua plugin listens to BufChange, the editor hands it the edit it just
applied: `edit_start_line`/`edit_start_col` (1-based insertion point),
`edit_end_line`/`edit_end_col` (exclusive end in the new text), `edit_inserted`
/ `edit_removed`, and `edit_multiline` (docs/LUA_API.md). The delta used to be
computed by joining the whole buffer into one string and splitting both that
and the plugin's previous copy back into lines, an allocation per line of the
file on every keystroke. It is now computed straight off the buffer's lines
against a snapshot that shares the lines the edit did not touch, so the numbers
below are the contract that rewrite has to keep exactly.

Each scene loads a file, applies one command, and reads the line the plugin
wrote for that delta off disk, which is what makes the check about the values
rather than about the screen the row happens to paint. The last check is the
cost, which the values cannot see: an 80-key burst is typed into a 3000-line
file and into a ten-times bigger one, and the per-keystroke time may not grow
with the file.

Usage: test/bufchange_delta_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

DOWN = b"\x1b[B"
RIGHT = b"\x1b[C"
BACKSPACE = b"\x7f"
CTRL_Z = b"\x1a"
PASTE = b"\x1b[200~one\ntwo\x1b[201~"

# The plugin prints one line per delta: start line,col end line,col [inserted]
# [removed] multiline, with newlines spelled out so a line stays a line.
PLUGIN = """\
local log = os.getenv("JOT_PROBE_DELTA_LOG")
if log then
  jot.autocmd("BufChange", function(e)
    if not e.edit_start_line then
      return
    end
    local fh = io.open(log, "a")
    if fh then
      local function show(text)
        return (text or ""):gsub("\\n", "\\\\n")
      end
      fh:write(string.format("%d,%d %d,%d [%s] [%s] %s\\n", e.edit_start_line, e.edit_start_col,
        e.edit_end_line, e.edit_end_col, show(e.edit_inserted), show(e.edit_removed),
        tostring(e.edit_multiline)))
      fh:close()
    end
  end)
end
"""

# Every scene starts with one warm-up command: the first change to a buffer has
# no previous state to diff against, so it carries no delta fields and writes no
# line. Each phase after it is one command followed by a drain, so its delta is
# dispatched before the next key lands (BufChange is coalesced per flush).
WARMUP = b"W"

# name, file body, [(delay, keys, the lines that command must produce)]
SCENES = [
    # A character typed into the middle of a line is an insertion of one column
    # at (2,3), so the exclusive end is (2,4).
    ("insert mid-line", "alpha\nbeta\ngamma\n",
     [(1.0, WARMUP, []),
      (1.2, DOWN + RIGHT + b"X", ["2,3 2,4 [X] [] false"]),
      # A second edit elsewhere: its delta is computed against the snapshot the
      # first one left behind, so a snapshot that lost a line shows up here.
      (1.2, DOWN + b"Z", ["3,4 3,5 [Z] [] false"])]),
    # Enter inside line 1 splits it at (1,4). The tail that moved down is matched
    # as shared context by the common-suffix pass, so the delta is the newline
    # alone: inserted at (1,4), ending at the start of line 2, and multiline.
    ("enter splits a line", "alpha\nbeta\n",
     [(1.0, WARMUP, []),
      (1.2, RIGHT * 2 + b"\r", ["1,4 2,1 [\\n] [] true"])]),
    # Backspace at (2,3) removes the "e" of "beta": an empty insertion at
    # (2,2), which the delta reports by naming the text it took out.
    ("backspace deletes a char", "alpha\nbeta\n",
     [(1.0, WARMUP, []),
      (1.2, DOWN + RIGHT + BACKSPACE, ["2,2 2,2 [] [e] false"])]),
    # Undo reports the same edit it takes back.
    ("undo of a typed char", "alpha\n",
     [(1.0, WARMUP, []),
      (1.2, b"X", ["1,2 1,3 [X] [] false"]),
      # A deletion inserts nothing, so the end is the start: the new text has
      # no column after the point the "X" came out of.
      (1.2, CTRL_Z, ["1,2 1,2 [] [X] false"]),
      # And the snapshot the undo left behind still diffs correctly.
      (1.2, b"Y", ["1,2 1,3 [Y] [] false"])]),
    # A pasted block is one command. Pasted mid-line it keeps the text after the
    # caret on its own line, so the paste ends on line 2 just past "two".
    ("paste a block", "alpha\n",
     [(1.0, WARMUP, []),
      (1.2, PASTE, ["1,2 2,4 [one\\ntwo] [] true"])]),
]


def run_scene(binary: str, name: str, body: str, phases, dump: bool):
    """Runs one scene and returns (log lines, the deltas each phase owed)."""
    work = "/tmp/jot_bufchange_probe_" + name.replace(" ", "_")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, "cfg"))
    with open(os.path.join(work, "cfg", "init.lua"), "w") as fh:
        fh.write(PLUGIN)
    path = os.path.join(work, "probe.txt")
    with open(path, "w") as fh:
        fh.write(body)
    log = os.path.join(work, "delta.log")

    run_in_pty(binary, [path], b"", settle=3.0, after=0.5,
               cfg=os.path.join(work, "cfg"),
               phases=[(delay, keys) for delay, keys, _ in phases],
               env={"JOT_PROBE_DELTA_LOG": log})

    lines = []
    if os.path.exists(log):
        with open(log) as fh:
            lines = [line.rstrip("\n") for line in fh if line.strip()]
    if dump:
        print("=== %s ===" % name)
        for line in lines:
            print("   " + line)
    # A phase that coalesced with the one before it would drop a line, so the
    # count is part of the check and not just the values.
    return lines, [line for _, _, owed in phases for line in owed]


# Typing latency, the other half of what the delta costs. A delta used to join
# the whole buffer into one string and split it back into lines, an allocation
# per line of the file on every keystroke, so a keystroke got slower the more the
# file grew. The scenes above cannot see that -- the old code produced the same
# deltas -- so a burst is timed here instead: the keys go out at once and the
# check is how long the editor takes to put the last of them on screen.
#
# What is asserted is the difference between two file sizes rather than a
# wall-clock bound, so a machine's own frame cost cancels out and what is left is
# the per-keystroke work that has to stay flat as the file grows.
LATENCY_SMALL_LINES = 3000
LATENCY_BIG_LINES = 30000
LATENCY_KEYS = 80
# Ten times the file may not cost more than a few milliseconds a keystroke more.
# Copying the buffer into one string per keystroke cost ~10 ms more at this size,
# and comparing a line per unchanged line costs about one.
MAX_GROWTH_MS_PER_KEY = 4.0
SETTLE_SECONDS = 3.0
# The burst stays inside one screen row: the keys are typed at the start of line
# 1 and the row ends at the pane's right edge, so a longer run would push the
# marker off screen and leave the check waiting for text that is already there.


def measure_burst(binary: str, label: str, lines: int, dump: bool) -> float:
    """Milliseconds per keystroke for a burst typed into a `lines`-line file."""
    work = "/tmp/jot_bufchange_probe_" + label
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, "cfg"))
    with open(os.path.join(work, "cfg", "init.lua"), "w") as fh:
        fh.write(PLUGIN)
    # Long lines are the point: the cost this measures was per byte of the file
    # too, so a file of short lines hides it behind the frame's own work.
    path = os.path.join(work, "big.txt")
    with open(path, "w") as fh:
        for i in range(lines):
            text = "line %d of the burst file" % i
            fh.write(text + " " * (200 - len(text)) + "\n")

    log = os.path.join(work, "delta.log")
    started = time.time()
    run_in_pty(binary, [path], b"x" * LATENCY_KEYS + b"ZEND",
               settle=SETTLE_SECONDS, after=0.0, cfg=os.path.join(work, "cfg"),
               env={"JOT_PROBE_DELTA_LOG": log},
               phases=[(0.0, lambda screen: "ZEND" in screen.text())])
    per_key = (time.time() - started - SETTLE_SECONDS) * 1000.0 / LATENCY_KEYS
    if dump:
        print("=== burst in a %d-line file ===" % lines)
        print("   %.2f ms per keystroke" % per_key)
    return per_key


def main() -> int:
    positional = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    binary = positional[0] if positional else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("bufchange delta probe: SKIP - no binary at %s" % binary)
        return 2

    failures = []
    small = measure_burst(binary, "latency_small", LATENCY_SMALL_LINES, dump)
    big = measure_burst(binary, "latency_big", LATENCY_BIG_LINES, dump)
    growth = big - small
    print("%-24s %s (%.2f ms/keystroke at %d lines, %.2f at %d lines, growth %.2f)"
          % ("typing burst", "ok" if growth <= MAX_GROWTH_MS_PER_KEY else "FAIL",
             small, LATENCY_SMALL_LINES, big, LATENCY_BIG_LINES, growth))
    if growth > MAX_GROWTH_MS_PER_KEY:
        failures.append("a keystroke cost %.2f ms more in the bigger file" % growth)

    for name, body, phases in SCENES:
        got, expected = run_scene(binary, name, body, phases, dump)
        ok = got == expected
        print("%-24s %s" % (name, "ok" if ok else "FAIL"))
        if not ok:
            failures.append("%s: got %r, want %r" % (name, got, expected))

    if failures:
        print("bufchange delta probe: FAIL")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("bufchange delta probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
