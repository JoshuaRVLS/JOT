#!/usr/bin/env python3
"""Workspace scan latency: exit 0 pass, 1 fail, 2 missing binary."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty


def main():
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    if not os.path.isfile(binary):
        print("C++ scan performance: SKIP - missing binary")
        return 2
    with tempfile.TemporaryDirectory(prefix="jot_cpp_scan_perf_") as base:
        root = os.path.join(base, "workspace")
        cfg = os.path.join(base, "cfg")
        os.makedirs(root)
        os.makedirs(os.path.join(cfg, "configs"))
        with open(os.path.join(cfg, "configs", "settings.conf"), "w") as file:
            file.write("cpp_definitions=true\ndiscord_rpc=false\n")
        for i in range(350):
            with open(os.path.join(root, "unit_%03d.cpp" % i), "w") as file:
                file.write("".join("void helper_%d_%d() {}\n" % (i, j) for j in range(40)))
        target = os.path.join(root, "missing.cpp")
        with open(target, "w") as file:
            file.write("void needs_body();\n")
        landed = []
        start = time.monotonic()
        def ready(screen):
            if 'No definition found for "needs_body()"' in screen.text():
                if not landed:
                    landed.append(time.monotonic() - start)
                return True
            return False
        run_in_pty(binary, [root], b"", settle=.1, after=.1,
                   cols=140, rows=30, cfg=cfg, cwd=root,
                   phases=[(.1, b"\x1b[112;6u"), (.1, b"open " + target.encode()),
                           (.1, b"\r"), (.1, ready)], until_timeout=30)
        elapsed = landed[0] if landed else float("inf")
        passed = elapsed < 6.0
        print("C++ scan performance: %s - 14,001 signatures, diagnostic in %.2fs (limit 6s)"
              % ("PASS" if passed else "FAIL", elapsed))
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
