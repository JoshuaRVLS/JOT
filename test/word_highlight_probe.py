#!/usr/bin/env python3
"""Probe: the identifier under the caret is tinted at every occurrence.

A unit test can read the cell grid, but the claim here is about what the
terminal shows: the moment the caret lands on a word, every other place that
word appears in the viewport wears the theme's band -- the caret's own word on
the strong one, the rest on the plain one -- and nothing wears it when the
`word_highlight` setting is off.

This drives the real binary over a pty with a probe theme whose two bands are
unmistakable (bg 11 plain, bg 13 strong) and reads the cells back out of the
reconstructed screen. The file is plain text so no language server is in the
picture: the highlight is the editor's own, and a server's decorations would
sit on top of the cells it paints.

Usage: test/word_highlight_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 100, 24
# The two bands the probe theme paints with. Nothing else in this file's palette
# reaches either index (the default theme's other bands are 3, 4, 6, 8), so a
# background of 11 or 13 is the occurrence highlight and nothing else.
PLAIN_BG, STRONG_BG = 11, 13
# What the selected cells wear while the highlight follows a selection: the
# theme's Visual band, which the probe theme leaves at its own default (6).
SELECT_BG = 6

# The last line repeats the first statement word for word: it is what the span
# scene marks part of, and the reason that scene can tell the selection driving
# the highlight from the caret's own word driving it.
SOURCE = """int alpha = 1;
int beta = alpha + 1;
int gamma = alpha * beta;
int alpha = 1;
"""

# Where each identifier sits: (line index, char offset, length). `alpha` is the
# word to point at; `beta` is the second scene, and it is also the identifier
# that must stay unlit while the caret is on `alpha`.
ALPHA = ((0, 4, 5), (1, 11, 5), (2, 12, 5), (3, 4, 5))
BETA = ((1, 4, 4), (2, 20, 4))
# The text the span scene marks on the first line, `alpha = ` (the trailing cell
# is the `1`, which the drag's end puts the caret on instead).
SPAN = ((0, 4, 8), (3, 4, 8))

# Only the two occurrence bands are set. `fg` -1 is the theme spelling for
# "leave the token's own colour alone", the same way the git slots name no bg:
# the highlight moves the band behind the word, never the word's ink.
THEME = """{
  "WordHighlight": {"fg": -1, "bg": 11},
  "WordHighlightStrong": {"fg": -1, "bg": 13}
}
"""


def workspace(root: str, settings: str) -> None:
    """A config home with the probe theme, plus `settings` verbatim."""
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "configs", "colors"))
    with open(os.path.join(root, "configs", "settings.conf"), "w") as fh:
        fh.write("# jot configuration file\n\ncolor_scheme=wordprobe\n" + settings)
    with open(os.path.join(root, "configs", "colors", "wordprobe.json"), "w") as fh:
        fh.write(THEME)


def click(col: int, row: int) -> bytes:
    """SGR left press and release on a 0-based screen cell."""
    press = b"\x1b[<0;%d;%dM" % (col + 1, row + 1)
    release = b"\x1b[<0;%d;%dm" % (col + 1, row + 1)
    return press + release


def drag(src, dst) -> bytes:
    """A left press at `src`, a held motion to `dst`, and the release there."""
    x1, y1 = src[0] + 1, src[1] + 1
    x2, y2 = dst[0] + 1, dst[1] + 1
    return (b"\x1b[<0;%d;%dM" % (x1, y1)
            + b"\x1b[<32;%d;%dM" % (x2, y2)
            + b"\x1b[<0;%d;%dm" % (x2, y2))


def run_scene(binary: str, cfg: str, keys: bytes, dump: bool):
    """One run of the editor on the probe file, over a pty."""
    path = os.path.join(cfg, "words.txt")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    screen = run_in_pty(binary, [path], keys, settle=2.5, after=1.5, cols=COLS,
                        rows=ROWS, cfg=cfg, cwd=cfg)
    if dump:
        print(screen.text())
        print("-" * 70)
    return screen


def cell_for(screen, line_index: int, offset: int):
    """The 0-based (col, row) of `offset` chars into a source line, or None.

    The screen row carries the gutter and, to its left, whatever chrome the
    layout put there, so the line is located by its own text rather than by an
    assumed left edge. A line the file repeats word for word (its last one is
    its first one again) is told apart by its order: the nth line carrying the
    text is the nth such line of the file.
    """
    lines_in_file = SOURCE.splitlines()
    needle = lines_in_file[line_index]
    wanted = lines_in_file[: line_index + 1].count(needle)
    seen = 0
    for row, line in enumerate(screen.text().split("\n")):
        at = line.find(needle)
        if at < 0:
            continue
        seen += 1
        if seen == wanted:
            return at + offset, row
    return None


def word_cells(screen, occurrence):
    """Every cell of one occurrence, or None if its line is off screen."""
    line_index, offset, length = occurrence
    cells = []
    for i in range(length):
        cell = cell_for(screen, line_index, offset + i)
        if cell is None:
            return None
        cells.append(cell)
    return cells


def bands(screen, cells):
    """The background of each cell, in order."""
    return [screen.bg[row][col] for col, row in cells]


def label(occurrence) -> str:
    line_index, offset, length = occurrence
    return f"line {line_index + 1} col {offset} ({length} cells)"


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"word highlight probe: SKIP - no binary at {binary}")
        return 2

    root = "/tmp/jot_word_highlight_probe"
    off_root = root + "_off"
    workspace(root, "")
    workspace(off_root, "word_highlight = false\n")

    # One mapping run: where each identifier sits on the rendered grid. The
    # sidebar and gutter decide the code area's left edge, so nothing can be
    # assumed; every scene below reads its own screen for the cells.
    screen = run_scene(binary, root, b"", dump)
    spans = ALPHA + BETA + SPAN
    if any(cell_for(screen, line, offset) is None for line, offset, _length in spans):
        print("word highlight probe: FAIL - the probe file is not on screen")
        return 1

    def point(line: int, col: int):
        """A press and release at one character of the file."""
        return click(*cell_for(screen, line, col))

    def double(line: int, col: int):
        """Two presses in one write, which the editor reads as a double click."""
        return point(line, col) * 2

    def mark(line: int, start: int, end: int):
        """A drag from one character to another: a range selection."""
        return drag(cell_for(screen, line, start), cell_for(screen, line, end))

    # label: (keys, {occurrence: band it must wear}). A None band means the cells
    # must carry neither of the two.
    scenes = (
        ("alpha", point(0, 4),
         {ALPHA[0]: STRONG_BG, ALPHA[1]: PLAIN_BG, ALPHA[2]: PLAIN_BG,
          BETA[0]: None, BETA[1]: None}),
        ("beta", point(1, 4),
         {BETA[0]: STRONG_BG, BETA[1]: PLAIN_BG,
          ALPHA[0]: None, ALPHA[1]: None, ALPHA[2]: None}),
        # A double click selects the word, and the selection's own text drives
        # the highlight from there: the cells the selection covers are its own
        # band, and the other uses of the word light up around it.
        ("selected", double(1, 11),
         {ALPHA[1]: SELECT_BG, ALPHA[0]: PLAIN_BG, ALPHA[2]: PLAIN_BG, ALPHA[3]: PLAIN_BG,
          BETA[0]: None, BETA[1]: None}),
        # A drag marks `alpha = ` and stops on the `1`, so the caret's own word
        # (`1`) is not the text the highlight is following. The fourth line
        # repeats the statement word for word, so only a highlight reading the
        # marked text lights it: the caret's word would light the two `1` cells
        # instead, and those must stay plain.
        ("span", mark(0, 4, 12),
         {SPAN[0]: SELECT_BG, SPAN[1]: PLAIN_BG,
          (0, 12, 1): None, (3, 12, 1): None,
          BETA[0]: None, BETA[1]: None}),
    )

    failures = []
    for name, keys, expected in scenes:
        screen = run_scene(binary, root, keys, dump)
        print(f"word highlight {name:<8}")
        for occurrence, want in expected.items():
            cells = word_cells(screen, occurrence)
            if cells is None:
                failures.append(f"{name}: {label(occurrence)} is not on screen")
                continue
            got = bands(screen, cells)
            print(f"  {label(occurrence):<28} {cells[0]} -> {got}")
            if want is None:
                if any(bg in (PLAIN_BG, STRONG_BG) for bg in got):
                    failures.append(f"{name}: {label(occurrence)} at {cells[0]} wears {got} "
                                    f"with the caret elsewhere")
            elif set(got) != {want}:
                failures.append(f"{name}: {label(occurrence)} at {cells[0]} is {got}, "
                                f"want all {want}")

    # The setting off: the same click on the same word, and no band anywhere.
    off_mapping = run_scene(binary, off_root, b"", dump)
    screen = run_scene(binary, off_root, click(*cell_for(off_mapping, 0, 4)), dump)
    print("word highlight off    caret on " + label(ALPHA[0]))
    for occurrence in ALPHA:
        cells = word_cells(screen, occurrence)
        if cells is None:
            failures.append(f"word_highlight=false: {label(occurrence)} is not on screen")
            continue
        got = bands(screen, cells)
        print(f"  {label(occurrence):<28} {cells[0]} -> {got}")
        if any(bg in (PLAIN_BG, STRONG_BG) for bg in got):
            failures.append(f"word_highlight=false: {label(occurrence)} at {cells[0]} "
                            f"still wears {got}")

    if failures:
        for failure in failures:
            print(f"word highlight probe: FAIL - {failure}")
        return 1
    print("word highlight probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
