#!/usr/bin/env python3
"""End-to-end check: when the inline completion preview is allowed to paint.

The ghost text previews the selected completion's remaining insert text at the
caret. It is a preview of *the word being typed*, so it only belongs where the
caret owns the rest of the row, on a row whose word really continues into the
selected item, and after the typing has paused.

Four scenes, one per rule, driven against a real clangd:

  * `printf(p);` with the caret between the typed text and the `)`: the bracket
    owns the rest of the row, so nothing may be painted there. The completion
    popup is asserted to be up for the same typed prefix, so the case cannot
    pass by the preview never having existed.
  * `w.` at the end of a line, where `wi` completes to the member `width`: the
    caret owns the row's tail and the typing has stopped, so the preview belongs
    there -- and it can only appear if the frame that ends the typing pause was
    asked for, since nothing is typed after the two characters.
  * `wih` at the same site, one character of typing after the popup has settled
    on `wi`: the popup goes away as soon as the word stops leading into what the
    server answered (the `width` row is still in hand, and no response has
    arrived for `wih`), and the preview goes with it -- the probe reads the whole
    run's timeline of italic runs, so a preview that only survived a frame or two
    would still be caught.
  * `wi` with `lsp_completion_ghost_delay_ms` set to five seconds: the popup
    lists `width` and the row is left alone, which is the pause the preview
    waits for.

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

# The sites sit a few rows into the pane: the signature and completion popups
# hang above the caret, and a caret near the pane's top gets a popup clamped
# down over its own row, which would hide the very cells under test.
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
CFG_TYPED = "/tmp/jot_completion_ghost_cfg_typed"
CFG_SLOW = "/tmp/jot_completion_ghost_cfg_slow"

# The wait the preview does (lsp_completion_ghost_delay_ms), and how long this
# probe drains after the keystrokes. The slow scene's drain has to end well
# inside the wait for its case to mean anything.
PAUSE_MS = 5000
PAUSE_DRAIN = 2.0


def seed_config(cfg: str, extra: dict | None = None) -> None:
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
        for key, value in (extra or {}).items():
            fh.write(f"{key} = {value}\n")


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


def popup_for(screen, prefix: str):
    """The popup's footer rows on screen for `prefix`: [(row, text), ...].

    The footer reads `1/5  pri` -- the selected index over the listed total,
    then the prefix being filtered on -- so seeing the typed word there is what
    says the popup is (still) up for it.
    """
    return [(row, text) for row, text in enumerate(rows(screen))
            if POPUP_FOOTER.search(text) and prefix in text]


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
    seed_config(CFG_PARENS)
    seed_config(CFG_EOL)
    seed_config(CFG_TYPED)
    seed_config(CFG_SLOW, {"lsp_completion_ghost_delay_ms": str(PAUSE_MS)})

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
        if not popup_for(screen, "prin"):
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
    # puts the caret after it, and `wi` asks for the member `width`, previewed as
    # `dth`. Nothing is typed after those two characters, so the preview landing
    # at all means the frame that ends the typing pause was asked for.
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

    # Scene 3: typing on past what the server answered. The phases are what make
    # this the real case: `wi` is left to settle so its response (the member
    # `width`) is in hand *and* the row is listed, then `h` takes the word to
    # `wih` -- which `width` does not lead into, while still being a subsequence
    # of it (h is in the middle), the shape that a fuzzy pass keeps and that used
    # to preview the whole identifier for the frame or two before the response for
    # the new word arrives. So the row has to go on the keystroke, and the
    # timeline of inset screens is what can see a preview that only lasted a
    # frame: `italic_log` collects every italic run the screen carried.
    screen = run_in_pty(binary,
                        [path],
                        END_OF_FILE + UP + END + b"wi",
                        settle=6.0,
                        after=1.5,
                        cols=120,
                        rows=34,
                        cfg=CFG_TYPED,
                        cwd="/tmp",
                        phases=[(1.5, b"h")])
    if dump:
        print(screen.text())
        print("-" * 70)

    if find_row(screen, "w.wih") < 0:
        failures.append("the typed characters never reached the member site")
    stale = popup_for(screen, "wih")
    if stale:
        failures.append(f"the popup outlived the word it answered: {stale!r}")
    if ghost_runs(screen):
        failures.append(f"a preview was painted for a word nothing completes: "
                        f"{ghost_runs(screen)!r}")
    if dump:
        print("italic log:", screen.italic_log)
    # The whole identifier is never the remainder of a typed prefix (with nothing
    # typed there is no preview at all), so seeing it as an italic run anywhere in
    # the timeline is the flash this rule removes.
    flashed = [(row, text) for row, text in screen.italic_log if text == "width"]
    if flashed:
        failures.append(f"the whole identifier was previewed while typing: {flashed!r}")

    # Scene 4: the pause itself. The same `wi` as scene 2, with the wait long
    # enough to outlast this probe's drain: the popup answers (the member is
    # listed) and the row is left alone, because the typing has not paused yet.
    screen = run_in_pty(binary,
                        [path],
                        END_OF_FILE + UP + END + b"wi",
                        settle=6.0,
                        after=PAUSE_DRAIN,
                        cols=120,
                        rows=34,
                        cfg=CFG_SLOW,
                        cwd="/tmp")
    if dump:
        print(screen.text())
        print("-" * 70)

    row = find_row(screen, "w.wi")
    if row < 0:
        failures.append("the typed characters never reached the member site")
    else:
        line = rows(screen)[row].rstrip()
        # The preview of `width` is its remainder, so its absence is what the
        # wait means -- and the popup below is what says there was one to draw.
        if "dth" in line:
            failures.append(f"the preview was painted before the typing paused: "
                            f"{line.strip()!r}")
        if not popup_for(screen, "wi"):
            failures.append("the popup never listed the member, so the wait was vacuous")
    if ghost_runs(screen):
        failures.append(f"a preview was painted before the typing paused: "
                        f"{ghost_runs(screen)!r}")

    if failures:
        for failure in failures:
            print(f"completion ghost probe: FAIL - {failure}")
        return 1
    print("completion ghost probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
