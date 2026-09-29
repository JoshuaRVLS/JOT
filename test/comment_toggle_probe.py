#!/usr/bin/env python3
"""Probe: Ctrl+/ comments the line under the cursor even when it is blank.

The toggle stamps every line of its range, but a range that is blank is
skipped so a selection never grows marker-only rows. Without a selection there
is no range: the cursor line is the whole target, and skipping it leaves Ctrl+/
dead on an empty line - the one place a comment is started from scratch. The
rule has to give way there while still holding inside a real range.

Each scene opens a file, sends the keys, saves with Ctrl+S and reads the file
back off disk, so what is checked is the buffer the toggle actually produced.
The marker is per extension (// for C++, # for Python), which also proves the
empty-line stamp goes through the same style lookup as any other line.

Usage: test/comment_toggle_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

CTRL_SLASH = b"\x1f"
CTRL_SAVE = b"\x13"
CTRL_A = b"\x01"
DOWN = b"\x1b[B"

# name, filename, body, keys before Ctrl+/, expected file contents
SCENES = [
    ("blank line gains //", "a.cpp", "int a = 1;\n\nint b = 2;\n", DOWN,
     "int a = 1;\n//\nint b = 2;\n"),
    ("blank line toggles back", "a.cpp", "int a = 1;\n\nint b = 2;\n", DOWN + CTRL_SLASH,
     "int a = 1;\n\nint b = 2;\n"),
    ("text line still comments", "a.cpp", "int a = 1;\nint b = 2;\n", b"",
     "//int a = 1;\nint b = 2;\n"),
    # A real range keeps skipping its blank line: only the text rows grow markers.
    ("range skips its blank", "a.cpp", "int a = 1;\n\nint b = 2;\n", CTRL_A,
     "//int a = 1;\n\n//int b = 2;\n"),
    ("python blank gains #", "b.py", "a = 1\n\nb = 2\n", DOWN, "a = 1\n#\nb = 2\n"),
]


def run_scene(binary: str, name: str, filename: str, body: str, keys: bytes, dump: bool) -> str:
    tmp = "/tmp/jot_comment_toggle_probe_" + name.replace(" ", "_")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    path = os.path.join(tmp, filename)
    with open(path, "w") as fh:
        fh.write(body)
    screen = run_in_pty(binary, [path], keys + CTRL_SLASH + CTRL_SAVE,
                        settle=2.0, after=1.0, cfg=tmp + "_cfg", cwd=tmp,
                        cols=100, rows=12)
    if dump:
        print("=== %s ===" % name)
        print(screen.text())
        print("-" * 70)
    with open(path) as fh:
        return fh.read()


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("comment toggle probe: SKIP - no binary at %s" % binary)
        return 2

    failures = []
    for name, filename, body, keys, expected in SCENES:
        got = run_scene(binary, name, filename, body, keys, dump)
        ok = got == expected
        print("%-24s %s" % (name, "ok" if ok else "FAIL"))
        if not ok:
            failures.append("%s: got %r, want %r" % (name, got, expected))

    if failures:
        print("comment toggle probe: FAIL")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("comment toggle probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
