#!/usr/bin/env python3
"""Probe: the completion popup ranks by what this user accepts, not by length.

The list a server answers with is filtered by the typed word and then ranked. The
ranking puts the learned table (features/completion_rank.h) ahead of the length of
the label, so a name the user reaches for often comes first even when a shorter
one is offered beside it, and it fades with age so a habit from last month stops
leading the one reached for today.

Three scenes against a real clangd, on the same workspace and the same typed
prefix, differing in the usage table the config home holds:

  * no table at all: the shorter label leads, which is the baseline.
  * a table counting `quicksilver` today: the habit leads.
  * a table counting `quicksilver` a month ago against two uses of `quick_scan`
    today: the fresh name leads again, because a count halves every two weeks.

The two names are declared in a header the probe never opens, so the labels on
screen can only be the popup's rows.

Usage: test/completion_rank_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary or no clangd).
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 120, 34
WORK = "/tmp/jot_completion_rank_probe"
CFG_PLAIN = "/tmp/jot_completion_rank_probe_cfg_plain"
CFG_HABIT = "/tmp/jot_completion_rank_probe_cfg_habit"
CFG_AGED = "/tmp/jot_completion_rank_probe_cfg_aged"
PROBE = os.path.join(WORK, "probe.cpp")

# The two candidates share the typed prefix. `quick_scan` is a character shorter,
# so it leads on length alone; `quicksilver` is the one the second config home
# says this user keeps reaching for.
SHORTER = "quick_scan"
FAMILIAR = "quicksilver"

# The file ends on the line being typed, so `escape G` (end of buffer) lands the
# caret right after the `qu` already there and the `i` typed after it makes the
# prefix `qui`. The declaration names live in the header, never on screen.
SOURCE = '#include "api.h"\n\nint main() {\n  qu\n'
HEADER = "int quick_scan(int x);\nint quicksilver(int x);\n"
END_OF_FILE = b"\x1bG"


def write_workspace() -> None:
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    with open(PROBE, "w") as fh:
        fh.write(SOURCE)
    with open(os.path.join(WORK, "api.h"), "w") as fh:
        fh.write(HEADER)
    # clangd's own flags, so the header resolves without a compile command.
    with open(os.path.join(WORK, "compile_flags.txt"), "w") as fh:
        fh.write("-xc++\n-std=c++17\n")


def seed_config(cfg: str, usage: str | None) -> None:
    shutil.rmtree(cfg, ignore_errors=True)
    os.makedirs(os.path.join(cfg, "configs"))
    # Inlay hints and diagnostic text would paint over the rows the popup is read
    # from, and neither is what this probe is about.
    with open(os.path.join(cfg, "configs", "settings.conf"), "w") as fh:
        fh.write("lsp_inlay_hints = false\n")
        fh.write("diagnostics_virtual_text = false\n")
        fh.write("decorations_inline_diagnostics = false\n")
    if usage is not None:
        with open(os.path.join(cfg, "configs", "completion_usage.tsv"), "w") as fh:
            fh.write(usage)


def popup_capture(binary: str, cfg: str) -> str:
    """Types `i` after the `qu` and returns the screen with the popup up.

    The wait is a callable phase rather than a fixed drain: what is asserted is the
    order of the two rows, so the snapshot has to be the moment both are listed.
    """
    found: dict[str, str] = {}

    def both_listed(s) -> bool:
        text = s.text()
        if SHORTER in text and FAMILIAR in text:
            found["text"] = text
            return True
        return False

    run_in_pty(binary,
               [PROBE],
               END_OF_FILE + b"i",
               settle=10.0,
               after=1.0,
               cols=COLS,
               rows=ROWS,
               cfg=cfg,
               cwd=WORK,
               phases=[(0.5, lambda s: True), (10.0, both_listed)])
    return found.get("text", "")


def listed_order(text: str) -> list[str]:
    """The two candidate rows in the order they appear, top to bottom."""
    order = []
    for line in text.split("\n"):
        if SHORTER in line or FAMILIAR in line:
            order.append(SHORTER if SHORTER in line else FAMILIAR)
    return order


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("completion rank probe: SKIP - no binary at %s" % binary)
        return 2
    if shutil.which("clangd") is None:
        print("completion rank probe: SKIP - clangd not installed")
        return 2

    write_workspace()
    failures: list[str] = []

    # Scene 1: no history at all. The short label leads, which is the order the
    # list has always had and the baseline the second scene moves.
    seed_config(CFG_PLAIN, None)
    plain = popup_capture(binary, CFG_PLAIN)
    if dump:
        print("--- no history ---")
        print(plain)
        print("-" * 70)
    order = listed_order(plain)
    print("no history:      %s" % (order or "(popup never listed both)"))
    if len(order) < 2:
        failures.append("the popup never listed both candidates, so nothing was ranked")
    elif order[0] != SHORTER:
        failures.append("the shorter label did not lead with no history to rank by")

    # Scene 2: the same workspace and the same keystroke, with a usage table that
    # says this user keeps accepting `quicksilver`. The habit outweighs the one
    # character of length, which is the whole point of the model.
    seed_config(CFG_HABIT, "cpp\t%s\t5\n" % FAMILIAR)
    learned = popup_capture(binary, CFG_HABIT)
    if dump:
        print("--- with a learned habit ---")
        print(learned)
        print("-" * 70)
    order = listed_order(learned)
    print("learned habit:   %s" % (order or "(popup never listed both)"))
    if len(order) < 2:
        failures.append("the popup never listed both candidates to re-rank")
    elif order[0] != FAMILIAR:
        failures.append("the accepted name did not lead over the shorter one")

    # Scene 3: the same table with both counts stamped. The month-old habit has
    # halved twice against a fortnight of half-life, so it is worth less than the
    # two uses from today and the fresh name leads again. With the previous scene
    # beside it, this is the pair that says the ranking is dated and not just
    # counting.
    now = int(time.time())
    seed_config(CFG_AGED, "cpp\t%s\t5\t%d\ncpp\t%s\t2\t%d\n"
                % (FAMILIAR, now - 28 * 86400, SHORTER, now))
    aged = popup_capture(binary, CFG_AGED)
    if dump:
        print("--- with an aged habit ---")
        print(aged)
        print("-" * 70)
    order = listed_order(aged)
    print("aged habit:      %s" % (order or "(popup never listed both)"))
    if len(order) < 2:
        failures.append("the popup never listed both candidates to age")
    elif order[0] != SHORTER:
        failures.append("a month-old habit still led the name used today")

    if failures:
        print("completion rank probe: FAIL")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("completion rank probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
