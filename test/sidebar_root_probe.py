#!/usr/bin/env python3
"""Selectable explorer root: exit 0 pass, 1 fail, 2 missing binary."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty


def main():
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    if not os.path.isfile(binary):
        return 2
    with tempfile.TemporaryDirectory(prefix="jot_root_probe_") as base:
        root = os.path.join(base, "workspace")
        os.makedirs(os.path.join(root, "child"))
        cfg = os.path.join(base, "cfg")
        observed = []
        def selected(screen):
            return screen.bg[0][8] != screen.bg[3][8]
        def capture(screen):
            observed.append(selected(screen))
            return True
        screen = run_in_pty(binary, [root], b"", settle=2, after=.2,
                            cols=100, rows=24, cfg=cfg, cwd=root,
                            phases=[(.2, b"\x1b[<0;9;1M\x1b[<0;9;1m"),
                                    (.4, capture), (.2, b"j"), (.2, b"k"),
                                    (.4, capture), (.2, b"a"),
                                    (.2, b"root_created.txt"), (.2, b"\r")])
        passed = len(observed) == 2 and all(observed) and os.path.isfile(os.path.join(root, "root_created.txt"))
        if "--dump" in sys.argv:
            print(screen.text())
        print("sidebar root probe: %s - mouse, keyboard, root file creation" % ("PASS" if passed else "FAIL"))
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
