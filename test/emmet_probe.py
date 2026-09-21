#!/usr/bin/env python3
"""Emmet probe: type an abbreviation, press Tab, read the buffer off the screen.

The unit tests call the expander and the editor entry point directly. What they
cannot cover is the wiring that makes the gesture work: Tab is bound in the
bundled snippet keymap (runtime/lua/features/snippet/keymaps.lua), which asks
`jot.emmet.expand()` after the user's own snippet triggers and before the
editor's own indentation. That keymap only runs in the real input path, so this
drives the binary with a pty and types.

Scenes: a markup abbreviation that becomes an element (with the caret left inside
it), a chain that becomes a tree on several lines, a CSS shorthand that becomes a
declaration, and the refusals -- a token that does not parse, and a file that is
neither markup nor a style sheet -- where Tab has to fall through to indentation
and leave the text as typed.

Usage: test/emmet_probe.py [path-to-jot]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
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


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    if not os.path.exists(binary):
        print(f"emmet probe: SKIP - no binary at {binary}")
        return 2

    failures: list[str] = []
    root = "/tmp/jot_emmet_probe"

    # ── Scene 1: markup, one element ─────────────────────────────────────────
    path = write_file(root, "page.html")
    screen = run(binary, path, root, "/tmp/jot_emmet_probe_cfg1", b"div.card\t")
    view = screen.text()
    print("scene 1: div.card + Tab")
    if '<div class="card"></div>' not in view:
        failures.append("the markup abbreviation did not expand into an element")
    # The caret is the `$0` the expansion carried, so the next thing typed lands
    # inside the element rather than after its closing tag.
    screen = run(binary, path, root, "/tmp/jot_emmet_probe_cfg1b", b"div.card\tx")
    if '<div class="card">x</div>' not in screen.text():
        failures.append("the caret did not land inside the expanded element")

    # ── Scene 2: markup, a tree on several lines ─────────────────────────────
    path = write_file(root, "list.html")
    screen = run(binary, path, root, "/tmp/jot_emmet_probe_cfg2", b"ul>li*2\t")
    view = screen.text()
    print("scene 2: ul>li*2 + Tab")
    if view.count("<li></li>") != 2:
        failures.append(f"expected two list items, saw {view.count('<li></li>')}")
    if "<ul>" not in view or "</ul>" not in view:
        failures.append("the expansion is not wrapped in its own element")

    # ── Scene 3: a bare element takes its implied attributes ─────────────────
    path = write_file(root, "link.html")
    screen = run(binary, path, root, "/tmp/jot_emmet_probe_cfg3", b"a\t")
    view = screen.text()
    print("scene 3: a + Tab")
    if '<a href=""></a>' not in view:
        failures.append("an anchor abbreviation did not get its implied href")

    # ── Scene 4: CSS ────────────────────────────────────────────────────────
    path = write_file(root, "style.css")
    screen = run(binary, path, root, "/tmp/jot_emmet_probe_cfg4", b"  m10-20\t")
    view = screen.text()
    print("scene 4: m10-20 + Tab in a stylesheet")
    if "margin: 10px 20px;" not in view:
        failures.append("the CSS shorthand did not expand into a declaration")

    # ── Scene 5: a token that does not parse keeps the Tab for indentation ───
    path = write_file(root, "refuse.html")
    screen = run(binary, path, root, "/tmp/jot_emmet_probe_cfg5", b"div>\t")
    view = screen.text()
    print("scene 5: div> + Tab must not expand")
    if "<div" in view:
        failures.append("an unparseable abbreviation was expanded anyway")
    if "div>" not in view:
        failures.append("the typed text did not survive the refused Tab")

    # ── Scene 6: a file that is neither markup nor a stylesheet ──────────────
    path = write_file(root, "notes.txt")
    screen = run(binary, path, root, "/tmp/jot_emmet_probe_cfg6", b"div.card\t")
    view = screen.text()
    print("scene 6: div.card + Tab in a text file must not expand")
    if "<div" in view:
        failures.append("a plain text file was expanded as markup")
    if "div.card" not in view:
        failures.append("the typed text did not survive in the text file")

    if failures:
        for failure in failures:
            print(f"emmet probe: FAIL - {failure}")
        return 1
    print("emmet probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
