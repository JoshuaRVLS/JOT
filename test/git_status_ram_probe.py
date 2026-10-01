#!/usr/bin/env python3
"""Probe: a repo with a huge `git status` does not make jot climb poll after poll.

The 1.5 s git poll used to parse the whole `git status --porcelain` output into a
path -> status map on every tick. On a repo with tens of thousands of dirty
files - the home directory turns into one easily, which is how the report that
started this read 180 MB and climbing - that map is tens of megabytes per poll.
The parse runs on the task queue's workers, and every worker that ran one kept
its own freed arena, so RSS climbed in steps until each worker had parsed once
and then stayed high. The poll now digests the output in one streaming pass and
skips the parse when it is byte-for-byte the output the installed map was built
from.

Scene: jot starts inside a throwaway repo holding 30000 untracked files and
opens a small source file. The branch of that repo has to reach the status line
(the poll saw it), and after the first parse settles the RSS may neither keep
climbing nor sit anywhere near what a parse per poll costs.

Usage: test/git_status_ram_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary or no git).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty

BINARY = "build/apps/jot/jot"
REPO = "/tmp/jot_git_status_ram_probe"
CFG = os.path.join(REPO, "cfg")
# Enough untracked files that one parse is tens of megabytes: a step per worker
# arena was what the report measured, and a leak-free poll still has to stay far
# below that.
FILES = 30000
# Samples before this many seconds are the startup parse; the bound applies to
# what the process does after it has settled.
WARMUP_SECONDS = 4.0
# A parse per worker (eight of them) measured 28 MB on this repo; the fixed
# poll stays under 1 MB. Twelve leaves room for allocator noise from the file
# tree and LSP while still failing by more than 2x when the parse comes back.
MAX_GROWTH_KIB = 12 * 1024
MAX_RSS_KIB = 128 * 1024


def make_repo() -> tuple:
    """A throwaway repo with `FILES` untracked files; (repo, target file)."""
    shutil.rmtree(REPO, ignore_errors=True)
    os.makedirs(REPO)
    subprocess.check_call(["git", "init", "-q", REPO])
    # One commit so the branch has a name: an unborn HEAD makes porcelain print
    # "## No commits yet on master", which the poll reads as the branch "No".
    subprocess.check_call(["git", "-C", REPO, "-c", "user.email=probe@jot",
                           "-c", "user.name=jot", "commit", "--allow-empty", "-qm", "init"])
    for i in range(FILES):
        open(os.path.join(REPO, "dirty_%05d.txt" % i), "w").close()
    target = os.path.join(REPO, "main.c")
    with open(target, "w") as fh:
        fh.write("int main(void)\n{\n  return 0;\n}\n")
    return REPO, target


def find_pid(target: str):
    """The jot process whose command line names `target` (unique per run)."""
    needle = target.encode() + b"\x00"
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % entry, "rb") as fh:
                cmdline = fh.read()
        except OSError:
            continue
        if needle in cmdline:
            return int(entry)
    return None


def rss_kib(pid: int):
    try:
        with open("/proc/%d/status" % pid) as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def main() -> int:
    plain = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    binary = plain[0] if plain else BINARY
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("git status ram probe: SKIP - no binary at %s" % binary)
        return 2
    if shutil.which("git") is None:
        print("git status ram probe: SKIP - git is not installed")
        return 2
    if not os.path.isdir("/proc"):
        print("git status ram probe: SKIP - /proc is not available")
        return 2

    repo, target = make_repo()
    branch = subprocess.check_output(
        ["git", "-C", repo, "branch", "--show-current"]).decode().strip()

    samples = []
    stop = threading.Event()

    def sample_loop() -> None:
        pid = None
        start = time.time()
        while not stop.is_set():
            if pid is None:
                pid = find_pid(target)
            else:
                rss = rss_kib(pid)
                if rss is not None:
                    samples.append((time.time() - start, rss))
            time.sleep(0.25)

    sampler = threading.Thread(target=sample_loop, daemon=True)
    sampler.start()
    failures = []
    try:
        # A callable phase that never turns true keeps the editor running for
        # until_timeout while the sampler reads its RSS.
        screen = run_in_pty(binary, [target], b"", settle=3.0, after=0.5,
                            cols=140, rows=30, cfg=CFG, cwd=repo,
                            phases=[(0.0, lambda s: False)], until_timeout=14.0)
    finally:
        stop.set()
        sampler.join(timeout=2.0)

    if not samples:
        print("git status ram probe: FAIL - never found the jot process")
        return 1
    if dump:
        for at, rss in samples:
            print("  %5.2fs %6d kB" % (at, rss))
    warm = [item for item in samples if item[0] >= WARMUP_SECONDS]
    if len(warm) < 5:
        print("git status ram probe: FAIL - only %d RSS samples" % len(samples))
        return 1
    settled = warm[0][1]
    peak = max(item[1] for item in warm)
    growth = peak - settled
    print("repo with %d untracked files: rss %d kB after warmup, peak %d kB (%+d kB), "
          "last %d kB" % (FILES, settled, peak, growth, samples[-1][1]))

    if branch and branch not in screen.text():
        failures.append("the poll never saw the repo: branch %r is not on screen" % branch)
    if growth > MAX_GROWTH_KIB:
        failures.append("rss kept climbing after warmup: %+d kB over %d kB" % (growth, settled))
    if peak > MAX_RSS_KIB:
        failures.append("the process holds %d kB on a %d-file repo" % (peak, FILES))
    if failures:
        for failure in failures:
            print("git status ram probe: FAIL - %s" % failure)
        return 1
    print("git status ram probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
