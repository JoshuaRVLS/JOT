#!/usr/bin/env python3
"""Probe: BufChange edit snapshots stay inside their line budget.

`LuaAPI::on_buffer_change` keeps a snapshot of each edited buffer's lines so a
plugin listening to BufChange gets a line/column delta instead of nothing. The
snapshot copies every line of its file, and it used to be dropped only past 256
files, so a session with a handful of large buffers kept a copy of all of them
alive for the rest of the session. It is budgeted by total lines now (200k),
dropping the file a plugin has left alone longest first, so the cost stops
growing once the budget is reached however many files are open.

The probe opens twelve 90k-line files twice, once with no edit after each open
and once with one edit, and reads the process's own RSS. The difference between
the two runs is what the snapshots cost: the files, the buffers and every other
per-open feature are in both runs, so only the snapshot side moves. With the
budget that is ~70 MB; before the fix the same run kept all twelve snapshots and
cost ~180 MB. The run logs each file it reached and the probe asserts all of
them, so a driver that stopped early fails instead of passing on a difference
that never happened.

Usage: test/edit_snapshot_memory_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary, no /proc).
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# Twelve files of 90k lines: 1.08M lines in total against a 200k-line budget,
# and 90k lines under the per-file guard, so every one of them is snapshotted.
FILES = 12
LINES = 90000
COLS = 40
MAX_EXTRA_KB = 110 * 1024
MIN_EXTRA_KB = 24 * 1024

# Deferred: init.lua runs before the first frame, and opening buffers from Lua
# wants the loop already running. An open is asynchronous, so the walk asks for
# the next file only once the previous one is the current buffer, and logs each
# one it reached; an edit sent too early would land on whatever was current.
INIT_LUA = """
local files = {}
for i = 1, %(files)d do
  files[i] = "%(root)s/f" .. i .. ".xyz"
end
local log = io.open("%(log)s", "w")

local next_index = 0
local function advance()
  next_index = next_index + 1
  if next_index <= #files then
    jot.file.open(files[next_index])
  end
end

jot.timer.set_timeout(600, advance)
jot.timer.set_interval(50, function()
  if next_index < 1 or next_index > #files then
    return
  end
  if jot.buffer.current_file() ~= files[next_index] then
    return
  end
  log:write("edit " .. next_index .. " " .. tostring(#jot.buffer.get_text()) .. "\\n")
  log:flush()
  if %(edit)d == 1 then
    -- One edit is what makes LuaAPI snapshot the buffer for the plugins that
    -- listen to BufChange (todo_comments, cpp_inactive, ...).
    jot.buffer.set_text(jot.buffer.get_text() .. "\\n")
  end
  advance()
end)
"""

SAMPLES: list[int] = []


def write_files(root: str) -> None:
    row = "x" * COLS
    body = (row + "\n") * LINES
    for i in range(1, FILES + 1):
        with open(os.path.join(root, "f%d.xyz" % i), "w") as fh:
            fh.write(body)


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


def take_sample() -> None:
    pid = child_pid()
    if pid:
        value = rss_kb(pid)
        if value >= 0:
            SAMPLES.append(value)


def sample_for(seconds: float):
    """Keeps sampling for `seconds`: the peak lives at the end of the run."""
    state = {"t0": None}

    def check(_screen) -> bool:
        take_sample()
        if state["t0"] is None:
            state["t0"] = time.time()
        return time.time() - state["t0"] > seconds

    return check


def run_case(binary: str, work: str, name: str, edit: bool):
    """Opens the files, edits them or not, and returns (peak RSS, log lines)."""
    global SAMPLES
    SAMPLES = []
    cfg = os.path.join(work, "cfg_" + name)
    os.makedirs(cfg, exist_ok=True)
    log_path = os.path.join(work, "log_" + name)
    if os.path.exists(log_path):
        os.remove(log_path)
    with open(os.path.join(cfg, "init.lua"), "w") as fh:
        fh.write(INIT_LUA % {"files": FILES, "root": work, "edit": 1 if edit else 0,
                             "log": log_path})
    run_in_pty(binary, [], b"", settle=1.0, after=0.5, cols=120, rows=34, cfg=cfg,
               phases=[(0.0, sample_for(6.0))])
    lines = []
    if os.path.exists(log_path):
        with open(log_path) as fh:
            lines = [line for line in fh.read().splitlines() if line.strip()]
    return (max(SAMPLES) if SAMPLES else -1), lines


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("edit snapshot memory probe: SKIP - no binary at %s" % binary)
        return 2
    if not os.path.isdir("/proc"):
        print("edit snapshot memory probe: SKIP - no /proc to read the process's RSS")
        return 2

    work = "/tmp/jot_edit_snapshot_probe"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    write_files(work)

    opened, opened_log = run_case(binary, work, "opened", False)
    edited, edited_log = run_case(binary, work, "edited", True)
    if opened < 0 or edited < 0:
        print("edit snapshot memory probe: SKIP - the process's RSS could not be read")
        return 2

    if len(opened_log) < FILES or len(edited_log) < FILES:
        print("edit snapshot memory probe: FAIL")
        print("  - the run reached %d of %d files opened and %d of %d edited, so it"
              " measured a session that never held them all"
              % (len(opened_log), FILES, len(edited_log), FILES))
        if dump:
            print("log:     %s" % " ".join(l.split()[1] for l in opened_log))
            print("log:     %s" % " ".join(l.split()[1] for l in edited_log))
        return 1

    extra = edited - opened
    print("rss:     opened %d kB, edited %d kB, gap %d kB (bound %d..%d)"
          % (opened, edited, extra, MIN_EXTRA_KB, MAX_EXTRA_KB))
    if dump:
        print("files:   %s/f1.xyz .. f%d.xyz" % (work, FILES))
        print("log:     %s" % " ".join(line.replace(" ", "=") for line in edited_log))
    if extra > MAX_EXTRA_KB:
        print("edit snapshot memory probe: FAIL")
        print("  - editing %d files cost %d kB more than opening them; the"
              " snapshots are not inside their line budget" % (FILES, extra))
        return 1
    if extra < MIN_EXTRA_KB:
        print("edit snapshot memory probe: FAIL")
        print("  - editing %d files cost only %d kB more than opening them, so"
              " the probe never made a snapshot" % (FILES, extra))
        return 1
    print("edit snapshot memory probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
