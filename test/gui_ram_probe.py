#!/usr/bin/env python3
"""Probe: the GUI's own memory stays small and bounded while scrolling.

The GUI frontend is the only part of jot that holds GPU-side buffers, and the
one that got looked at with a process monitor and called fat: a fixed 4 MB quad
scratch reserved up front, a 4 MB CPU copy of the glyph atlas kept beside the
texture, and a second copy of the cell grid kept for a terminal diff the GUI
never runs. This drives the real binary on a private X display and reads its
own report of those buffers (JOT_GUI_DEBUG), then scrolls hard at a big grid
and checks the process does not grow with the history.

Scenes:
  * boot: the batch scratch is one flush and the diff baseline is not allocated,
    both reported by the frontend itself under JOT_GUI_DEBUG, and text is on
    screen, which is what shows the atlas still rasterizes without its CPU copy,
  * big grid: zooming the font out multiplies the cell count while the scratch
    stays the same size (it is a working set, not a buffer sized for the
    largest grid the frontend might ever draw),
  * scroll: six rounds of wheel bursts with the frontend retaining viewports
    for the slide; the process may not keep growing round after round, and its
    peak stays inside a bound that a retained history would blow through.

Usage: test/gui_ram_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary or no python-xlib).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from gui_screen import GuiSession, modal_row_color
except ImportError:
    GuiSession = None

if GuiSession is not None:
    try:
        import Xlib.display  # noqa: F401
        from Xlib import XK
    except ImportError:
        GuiSession = None

# One flush of the quad scratch. Anything near a megabyte means the frontend
# went back to sizing the buffer for the largest grid it could draw.
MAX_BATCH_KIB = 512
# The scroll scenes may hold retained viewports for a slide (a few pane bodies
# at this grid, dropped again as the slide settles) and llvmpipe's arenas grow
# with the glyphs a frame uses. A leak or an unbounded frame history is far
# past this; the round-to-round check below is the sharp one.
MAX_SCROLL_GROWTH_KIB = 24 * 1024
MAX_ROUND_GROWTH_KIB = 6 * 1024


def workspace(tmp: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    path = os.path.join(tmp, "probe.txt")
    with open(path, "w") as fh:
        for i in range(400):
            fh.write("line %d of the probe file, long enough to paint\n" % i)
    return path


def buffers_line(session):
    """The last (batch KiB, diff rows, grid) the frontend reported."""
    found = None
    for line in session.debug_lines():
        match = re.search(r"batch_kib=([0-9.]+) atlas=[0-9x]+ diff_rows=([0-9]+)", line)
        if match:
            found = (float(match.group(1)), int(match.group(2)))
    return found


def grid_of(session):
    """(cols, rows) of the last grid the frontend reported, or None."""
    found = None
    for line in session.debug_lines():
        match = re.search(r"grid=([0-9]+)x([0-9]+)", line)
        if match:
            found = (int(match.group(1)), int(match.group(2)))
    return found


def text_pixels(session, cell_h):
    """Pixels on the first text rows that are not the pane's own background."""
    top = int(2 * cell_h)
    rows = session.capture(x0=30, x1=70, y0=top, y1=top + int(2 * cell_h))
    modal = modal_row_color(rows)
    return sum(1 for row in rows for px in row if px != modal)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("gui ram probe: SKIP - no binary at %s" % binary)
        return 2
    if GuiSession is None:
        print("gui ram probe: SKIP - python-xlib is not installed")
        return 2

    tmp = "/tmp/jot_gui_ram_probe"
    path = workspace(tmp)
    session = GuiSession(binary, [path], cfg=tmp, screen=(1280, 1024))
    failures = []
    try:
        size = session.cell_size()
        if size is None:
            print("gui ram probe: FAIL - the frontend never reported its cell size")
            return 1
        cell_h = size[1]
        # Wait for the first painted frame: until it draws, the window is black
        # and the glyph check below would report a frontend that never painted.
        deadline = time.time() + 20.0
        pixels = 0
        while time.time() < deadline:
            pixels = text_pixels(session, cell_h)
            if pixels >= 20:
                break
            time.sleep(0.2)
        boot = buffers_line(session)
        if boot is None:
            print("gui ram probe: FAIL - no buffer report (JOT_GUI_DEBUG is off?)")
            return 1
        boot_rss = session.rss_kb()
        print("boot: batch %.0f KiB, diff baseline %d rows, grid %s, rss %d kB"
              % (boot[0], boot[1], grid_of(session), boot_rss))
        if boot[0] > MAX_BATCH_KIB:
            failures.append("boot: the quad scratch is %.0f KiB, not one flush" % boot[0])
        if boot[1] != 0:
            failures.append("boot: the frontend keeps a %d-row terminal diff baseline" % boot[1])
        if pixels < 20:
            failures.append("boot: only %d text pixels on screen, the atlas is not painting"
                            % pixels)

        # Big grid: the font zooms out, the cell count multiplies, the scratch
        # may not follow it.
        before = grid_of(session)
        for _ in range(8):
            session.key(XK.XK_minus, ctrl=True)
            time.sleep(0.3)
        time.sleep(0.8)
        after = grid_of(session)
        grown = buffers_line(session)
        if after is None or before is None or after[0] * after[1] <= before[0] * before[1]:
            failures.append("font zoom did not grow the grid: %s -> %s" % (before, after))
        else:
            print("grid %sx%s -> %sx%s, batch %.0f KiB, rss %d kB"
                  % (before[0], before[1], after[0], after[1], grown[0], session.rss_kb()))
        if grown[0] > MAX_BATCH_KIB:
            failures.append("big grid: the quad scratch grew to %.0f KiB" % grown[0])
        if grown[1] != 0:
            failures.append("big grid: the frontend keeps a %d-row diff baseline" % grown[1])
        if text_pixels(session, cell_h) < 20:
            failures.append("big grid: the zoomed atlas stopped painting text")

        # Scroll: the frontend retains viewports for the slide, so the process
        # grows while a burst is in flight and gives the memory back as each
        # slide settles. What it may not do is grow round after round.
        start_rss = session.rss_kb()
        peaks = []
        for round_i in range(3):
            peak = session.rss_kb()
            for _ in range(8):
                session.wheel(clicks=8, down=True, delay=0.01)
                peak = max(peak, session.rss_kb())
            time.sleep(0.6)
            for _ in range(8):
                session.wheel(clicks=8, down=False, delay=0.01)
                peak = max(peak, session.rss_kb())
            time.sleep(0.6)
            peaks.append(peak)
            if dump:
                print("   round %d: peak %d kB (%+d from idle)"
                      % (round_i, peak, peak - start_rss))
        print("scroll: peak %d kB (rounds %s), start %d kB" % (max(peaks), peaks, start_rss))
        if max(peaks) - start_rss > MAX_SCROLL_GROWTH_KIB:
            failures.append("scroll: peak grew %d kB over the idle process"
                            % (max(peaks) - start_rss))
        if peaks[-1] > peaks[0] + MAX_ROUND_GROWTH_KIB:
            failures.append("scroll: kept growing round after round (%s)" % peaks)
        if session.rss_kb() > 400 * 1024:
            failures.append("scroll: the process holds %d kB" % session.rss_kb())
    finally:
        session.stop()

    if failures:
        for failure in failures:
            print("gui ram probe: FAIL - %s" % failure)
        return 1
    print("gui ram probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
