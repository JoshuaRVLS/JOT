#!/usr/bin/env python3
"""Probe: TODO/FIXME comments wear the todo-comments.nvim band and colour.

The bundled feature (runtime/lua/features/todo_comments.lua, a port of
folke/todo-comments.nvim) paints a background band behind the keyword and the
colon and re-inks the text after it, in the family colour the live theme names
(error -> DiagnosticError, info -> DiagnosticInfo). The browser drives the real
binary over a Lua file and reads the truecolour cells back out of the pty
stream, so it is the renderer's paint being checked, not a config value:

  * `-- TODO: ...` carries the DiagnosticInfo band, its ink is the theme's dark
    normal background (maximize_contrast) and "fix this later" takes the band's
    fg;
  * `-- FIXME: ...` carries the DiagnosticError band;
  * `-- NOTODO: ...` and `-- TODO no colon` stay clean (the word boundary and
    the colon are both required).

A second scene drives `:Todo` through the command palette and checks the
workspace picker lists the same two comments with file and line, which is the
path that also exercises the picker's label/value split.

Usage: test/todo_comments_probe.py [path-to-jot-binary] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# The jot-dark groups the feature resolves: DiagnosticInfo for TODO,
# DiagnosticError for FIXME. A truecolour cell is tagged as 1000 + 0xRRGGBB.
INFO = 1000 + 0x7B98C2
ERROR = 1000 + 0xCC7F86
# The theme's normal background. Both bands are mid-light, so the contrast
# pick (upstream's maximize_contrast) lands on this dark ink, not on #c5c0d4.
# Tagged like every truecolour cell (1000 + 0xRRGGBB).
INK = 1000 + 0x07060E

SOURCE = """\
local a = 1
-- TODO: fix this later
local b = 2
-- FIXME: broken
local c = 3
-- NOTODO: not a keyword
-- TODO no colon
local d = 4
"""

CTRL_P = b"\x10"
ENTER = b"\r"


def find_row(screen, needle: str):
    """(row index, row text) of the first screen row holding the needle."""
    for y in range(screen.rows):
        text = "".join(screen.cells[y])
        if needle in text:
            return y, text
    return None, ""


def band_at(screen, row: int, x: int) -> int:
    return screen.bg[row][x]


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("todo comments probe: SKIP - no binary at %s" % binary)
        return 2

    work = "/tmp/jot_todo_comments_probe"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    path = os.path.join(work, "todo.lua")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    cfg = os.path.join(work, "cfg")

    failures = []

    # Scene 1: the paint. Wait until the BufOpen retry has had its chance by
    # draining a little extra; the band is read off the final grid.
    screen = run_in_pty(binary, [path], b"", settle=2.5, after=1.5, cols=100, rows=30,
                        cfg=cfg, cwd=work, phases=[(0.5, b"")])
    if dump:
        print(screen.text())
        print("-" * 70)

    todo_row, todo_text = find_row(screen, "-- TODO: fix this later")
    fixme_row, fixme_text = find_row(screen, "-- FIXME: broken")
    notodo_row, _ = find_row(screen, "-- NOTODO: not a keyword")
    nocolon_row, _ = find_row(screen, "-- TODO no colon")

    if todo_row is None:
        failures.append("the TODO comment row never reached the screen")
    else:
        x = todo_text.find("TODO")
        # The band starts at the comment, so `-- ` through the space after the
        # colon: three prefix cells plus TODO: plus the trailing byte.
        start = x - 3
        for col in range(start, x + 6):
            if band_at(screen, todo_row, col) != INFO:
                failures.append(
                    "TODO band: cell %d is %s, want the DiagnosticInfo fill"
                    % (col, band_at(screen, todo_row, col)))
                break
        if screen.fg[todo_row][x] != INK:
            failures.append("TODO band ink is %s, want the dark normal background"
                            % screen.fg[todo_row][x])
        for col in range(x + 6, x + 9):  # "fix"
            if screen.fg[todo_row][col] != INFO:
                failures.append("text after TODO: cell %d lost the keyword colour"
                                % col)
                break
        if any(band_at(screen, todo_row, col) == INFO for col in range(x + 6, len(todo_text))):
            failures.append("the colour after TODO: came as a band, not as text")
    if fixme_row is None:
        failures.append("the FIXME comment row never reached the screen")
    else:
        x = fixme_text.find("FIXME")
        start = x - 3
        for col in range(start, x + 7):
            if band_at(screen, fixme_row, col) != ERROR:
                failures.append(
                    "FIXME band: cell %d is %s, want the DiagnosticError fill"
                    % (col, band_at(screen, fixme_row, col)))
                break
        if screen.fg[fixme_row][x] != INK:
            failures.append("FIXME band ink is %s, want the dark normal background"
                            % screen.fg[fixme_row][x])
        for col in range(x + 7, x + 13):  # "broken"
            if screen.fg[fixme_row][col] != ERROR:
                failures.append("text after FIXME: cell %d lost the keyword colour"
                                % col)
                break

    for row, label in ((notodo_row, "NOTODO:"), (nocolon_row, "TODO without a colon")):
        if row is None:
            failures.append("%s row never reached the screen" % label)
            continue
        for col in range(screen.cols):
            if screen.bg[row][col] in (INFO, ERROR):
                failures.append("%s got a band" % label)
                break
            if screen.fg[row][col] in (INFO, ERROR):
                failures.append("%s got the keyword colour" % label)
                break

    # Scene 2: :Todo lists the workspace's comments. The palette's Enter can
    # dispatch late in the harness, so wait for the picker title instead of
    # asserting a fixed delay.
    screen = run_in_pty(binary, [path], b"", settle=2.5, after=1.0, cols=100, rows=30,
                        cfg=cfg, cwd=work,
                        phases=[(0.6, CTRL_P), (0.6, b"Todo"), (0.6, ENTER),
                                (0.0, lambda s: "Todo Comments" in s.text()),
                                (0.8, b"")])
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()
    if "Todo Comments" not in text:
        failures.append(":Todo did not open the workspace picker")
    for needle in ("todo.lua:2", "-- TODO: fix this later", "todo.lua:4", "-- FIXME: broken"):
        if needle not in text:
            failures.append(":Todo list is missing %r" % needle)

    if failures:
        print("todo comments probe: FAIL")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("todo comments probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
