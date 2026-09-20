#!/usr/bin/env python3
"""End-to-end check: where the inline completion preview is allowed to paint.

The ghost text previews the selected completion's remaining insert text at the
caret. It is an inline completion *of what is being typed*, so it only belongs
where the caret can own the rest of the row. Inside a call the editor
auto-closed (`printf(|)`) the preview was painted past the `)`, which splits the
preview from the text it completes -- and when the tail is a bracket or a `;`,
the preview reads as the editor having covered it.

Two scenes, one per side of the rule, driven against a real clangd:

  * `printf(p);` with the caret between the typed text and the `)`: the bracket
    owns the rest of the row, so nothing may be painted there. The completion
    popup is asserted to be up for the same typed prefix, so the case cannot
    pass by the preview never having existed.
  * `w.` at the end of a line, where `wi` completes to the member `width`: the
    caret owns the row's tail, so the preview belongs there. (Two characters:
    one after a `.` is below the automatic trigger's minimum prefix, so the
    member list is only asked for once the word is really under way.)

The preview is the row's italic run. The buffer painter draws it italic (the
inlay hints are the only other italic text), which is what tells it apart from
the completion popup's rows: those show the same insert text and share its
colour.

Usage: test/completion_ghost_probe.py [path-to-jot-binary]
Set JOT_PROBE_DUMP=1 to print the captured screen.
Exit codes: 0 pass, 1 fail, 2 binary or clangd missing.
"""
from __future__ import annotations

import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# The two sites sit a few rows into the pane: the signature and completion
# popups hang above the caret, and a caret near the pane's top gets a popup
# clamped down over its own row, which would hide the very cells under test.
SOURCE = """#include <cstdio>

struct Widget { int width; };

int main() {
  int a = 0;
  int b = 1;
  int c = 2;
  printf(p);
  Widget w;
  w.
}
"""

# The editor starts in insert mode, so a typed character is text: no `i` first.
END_OF_FILE = b"\x1bG"
UP = b"\x1b[A"
END = b"\x1b[F"
LEFT = b"\x1b[D"

# The completion popup's footer, `1/5  prin`: the selected row's index over the
# listed total, then the prefix it filtered on. Seeing the typed prefix there is
# what proves the popup found something to preview.
POPUP_FOOTER = re.compile(r"\d+/\d+")

CFG_PARENS = "/tmp/jot_completion_ghost_cfg_parens"
CFG_EOL = "/tmp/jot_completion_ghost_cfg_eol"


def seed_config(cfg: str) -> None:
    """Takes the end-of-line diagnostic message off the row under test.

    It is painted from the end of the line, which is exactly where a spilled
    preview lands: leaving it on lets it cover the very cells this probe reads,
    and the case would pass however the preview behaves. Only the message is
    turned off (`diagnostics_virtual_text`); the underline stays, which also
    keeps the native fallback popup suppressed -- that one takes over when
    `decorations_inline_diagnostics` is off and draws the same message.
    """
    os.makedirs(os.path.join(cfg, "configs"), exist_ok=True)
    # The settings file the editor reads and rewrites. A run that has already
    # saved one keeps it, so the key is written where it will be found rather
    # than into the fallback path next to it.
    with open(os.path.join(cfg, "configs", "settings.conf"), "w") as fh:
        fh.write("diagnostics_virtual_text = false\n")


def rows(screen):
    return ["".join(screen.cells[row]) for row in range(screen.rows)]


def find_row(screen, needle: str) -> int:
    for row, text in enumerate(rows(screen)):
        if needle in text:
            return row
    return -1


def ghost_runs(screen):
    """The italic runs on screen: [(row, start, text), ...], empties dropped."""
    runs = []
    for row in range(screen.rows):
        for start, _end, text in screen.italic_runs(row):
            if text:
                runs.append((row, start, text))
    return runs


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    if not os.path.exists(binary):
        print(f"completion ghost probe: SKIP - no binary at {binary}")
        return 2
    if shutil.which("clangd") is None:
        print("completion ghost probe: SKIP - clangd not installed")
        return 2

    work = "/tmp/jot_completion_ghost"
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, "probe.cpp")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    for cfg in (CFG_PARENS, CFG_EOL):
        seed_config(cfg)

    dump = bool(os.environ.get("JOT_PROBE_DUMP"))
    failures: list[str] = []

    # Scene 1: inside the auto-closed call. Alt+Shift+G ends at the last line,
    # three Ups reach `printf(p);`, End puts the caret after the `;`, two Lefts
    # step back inside the parens, then `rin` is typed after the `p`.
    screen = run_in_pty(binary,
                        [path],
                        END_OF_FILE + UP + UP + UP + END + LEFT + LEFT + b"rin",
                        settle=6.0,
                        after=7.0,
                        cols=120,
                        rows=34,
                        cfg=CFG_PARENS,
                        cwd="/tmp")
    if dump:
        print(screen.text())
        print("-" * 70)

    row = find_row(screen, "printf(p")
    if row < 0:
        failures.append("the typed characters never reached the call")
    else:
        line = rows(screen)[row].rstrip()
        if not line.endswith("printf(prin);"):
            failures.append(
                f"the typed characters and the bracket are not the end of the row: {line.strip()!r}")
        # Non-vacuity: the popup lists something for this prefix, so a preview
        # exists to be suppressed. Without this the case would pass even if the
        # completion had stopped working entirely.
        listed = any(POPUP_FOOTER.search(text) and "prin" in text
                     for other, text in enumerate(rows(screen)) if other != row)
        if not listed:
            failures.append("the popup never listed anything for the typed prefix")
        # The bracket owns everything past the caret, so nothing may preview
        # there. Italic text *inside* the call would be the inlay hint clangd
        # sends for the argument, which is not the preview.
        close = line.rfind(")")
        spilled = [(start, text) for start, _end, text in screen.italic_runs(row)
                   if text and start > close]
        if spilled:
            failures.append(f"a preview was painted past the bracket: {spilled!r}")

    # Scene 2: the same completion kind at the end of a row, where the preview
    # has the rest of it to itself. One Up from the last line reaches `w.`, End
    # puts the caret after it, and `wi` asks for the member `width`, previewed
    # as `dth`.
    screen = run_in_pty(binary,
                        [path],
                        END_OF_FILE + UP + END + b"wi",
                        settle=6.0,
                        after=7.0,
                        cols=120,
                        rows=34,
                        cfg=CFG_EOL,
                        cwd="/tmp")
    if dump:
        print(screen.text())
        print("-" * 70)

    runs = ghost_runs(screen)
    if not runs:
        failures.append("no preview where the caret owns the row's tail")
    else:
        row, _start, text = runs[0]
        # The preview reads as the finished word, one caret cell after the typed
        # text (`w.wi` + `dth` = `w.width`).
        line = rows(screen)[row].rstrip()
        if not re.search(r"w\.wi ?dth$", line):
            failures.append(
                f"the preview was painted on a row other than the text it completes: {line.strip()!r}")
        if text != "dth":
            failures.append(f"the preview is not the completion's remainder: {text!r}")

    if failures:
        for failure in failures:
            print(f"completion ghost probe: FAIL - {failure}")
        return 1
    print("completion ghost probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
