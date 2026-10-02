#!/usr/bin/env python3
"""Require a half-name squiggle before save, and clear it when a body arrives.

Exit codes: 0 pass, 1 fail, 2 skip (missing binary).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty


def name_runs(screen):
    for row, line in enumerate(screen.text().split("\n")):
        if "void render" in line:
            start = line.index("render")
            runs = [(a, b, text) for a, b, text in screen.underline_runs(row)
                    if a < start + 6 and b > start]
            return row, start, runs
    return -1, -1, []


def half_wave(screen):
    row, start, runs = name_runs(screen)
    return (row >= 0 and runs == [(start, start + 3, "ren")]
            and all(screen.underline[row][col] == 3 for col in range(start, start + 3)))


def paste(text):
    return b"\x1b[200~" + text.encode() + b"\x1b[201~"


def main():
    binary = next((arg for arg in sys.argv[1:] if not arg.startswith("--")), "build/apps/jot/jot")
    if not os.path.isfile(binary):
        print("missing underline probe: SKIP - missing binary")
        return 2
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="jot_missing_underline_") as root:
        game = os.path.join(root, "game")
        os.makedirs(game)
        source = os.path.join(game, "triangle.c")
        # Pacman's unrelated helper must not hide Triangle's missing body.
        with open(os.path.join(game, "pacman.c"), "w") as file:
            file.write("void render();\nint main() { render(); }\nvoid render() {}\n")
        for saved in (True, False):
            cfg = os.path.join(root, "cfg_saved" if saved else "cfg_unsaved")
            os.makedirs(os.path.join(cfg, "configs"))
            with open(os.path.join(cfg, "configs", "settings.conf"), "w") as file:
                file.write("cpp_definitions=true\nauto_save=false\ndecorations_inline_diagnostics=true\n")
            original = ("void render();\n" if saved else "") + "int main() {}\n"
            with open(source, "w") as file:
                file.write(original)
            phases = [(0.2, b"\x1b[112;6u"), (0.2, b"open " + source.encode()),
                      (0.2, b"\r"), (0.2, lambda s: "int main" in s.text())]
            if not saved:
                phases += [(0.2, paste("void render();\n"))]
            phases += [(0.2, lambda s: half_wave(s))]
            if not saved:
                def capture(screen):
                    outcomes.append(("unsaved half wave", half_wave(screen)))
                    return True
                phases += [(0.0, capture), (0.2, paste("void render() {}\n")),
                           (0.2, lambda s: "void render() {}" in s.text() and not name_runs(s)[2])]
            screen = run_in_pty(binary, [root], b"", settle=2.0, after=0.2,
                                cols=130, rows=36, cfg=cfg, cwd=root,
                                phases=phases, until_timeout=10.0)
            if "--dump" in sys.argv:
                print(screen.text())
                print(name_runs(screen))
            if saved:
                outcomes.append(("saved half wave", half_wave(screen)))
            else:
                outcomes.append(("body clears wave", "void render() {}" in screen.text()
                                 and not name_runs(screen)[2]))
                with open(source) as file:
                    outcomes.append(("disk unchanged", file.read() == original))
    for label, passed in outcomes:
        print("missing underline probe: %s - %s" % ("PASS" if passed else "FAIL", label))
    return 0 if outcomes and all(passed for _, passed in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
