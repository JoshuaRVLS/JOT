#!/usr/bin/env python3
"""Probe: a language whose parser is installed actually becomes active.

The bundled highlight query is compiled as one unit, so a single node name the
grammar does not define fails the whole thing and the editor drops that language
to the regex fallback -- quietly. :tsstatus still lists the parser under
"Installed" ("parser loaded"), so the language looks healthy while nothing in
the buffer is highlighted. That is exactly how `(preproc_endif)` in the bundled
`c` query disabled tree-sitter for every .c and .h file: tree-sitter-c has no
such node, `#endif` is part of its enclosing preproc_if.

This drives the real binary with a small C file, opens :tsstatus, and reads the
Active section: the `c` row has to say it is active in the open buffer. A parser
that is not on the machine skips (exit 2), the same way the harness treats a
missing dependency.

Usage: test/treesitter_c_query_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary or no C parser).
"""
from __future__ import annotations

import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

PALETTE = b"\x1b[112;6u"  # Ctrl+Shift+P
ROOT = "/tmp/jot_treesitter_c_probe"
CFG = "/tmp/jot_treesitter_c_probe_cfg"

SOURCE = """#include <stdio.h>

int main(void) {
  int apples = 1;
  return apples;
}
"""


def c_parser_paths() -> list[str]:
    home = os.environ.get("HOME", "")
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
    return [
        os.path.join(data, "jot", "treesitter", "parsers", "libtree-sitter-c.so"),
        os.path.join(cache, "jot", "treesitter", "parsers", "libtree-sitter-c.so"),
        "/usr/lib/libtree-sitter-c.so",
        "/usr/local/lib/libtree-sitter-c.so",
    ]


def write_workspace() -> str:
    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.rmtree(CFG, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    path = os.path.join(ROOT, "main.c")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    return path


def active_rows(view: str) -> list[str]:
    """The language column of every row that says `active in open buffer`."""
    rows = []
    for line in view.splitlines():
        if "active in open buffer" in line:
            match = re.search(r"│\s*([a-z0-9_+]+)\s+active in open buffer", line)
            if match:
                rows.append(match.group(1))
    return rows


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"treesitter c probe: SKIP - no binary at {binary}")
        return 2
    if not any(os.path.exists(p) for p in c_parser_paths()):
        print("treesitter c probe: SKIP - no tree-sitter C parser installed")
        return 2

    path = write_workspace()
    screen = run_in_pty(binary, [path], PALETTE + b"tsstatus\r",
                        settle=4.0, after=3.0, cols=120, rows=40, cfg=CFG, cwd=ROOT)
    view = screen.text()
    if dump:
        print(view)
        print("-" * 70)

    rows = active_rows(view)
    print(f"active in open buffer: {rows}")
    if "c" not in rows:
        detail = ""
        for line in view.splitlines():
            if re.search(r"│\s*c\s+", line):
                detail = line.strip()
                break
        print(f"treesitter c probe: FAIL - the .c buffer is not using tree-sitter; "
              f"its parser row reads {detail!r}")
        return 1

    print("treesitter c probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
