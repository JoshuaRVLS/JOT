#!/usr/bin/env python3
"""Probe: the floating terminal (Alt+Shift+T, :termfloat), in a real session.

C++ pins the box's geometry, its painting and its selection against a shell-less
vterm. What only a real session proves is the shell inside the box: that the
chord opens one at the workspace root, that the keys land in the pty, that
Escape hides the box without ending the process, and that the wheel walks the
same shell's scrollback once the box is back.

Three scenes, each its own session (a scene must not inherit another's shell):
  * open: the box appears over the buffer, covering a filler line that was on
    the grid a moment earlier, and leaves the status line alone; `echo FLOAT_OK`
    lands in it and the shell reports the workspace as its directory;
  * hide: Escape removes the box, and the chord brings it back with the same
    shell, which is what FLOAT_OK still being on screen proves;
  * scroll: `seq 1 40` pushes that line out of the box and the wheel over the
    box walks back to it.

Usage: test/float_terminal_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 120, 36
ROOT = os.path.realpath("/tmp/jot_termfloat_probe")
CFG = "/tmp/jot_termfloat_probe_cfg"
SOURCE = os.path.join(ROOT, "tall.cpp")

# Alt+Shift+T as the kitty protocol reports it: 116 = 't', modifier 4 = the
# 1+base form of shift(1) + alt(2). The editor matches the uppercase letter,
# which is what Alt+Shift decodes to on every terminal.
FLOAT_KEY = b"\x1b[116;4u"
ESC = b"\x1b"
ENTER = b"\r"
FLOAT_OK = "FLOAT_OK_123"
TITLE = "Floating terminal"


def rows(screen) -> list[str]:
    return [line.rstrip() for line in screen.text().split("\n")]


def grid(text: str) -> list[str]:
    return [line.rstrip() for line in text.split("\n")]


def row_of_grid(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines):
        if needle in line:
            return i
    return -1


def row_of(screen, needle: str) -> int:
    return row_of_grid(rows(screen), needle)


def box_edges(lines: list[str]):
    """(top, left, right, bottom) of the box's frame, or None when it is gone.

    The title rides the top border, so its row is the box's first; the bottom is
    the corner under the same left edge, which the sidebar's tree glyphs cannot
    fake.
    """
    top = row_of_grid(lines, TITLE)
    if top < 0:
        return None
    line = lines[top]
    left = line.find("┌")
    right = line.rfind("┐")
    if left < 0 or right < 0:
        return None
    for y in range(top + 1, len(lines)):
        if lines[y][left : left + 1] == "└":
            return (top, left, right, y)
    return None


def covered_rows(pre_lines: list[str], lines: list[str], edges) -> list[int]:
    """Rows the box covers where the earlier grid had buffer text inside its frame.

    A line the box lands on is only partly hidden when it is shorter than the
    box's left edge, so the check is per cell range: what is under the frame has
    to have changed, not the whole line.
    """
    top, left, right, bottom = edges
    covered: list[int] = []
    for y in range(top + 1, bottom):
        before = pre_lines[y][left + 1 : right - 1]
        if not before.strip():
            continue
        if lines[y][left + 1 : right - 1] == before:
            covered.append(y)
    return covered


def capture(screen, store) -> bool:
    store.setdefault("pre", screen.text())
    return True


def write_workspace() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT)
    with open(SOURCE, "w") as fh:
        for i in range(60):
            # Long enough that the centered box cannot miss the text.
            fh.write("int marker_%d = %d;  // filler %d, padded out to reach under "
                     "the floating box\n" % (i, i, i))


def run(binary, phases):
    return run_in_pty(binary, [SOURCE], b"", settle=5.0, after=0.6, cols=COLS, rows=ROWS,
                      cfg=CFG, cwd=ROOT, phases=phases)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("float terminal probe: SKIP - no binary at %s" % binary)
        return 2

    write_workspace()
    shutil.rmtree(CFG, ignore_errors=True)
    # An empty HOME and a plain shell, as in the terminal cwd probe: no shell rc
    # file may move the shell or decorate its prompt, so what is measured is the
    # editor's own box.
    home = "/tmp/jot_termfloat_probe_home"
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home)
    os.environ["HOME"] = home
    if os.path.exists("/bin/bash"):
        os.environ["SHELL"] = "/bin/bash"
    failures: list[str] = []

    # The box opens, covers buffer text, runs commands in its shell.
    seen: dict[str, str] = {}
    screen = run(
        binary,
        [
            (0.5, lambda s: capture(s, seen)),
            (0.5, FLOAT_KEY),
            (1.2, lambda s: box_edges(rows(s)) is not None),
            (0.6, b"echo " + FLOAT_OK.encode() + ENTER),
            (1.2, lambda s: row_of(s, FLOAT_OK) >= 0),
            (0.6, b'echo "cwd:$PWD"' + ENTER),
            (1.2, lambda s: any("cwd:" in row for row in rows(s))),
        ],
    )
    if dump:
        print(screen.text())
        print("-" * 70)
    lines = rows(screen)
    edges = box_edges(lines)
    if edges is None:
        failures.append("open: no box on screen after Alt+Shift+T")
    else:
        top, left, right, bottom = edges
        if right - left + 1 < 20 or bottom - top + 1 < 5:
            failures.append("open: the box is %dx%d cells" % (right - left + 1, bottom - top + 1))
        # The default asks for a big share of the pane's text rows (85% by
        # 75%), not a small dialog in the middle of the screen.
        if right - left + 1 < COLS * 3 // 4 or bottom - top + 1 < (ROWS - 3) * 2 // 3:
            failures.append("open: the box is %dx%d, under the share the settings ask for"
                            % (right - left + 1, bottom - top + 1))
        if TITLE not in lines[top]:
            failures.append("open: the title is not on the box's first row")
        # The frame stays inside the pane's text rows: the breadcrumb row above
        # them (a chrome float painted after this overlay) still shows the file
        # and starts above the box.
        pre = grid(seen["pre"])
        bar = row_of_grid(lines, "›")
        if bar >= 0 and bar >= top:
            failures.append("open: the box's first row is the breadcrumb row (%d)" % bar)
        if right >= COLS or bottom >= ROWS - 1:
            failures.append("open: the frame reaches (%d,%d) on a %dx%d screen"
                            % (right, bottom, COLS, ROWS))
        elif "tall.cpp" not in lines[-1]:
            failures.append("open: the status line is gone from under the box")
        # Buffer text was inside the frame's columns at those rows; the box now
        # covers those cells.
        covered_count = sum(
            1 for y in range(top + 1, bottom) if pre[y][left + 1 : right - 1].strip()
        )
        if not covered_count:
            failures.append("cover: the buffer had no text where the box lands")
        else:
            still_there = covered_rows(pre, lines, edges)
            if still_there:
                failures.append("cover: the buffer still shows through the box on row(s) %r"
                                % still_there)
    if FLOAT_OK not in "\n".join(lines):
        failures.append("open: %r never appeared in the box" % FLOAT_OK)
    wanted_cwd = "cwd:" + ROOT
    reported = [row.strip() for row in lines if "cwd:" in row]
    if not any(wanted_cwd in row for row in reported):
        failures.append("open: wanted %r, the shell reported %r" % (wanted_cwd, reported))
    print("open + run:   %s" % ("ok" if not failures else "FAILED"))

    # Escape hides the box; the chord brings it back with the same shell.
    before = len(failures)
    screen = run(
        binary,
        [
            (0.5, FLOAT_KEY),
            (1.2, lambda s: box_edges(rows(s)) is not None),
            (0.6, b"echo " + FLOAT_OK.encode() + ENTER),
            (1.2, lambda s: row_of(s, FLOAT_OK) >= 0),
            (0.5, ESC),
            (1.2, lambda s: box_edges(rows(s)) is None),
            (0.5, FLOAT_KEY),
            (1.2, lambda s: box_edges(rows(s)) is not None),
        ],
    )
    if dump:
        print(screen.text())
        print("-" * 70)
    if box_edges(rows(screen)) is None:
        failures.append("hide: the chord did not bring the box back")
    if FLOAT_OK not in "\n".join(rows(screen)):
        failures.append("hide: %r is gone, so the shell did not survive Escape" % FLOAT_OK)
    print("hide + back:  %s" % ("ok" if len(failures) == before else "FAILED"))

    # The wheel over the box walks the same shell's scrollback. The cell is the
    # screen's centre, which the centered box always contains at this size (SGR
    # coordinates are 1-based).
    before = len(failures)
    wheel = b"".join(
        b"\x1b[<64;%d;%dM" % (COLS // 2 + 1, ROWS // 2 + 1) for _ in range(12)
    )
    screen = run(
        binary,
        [
            (0.5, FLOAT_KEY),
            (1.2, lambda s: box_edges(rows(s)) is not None),
            (0.6, b"echo " + FLOAT_OK.encode() + ENTER),
            (1.2, lambda s: row_of(s, FLOAT_OK) >= 0),
            (0.6, b"seq 1 40" + ENTER),
            (1.2, lambda s: row_of(s, "40") >= 0),
            (0.4, lambda s: row_of(s, FLOAT_OK) < 0),
            (0.4, wheel),
            (0.6, lambda s: row_of(s, FLOAT_OK) >= 0),
        ],
    )
    if dump:
        print(screen.text())
        print("-" * 70)
    if row_of(screen, FLOAT_OK) < 0:
        failures.append("scroll: the wheel did not reach %r again" % FLOAT_OK)
    print("scrollback:   %s" % ("ok" if len(failures) == before else "FAILED"))

    if failures:
        print("float terminal probe: FAIL")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("float terminal probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
