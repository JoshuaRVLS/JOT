#!/usr/bin/env python3
"""Probe: the statement keybind selects a statement whole, rows and all.

The statement object exists because the selection that comes up while editing is
the statement, and a statement written over several rows otherwise takes one
`expand` press per level to reach. This drives the real binary and the real
tree-sitter grammar: the caret is placed inside a multi-line declaration, the
key is pressed, and the whole statement is replaced -- which is only visible if
every row of it was selected.

Three spellings are exercised. The fast chord (`Alt+V` then `Shift+S`) is sent
twice, once the way a plain terminal sends a shifted letter and once as a kitty
CSI-u report, so neither encoding is assumed. The third scene takes the menu
path (`Alt+V s s`) and is what guards the fast chord: which-key matches a
pressed key against a child case-insensitively, so a bare `S` child would be the
one a plain `s` matched and the menu would never open. That scene asserts the
marker stands alone where the statement was, so a stray letter left behind by a
shadowed menu is a failure.

Usage: test/select_statement_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 120, 30
ROOT = "/tmp/jot_select_statement_probe"
CFG = "/tmp/jot_select_statement_probe_cfg"
SOURCE = os.path.join(ROOT, "probe.cpp")

# The statement the probe selects spans four rows; the declaration before it and
# the return after it must both survive.
TEXT = ("int compute(int a, int b, int c) { return a + b + c; }\n"
        "int main() {\n"
        "  int total = compute(\n"
        "      1,\n"
        "      2,\n"
        "      3);\n"
        "  return total;\n"
        "}\n")
NEEDLE = "1,"
STATEMENT = "int total = compute("
# The statusline reports a four-row selection, so the chord can be waited on
# rather than slept past.
SELECTED = "Sel 4L"

ALT_V_LEGACY = b"\x1bv"       # ESC <letter>: what a plain terminal sends
ALT_V_CSI = b"\x1b[118;3u"    # kitty CSI-u: 118 = 'v', modifier 3 = Alt
SHIFT_S_LEGACY = b"S"         # a shifted letter arrives uppercase
SHIFT_S_CSI = b"\x1b[83;2u"   # kitty CSI-u: 83 = 'S', modifier 2 = Shift


def write_source() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT)
    with open(SOURCE, "w") as fh:
        fh.write(TEXT)


def cell_of(screen, needle: str):
    for y, line in enumerate(screen.text().split("\n")):
        x = line.find(needle)
        if x >= 0:
            return x, y
    return -1, -1


def click(x: int, y: int) -> bytes:
    return b"\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (x + 1, y + 1, x + 1, y + 1)


def scene(binary: str, keys: list, dump: bool) -> str:
    # The layout pass gives the click its target: the argument row inside the
    # multi-line statement.
    pre = run_in_pty(binary, [SOURCE], b"", settle=4.0, after=1.0, cols=COLS, rows=ROWS,
                     cfg=CFG, cwd=ROOT, phases=[(0.5, lambda s: True)])
    x, y = cell_of(pre, NEEDLE)
    if x < 0:
        return ""
    # Each step waits for the editor to answer the last one, so nothing is a
    # race: the chord is not sent until the caret has moved, the marker not until
    # the selection is on screen, and the result not until it has landed. Each
    # key of the sequence is its own write, the way a person types it.
    phases = [(0.5, lambda s: True), (0.4, click(x, y))]
    phases += [(0.2, blob) for blob in keys]
    phases += [(0.0, lambda s: SELECTED in s.text()),
               (0.2, b"X"),
               (0.0, lambda s: STATEMENT not in s.text())]
    screen = run_in_pty(binary, [SOURCE], b"", settle=4.0, after=1.0, cols=COLS, rows=ROWS,
                        cfg=CFG, cwd=ROOT, phases=phases)
    if dump:
        print(screen.text())
        print("-" * 70)
    return screen.text()


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("select statement probe: SKIP - no binary at %s" % binary)
        return 2

    write_source()
    failures: list[str] = []

    scenes = (
        ("Alt+V Shift+S (esc)", [ALT_V_LEGACY, SHIFT_S_LEGACY]),
        ("Alt+V Shift+S (csi)", [ALT_V_CSI, SHIFT_S_CSI]),
        ("Alt+V s s     (menu)", [ALT_V_LEGACY, b"s", b"s"]),
    )
    for label, keys in scenes:
        text = scene(binary, keys, dump)
        statement_gone = STATEMENT not in text
        next_statement = "return total;" in text
        markers = [line for line in text.split("\n") if "X" in line]
        # The marker must stand alone where the statement was: a shadowed menu
        # would leave the object letter typed next to it.
        marked = len(markers) == 1 and markers[0].split()[-1] == "X"
        print("%-21s -> statement replaced: %s, next statement kept: %s, marker alone: %s"
              % (label, statement_gone, next_statement, marked))
        if not marked:
            failures.append("%s: the statement was not replaced by the marker alone" % label)
        if not statement_gone:
            failures.append("%s: the multi-line statement was not selected whole" % label)
        if not next_statement:
            failures.append("%s: the selection ran past the statement" % label)

    if failures:
        print("select statement probe: FAIL")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("select statement probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
