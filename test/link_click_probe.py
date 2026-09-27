#!/usr/bin/env python3
"""Probe: Ctrl+click on a link opens it, and on anything else still goes to the
symbol.

The click path is the real binary's: the editor looks the platform opener up on
PATH (`xdg-open` here) and runs it. This probe puts a stub `xdg-open` first on
PATH that appends the URL it was handed to a file, so what the editor opened is
read back from the file the opener wrote -- the production path, not a seam.
The same file's ordinary words are clicked in the same run to pin that a name is
not a link: no opener runs, and the caret moves to the element, which is what
the definition path does.

The Ctrl+hover underline is read from the rendered grid (the pty probe harness
tracks underline cells), because the affordance has to cover the URL the click
will open, not the first name inside it.

Usage: test/link_click_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

ROOT = "/tmp/jot_link_click_probe"
BIN = ROOT + "_bin"
FILE = "links.txt"

# One URL per line, each with the text around it that decides where the link
# ends. The wiki line and the markdown line are the two tails: a URL that opens
# a paren of its own keeps it, a markdown link's closing `)` does not belong to
# it. The last two lines are the counter-cases: a sentence's dot and a plain
# word, neither of which may open a browser.
LINES = [
    "// see https://example.com/docs for more",
    "// wiki https://en.wikipedia.org/wiki/Jot_(editor) page",
    "// md [docs](https://example.com/guide) here",
    "// mail mailto:dev@example.com now",
    "// file file:///tmp/jot_link_click_probe/notes.md end",
    "// tail https://example.com/page.",
    "// plain alpha beta gamma",
]

# label: (needle, offset into it, what the opener must have been handed)
# `None` means no opener may run at all.
CLICKS = {
    "open url":    ("https://example.com/docs", 12, "https://example.com/docs"),
    "wiki paren":  ("https://en.wikipedia.org/wiki/Jot_(editor)", 30,
                    "https://en.wikipedia.org/wiki/Jot_(editor)"),
    "md link":     ("https://example.com/guide", 5, "https://example.com/guide"),
    "mailto":      ("mailto:dev@example.com", 10, "mailto:dev@example.com"),
    "file link":   ("file:///tmp/jot_link_click_probe/notes.md", 10,
                    "file:///tmp/jot_link_click_probe/notes.md"),
    # Offset 24 is the dot itself: the sentence's, not the link's.
    "sentence dot": ("https://example.com/page.", 24, None),
    "plain word":  ("alpha", 1, None),
}

# The caret the status line must report after each of these clicks, 1-based, the
# way the status line prints it: unchanged for a link (the click was about the
# link), on the element for a word (the definition path answered).
CARETS = {
    "open url":   (1, 1),
    "plain word": (7, 10),
}


def write_workspace() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.rmtree(BIN, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(BIN, exist_ok=True)
    with open(os.path.join(ROOT, FILE), "w") as fh:
        fh.write("\n".join(LINES) + "\n")
    # The stub the editor will find first on PATH. It records what it was
    # handed and exits; the editor backgrounds it, so the record may land a
    # moment after the click.
    stub = os.path.join(BIN, "xdg-open")
    with open(stub, "w") as fh:
        fh.write('#!/bin/sh\nprintf \'%s\\n\' "$1" >> "$JOT_LINK_RECORD"\n')
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def ctrl_click(col: int, row: int) -> bytes:
    """SGR ctrl+left press and release on a 0-based screen cell (16 is Ctrl)."""
    press = b"\x1b[<16;%d;%dM" % (col + 1, row + 1)
    release = b"\x1b[<16;%d;%dm" % (col + 1, row + 1)
    return press + release


def ctrl_motion(col: int, row: int) -> bytes:
    """SGR ctrl+motion (32 is motion); sent twice so the second lands on the
    frame the first caused."""
    return b"\x1b[<48;%d;%dM" % (col + 1, row + 1) * 2


def cell_of(screen, needle: str, offset: int = 0):
    """The 0-based (col, row) of `needle` on screen, plus a character offset."""
    for row, line in enumerate(screen.text().split("\n")):
        idx = line.find(needle)
        if idx >= 0:
            return idx + offset, row
    return None


def caret(screen):
    """The (line, column) the status line reports, 1-based, or None.

    The status row is the one naming the file and carrying the position; the
    tab strip names the file too but has no position, and with no language
    server attached there is no `@ server` chip to anchor on.
    """
    for line in screen.text().split("\n"):
        if FILE not in line:
            continue
        match = re.search(r"\b(\d+):(\d+)\b", line)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def run(binary: str, keys: bytes, cfg: str, record: str, settle: float = 2.0,
        after: float = 1.5):
    env = {
        "PATH": BIN + ":/usr/bin:/bin",
        "JOT_LINK_RECORD": record,
    }
    return run_in_pty(binary, [os.path.join(ROOT, FILE)], keys, settle=settle,
                      after=after, cols=100, rows=30, cfg=cfg, cwd=ROOT, env=env)


def recorded(record: str, want: int, timeout: float = 1.5):
    """The URLs the stub recorded, once there are `want` of them."""
    deadline = time.time() + timeout
    lines = []
    while time.time() < deadline:
        lines = []
        if os.path.exists(record):
            with open(record) as fh:
                lines = [line.rstrip("\n") for line in fh if line.strip()]
        if len(lines) >= want:
            return lines
        time.sleep(0.05)
    return lines


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"link click probe: SKIP - no binary at {binary}")
        return 2

    write_workspace()

    # One settled frame maps the rendered grid: the gutter decides where the
    # code starts, so the cells cannot be assumed.
    base = run(binary, b"", "/tmp/jot_link_click_probe_cfg_base",
               "/tmp/jot_link_click_probe_urls_base")
    if dump:
        print(base.text())
        print("-" * 70)

    failures = []
    screen_text = base.text()
    for index, (label, (needle, offset, want)) in enumerate(CLICKS.items()):
        if needle not in screen_text:
            failures.append(f"{label}: {needle!r} is not visible on screen")
            continue
        cell = cell_of(base, needle, offset)
        col, row = cell
        record = f"/tmp/jot_link_click_probe_urls_{index}"
        if os.path.exists(record):
            os.remove(record)
        screen = run(binary, ctrl_click(col, row),
                     f"/tmp/jot_link_click_probe_cfg_{index}", record)
        if dump:
            print(screen.text())
            print("-" * 70)
        got = recorded(record, 1 if want else 0)
        caret_after = caret(screen)
        print(f"ctrl+click {label:<13} {needle}[{offset}] at ({col},{row}) -> "
              f"opened {got if got else '(nothing)'} (want "
              f"{[want] if want else '[]'}), caret {caret_after}")
        if want is None:
            if got:
                failures.append(f"{label}: opened {got}, but it is not a link")
        elif got[:1] != [want]:
            failures.append(f"{label}: expected {want!r}, the opener got {got}")
        if label in CARETS and caret_after != CARETS[label]:
            failures.append(f"{label}: expected the caret on "
                            f"{CARETS[label][0]}:{CARETS[label][1]}, got "
                            f"{caret_after if caret_after else '(none)'}")

    # The affordance: Ctrl+hover over the URL must underline exactly it, from
    # its first cell to its last, because that is the click's target.
    cell = cell_of(base, "https://example.com/docs", 12)
    if cell is None:
        failures.append("underline: the docs URL is not visible on screen")
    else:
        col, row = cell
        record = "/tmp/jot_link_click_probe_urls_hover"
        if os.path.exists(record):
            os.remove(record)
        screen = run(binary, ctrl_motion(col, row),
                     "/tmp/jot_link_click_probe_cfg_hover", record)
        if dump:
            print(screen.text())
            print("-" * 70)
        runs = [text for _start, _end, text in screen.underline_runs(row)]
        print(f"ctrl+hover docs URL at ({col},{row}) -> underlines {runs} "
              f"(want ['https://example.com/docs'])")
        if runs != ["https://example.com/docs"]:
            failures.append(f"underline: expected exactly the URL, got {runs}")
        if recorded(record, 1):
            failures.append("underline: a hover must not open anything")

    if failures:
        for failure in failures:
            print(f"link click probe: FAIL - {failure}")
        return 1
    print("link click probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
