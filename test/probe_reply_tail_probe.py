#!/usr/bin/env python3
"""Probe: a cursor-position reply the editor gave up on is not typed back.

At startup (and on a resize) the editor parks the cursor and asks the terminal
where it is with DSR, ``CSI 6 n``, then reads the ``ESC [ row ; col R`` reply
under a short budget. Giving up part-way leaves the head of that reply consumed
and its tail in the input queue, where the next read takes it for typing: on a
191-column terminal the reply is ``ESC [ 24 ; 191 R`` and the buffer gets
``191R``.

This is the only probe that answers the query, so it is the only one that can
see the tail: it watches the child's output for the request, writes the reply
back in two halves with a gap that outlasts the read budget, and then types.

Usage: test/probe_reply_tail_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
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
# Longer than the probe's read budget, so the editor has given up by the time
# the tail lands.
GAP_S = 0.12

SOURCE = "alpha\nbeta\ngamma\n"


def workspace(tmp: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    path = os.path.join(tmp, "a.txt")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    return path


class Reply:
    """Writes the position reply back in two halves, once."""

    def __init__(self) -> None:
        self.sent = False

    def __call__(self, fd: int, chunk: bytes) -> None:
        if self.sent or REQUEST not in chunk:
            return
        self.sent = True
        os.write(fd, REPLY_HEAD)
        time.sleep(GAP_S)
        os.write(fd, REPLY_TAIL)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("probe reply tail probe: SKIP - no binary at %s" % binary)
        return 2

    tmp = "/tmp/jot_probe_reply_tail"
    path = workspace(tmp)
    reply = Reply()
    screen = run_in_pty(binary, [path], b"", settle=2.0, after=1.0,
                        cfg=tmp + "_cfg", cwd=tmp, cols=COLS, rows=ROWS,
                        phases=[(0.3, b"zz"), (0.3, b"")],
                        on_output=reply)
    if dump:
        print(screen.text())
        print("-" * 70)

    text = screen.text()
    failures = []
    print("answered the position query: %s" % reply.sent)
    print("tail %r on screen: %s" % (REPLY_TAIL.decode(), REPLY_TAIL.decode() in text))
    if not reply.sent:
        failures.append("the editor never asked for the cursor position, so the leak "
                        "was not exercised")
    if REPLY_TAIL.decode() in text:
        failures.append("the position reply's tail %r was read back as typed text"
                        % REPLY_TAIL.decode())
    if "zz" not in text:
        failures.append("the text typed after the reply never reached the buffer")

    if failures:
        print("probe reply tail probe: FAIL")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("probe reply tail probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
