#!/usr/bin/env python3
"""Probe: where the signature popup lands, against a real clangd.

The box anchors above the caret so it does not fight the completion list that
opens below. Near the top of the window there is no space above, and the old
fallback pinned the box to the pane's first row, which put it straight over the
line being typed: the code and the caret disappeared behind it.

Two scenes, each triggered by typing a comma inside a valid call (clangd answers
that request, and the popup stays up while the caret sits in the argument list):
  * top: the call site on line 2, where the box has to go below the line;
  * middle: the call site mid-window, where either side is fine as long as the
    caret's own row stays visible.

The popup's frame is what is read, not its text: the label is the function's own
signature, which the declaration line above it repeats verbatim.

Usage: test/signature_place_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary or no clangd).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 110, 34
ROOT = "/tmp/jot_signature_probe"
CFG = "/tmp/jot_signature_probe_cfg"
SOURCE = os.path.join(ROOT, "probe.cpp")

HEAD = "int add(int left, int right) { return left + right; }\n"
CALL_TOP = "int use() { return add(1, 2); }\n"
CALL_MID = "int mid() { return add(1, 2); }\n"
NEEDLE = "add(1, 2)"
TYPED = b","


def write_source() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.rmtree(CFG, ignore_errors=True)
    os.makedirs(ROOT)
    os.makedirs(os.path.join(CFG, "configs"))
    # Inlay hints would rewrite the call sites on screen ("add(left: 1, ...)"),
    # and the probe finds its click targets by their source text.
    with open(os.path.join(CFG, "configs", "settings.conf"), "w") as fh:
        fh.write("lsp_inlay_hints=false\n")
    with open(SOURCE, "w") as fh:
        fh.write(HEAD)
        fh.write(CALL_TOP)
        for i in range(14):
            fh.write("// filler %d\n" % i)
        fh.write(CALL_MID)
    with open(os.path.join(ROOT, "compile_flags.txt"), "w") as fh:
        fh.write("-xc++\n-std=c++17\n")


def cell_of(screen, needle: str, skip_rows: int = 0):
    """0-based (x, y) of `needle`, skipping the first `skip_rows` matches."""
    seen = 0
    for y, line in enumerate(screen.text().split("\n")):
        x = line.find(needle)
        if x >= 0:
            if seen >= skip_rows:
                return x, y
            seen += 1
    return -1, -1


def click(x: int, y: int) -> bytes:
    return b"\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (x + 1, y + 1, x + 1, y + 1)


def scene(binary: str, second_call: bool, dump: bool):
    """Types a comma at the call site; returns (caret row, box rows, the grid).

    The popup is transient, so the grid is captured the moment its frame is on
    screen. The caret's row comes from the grid measured before typing: when the
    box is over that row, the row's text is exactly what is missing.
    """
    settle = 9.0  # clangd has to attach before the request means anything
    pre = run_in_pty(binary, [SOURCE], b"", settle=settle, after=1.0, cols=COLS, rows=ROWS,
                     cfg=CFG, cwd=ROOT, phases=[(0.5, lambda s: True)])
    x, y = cell_of(pre, NEEDLE, skip_rows=1 if second_call else 0)
    if x < 0:
        return -1, [], ""
    seen: dict[str, str] = {}

    def capture_when_popup(s) -> bool:
        if "┌" in s.text() and "add(" in s.text():
            seen.setdefault("grid", s.text())
            return True
        return False

    run_in_pty(binary, [SOURCE], b"", settle=settle, after=1.0, cols=COLS, rows=ROWS,
               cfg=CFG, cwd=ROOT,
               # The caret goes just inside the call's parenthesis (the needle is
               # `add(1, 2)`, so four cells in sits before the first argument).
               phases=[(0.5, lambda s: True), (0.4, click(x + 4, y)), (0.4, TYPED),
                       (5.0, capture_when_popup)])
    grid = seen.get("grid", "")
    if dump:
        print(grid or "(no popup seen)")
        print("-" * 70)
    text = grid.split("\n")
    top = next((i for i, line in enumerate(text) if "┌" in line), -1)
    bottom = next((i for i, line in enumerate(text) if i > top >= 0 and "└" in line), -1)
    if top < 0 or bottom < 0:
        return y, [], grid
    return y, list(range(top, bottom + 1)), grid


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("signature place probe: SKIP - no binary at %s" % binary)
        return 2
    if shutil.which("clangd") is None:
        print("signature place probe: SKIP - clangd not installed")
        return 2

    write_source()
    failures: list[str] = []

    # The call site on line 2: the box has to go below the line being typed.
    caret_row, box, grid = scene(binary, False, dump)
    if not box:
        failures.append("top: the signature popup never appeared")
    elif caret_row in box:
        failures.append("top: the popup covers the caret's row (caret %d, box rows %d..%d)"
                        % (caret_row, box[0], box[-1]))
    print("caret at line 2:  %s" % ("ok" if not failures else "FAILED"))

    # The call site mid-window: either side, as long as the caret's row is there.
    before = len(failures)
    caret_row, box, grid = scene(binary, True, dump)
    if not box:
        failures.append("middle: the signature popup never appeared")
    elif caret_row in box:
        failures.append("middle: the popup covers the caret's row (caret %d, box rows %d..%d)"
                        % (caret_row, box[0], box[-1]))
    print("caret mid-file:   %s" % ("ok" if len(failures) == before else "FAILED"))

    if failures:
        print("signature place probe: FAIL")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("signature place probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
