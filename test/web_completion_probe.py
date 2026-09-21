#!/usr/bin/env python3
"""Probe: the workspace's CSS vocabulary reaches a completion popup.

The unit tests pin the scan and the context test, and they can hand the editor an
index directly. What they cannot cover is the part that only a real session
proves: opening a workspace kicks the walk off on the worker thread, what it
finds lands in the editor's index, and the completion path reads it for the
buffer being typed in -- including when the name and the caret are in *different*
files, which is the whole point of scanning the tree.

The workspace holds a style sheet that declares a class name and a custom
property, and a markup file that uses neither. Typing inside a class attribute in
that markup must offer the style sheet's class; typing inside `var(--)` in the
style sheet must offer its custom property; and the same `class="` text in a C++
buffer, which has no class attribute, must offer neither.

Usage: test/web_completion_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 150, 34

# Ctrl+Shift+P opens the command palette; Enter runs what is typed in it. The
# workspace has to be the launch argument (that is what kicks the scan off), so
# the file being typed in is opened from inside the session rather than from the
# command line: `:e <file>` through the same palette a user reaches it by.
PALETTE = b"\x1b[112;6u"
ENTER = b"\r"
# Running a command from the palette leaves the command line up, so Escape
# closes it before the next keystroke is meant for the buffer.
ESC = b"\x1b"

# The names are deliberately unlike anything the editor ships as a builtin tag or
# as its own chrome, so finding one on screen can only mean the scan read the
# workspace.
STYLE = """/* The vocabulary the markup does not carry. */
:root {
  --brand-ink-9f3: #d92a76;
}
.hero-band-9f3 {
  color: var(--brand-ink-9f3);
}
"""

MARKUP = """<html>
<body>
</body>
</html>
"""

SOURCE = """// Nothing here is a class attribute.
void probe() {}
"""


def write_workspace(root: str) -> None:
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "styles"), exist_ok=True)
    with open(os.path.join(root, "styles", "site.css"), "w") as fh:
        fh.write(STYLE)
    # No class names of its own: everything the popup can offer about classes
    # here has to have come from the style sheet.
    with open(os.path.join(root, "index.html"), "w") as fh:
        fh.write(MARKUP)
    with open(os.path.join(root, "probe.cpp"), "w") as fh:
        fh.write(SOURCE)


def run(binary: str, root: str, cfg: str, path: str, keys: bytes,
        open_delay: float = 1.2):
    """Opens `root` as the workspace, `:e`s `path` inside it, then types `keys`.

    The staged phases are the point: the scan is a worker-thread walk of the
    tree, so the session is given the `settle` window to run it, and the file has
    to be open before a keystroke can be a completion request.
    """
    return run_in_pty(binary, [root], b"", settle=5.0, after=0.6, cols=COLS,
                      rows=ROWS, cfg=cfg, cwd=root,
                      phases=[(0.6, PALETTE), (0.6, b"e " + path.encode()),
                              (0.6, ENTER), (0.5, ESC), (open_delay, keys),
                              (3.0, b"")])


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"web completion probe: SKIP - no binary at {binary}")
        return 2

    root = "/tmp/jot_web_completion_probe"
    write_workspace(root)
    failures: list[str] = []

    # Scene 1: a class name that lives in another file, offered while the class
    # attribute is being typed in the markup.
    screen = run(binary, root, "/tmp/jot_web_completion_probe_cfg1",
                 "index.html", b"<div class=\"hero")
    if dump:
        print(screen.text())
        print("-" * 70)
    view = screen.text()
    print("scene 1: class=\"hero in markup, name declared in styles/site.css")
    if "hero-band-9f3" not in view:
        failures.append("the workspace's class name was not offered in class=\"...\"")
    if "brand-ink-9f3" in view:
        failures.append("a custom property was offered inside a class attribute")
    # The row is tagged as coming from the workspace rather than from a server,
    # which is the only thing that distinguishes it from a server's answer.
    if "Workspace class" not in view:
        failures.append("the offered name is not marked as the workspace's own")

    # Scene 2: a custom property, offered inside var(--) in the style sheet.
    screen = run(binary, root, "/tmp/jot_web_completion_probe_cfg2",
                 "styles/site.css", b"i{color:var(--brand")
    if dump:
        print(screen.text())
        print("-" * 70)
    view = screen.text()
    print("scene 2: var(--brand in the style sheet")
    # The line as typed is `i{color:var(--brand`, so the property is offered past
    # the `--` the buffer already holds: what the popup shows is the bare name.
    if "brand-ink-9f3" not in view:
        failures.append("the workspace's custom property was not offered inside var(--)")

    # Scene 3: the same text in a language that has no class attribute.
    screen = run(binary, root, "/tmp/jot_web_completion_probe_cfg3",
                 "probe.cpp", b"  printf(\"class=\\\"hero")
    if dump:
        print(screen.text())
        print("-" * 70)
    view = screen.text()
    print("scene 3: class=\"hero inside a C++ string literal")
    if "hero-band-9f3" in view:
        failures.append("a class name was offered in a buffer that is not markup")

    if failures:
        for failure in failures:
            print(f"web completion probe: FAIL - {failure}")
        return 1
    print("web completion probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
