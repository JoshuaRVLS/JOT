#!/usr/bin/env python3
"""Probe: undo after a "Format Document" that formatted nothing undoes the edit.

The palette's "Format Document" snapshots the buffer before it runs, so on a
document with no tab to expand it pushed an undo state describing the buffer
exactly as it already was. Ctrl+Z then restored that state - nothing visible
happened at all, and the real edit the user meant to undo stayed in place: the
keypress read as dead.

This probe types a character into a tab-free file, runs the palette command
(waited for by its "Formatted document" message, so a run that never reached
the command fails with that stated), and presses Ctrl+Z. With the no-op state
skipped the character is gone; with it left in place the line still reads
"xalpha".

Usage: test/format_undo_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# The gutter marks a code row: this probe reads the text after the line number.
CODE_ROW = re.compile(r"^\s*(\d+)\s")
FORMAT_MESSAGE = "Formatted document"


def probe_text() -> str:
    # No tab anywhere: the formatter has nothing to expand.
    return "alpha\nbeta\ngamma\n"


def write_file(path: str, text: str) -> None:
    with open(path, "w") as fh:
        fh.write(text)


def code_rows(screen):
    """The painted code rows as (line number, text after the number)."""
    rows = screen.text().splitlines()
    out = []
    for text in rows:
        match = CODE_ROW.match(text)
        if not match:
            continue
        out.append((int(match.group(1)), text[match.end():].rstrip()))
    return out


def run(binary: str, path: str, cfg: str):
    """Types a character, runs the palette format, then undoes it."""
    seen = {"formatted": False, "rows": []}

    def saw_format_message(screen) -> bool:
        if FORMAT_MESSAGE in screen.text():
            seen["formatted"] = True
            return True
        return False

    def snapshot(screen) -> bool:
        seen["rows"] = code_rows(screen)
        return True

    phases = [
        (1.5, b"x"),
        (0.4, b"\x10"),  # command palette
        (0.5, b"format"),
        (0.6, b"\r"),  # run "Format Document"
        (0.0, saw_format_message),
        (0.5, b"\x1a"),  # Ctrl+Z
        (0.6, snapshot),
    ]
    run_in_pty(binary, [path], b"", settle=2.0, after=0.4, cols=90, rows=20,
               cfg=cfg, phases=phases)
    return seen


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("format undo probe: SKIP - no binary at %s" % binary)
        return 2

    work = "/tmp/jot_format_undo_probe"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    path = os.path.join(work, "sample.txt")
    cfg = os.path.join(work, "cfg")
    os.makedirs(os.path.join(cfg, "configs"), exist_ok=True)
    write_file(path, probe_text())

    seen = run(binary, path, cfg)

    if dump:
        for number, text in seen["rows"]:
            print("  %4d %s" % (number, text))

    if not seen["formatted"]:
        print("format undo probe: FAIL - the palette never ran Format Document"
              " (no %r message)" % FORMAT_MESSAGE)
        return 1

    first = [text for number, text in seen["rows"] if number == 1]
    if first != ["alpha"]:
        print("format undo probe: FAIL - after Ctrl+Z line 1 reads %r, expected"
              " 'alpha' (the typed character undone, not the no-op state)"
              % (first[0] if first else "<not painted>"))
        return 1

    print("format undo probe: PASS (undo skipped the no-op format and undid the"
          " typed character)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
