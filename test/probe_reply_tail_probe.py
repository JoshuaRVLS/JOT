#!/usr/bin/env python3
"""Probe: a cursor-position reply the editor gave up on is not typed back.

At startup (and on a resize) the editor parks the cursor and asks the terminal
where it is with DSR, ``CSI 6 n``, then reads the ``ESC [ row ; col R`` reply
under a short budget. Giving up part-way leaves the head of that reply consumed
and its tail in the input queue, where the next read takes it for typing: on a
191-column terminal the reply is ``ESC [ 24 ; 191 R`` and the buffer gets
``191R``.

This is the only probe that answers the query, so it is the only one that can
see the tail. It runs the reply twice, because the editor claims the tail two
ways: a short gap is finished on the spot by the drain, and a long one -- past
the drain, while the user is typing into the gap -- has to be finished later,
off a claim that only eats bytes shaped like the reply. Both runs also type
``zz`` into the gap, which the editor must still deliver.

The probe also has to give back what it read and did not use: a mouse report or
keystroke that lands in its read window is not its business. The third scenario
races a wheel event against the probe and checks both halves of that -- the
report's tail never becomes text, and the event still scrolls the view.

Usage: test/probe_reply_tail_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# The terminal the reply describes. Wider than the reply's own tail is long, so
# the leak is unmistakable in the buffer.
ROWS, COLS = 30, 191
REQUEST = b"\x1b[6n"
# The reply: row 30, column 191. The head stops after the row, the tail carries
# the rest of the column and the final byte.
REPLY_HEAD = b"\x1b[30;1"
REPLY_TAIL = b"91R"
# Past the read budget, so the editor has given up by the time the tail lands;
# the second gap is past the drain too, so only the later claim can catch it.
SCENARIOS = (("drain", 0.12), ("claim", 0.6))

SOURCE = "alpha\nbeta\ngamma\n"
# A wheel-down event, SGR (`ESC [ < 65 ; x ; y M`). Wheel rather than a click
# because the view's first line number says plainly whether it was delivered.
WHEEL = b"\x1b[<65;10;10M"
WHEEL_HEAD = b"\x1b[<65;10"
WHEEL_TAIL = b";10M"
LONG_SOURCE = "".join("line %d\n" % i for i in range(1, 61))


def workspace(tmp: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    path = os.path.join(tmp, "a.txt")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    return path


class Reply:
    """Writes the position reply back in two halves, once."""

    def __init__(self, gap_s: float) -> None:
        self.sent = False
        self.gap_s = gap_s

    def __call__(self, fd: int, chunk: bytes) -> None:
        if self.sent or REQUEST not in chunk:
            return
        self.sent = True
        os.write(fd, REPLY_HEAD)
        time.sleep(self.gap_s)
        os.write(fd, REPLY_TAIL)


def first_line(text: str):
    match = re.search(r"^\s*(\d+)\s", text, re.M)
    return int(match.group(1)) if match else None


def run_wheel_race(binary: str, tmp: str, dump: bool) -> list[str]:
    """Splits a wheel event around the probe's read window."""
    path = workspace(tmp)
    with open(path, "w") as fh:
        fh.write(LONG_SOURCE)
    sent = []

    def on_output(fd, chunk):
        if sent or REQUEST not in chunk:
            return
        sent.append(True)
        os.write(fd, WHEEL_HEAD)
        time.sleep(0.15)
        os.write(fd, WHEEL_TAIL)

    screen = run_in_pty(binary, [path], b"", settle=2.0, after=0.8,
                        cfg=tmp + "_cfg", cwd=tmp, cols=COLS, rows=ROWS,
                        on_output=on_output)
    if dump:
        print(screen.text())
        print("-" * 70)

    text = screen.text()
    top = first_line(text)
    failures = []
    print("  wheel raced the probe: asked=%s, first visible line=%s" % (bool(sent), top))
    if not sent:
        failures.append("the editor never asked for the cursor position, so the race "
                        "was not exercised")
    if WHEEL_TAIL.decode() in text:
        failures.append("the wheel report's tail %r was read back as typed text"
                        % WHEEL_TAIL.decode())
    if top is not None and top <= 1:
        failures.append("the wheel event read while the probe was running was dropped "
                        "instead of being handed back (view still at line %s)" % top)
    return failures


def run(binary: str, tmp: str, gap_s: float, dump: bool) -> list[str]:
    path = workspace(tmp)
    reply = Reply(gap_s)
    screen = run_in_pty(binary, [path], b"", settle=2.0, after=1.0,
                        cfg=tmp + "_cfg", cwd=tmp, cols=COLS, rows=ROWS,
                        phases=[(0.3, b"zz"), (0.3, b"")],
                        on_output=reply)
    if dump:
        print(screen.text())
        print("-" * 70)

    text = screen.text()
    tail = REPLY_TAIL.decode()
    failures = []
    print("  answered the position query: %s" % reply.sent)
    print("  tail %r on screen: %s" % (tail, tail in text))
    if not reply.sent:
        failures.append("the editor never asked for the cursor position, so the leak "
                        "was not exercised")
    if tail in text:
        failures.append("the position reply's tail %r was read back as typed text" % tail)
    if "zz" not in text:
        failures.append("the text typed into the gap never reached the buffer")
    return failures


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("probe reply tail probe: SKIP - no binary at %s" % binary)
        return 2

    failures = []
    for name, gap_s in SCENARIOS:
        print("%s: reply tail %.2fs late" % (name, gap_s))
        found = run(binary, "/tmp/jot_probe_reply_tail_" + name, gap_s, dump)
        failures.extend("%s: %s" % (name, failure) for failure in found)

    print("race: input arrives while the probe reads")
    failures.extend("race: %s" % failure for failure in
                    run_wheel_race(binary, "/tmp/jot_probe_reply_tail_race", dump))

    if failures:
        print("probe reply tail probe: FAIL")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("probe reply tail probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
