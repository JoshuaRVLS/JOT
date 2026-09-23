#!/usr/bin/env python3
"""Probe: the tail of an escape sequence the editor abandoned never becomes text.

The reader consumes a terminal reply byte by byte, and two of its paths give up
part-way: the size probe stops at its deadline, and a mouse report whose next
byte is more than 5 ms away stops there. Both had already eaten the head of the
sequence. Its tail stayed in the pty, where the next read took it for typing --
a DSR position reply ("ESC [ 12 ; 9 R") showing up in the buffer as `9R`, a mouse
report ("ESC [ < 0 ; 12 ; 3 M") as `;12;3M`. This drives the real binary and types
into a file after splitting a report across a gap, then reads the buffer back.

Usage: test/input_tail_probe.py [path-to-jot]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 100, 24
SOURCE = "alpha\nbeta\ngamma\n"

# The two truncations, split where the reader times out: the head as one write,
# the tail late. An SGR mouse press, and a DSR cursor-position reply.
MOUSE_HEAD, MOUSE_TAIL = b"\x1b[<0;5", b";12;3M"
CPR_HEAD, CPR_TAIL = b"\x1b[1", b"2;9R"


def workspace(tmp: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    path = os.path.join(tmp, "a.txt")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    return path


def run(binary: str, tmp: str, path: str, head: bytes, tail: bytes, typed: bytes):
    """Splits `head` and `tail` across a short gap, then types `typed`."""
    return run_in_pty(binary, [path], head, settle=2.5, after=0.05,
                      cfg=tmp + "_cfg", cwd=tmp, cols=COLS, rows=ROWS,
                      phases=[(0.02, tail), (0.3, typed)])


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    if not os.path.exists(binary):
        print("input tail probe: SKIP - no binary at %s" % binary)
        return 2

    tmp = "/tmp/jot_input_tail_probe"
    path = workspace(tmp)
    failures = []

    cases = [
        ("truncated mouse report", MOUSE_HEAD, MOUSE_TAIL, b";12;3M"),
        ("truncated position reply", CPR_HEAD, CPR_TAIL, b"2;9R"),
    ]
    for label, head, tail, leaked in cases:
        screen = run(binary, tmp, path, head, tail, b"zz")
        text = screen.text()
        typed = "zz" in text
        print("%s: tail %r on screen: %s" % (label, leaked.decode(), leaked.decode() in text))
        if not typed:
            failures.append("%s: the text typed after the sequence never reached the buffer"
                            % label)
        if leaked.decode() in text:
            failures.append("%s: the sequence tail %r was read back as typed text"
                            % (label, leaked.decode()))

    # A report that arrives whole still has to be one mouse event and no text.
    whole = b"\x1b[<0;5;7M\x1b[<0;5;7m"
    screen = run(binary, tmp, path, whole, b"", b"zz")
    text = screen.text()
    print("complete mouse report: click on screen: %s" % (";7M" in text))
    if "zz" not in text:
        failures.append("a complete mouse report swallowed the next keystroke")
    if ";7M" in text:
        failures.append("a complete mouse report was read back as typed text")

    if failures:
        print("input tail probe: FAIL")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("input tail probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
