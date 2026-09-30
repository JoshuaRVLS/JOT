#!/usr/bin/env python3
"""Probe: the colour preview only paints in web filetypes.

A hex literal is a colour in a stylesheet or a React component; in a C++ comment,
a Lua string or a log line it is usually just text, and painting its background
turns every "#ffffff" into a distracting swatch. The renderer now gates the whole
preview - every format and every display mode - behind `colorizer_filetypes`,
which defaults to the HTML/React/CSS set.

This drives the real binary over three files: a `.lua` file must show the literal
with no fill, while a `.css` and a `.jsx` file must show it filled with the exact
colour. The fill is read from the reconstructed cell grid's truecolour
backgrounds, so it is the renderer's output being checked, not a config value.

Usage: test/colorizer_filetype_probe.py [path-to-jot-binary] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

HEX = "#ff8800"
# The harness tags a truecolour background as 1000 + 0xRRGGBB so it cannot be
# mistaken for a palette index; this is the same literal the source declares.
FILL = 1000 + 0xFF8800


def content(ext: str) -> str:
    """The literal in a shape that suits the filetype, one line either way."""
    if ext in (".html", ".jsx", ".tsx"):
        return '<a style="color: %s">x</a>\n' % HEX
    if ext == ".lua":
        return 'local c = "%s"\n' % HEX
    return "a { color: %s; }\n" % HEX


def scene(binary: str, work: str, ext: str):
    """Opens colors<ext> and reports whether the literal is visible and filled."""
    path = os.path.join(work, "colors" + ext)
    with open(path, "w") as fh:
        fh.write(content(ext))
    screen = run_in_pty(binary, [path], b"", settle=2.5, after=1.5, cols=100, rows=30,
                        cfg=os.path.join(work, "cfg"))
    visible = HEX in screen.text()
    filled = any(FILL in row for row in screen.bg)
    return visible, filled, screen


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("colorizer filetype probe: SKIP - no binary at %s" % binary)
        return 2

    work = "/tmp/jot_colorizer_filetype_probe"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)

    # The first filetype is the one that must stay clean; the rest are the web
    # set the feature is for, spread across markup, React and stylesheets.
    scenes = [(".lua", False), (".css", True), (".jsx", True)]
    failures = []
    for ext, want_fill in scenes:
        visible, filled, screen = scene(binary, work, ext)
        print("%-5s literal on screen: %-5s preview fill: %s"
              % (ext, "yes" if visible else "no", "yes" if filled else "no"))
        if dump:
            print(screen.text())
            print("-" * 70)
        if not visible:
            failures.append("%s: the literal never reached the screen" % ext)
        if filled != want_fill:
            if want_fill:
                failures.append("%s: no colour fill was painted" % ext)
            else:
                failures.append("%s: a non-web file got a colour fill" % ext)

    if failures:
        print("colorizer filetype probe: FAIL")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("colorizer filetype probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
