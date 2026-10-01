#!/usr/bin/env python3
"""Probe: a plain .txt file is not parsed with the vimdoc tree-sitter grammar.

`runtime/lua/treesitter/registry.lua` used to map vimdoc to `.txt`, which meant
every plain-text note was parsed with a grammar written for vim help files.
Tree-sitter's parse of token-dense text allocates a few hundred bytes per word
that the allocator then cannot give back (the live tree pins the heap, so
malloc_trim cannot release the freed parse scratch): a 2000-line file of
`x x x ...` cost ~23 MB of RSS, and ordinary prose a few MB per 2000 lines.
vimdoc is registered for `.vimdoc` only now, so `.txt` takes the no-grammar
path.

The probe drives the real binary over the same token-dense file twice, once as
`.txt` and once as `.xyz` (an extension no grammar and no regex rule claims),
and reads the process's own RSS around it. The .txt run must not pay megabytes
more than the .xyz one; before the fix the gap was ~23 MB against a bound of 8.

Usage: test/txt_grammar_memory_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary, no /proc).
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# 2000 lines of "x x x ..." is the shape that made the vimdoc parse churn the
# most per line: ~40 word boundaries each. The bound sits far below the ~23 MB
# the mapping cost and far above the run-to-run noise (well under 1 MB).
LINES = 2000
COLS = 80
MAX_EXTRA_KB = 8 * 1024


def write_file(path: str, lines: int, cols: int) -> None:
    row = ("x " * (cols // 2 + 1))[:cols]
    with open(path, "w") as fh:
        for _ in range(lines):
            fh.write(row + "\n")


def child_pid() -> int:
    """The jot the harness forked: the direct child of this process."""
    me = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % entry) as fh:
                fields = fh.read().split()
        except OSError:
            continue
        if len(fields) > 3 and fields[3] == str(me) and "jot" in fields[1]:
            return int(entry)
    return 0


def rss_kb(pid: int) -> int:
    try:
        with open("/proc/%d/status" % pid) as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return -1


def peak_rss(binary: str, work: str, name: str, ext: str) -> int:
    """Opens one file and returns the peak RSS seen while it is idle."""
    path = os.path.join(work, name + ext)
    write_file(path, LINES, COLS)
    samples = []
    state = {"t0": None}

    def sampler(_screen) -> bool:
        pid = child_pid()
        if pid:
            samples.append(rss_kb(pid))
        if state["t0"] is None:
            state["t0"] = time.time()
        return time.time() - state["t0"] > 2.5

    run_in_pty(binary, [path], b"", settle=4.0, after=0.5, cols=120, rows=34,
               cfg=os.path.join(work, "cfg_" + name),
               phases=[(0.0, sampler)])
    if not samples or max(samples) < 0:
        return -1
    return max(samples)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("txt grammar memory probe: SKIP - no binary at %s" % binary)
        return 2
    if not os.path.isdir("/proc"):
        print("txt grammar memory probe: SKIP - no /proc to read the process's RSS")
        return 2

    work = "/tmp/jot_txt_grammar_probe"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)

    txt_peak = peak_rss(binary, work, "sample", ".txt")
    xyz_peak = peak_rss(binary, work, "sample", ".xyz")
    if txt_peak < 0 or xyz_peak < 0:
        print("txt grammar memory probe: SKIP - the process's RSS could not be read")
        return 2

    extra = txt_peak - xyz_peak
    print("rss:     .txt %d kB, .xyz %d kB, gap %d kB (bound %d)"
          % (txt_peak, xyz_peak, extra, MAX_EXTRA_KB))
    if dump:
        print("files:   %s" % work)
    if extra > MAX_EXTRA_KB:
        print("txt grammar memory probe: FAIL")
        print("  - the .txt run cost %d kB more than the same file with no grammar"
              % extra)
        return 1
    print("txt grammar memory probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
