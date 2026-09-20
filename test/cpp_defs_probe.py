#!/usr/bin/env python3
"""Probe: the C++ definition checks, on the rendered screen.

A unit test can pin the parser and the pairing rules, but only a real session
proves the whole path: opening a workspace kicks a scan off on the worker thread,
what it finds is merged into the diagnostics every surface reads, and those rows
reach the Problems panel and the status line with no extra nudge.

This drives the real binary with a three-file workspace that holds exactly one
declaration with no body (`missing_body`) and exactly one signature with two
bodies (`twice`), plus one implemented declaration that must stay quiet:

  * opening the workspace runs the scan (the request is made before the worker
    queue exists, so a regression there is invisible until a save), and the rows
    appear when the Problems panel is shown (Ctrl+Shift+M), naming both files
    and both problems,
  * `implemented` -- declared in the header and defined in the source -- has no
    row at all: the false positive that would make the checker unusable,
  * `:cppcheck` re-runs the scan on demand and summarizes it in the status line.

Usage: test/cpp_defs_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# Ctrl+Shift+M shows the bottom panel on its Problems view, and Ctrl+Shift+P is
# the command palette (both as kitty CSI-u reports; 6 is Ctrl+Shift).
PROBLEMS = b"\x1b[109;6u"
PALETTE = b"\x1b[112;6u"
ENTER = b"\r"
# Backspace: the palette resumes its last query, so a second command has to
# clear the box before typing.
CLEAR_BOX = b"\x7f" * 32

HEADER = """#pragma once

int missing_body();
int twice();
int implemented();
"""

SOURCE = """#include "shapes.h"

int implemented() { return 1; }

int twice() { return 2; }
"""

OTHER = """int twice() { return 3; }
"""


def write_workspace(root: str) -> None:
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "shapes.h"), "w") as fh:
        fh.write(HEADER)
    with open(os.path.join(root, "shapes.cpp"), "w") as fh:
        fh.write(SOURCE)
    with open(os.path.join(root, "more.cpp"), "w") as fh:
        fh.write(OTHER)


def marked_row(screen) -> str:
    """The Problems finding row the list marks with its accent sliver.

    The tab strip uses the same sliver for the active tab, so a finding row is
    the one that carries a finding's text.
    """
    for line in screen.text().split("\n"):
        if "\u258c" in line and ("No definition found" in line
                                 or "defined more than once" in line):
            return line.rstrip()
    return ""


def header_row(screen) -> str:
    """The Problems list's header line (the one carrying the tally)."""
    for line in screen.text().split("\n"):
        if "findings in" in line:
            return line.rstrip()
    return ""


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"cpp defs probe: SKIP - no binary at {binary}")
        return 2

    root = "/tmp/jot_cpp_defs_probe"
    write_workspace(root)
    header = os.path.join(root, "shapes.h")

    failures = []

    # Scene 1: the scan the workspace open kicks off, read through the Problems
    # panel. The workspace is the launch argument (the path a project is opened
    # by) so this is exactly the startup path, deferral included.
    screen = run_in_pty(binary, [root], PROBLEMS, settle=4.5, after=4.0,
                        cols=140, rows=34, cfg="/tmp/jot_cpp_defs_probe_cfg",
                        cwd="/tmp", phases=[(0.5, b"")])
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()

    if 'No definition found for "missing_body()"' not in text:
        failures.append("the missing declaration's row is not in the Problems panel")
    if "shapes.h" not in text:
        failures.append("the missing declaration does not name its header")
    if '"twice()" is defined more than once' not in text:
        failures.append("the repeated definition's row is not in the Problems panel")
    if "more.cpp" not in text:
        failures.append("the repeated definition does not name the file that holds it")
    if 'No definition found for "implemented()"' in text:
        failures.append("a declaration with a body was reported as missing")
    if '"implemented()" is defined more than once' in text:
        failures.append("a single definition was reported as repeated")

    # The list leads with a per-file tally: totals, then severities, then the
    # files that hold them -- this is what says whether the scan found one thing
    # in one file or forty across the tree.
    header = header_row(screen)
    if not header:
        failures.append("the Problems list has no per-file header")
    else:
        if "2 findings in 2 files" not in header:
            failures.append(f"the header miscounts the findings: {header.strip()!r}")
        if "1 error, 1 warning" not in header:
            failures.append(f"the header does not tally the severities: {header.strip()!r}")
        if "shapes.h 1" not in header or "shapes.cpp 1" not in header:
            failures.append(f"the header does not tally the files: {header.strip()!r}")

    # Scene 2: `:cppcheck` -- the on-demand path, which also announces what it
    # found (a toast: the statusline message channel is silent under the Lua UI
    # kit) and focuses the Problems list. The scan lands asynchronously, so the
    # keys are staged and the read happens while the toast is still up.
    screen = run_in_pty(binary, [root], PALETTE, settle=5.0, after=0.5, cols=150,
                        rows=34, cfg="/tmp/jot_cpp_defs_probe_cfg_cmd", cwd="/tmp",
                        phases=[(0.6, b"cppcheck"), (0.6, ENTER), (0.6, b"")])
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()

    if "C++ definitions:" not in text:
        failures.append(":cppcheck announced nothing the user can see")
    if "1 missing, 1 repeated across 3" not in text:
        failures.append("the announced summary does not carry both counts and the file tally")
    if 'No definition found for "missing_body()"' not in text:
        failures.append(":cppcheck did not focus the Problems list on its findings")
    # The command is also the way *to* a finding: with nothing open, the jump
    # lands on the first one the walk would visit (the repeated body in
    # shapes.cpp), the status line says where it went, and the list's marker
    # follows so opening the panel lands on the same row.
    status = next((line for line in text.split("\n") if "cpp @" in line), "")
    if "shapes.cpp" not in status or "5:5" not in status:
        failures.append(f":cppcheck did not land on the first finding: {status.strip()!r}")
    marked = marked_row(screen)
    if "defined more than once" not in marked:
        failures.append(f"the list did not mark the finding that was jumped to: {marked.strip()!r}")

    # Scene 3: the walk itself -- the startup scan has landed by now, so two
    # `next`s (a separate command each, and the palette resumes its last query,
    # so the box is cleared first) step from the placeholder to the first finding
    # and then to the header's declaration. The status line is the editor's own;
    # the panel rows name the same files, so only it can say where the caret
    # went.
    screen = run_in_pty(binary, [root], PALETTE, settle=5.0, after=0.5, cols=150,
                        rows=34, cfg="/tmp/jot_cpp_defs_probe_cfg_walk", cwd="/tmp",
                        phases=[(0.6, b"cppcheck next"), (0.6, ENTER),
                                (0.6, PALETTE), (0.6, CLEAR_BOX),
                                (0.6, b"cppcheck next"), (0.6, ENTER), (0.6, b"")])
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()

    status = next((line for line in text.split("\n") if "cpp @" in line), "")
    if "shapes.h" not in status or "3:5" not in status:
        failures.append(f":cppcheck next did not walk to the header's finding: {status.strip()!r}")

    if failures:
        for failure in failures:
            print(f"cpp defs probe: FAIL - {failure}")
        return 1
    print("cpp defs probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
