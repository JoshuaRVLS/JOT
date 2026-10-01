#!/usr/bin/env python3
"""Probe: Tab over a tag name in markup, and the tag list's rows.

The unit cases call the request and accept paths directly. What they cannot cover
is the wiring the real keystrokes go through: the letters typed after the `<` ask
for completions, the popup filters, and the bundled snippet keymap hands Tab to
whatever is on screen. This drives the binary with a pty and reads the buffer off
the screen.

The list's rows are whole opening elements (`<div>|</div>`), where a tag name is
the word right after its `<`. Two things follow: accepting a row has to cover the
`<` the author already typed rather than write a second one, and the list has to
stay away from the words that are not tag names -- the name in a closing tag, or
a word anywhere else on the line.

Usage: test/tag_list_probe.py [path-to-jot]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 110, 32


def write_file(root: str, name: str) -> str:
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, name)
    with open(path, "w"):
        pass
    return path


def run(binary: str, path: str, root: str, cfg: str, keys: bytes):
    return run_in_pty(binary, [path], keys, settle=3.0, after=3.0,
                      cols=COLS, rows=ROWS, cfg=cfg, cwd=root)


def buffer_line(view: str) -> str:
    """The first buffer row on the screen, with the line-number gutter cut off."""
    for line in view.splitlines():
        match = re.match(r"^\s*\d+\s(.*)$", line)
        if match:
            return match.group(1).rstrip()
    return ""


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    if not os.path.exists(binary):
        print(f"tag list probe: SKIP - no binary at {binary}")
        return 2

    failures: list[str] = []
    root = "/tmp/jot_tag_list_probe"

    # ── Scene 1: a tag name is being typed, accepted ────────────────────────
    # The row is written over the `<` and the word after it: the opening bracket
    # appeared twice while the site was the word alone.
    path = write_file(root, "partial.html")
    screen = run(binary, path, root, "/tmp/jot_tag_list_probe_cfg1", b"<d\t")
    line = buffer_line(screen.text())
    print(f"scene 1: <d + Tab -> {line!r}")
    if line != "<div></div>":
        failures.append(f"the tag name was not completed over its `<` (line {line!r})")

    # ── Scene 2: the same from the bare `<` ────────────────────────────────
    # Nothing is typed yet, so the first row of the list lands on the `<`.
    path = write_file(root, "bare.html")
    screen = run(binary, path, root, "/tmp/jot_tag_list_probe_cfg2", b"<\t")
    line = buffer_line(screen.text())
    print(f"scene 2: < + Tab -> {line!r}")
    if line.startswith("<<") or "</" not in line:
        failures.append(f"the bare `<` was not completed into one element (line {line!r})")

    # ── Scene 3: a `>` that did not close a tag ────────────────────────────
    # No tag name is being typed there, so the list stays away and the Tab falls
    # through to Emmet (which refuses a trailing operator) and then to
    # indentation: the text survives.
    path = write_file(root, "stray.html")
    screen = run(binary, path, root, "/tmp/jot_tag_list_probe_cfg3", b"div>\t")
    line = buffer_line(screen.text())
    print(f"scene 3: div> + Tab -> {line!r}")
    if line != "div>":
        failures.append(f"a `>` over text was treated as a tag name (line {line!r})")

    # ── Scene 4: the name in a closing tag ────────────────────────────────
    path = write_file(root, "closing.html")
    screen = run(binary, path, root, "/tmp/jot_tag_list_probe_cfg4", b"</d\t")
    line = buffer_line(screen.text())
    print(f"scene 4: </d + Tab -> {line!r}")
    if line != "</d":
        failures.append(f"an opening element was written into a closing tag (line {line!r})")

    if failures:
        for failure in failures:
            print(f"tag list probe: FAIL - {failure}")
        return 1
    print("tag list probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
