#!/usr/bin/env python3
"""Probe: signature help marks the parameter, and a function completion types no parameters.

Two halves of one idea -- the language server knowing a function's parameters
should help you fill them, not fill them for you:

  * the signature popup highlights the parameter the caret is filling. clangd
    sends that index on the SignatureHelp *result* (`activeParameter`), not on
    the signature, so a client reading only the per-signature spelling left every
    call unhighlighted,
  * accepting a function completion writes `add()`, not `add(int left, int
    right)`: the snippet's argument placeholders are dropped and the caret lands
    inside the parens for the user to type.

Both scenes drive the real binary against a real clangd over a pty: the popup's
highlight is a colour on the rendered cells, and what a completion actually
types is only visible in the edited buffer.

Usage: test/lsp_function_args_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary or no clangd).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 120, 34
ROOT = "/tmp/jot_lsp_function_args_probe"
CFG = "/tmp/jot_lsp_function_args_probe_cfg"
SOURCE = os.path.join(ROOT, "probe.cpp")

HEADER = "int add(int left, int right) { return left + right; }\n"
CALL = "int use() { return add(); }\n"
TYPED_CALL = "return add();"
COMPLETE_LINE = "int other() {\n  a\n  return 0;\n}\n"


def write_source() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.rmtree(CFG, ignore_errors=True)
    os.makedirs(ROOT)
    os.makedirs(os.path.join(CFG, "configs"))
    # Inlay hints would rewrite the call on screen ("add(left: , right: )") and
    # hide the text the probe reads.
    with open(os.path.join(CFG, "configs", "settings.conf"), "w") as fh:
        fh.write("lsp_inlay_hints=false\n")
    with open(SOURCE, "w") as fh:
        fh.write(HEADER)
        fh.write(CALL)
        fh.write(COMPLETE_LINE)
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


def label_row(text: str) -> int:
    """The popup row carrying the signature label, or -1.

    clangd's label carries the return type (`add(int left, int right) -> int`),
    which is what tells the popup's row apart from the buffer line that declares
    the same function.
    """
    for y, line in enumerate(text.split("\n")):
        if "add(" in line and "->" in line:
            return y
    return -1


def highlight_span(snapshot) -> tuple[str, str]:
    """(highlighted parameter, its colour) read off the captured popup.

    The signature label is `int add(int left, int right)`; the parameter the
    caret is filling is painted in the theme's accent, which the rest of the row
    (the return type and the other parameter) is not. A row with no accent run
    returns ("", ""), which is the unhighlighted case the fix is about.
    """
    text, fg = snapshot
    rows = text.split("\n")
    y = label_row(text)
    if y < 0 or y >= len(fg):
        return "", ""
    line = rows[y]
    row_fg = fg[y]
    for param in ("int left", "int right"):
        col = line.find(param)
        if col < 0:
            continue
        # The accent is the colour at the parameter's first cell; it is really a
        # highlight only if it differs from the label's own text colour.
        start_fg = row_fg[col] if col < len(row_fg) else -1
        # The function name is plain label text (not a parameter): the colour
        # there is the row's own, so a parameter that differs from it is the
        # highlighted one.
        plain_col = line.find("add(")
        plain_fg = row_fg[plain_col] if 0 <= plain_col < len(row_fg) else -2
        if start_fg != plain_fg:
            return param, str(start_fg)
    return "", ""


def signature_scene(binary: str, dump: bool) -> tuple[tuple[str, str], tuple[str, str], str]:
    """Types `1` then `,` inside a call; returns (before, after, grid) snapshots."""
    settle = 10.0
    pre = run_in_pty(binary, [SOURCE], b"", settle=settle, after=1.0, cols=COLS, rows=ROWS,
                     cfg=CFG, cwd=ROOT, phases=[(0.5, lambda s: True)])
    x, y = cell_of(pre, TYPED_CALL)
    if x < 0:
        return ("", ""), ("", ""), ""
    seen: dict[str, object] = {}

    def capture_first(s) -> bool:
        text = s.text()
        if "┌" in text and "int add(" in text and "int left" in text:
            seen.setdefault("first", (text, [list(r) for r in s.fg]))
            return True
        return False

    def capture_second(s) -> bool:
        text = s.text()
        if "┌" in text and "int add(" in text and "int right" in text:
            seen.setdefault("second", (text, [list(r) for r in s.fg]))
            return True
        return False

    # The caret goes just inside `add()` (the needle's `(` is four cells into
    # `return add();`), then an argument and a comma move the active parameter.
    run_in_pty(binary, [SOURCE], b"", settle=settle, after=1.0, cols=COLS, rows=ROWS,
               cfg=CFG, cwd=ROOT,
               phases=[(0.5, lambda s: True), (0.4, click(x + 11, y)), (0.4, b"1"),
                       (4.0, capture_first), (0.4, b","), (4.0, capture_second)])
    first = seen.get("first", ("", []))
    second = seen.get("second", ("", []))
    if dump:
        for name, snap in (("after '1'", first), ("after ','", second)):
            print("--- %s ---" % name)
            print(snap[0] if snap[0] else "(no popup seen)")
            print("-" * 70)
    return highlight_span(first), highlight_span(second), str(second[0])


def completion_scene(binary: str, dump: bool) -> str:
    """Types `ad` at a statement, waits for the popup, and accepts with Tab."""
    settle = 10.0
    pre = run_in_pty(binary, [SOURCE], b"", settle=settle, after=1.0, cols=COLS, rows=ROWS,
                     cfg=CFG, cwd=ROOT, phases=[(0.5, lambda s: True)])
    # The `a` sits on the line after `int other() {`, two spaces in; the click
    # goes just past it so a typed `d` turns it into the prefix `ad`.
    gx, gy = cell_of(pre, "int other()")
    if gx < 0:
        return ""
    x, y = gx + 2, gy + 1
    # Click past the lone `a`, type `d` and let clangd answer, then Tab accepts.
    def popup_up(s) -> bool:
        text = s.text()
        return "┌" in text and "add" in text

    screen = run_in_pty(binary, [SOURCE], b"", settle=settle, after=1.0, cols=COLS, rows=ROWS,
                        cfg=CFG, cwd=ROOT,
                        phases=[(0.5, lambda s: True), (0.4, click(x + 1, y)), (0.4, b"d"),
                                (4.0, popup_up), (0.5, b"\t"), (1.0, b"")])
    if dump:
        print("--- after accepting the completion ---")
        print(screen.text())
        print("-" * 70)
    return screen.text()


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("lsp function args probe: SKIP - no binary at %s" % binary)
        return 2
    if shutil.which("clangd") is None:
        print("lsp function args probe: SKIP - clangd not installed")
        return 2

    write_source()
    failures: list[str] = []

    (first_param, first_fg), (second_param, second_fg), grid = signature_scene(binary, dump)
    print("after '1': active parameter = %r" % (first_param or "none"))
    print("after ',': active parameter = %r" % (second_param or "none"))
    if first_param != "int left":
        failures.append("the first argument's parameter was not highlighted")
    if second_param != "int right":
        failures.append("the highlight did not move to the second parameter with the comma")

    text = completion_scene(binary, dump)
    rows = text.split("\n")
    other = next((i for i, line in enumerate(rows) if "int other() {" in line), -1)
    edited = ""
    if other >= 0:
        edited = next((line for line in rows[other + 1:other + 4] if "add(" in line), "")
    print("completed line: %r" % edited.strip())
    if "add()" not in edited:
        failures.append("accepting the function completion did not write the empty call")
    if "int left" in edited:
        failures.append("accepting the function completion typed the parameter list for the user")

    if failures:
        print("lsp function args probe: FAIL")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("lsp function args probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
