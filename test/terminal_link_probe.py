#!/usr/bin/env python3
"""Probe: Ctrl+click on a link in the integrated terminal, in a real session.

The C++ tests drive a shell-less vterm. What only a real session proves is the
shell's own output on the grid: a URL a command printed is detected on the row
the panel actually painted, Ctrl+click hands exactly that URL to the opener, the
click leaves no selection behind, and Ctrl+hover underlines the same cells.

The editor looks the platform opener up on PATH (`xdg-open` here) and runs it,
so the probe puts a stub first on PATH that appends the URL it was handed to a
file, and reads back what it got -- the production path, not a seam.

The mouse clicks are drags (press, move over a few cells a row down, button
still down): a terminal selection is painted as a band, so "this click
selected" is readable off the grid. A one-cell press would leave an empty
selection and nothing to see, and the status line's message channel is owned by
the Lua UI in a real session, so neither is used as the observable.

Four scenes, each its own session (a scene must not inherit another's shell):
  * click: Ctrl+click on the URL opens exactly it, with no selection band;
  * hover: Ctrl+motion over the URL underlines exactly its cells, and no
    opener runs;
  * off-link: Ctrl+click on the word beside the URL still selects, and nothing
    is opened;
  * plain: a click without Ctrl selects the URL instead of opening it, so Ctrl
    is what opens it.

The first scene's grid fixes the cells the other scenes click and the band's
absence is compared against: the panel's rows depend on the shell's prompt, so
nothing is assumed.

Usage: test/terminal_link_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 140, 36
WS = "/tmp/jot_terminal_link_probe"
HOME = "/tmp/jot_terminal_link_probe_home"
BIN = "/tmp/jot_terminal_link_probe_bin"
URL = "https://example.com/terminal-link"
# The row the shell prints: the URL with the prose around it that decides where
# the link ends, and a word before it for the off-link clicks.
LINE = "see %s now" % URL

# Ctrl+Shift+P opens the command palette; Enter runs what is typed in it, and
# the click below hands the keys to the panel (the workspace opens with the
# explorer focused, where typing would go to the tree).
PALETTE = b"\x1b[112;6u"
NEW_TERM = b"termnew"
ENTER = b"\r"
PANEL_CLICK = b"\x1b[<0;5;31M\x1b[<0;5;31m"
CAT = b"cat url.txt\r"
# SGR modifier bits: 4 is Shift, 16 is Ctrl, 32 is motion; the low two bits are
# the button (0 is the left one).
CTRL = 16
MOTION = 32


def rows(screen) -> list[str]:
    return [line.rstrip() for line in screen.text().split("\n")]


def write_workspace() -> None:
    shutil.rmtree(WS, ignore_errors=True)
    os.makedirs(WS)
    with open(os.path.join(WS, "url.txt"), "w") as fh:
        fh.write(LINE + "\n")
    shutil.rmtree(BIN, ignore_errors=True)
    os.makedirs(BIN)
    # The stub the editor will find first on PATH. It records what it was
    # handed and exits; the editor backgrounds it, so the record may land a
    # moment after the click.
    stub = os.path.join(BIN, "xdg-open")
    with open(stub, "w") as fh:
        fh.write('#!/bin/sh\nprintf \'%s\\n\' "$1" >> "$JOT_LINK_RECORD"\n')
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def press(col: int, row: int, mods: int = 0) -> bytes:
    return b"\x1b[<%d;%d;%dM" % (mods, col + 1, row + 1)


def release(col: int, row: int, mods: int = 0) -> bytes:
    return b"\x1b[<%d;%d;%dm" % (mods, col + 1, row + 1)


def click(col: int, row: int, mods: int = 0) -> bytes:
    """SGR press and release on one 0-based screen cell."""
    return press(col, row, mods) + release(col, row, mods)


def drag(col: int, row: int, cells: int, mods: int = 0) -> bytes:
    """Press at (col, row) and walk right `cells` times one row down.

    The walk ends a row below because a selection kept within one row paints
    no band; the button is not released, so the band is read while the drag is
    still in flight and the scene does not wait on the clipboard write.
    """
    out = press(col, row, mods)
    for step in range(1, cells + 1):
        out += press(col + step, row + 1, mods | MOTION)
    return out


def ctrl_motion(col: int, row: int) -> bytes:
    """SGR ctrl+motion; sent twice so the second lands on the frame the first
    caused."""
    return press(col, row, CTRL | MOTION) * 2


def cell_of(screen, needle: str, offset: int = 0):
    """The 0-based (col, row) of `needle` on screen, plus a character offset."""
    for row, line in enumerate(screen.text().split("\n")):
        idx = line.find(needle)
        if idx >= 0:
            return idx + offset, row
    return None


def wait_record(path: str):
    """A phase that waits until the stub's record file has an entry."""

    def phase(_screen) -> bool:
        try:
            return os.path.getsize(path) > 0
        except OSError:
            return False

    return phase


def run(binary: str, cfg: str, record: str, extra=None):
    env = {"PATH": BIN + ":/usr/bin:/bin", "JOT_LINK_RECORD": record}
    phases = [
        (0.3, lambda s: any("terminal_link_probe" in row for row in rows(s))),
        (0.6, PALETTE),
        (0.8, NEW_TERM),
        (0.6, ENTER),
        (1.0, PANEL_CLICK),
        (0.6, CAT),
        (0.3, lambda s: URL in s.text()),
    ]
    phases += extra or []
    return run_in_pty(binary, [WS], b"", settle=5.0, after=0.6, cols=COLS, rows=ROWS,
                      cfg=cfg, cwd=WS, env=env, phases=phases)


def recorded(record: str, want: int, timeout: float = 1.5):
    """The URLs the stub recorded, once there are `want` of them."""
    deadline = time.time() + timeout
    lines = []
    while time.time() < deadline:
        lines = []
        if os.path.exists(record):
            with open(record) as fh:
                lines = [line.rstrip("\n") for line in fh if line.strip()]
        if len(lines) >= want:
            return lines
        time.sleep(0.05)
    return lines


def fresh_record(name: str) -> str:
    path = "/tmp/jot_terminal_link_probe_urls_" + name
    if os.path.exists(path):
        os.remove(path)
    return path


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("terminal link probe: SKIP - no binary at %s" % binary)
        return 2

    write_workspace()
    # An empty HOME so no shell rc file decorates the prompt, and a plain shell
    # so the row layout is the editor's own business.
    shutil.rmtree(HOME, ignore_errors=True)
    os.makedirs(HOME)
    os.environ["HOME"] = HOME
    if os.path.exists("/bin/bash"):
        os.environ["SHELL"] = "/bin/bash"

    failures: list[str] = []

    # The mapping session: the shell's URL row, where its cells are, and what
    # they look like with no selection on them.
    base = run(binary, "/tmp/jot_terminal_link_probe_cfg_base", fresh_record("base"))
    if dump:
        print(base.text())
        print("-" * 70)
    cell = cell_of(base, URL, 0)
    if cell is None:
        print("terminal link probe: FAIL")
        print("  - the shell's URL row is not on screen: %r" % LINE)
        return 1
    url_col, row = cell
    url_end = url_col + len(URL)
    mid_col = url_col + 5            # a cell inside the URL
    word_col = url_col - 3           # the middle of the "see" before it
    base_url_bg = [base.bg[row][c] for c in range(url_col, url_end)]
    print("URL row: %s at cols %d..%d on row %d" % (URL, url_col, url_end - 1, row))

    # Scene 1: Ctrl+click opens exactly the URL, and paints no selection band
    # over it (the click was the link's, not a position in the output).
    record = fresh_record("click")
    screen = run(binary, "/tmp/jot_terminal_link_probe_cfg_click", record,
                 [(0.3, click(mid_col, row, CTRL)), (0.5, wait_record(record))])
    if dump:
        print(screen.text())
        print("-" * 70)
    got = recorded(record, 1)
    opens = got[:1]
    url_bg = [screen.bg[row][c] for c in range(url_col, url_end)]
    print("ctrl+click:   opener got %s, URL band=%s"
          % (opens if opens else "(nothing)", "yes" if url_bg != base_url_bg else "no"))
    if opens != [URL]:
        failures.append("ctrl+click: expected %r, the opener got %r" % (URL, got))
    if url_bg != base_url_bg:
        failures.append("ctrl+click: the URL cells carry a selection band")

    # Scene 2: Ctrl+hover underlines exactly the URL cells, and opens nothing.
    record = fresh_record("hover")
    screen = run(binary, "/tmp/jot_terminal_link_probe_cfg_hover", record,
                 [(0.3, ctrl_motion(mid_col, row))])
    if dump:
        print(screen.text())
        print("-" * 70)
    runs = [text for _start, _end, text in screen.underline_runs(row)]
    print("ctrl+hover:   underlines %s (want [%r])" % (runs, URL))
    if runs != [URL]:
        failures.append("ctrl+hover: expected exactly the URL, got %r" % runs)
    if recorded(record, 1):
        failures.append("ctrl+hover: a hover must not open anything")

    # Scene 3: off the link, Ctrl+click is still a selection.
    record = fresh_record("offlink")
    screen = run(binary, "/tmp/jot_terminal_link_probe_cfg_offlink", record,
                 [(0.3, drag(word_col, row, 2, CTRL))])
    if dump:
        print(screen.text())
        print("-" * 70)
    band_starts = [c for c in range(1, COLS) if screen.bg[row][c] != base.bg[row][c]]
    print("ctrl+off:     band starts at col %s (want %d), opener got %s"
          % (band_starts[0] if band_starts else "(nowhere)", word_col, recorded(record, 1)))
    if not band_starts or band_starts[0] != word_col:
        failures.append("ctrl+off: the click off the link did not select from its cell")
    if recorded(record, 1):
        failures.append("ctrl+off: it opened something")

    # Scene 4: no Ctrl, the URL is text like any other: it selects, not opens.
    record = fresh_record("plain")
    screen = run(binary, "/tmp/jot_terminal_link_probe_cfg_plain", record,
                 [(0.3, drag(mid_col, row, 2))])
    if dump:
        print(screen.text())
        print("-" * 70)
    print("plain click:  band starts at col %s (want %d), opener got %s"
          % (mid_col if screen.bg[row][mid_col] != base.bg[row][mid_col] else "(nowhere)",
             mid_col, recorded(record, 1)))
    if screen.bg[row][mid_col] == base.bg[row][mid_col]:
        failures.append("plain click: the URL was not selected like text")
    if screen.bg[row][url_col] != base_url_bg[0]:
        failures.append("plain click: the band starts before the click, not at it")
    if recorded(record, 1):
        failures.append("plain click: it opened without Ctrl")

    if failures:
        print("terminal link probe: FAIL")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("terminal link probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
