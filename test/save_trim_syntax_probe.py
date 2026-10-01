#!/usr/bin/env python3
"""Probe: a save that trims trailing whitespace leaves highlighting alone.

`Editor::save_buffer_at` drops trailing whitespace from the buffer before it
writes the bytes. That rewrite is a content mutation like any other, so it has
to go through the edit hooks (mark_edited, ts_begin_edit, decoration rebase) the
typing paths use; it used to only push an undo state afterwards, which left the
tree-sitter tree describing the untrimmed lines while the byte offsets were
rebuilt from the trimmed ones. Every highlight past a trimmed line then came
from somewhere else in the file - and stayed that way, since nothing marks the
tree out of sync again.

The probe opens a 220-line file whose first line has trailing spaces, saves it,
scrolls to lines no frame had painted before the save, and compares what it sees
with the same bytes opened from disk. The scroll is asserted to have landed past
the lines the first paint filled in, so a probe that stopped scrolling fails
instead of comparing two cached screens.

Usage: test/save_trim_syntax_probe.py [path-to-jot] [--dump]
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

LINES = 220
PAD = " " * 24
# Half a screen is filled by the first paint, so anything from here on can only
# have been highlighted after the save.
COLD_FROM = 30

# The gutter marks a code row, which is the only part of the frame the two runs
# can be compared on: the tab strip carries the modified dot a save clears.
CODE_ROW = re.compile(r"^\s*(\d+)\s")


def probe_text(trimmed: bool) -> str:
    """The file's bytes: the same lines, with the first one padded or not."""
    out = []
    for i in range(1, LINES + 1):
        if i % 2 == 0:
            line = "int value%d = %d;" % (i, i)
        else:
            line = "// note %d with words" % i
        if i == 1 and not trimmed:
            line += PAD
        out.append(line)
    return "\n".join(out) + "\n"


def write_file(path: str, text: str) -> None:
    with open(path, "w") as fh:
        fh.write(text)


def code_rows(screen):
    """The painted code rows as (line number, text, colours)."""
    rows = screen.text().splitlines()
    out = []
    for y, text in enumerate(rows):
        match = CODE_ROW.match(text)
        if not match:
            continue
        start = match.start(1)
        out.append((int(match.group(1)), text.rstrip(), list(screen.fg[y][start:len(text)])))
    return out


def run(binary: str, path: str, cfg: str, save: bool):
    """Opens the file, saves when asked, scrolls, and returns the code rows."""
    phases = []
    if save:
        phases.append((1.5, b"\x13"))
        phases.append((0.0, wait_saved()))
    # Enough page-downs to clear the rows the first paint already cached: each
    # one moves the caret ten lines and the viewport only as it has to follow.
    for _ in range(8):
        phases.append((0.35, b"\x1b[6~"))
    phases.append((0.4, b"\x1b[6~"))
    check, shots = capture_after(1.5)
    phases.append((0.0, check))
    run_in_pty(binary, [path], b"", settle=2.0, after=0.5, cols=110, rows=26,
               cfg=cfg, phases=phases)
    return shots.get("rows", [])


def wait_saved():
    def check(screen) -> bool:
        return "Saved:" in screen.text()

    return check


def capture_after(seconds: float):
    shots = {}
    state = {"t0": None}

    def check(screen) -> bool:
        if state["t0"] is None:
            state["t0"] = time.time()
        if time.time() - state["t0"] > seconds:
            shots["rows"] = code_rows(screen)
            return True
        return False

    return check, shots


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("save trim syntax probe: SKIP - no binary at %s" % binary)
        return 2

    work = "/tmp/jot_save_trim_syntax_probe"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    path = os.path.join(work, "probe.cpp")

    cfg = os.path.join(work, "cfg")
    os.makedirs(os.path.join(cfg, "configs"), exist_ok=True)
    # The on-save formatters are off so the only thing the save changes is the
    # trailing whitespace this probe is about.
    with open(os.path.join(cfg, "configs", "settings.conf"), "w") as fh:
        fh.write("clang_format_on_save = false\nprettier_on_save = false\n")

    write_file(path, probe_text(trimmed=False))
    after_trim = run(binary, path, cfg, save=True)
    with open(path) as fh:
        saved = fh.read()
    # The save has to be the trim and nothing else, or the comparison below is
    # between two different files.
    if saved != probe_text(trimmed=True):
        print("save trim syntax probe: FAIL - the save did not leave exactly the"
              " trimmed bytes on disk (%d bytes, expected %d)"
              % (len(saved), len(probe_text(trimmed=True))))
        return 1

    fresh = run(binary, path, cfg, save=False)

    if not after_trim or not fresh:
        print("save trim syntax probe: FAIL - no code rows were painted")
        return 1
    first_line = after_trim[0][0]
    if first_line < COLD_FROM:
        print("save trim syntax probe: FAIL - the scroll stopped at line %d, inside the"
              " rows the first paint had already highlighted" % first_line)
        return 1

    if dump:
        for number, text, fg in after_trim:
            print("  after %4d %-40s %s" % (number, text[:40], fg[:10]))
        for number, text, fg in fresh:
            print("  fresh %4d %-40s %s" % (number, text[:40], fg[:10]))

    if [r[0] for r in after_trim] != [r[0] for r in fresh]:
        print("save trim syntax probe: FAIL - the two runs scrolled to different lines")
        return 1

    mismatched = [a[0] for a, b in zip(after_trim, fresh) if a[2] != b[2]]
    if mismatched:
        print("save trim syntax probe: FAIL")
        print("  - lines %s highlight differently after the save than the same bytes do"
              " when opened" % ", ".join(str(n) for n in mismatched[:6]))
        return 1

    print("save trim syntax probe: PASS (%d rows from line %d match)"
          % (len(after_trim), first_line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
