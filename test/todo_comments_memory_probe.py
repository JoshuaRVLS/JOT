#!/usr/bin/env python3
"""Probe: the TODO scan does not hold a Lua copy of the whole buffer.

`runtime/lua/features/todo_comments.lua` reads every line of a buffer on open
(and on every edit) to find TODO-style comments. It used to build one Lua table
holding every line first: a string per line kept alive for the whole scan, so a
200k-line buffer cost ~40 MB of RSS on the open alone. The scan reads lines one
at a time now, keeping the peak bounded by the matches instead of the file.

The probe drives the real binary over a 200k-line file twice, once with the
feature on (the default) and once with `todo_comments = false`, and reads the
process's own RSS around it. The enabled run must not cost megabytes more than
the disabled one; before the fix the gap was ~40 MB against a bound of 20.

Usage: test/todo_comments_memory_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary, no /proc).
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# A file this size is lazy (>10 MB), so the native per-file edit snapshot is not
# in play: the gap is the feature's own scan. 200k lines of one word each is
# ~16 MB of text and no matches, the worst case for a scan that copies.
LINES = 200000
COLS = 80
MAX_EXTRA_KB = 20 * 1024


def write_file(path: str, lines: int, cols: int) -> None:
    row = "x" * cols
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


def peak_rss(binary: str, work: str, name: str, todo: bool) -> int:
    """Opens the file with the feature on or off and returns the peak RSS."""
    cfg = os.path.join(work, "cfg_" + name)
    if not todo:
        os.makedirs(os.path.join(cfg, "configs"), exist_ok=True)
        with open(os.path.join(cfg, "configs", "settings.conf"), "w") as fh:
            fh.write("todo_comments = false\n")
    path = os.path.join(work, "big.xyz")
    samples = []
    state = {"t0": None}

    def sampler(_screen) -> bool:
        pid = child_pid()
        if pid:
            samples.append(rss_kb(pid))
        if state["t0"] is None:
            state["t0"] = time.time()
        return time.time() - state["t0"] > 3.0

    run_in_pty(binary, [path], b"", settle=4.5, after=0.5, cols=120, rows=34,
               cfg=cfg, phases=[(0.0, sampler)])
    if not samples or max(samples) < 0:
        return -1
    return max(samples)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("todo comments memory probe: SKIP - no binary at %s" % binary)
        return 2
    if not os.path.isdir("/proc"):
        print("todo comments memory probe: SKIP - no /proc to read the process's RSS")
        return 2

    work = "/tmp/jot_todo_memory_probe"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    write_file(os.path.join(work, "big.xyz"), LINES, COLS)

    on_peak = peak_rss(binary, work, "on", True)
    off_peak = peak_rss(binary, work, "off", False)
    if on_peak < 0 or off_peak < 0:
        print("todo comments memory probe: SKIP - the process's RSS could not be read")
        return 2

    extra = on_peak - off_peak
    print("rss:     on %d kB, off %d kB, gap %d kB (bound %d)"
          % (on_peak, off_peak, extra, MAX_EXTRA_KB))
    if dump:
        print("file:    %s" % os.path.join(work, "big.xyz"))
    if extra > MAX_EXTRA_KB:
        print("todo comments memory probe: FAIL")
        print("  - the scan cost %d kB more than the same buffer with it off" % extra)
        return 1
    print("todo comments memory probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
