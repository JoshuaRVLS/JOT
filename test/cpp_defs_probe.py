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

    if failures:
        for failure in failures:
            print(f"cpp defs probe: FAIL - {failure}")
        return 1
    print("cpp defs probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
