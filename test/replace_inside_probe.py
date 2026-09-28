#!/usr/bin/env python3
"""Probe: the in-place edits act on what the caret is in, and on nothing else.

A unit test can drive the actions directly, but the chords are what the user
presses, and the path from a key to the buffer runs through the keymap grammar
and the native fallback -- neither of which a unit test sees. This drives the
real binary over a pty and reads the edited lines back off the screen:

  * Alt+Backspace takes the whole word the caret is sitting on, where
    Ctrl+Backspace would only have eaten to the word's edge from there;
  * Alt+X S clears the literal the caret is in and leaves the single-quoted
    literal next to it alone;
  * Alt+X B clears the call's arguments while the lines above and below it keep
    their text;
  * an already-empty pair reports and changes nothing, which is the case that
    would otherwise mark the file modified for no edit.

Alt chords are sent as kitty CSI-u reports (code;modifier), the spelling jot asks
terminals for: Alt is bitmask 2, so the protocol's modifier field is 3. The word
delete is sent both ways -- the protocol report and the plain ESC + DEL byte
pair a terminal without the protocol sends -- because that chord is also bound
in the native handler as the fallback for a session with no plugin keymaps.

Usage: test/replace_inside_probe.py [path-to-jot]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 100, 26

# Three lines, one per chord: a word with words either side of it, two literals
# of different kinds on one line, and a call whose arguments the caret can sit
# in the middle of.
SOURCE = """alpha beta gamma
x = "hello" + 'w';
call(alpha, beta);
"""

EMPTY_CALL = """call();
"""

DOWN = b"\x1b[B"
RIGHT = b"\x1b[C"
# Alt+Backspace as a terminal without the kitty protocol spells it: ESC prefix,
# then DEL.
ALT_BACKSPACE_BYTES = b"\x1b\x7f"


def alt(letter: str) -> bytes:
    """The kitty report for Alt+<letter> (Alt = bitmask 2, so modifier = 3)."""
    return b"\x1b[" + str(ord(letter)).encode() + b";3u"


def alt_key(code: int) -> bytes:
    """The kitty report for the Alt-modified key with this code."""
    return b"\x1b[" + str(code).encode() + b";3u"


def caret_move(line: int, col: int) -> bytes:
    """Down to `line`, then right to `col`: the caret starts at 1:1."""
    return DOWN * line + RIGHT * col


def row_of(view: str, needle: str) -> str:
    """The screen row holding `needle`, or an empty string when it is gone."""
    for row in view.split("\n"):
        if needle in row:
            return row.rstrip()
    return ""


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    if not os.path.exists(binary):
        print(f"replace-inside probe: SKIP - no binary at {binary}")
        return 2

    work = "/tmp/jot_replace_inside_probe"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, "edits.cpp")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    empty_path = os.path.join(work, "empty.cpp")
    with open(empty_path, "w") as fh:
        fh.write(EMPTY_CALL)
    cfg = "/tmp/jot_replace_inside_probe_cfg"

    failures = 0

    def check(label: str, ok: bool) -> None:
        nonlocal failures
        print(f"replace-inside probe: {label} = {ok}")
        if not ok:
            failures += 1
            print(f"replace-inside probe: FAIL - {label}")

    # 1. The word the caret is on, and only it: beta goes, alpha and gamma stay,
    # with the two spaces the deletion leaves between them.
    for label, keys in (("CSI-u", alt_key(127)), ("ESC+DEL", ALT_BACKSPACE_BYTES)):
        screen = run_in_pty(binary, [path], caret_move(0, 7) + keys, settle=3.0, after=2.0,
                            cols=COLS, rows=ROWS, cfg=cfg, cwd=work)
        view = screen.text()
        row = row_of(view, "gamma")
        print(f"replace-inside probe: Alt+Backspace ({label}) left {row.strip()!r}")
        check(f"Alt+Backspace ({label}) took the caret's word",
              "alpha  gamma" in row and "alpha beta" not in row)

    # 2. Alt+X S: the literal the caret is in. The caret is nudged into "hello",
    # so the double-quoted literal empties and the 'w' beside it stays.
    screen = run_in_pty(binary, [path], caret_move(1, 8) + alt("x") + b"S", settle=3.0,
                        after=2.0, cols=COLS, rows=ROWS, cfg=cfg, cwd=work)
    view = screen.text()
    quoted = row_of(view, "x =")
    print(f"replace-inside probe: Alt+X S left {quoted.strip()!r}")
    check("Alt+X S cleared the caret's literal",
          "x = \"\" + 'w';" in quoted and "hello" not in view)

    # 3. Alt+X B: the call the caret is inside, with the file's other lines
    # untouched. The caret sits in the middle of the arguments.
    screen = run_in_pty(binary, [path], caret_move(2, 8) + alt("x") + b"B", settle=3.0,
                        after=2.0, cols=COLS, rows=ROWS, cfg=cfg, cwd=work)
    view = screen.text()
    call = row_of(view, "call(")
    print(f"replace-inside probe: Alt+X B left {call.strip()!r}")
    check("Alt+X B cleared the caret's brackets",
          "call();" in call and "alpha, beta" not in view
          and "alpha beta gamma" in view and "x = \"hello\"" in view)

    # 4. An empty pair: the caret is inside the parens and there is nothing
    # between them to clear, so the line stands and the tab stays clean -- the
    # guard exists so this key cannot dirty a file it did not edit. The dirty
    # marker is the check: a deletion that ate the newline would show it.
    screen = run_in_pty(binary, [empty_path], caret_move(0, 5) + alt("x") + b"B", settle=3.0,
                        after=2.0, cols=COLS, rows=ROWS, cfg=cfg, cwd=work)
    view = screen.text()
    empty_row = row_of(view, "call();")
    tab = view.split("\n")[0]
    print(f"replace-inside probe: Alt+X B on an empty pair left {empty_row.strip()!r}, "
          f"tab {tab.strip()!r}")
    check("an empty pair leaves the line and the file alone",
          "call();" in empty_row and "\u25cf" not in tab)

    if failures:
        print("replace-inside probe: FAIL")
        return 1
    print("replace-inside probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
