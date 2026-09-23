#!/usr/bin/env python3
"""Probe: a bracketed paste lands as text, terminator and all.

The paste body is opaque -- how long it is can only be known from the
terminator, `ESC [ 201 ~` -- so the reader matches that terminator one byte at
a time and treats anything that does not carry the match on as body text. The
dark path is a body that stops inside the terminator (which a terminal writing
a large paste through a full pty buffer can do): the bytes still owed are the
rest of the terminator, and if they are not claimed they arrive later as typed
text, leaving a stray `~` in the file.

Two runs: a paste written whole, and one whose terminator is split across a gap
longer than the reader's terminator timeout (20 ms). Both paste `hello`, then
type `zz`; a leaked terminator shows up as `~` between the two.

Usage: test/paste_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 100, 24

PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"
# Split after `ESC [ 201`: the `~` is what the reader is owed and cannot see.
END_SPLIT = b"\x1b[201"


def workspace(tmp: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    path = os.path.join(tmp, "a.txt")
    with open(path, "w") as fh:
        fh.write("alpha\nbeta\ngamma\n")
    return path


def run(binary: str, tmp: str, path: str, body: bytes, keys: bytes, phases, dump: bool):
    screen = run_in_pty(binary, [path], keys, settle=2.0, after=0.3,
                        cfg=tmp + "_cfg", cwd=tmp, cols=COLS, rows=ROWS,
                        phases=phases)
    if dump:
        print(screen.text())
        print("-" * 70)
    return screen.text()


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("paste probe: SKIP - no binary at %s" % binary)
        return 2

    failures = []
    # `i` opens insert mode; the paste is one write, and `zz` is typed after it.
    for label, keys, phases in (
        ("whole terminator", b"i" + PASTE_START + b"hello" + PASTE_END,
         [(0.3, b"zz")]),
        ("split terminator", b"i" + PASTE_START + b"hello" + END_SPLIT,
         [(0.08, b"~"), (0.3, b"zz")]),
    ):
        tmp = "/tmp/jot_paste_probe_" + label.split()[0]
        path = workspace(tmp)
        text = run(binary, tmp, path, b"hello", keys, phases, dump)
        landed = "hellozz" in text
        stray = "hello~zz" in text
        print("%s: pasted text landed: %s, terminator typed: %s"
              % (label, landed, stray))
        if not landed:
            failures.append("%s: the paste never reached the buffer" % label)
        if stray:
            failures.append("%s: the terminator's tail was read back as typing" % label)

    if failures:
        print("paste probe: FAIL")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("paste probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
