#!/usr/bin/env python3
"""Probe: what Enter indents to in a real C++ buffer.

test_cpp_indent.cpp pins the rules; this drives the real binary, so the wiring
around them is covered too -- that the buffer's language is what selects the
C-family rules, and that the keys reach the newline path. The indentation is
read off disk after a save rather than off the screen, because a line's leading
spaces are exactly what a screen reconstruction drops.

Each case opens a small .cpp, moves the caret to the end of a line, presses
Enter, types a marker, and saves. What comes back is the file's text: the new
line's leading spaces are the indent under test.

Usage: test/cpp_indent_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 100, 24
END = b"\x1b[F"
DOWN = b"\x1b[B"
ENTER = b"\r"
SAVE = b"\x13"  # Ctrl+S


def write_file(tmp: str, text: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    path = os.path.join(tmp, "a.cpp")
    with open(path, "w") as fh:
        fh.write(text)
    return path


def type_and_save(binary: str, tmp: str, text: str, keys: bytes, dump: bool) -> str:
    path = write_file(tmp, text)
    screen = run_in_pty(binary, [path], keys, settle=2.5, after=0.8, cfg=tmp + "_cfg",
                        cwd=tmp, cols=COLS, rows=ROWS, phases=[(0.8, SAVE)])
    if dump:
        print(screen.text())
        print("-" * 70)
    with open(path) as fh:
        return fh.read()


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("cpp indent probe: SKIP - no binary at %s" % binary)
        return 2

    cases = [
        # label, file, keys after opening at the top, what the file holds after
        # the save
        ("continuation aligns to the argument", "  res = call(arg,\n",
         END + ENTER + b"X", "  res = call(arg,\n             X\n"),
        ("a control statement keeps its brace",
         "void f()\n{\n  if (x)\n", DOWN + DOWN + END + ENTER + b"X",
         "void f()\n{\n  if (x)\n  X\n"),
        ("an argument list keeps its own column",
         "  res = call(\n    arg,\n", DOWN + DOWN + END + ENTER + b"X",
         "  res = call(\n    arg,\n    X\n"),
        ("an access specifier pulls back out of the class",
         "class Widget\n{\n  int x;\n", DOWN + DOWN + END + ENTER + b"public:",
         "class Widget\n{\n  int x;\npublic:\n"),
        ("a case label pulls back to the switch",
         "switch (x)\n{\n  foo();\n", DOWN + DOWN + END + ENTER + b"case 1:",
         "switch (x)\n{\n  foo();\ncase 1:\n"),
        ("a preprocessor line goes to column 0",
         "int main()\n{\n", DOWN + END + ENTER + b"#",
         "int main()\n{\n#\n"),
    ]

    failures = []
    for index, (label, text, keys, want) in enumerate(cases):
        got = type_and_save(binary, "/tmp/jot_cpp_indent_probe_%d" % index, text, keys, dump)
        ok = got == want
        print("%-40s %s" % (label, "ok" if ok else "FAILED"))
        if not ok:
            failures.append("%s: file holds %r, wanted %r" % (label, got, want))

    if failures:
        print("cpp indent probe: FAIL")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("cpp indent probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
