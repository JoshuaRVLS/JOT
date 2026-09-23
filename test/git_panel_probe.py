#!/usr/bin/env python3
"""Probe: the git panel's view tabs, its scroll and its cursor.

C++ pins the model (the tab list, the hints, the scroll math). What only a real
session proves is the wiring: that clicking a view tab switches the panel, that
the wheel scrolls a list longer than the panel, and that the cursor keeps itself
on screen while it moves.

The panel's rows depend on what the frame spends above the dock (a tabline, a
breadcrumb), so the first run measures them off the grid and the later runs use
those cells:
  * measure: the Files view is up with its section header, the first file and
    the cursor bar on it, and the tab strip is where the grid says it is;
  * wheel: two notches over the list move the window, so the first file on
    screen is a later one;
  * tabs: clicking `3 Branches` switches the view, its hint and its branch row
    replacing the file list;
  * cursor: pressing j past the last visible row walks the selection to the last
    file, which can only be on screen if the window followed it.

Usage: test/git_panel_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary or no git).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 130, 36
PANEL_W = 42  # the default right_panel_width
PANEL_X = COLS - PANEL_W
ROOT = "/tmp/jot_git_panel_probe"
CFG = "/tmp/jot_git_panel_probe_cfg"
FILES = 36
PALETTE = b"\x1b[112;6u"
ENTER = b"\r"
BAR = "\u258c"
OPEN = [(0.6, PALETTE), (0.8, b"gitpanel"), (0.6, ENTER),
        (1.5, lambda s: "Files" in s.text() or True)]


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()


def write_workspace() -> str:
    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.rmtree(CFG, ignore_errors=True)
    os.makedirs(ROOT)
    for i in range(FILES):
        with open(os.path.join(ROOT, "f%02d.cpp" % i), "w") as fh:
            fh.write("int value_%d = %d;\n" % (i, i))
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.email=probe@local", "-c", "user.name=probe", "commit", "-qm", "init")
    # Every file changes, so the Files view is longer than the panel, and one
    # file is new, so there are two sections.
    for i in range(FILES):
        with open(os.path.join(ROOT, "f%02d.cpp" % i), "a") as fh:
            fh.write("int more_%d = %d;\n" % (i, i))
    with open(os.path.join(ROOT, "u_new.cpp"), "w") as fh:
        fh.write("int fresh = 1;\n")
    return git("rev-parse", "--abbrev-ref", "HEAD")


def lines(screen) -> list[str]:
    return screen.text().split("\n")


def cell_of(screen, needle: str) -> tuple[int, int]:
    """0-based (x, y) of `needle`'s first cell, or (-1, -1)."""
    for y, line in enumerate(lines(screen)):
        x = line.find(needle)
        if x >= 0:
            return x, y
    return -1, -1


def press(x: int, y: int) -> bytes:
    return b"\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (x + 1, y + 1, x + 1, y + 1)


def wheel_down(x: int, y: int, notches: int = 2) -> bytes:
    return b"".join(b"\x1b[<65;%d;%dM" % (x + 1, y + 1) for _ in range(notches))


def first_file_on_screen(screen) -> str:
    for _y, line in enumerate(lines(screen)):
        for i in range(FILES):
            name = "f%02d.cpp" % i
            if name in line[PANEL_X:]:
                return name
    return ""


def bar_rows(screen) -> list[int]:
    """Rows of the dock whose first inked cell is the cursor bar."""
    out = []
    for y, line in enumerate(lines(screen)):
        seg = line[PANEL_X : PANEL_X + PANEL_W].lstrip()
        if seg.startswith(BAR):
            out.append(y)
    return out


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("git panel probe: SKIP - no binary at %s" % binary)
        return 2
    if shutil.which("git") is None:
        print("git panel probe: SKIP - git is not on PATH")
        return 2

    branch = write_workspace()
    os.environ.setdefault("GIT_CONFIG_GLOBAL", "/dev/null")
    home = "/tmp/jot_git_panel_probe_home"
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home)
    os.environ["HOME"] = home
    if os.path.exists("/bin/bash"):
        os.environ["SHELL"] = "/bin/bash"

    def run(phases):
        return run_in_pty(binary, [], b"", settle=5.0, after=0.8, cols=COLS, rows=ROWS, cfg=CFG,
                          cwd=ROOT, phases=phases)

    failure: list[str] = []

    # Measure: the panel's own rows off the grid.
    screen = run(OPEN + [(0.6, lambda s: True)])
    if dump:
        print(screen.text())
        print("-" * 70)
    first_x, first_y = cell_of(screen, "f00.cpp")
    tab_x, tab_y = cell_of(screen, "3 Branches")
    text = screen.text()
    if "unstaged" not in text:
        failure.append("panel: the unstaged section header is not on screen")
    if first_y < 0 or tab_y < 0:
        failure.append("panel: the file list or the view tabs are not on screen")
    if not bar_rows(screen):
        failure.append("panel: the first row carries no cursor bar")
    print("panel:         %s" % ("ok" if not failure else "FAILED"))
    if failure and (first_y < 0 or tab_y < 0):
        for item in failure:
            print("  - " + item)
        return 1

    def body_cell():
        # A cell inside the list, on the first file's row.
        return PANEL_X + 20, first_y

    # The wheel scrolls the window: the first file on screen is a later one.
    before = len(failure)
    wx, wy = body_cell()
    screen = run(OPEN + [(0.6, press(wx, wy)), (0.4, wheel_down(wx, wy)), (0.8, lambda s: True)])
    if dump:
        print(screen.text())
        print("-" * 70)
    first = first_file_on_screen(screen)
    if first in ("", "f00.cpp"):
        failure.append("wheel: the list did not scroll (first file on screen: %r)" % first)
    print("wheel:         %s" % ("ok" if len(failure) == before else "FAILED"))

    # Clicking the Branches tab switches the view.
    before = len(failure)
    screen = run(OPEN + [(0.5, press(tab_x + 2, tab_y)), (0.8, lambda s: True)])
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()
    if "space checkout" not in text:
        failure.append("tabs: clicking the Branches tab did not switch the view")
    if branch and branch not in text:
        failure.append("tabs: the branch %r is not on screen" % branch)
    if "unstaged" in text:
        failure.append("tabs: the file view is still on screen")
    print("tabs:          %s" % ("ok" if len(failure) == before else "FAILED"))

    # The cursor walks to the last file and the window follows it.
    before = len(failure)
    cx, cy = body_cell()
    screen = run(OPEN + [(0.6, press(cx, cy)), (0.6, b"j" * (FILES + 4)), (0.8, lambda s: True)])
    if dump:
        print(screen.text())
        print("-" * 70)
    last = "f%02d.cpp" % (FILES - 1)
    if last not in screen.text():
        failure.append("cursor: %r never came on screen, so the window did not follow" % last)
    if not bar_rows(screen):
        failure.append("cursor: the selected row carries no cursor bar")
    print("cursor:        %s" % ("ok" if len(failure) == before else "FAILED"))

    if failure:
        print("git panel probe: FAIL")
        for item in failure:
            print("  - " + item)
        return 1
    print("git panel probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
