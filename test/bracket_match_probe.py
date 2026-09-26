#!/usr/bin/env python3
"""Probe: the pair under the caret is boxed, and only then.

A unit test can read the cell grid, but the highlight is a claim about what the
terminal shows: the moment the caret lands on a bracket, that bracket and its
partner wear the theme's BracketMatch colors -- on either side of the pair, on
one row or across rows -- and nothing wears them when the caret is elsewhere.

This drives the real binary over a pty with a probe theme whose BracketMatch
pair is unmistakable (fg 13 / bg 11) and reads the cells back out of the
reconstructed screen. The file is plain text so no language server is in the
picture: the highlight is the editor's own bracket logic, and a server's
decorations would sit on top of the cells it paints.

Usage: test/bracket_match_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 100, 24
# The two palette numbers the probe theme paints the match with: nothing else
# in this file's palette (syntax 7, brackets 1-6) collides with the pair.
MATCH_FG, MATCH_BG = 13, 11

SOURCE = """int alpha = 1;
int run() {
  int inner = 2;
  return inner;
}
"""

# The probe theme: only the match pair is set -- everything else stays on the
# theme defaults, so the box is the one thing on screen that moved.
THEME = """{
  "BracketMatch": {"fg": 13, "bg": 11}
}
"""


def workspace(tmp: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "configs", "colors"))
    with open(os.path.join(tmp, "configs", "settings.conf"), "w") as fh:
        fh.write("# jot configuration file\n\ncolor_scheme=bracketprobe\n")
    with open(os.path.join(tmp, "configs", "colors", "bracketprobe.json"), "w") as fh:
        fh.write(THEME)
    path = os.path.join(tmp, "pairs.txt")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    return path


def click(col: int, row: int) -> bytes:
    """SGR left press and release on a 0-based screen cell."""
    press = b"\x1b[<0;%d;%dM" % (col + 1, row + 1)
    release = b"\x1b[<0;%d;%dm" % (col + 1, row + 1)
    return press + release


def cell_of(screen, needle: str, offset: int = 0):
    """The 0-based (col, row) of `needle`, shifted by `offset`, or None."""
    for row, line in enumerate(screen.text().split("\n")):
        idx = line.find(needle)
        if idx >= 0:
            return idx + offset, row
    return None


def colors_at(screen, cell):
    col, row = cell
    return screen.fg[row][col], screen.bg[row][col]


def run_scene(binary: str, tmp: str, keys: bytes, dump: bool):
    path = os.path.join(tmp, "pairs.txt")
    screen = run_in_pty(binary, [path], keys, settle=2.5, after=1.5, cols=COLS,
                        rows=ROWS, cfg=tmp, cwd=tmp)
    if dump:
        print(screen.text())
        print("-" * 70)
    return screen


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"bracket match probe: SKIP - no binary at {binary}")
        return 2

    tmp = "/tmp/jot_bracket_match_probe"
    workspace(tmp)
    # One mapping run: where each bracket sits on the rendered grid. The
    # sidebar and gutter decide the code area's left edge, so nothing can be
    # assumed; the click scenes below read their own screen for the cells.
    screen = run_scene(binary, tmp, b"", dump)
    open_brace = cell_of(screen, "int run() {", 10)
    close_brace = cell_of(screen, "}")
    open_paren = cell_of(screen, "int run() {", 7)
    close_paren = cell_of(screen, "int run() {", 8)
    first_char = cell_of(screen, "int alpha = 1;", 0)
    if not all((open_brace, close_brace, open_paren, close_paren, first_char)):
        print("bracket match probe: FAIL - the probe file is not on screen")
        return 1

    failures = []

    # label: (cell to click, cells that must be boxed, cells that must not)
    scenes = (
        ("brace open", open_brace, ("open_brace", "close_brace"), ("first_char",)),
        ("brace close", close_brace, ("open_brace", "close_brace"), ("first_char",)),
        ("paren pair", open_paren, ("open_paren", "close_paren"), ("first_char",)),
        ("caret off", first_char, (),
         ("open_brace", "close_brace", "open_paren", "close_paren")),
    )

    for label, target, boxed, plain in scenes:
        screen = run_scene(binary, tmp, click(*target), dump)
        # Every needle is re-read off this scene's own frame: the assertion is
        # about the picture the click left behind.
        cells = {
            "open_brace": cell_of(screen, "int run() {", 10),
            "close_brace": cell_of(screen, "}"),
            "open_paren": cell_of(screen, "int run() {", 7),
            "close_paren": cell_of(screen, "int run() {", 8),
            "first_char": cell_of(screen, "int alpha = 1;", 0),
        }
        if not all(cells.values()):
            failures.append(f"{label}: the probe file is not on screen")
            continue
        print(f"bracket match {label:<12} click {target} -> " + ", ".join(
            f"{name} {colors_at(screen, cells[name])}" for name in cells))

        for name in boxed:
            got = colors_at(screen, cells[name])
            if got != (MATCH_FG, MATCH_BG):
                failures.append(f"{label}: {name} at {cells[name]} is {got}, "
                                f"want {(MATCH_FG, MATCH_BG)}")
        for name in plain:
            got = colors_at(screen, cells[name])
            if got == (MATCH_FG, MATCH_BG):
                failures.append(f"{label}: {name} at {cells[name]} carries the "
                                f"match pair with the caret elsewhere")

    if failures:
        for failure in failures:
            print(f"bracket match probe: FAIL - {failure}")
        return 1
    print("bracket match probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
