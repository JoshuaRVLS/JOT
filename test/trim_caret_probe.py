#!/usr/bin/env python3
"""Probe: the caret survives a trim of the line it sat at the end of.

The palette's "Trim Trailing Whitespace" shortens every line that ends in
whitespace, but the caret keeps the column it had. With the caret at the end of
a padded line, that column is then past the end of a shorter line, and the next
typed character goes through std::string::insert(), which throws for a position
past the end: the editor aborts with std::out_of_range (the message lands in
<cfg>/logs/jot_stderr.log, since the terminal's stderr is routed away).

This probe types through the real binary: caret to the end of the padded line
(so the column is the line's length), palette trim, then a character and another
one after a moved caret. A live editor ends up with both characters on that
line; a dead one stops painting where it died, so the two characters are the
liveness check as well as the position check. The trim itself is waited for by
its message, so a run that never reached the command fails with that stated
instead of silently passing on an untouched screen.

Usage: test/trim_caret_probe.py [path-to-jot] [--dump]
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
# The caret is walked right this many times to sit at the end of the padded
# first line ("alpha" plus the padding below).
CARET_STEPS = 8
TRIM_MESSAGE = "Trimmed trailing whitespace"


def probe_text() -> str:
    return "alpha" + " " * (CARET_STEPS - len("alpha")) + "\nbeta\ngamma\n"


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


def wait_for_text(needle: str):
    def check(screen) -> bool:
        return needle in screen.text()

    return check


def run(binary: str, path: str, cfg: str):
    """Walks the caret to EOL, runs the palette trim, then types two characters."""
    seen = {"trimmed": False, "rows": []}

    def saw_trim_message(screen) -> bool:
        if TRIM_MESSAGE in screen.text():
            seen["trimmed"] = True
            return True
        return False

    def snapshot(screen) -> bool:
        seen["rows"] = code_rows(screen)
        return True

    phases = [
        (1.5, b"\x1b[C" * CARET_STEPS),
        (0.4, b"\x10"),  # command palette
        (0.5, b"trim"),
        (0.6, b"\r"),  # run "Trim Trailing Whitespace"
        # The palette dispatch can lag a beat behind the Enter; the message is
        # what says the trim ran at all.
        (0.0, saw_trim_message),
        (0.5, b"Z"),
        (0.5, b"\x1b[C"),
        (0.5, b"Q"),
        (0.0, snapshot),
    ]
    run_in_pty(binary, [path], b"", settle=2.0, after=0.4, cols=90, rows=20,
               cfg=cfg, phases=phases)
    return seen


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("trim caret probe: SKIP - no binary at %s" % binary)
        return 2

    work = "/tmp/jot_trim_caret_probe"
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

    if not seen["trimmed"]:
        print("trim caret probe: FAIL - the palette never ran Trim Trailing"
              " Whitespace (no %r message)" % TRIM_MESSAGE)
        return 1

    first = [text for number, text in seen["rows"] if number == 1]
    if first != ["alphaZQ"]:
        print("trim caret probe: FAIL - line 1 reads %r, expected 'alphaZQ'"
              % (first[0] if first else "<not painted>"))
        # A dead editor is worth calling out: it is what the missing characters
        # mean, and the abort message is in the child's stderr log.
        if first and first[0] == "alpha":
            print("  - the editor stopped painting after the trim; see %s"
                  % os.path.join(cfg, "logs", "jot_stderr.log"))
        return 1

    print("trim caret probe: PASS (caret at the trimmed end, both typed"
          " characters on the line)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
