#!/usr/bin/env python3
"""Probe: the markdown preview reaches the browser without waiting on the CDN.

The preview page carries its whole document and its own stylesheet inline, so
the editor already holds everything the browser needs the moment
`:MarkdownPreview` returns. What it must not do is hold its first paint on a
remote asset: a plain script in the head stops the HTML parser before the body
even exists, and a stylesheet in the head is render blocking, so starting the
preview meant waiting for cdn.jsdelivr.net (blank page until it answered). A
unit test pins the tags the template emits; only the bytes a running server
hands a browser say what the browser actually gets, so this probe reads them off
the port the session bound:

  * the served page is the rendered document with the built-in sheet inline, so
    no network byte is needed to see it,
  * nothing ahead of the document in the page is a remote tag the browser has to
    fetch before it paints, while the CDN tags the enabled options ask for are
    still emitted (the fix is "off the critical path", not "stop shipping
    them"),
  * an edit that never reached disk is in a later body, so the page is this
    session's and the live path works.

The port is fixed through the config file so the probe knows where to knock, and
`browser = none` keeps the pty session from launching a browser.

The start time is measured and printed but not asserted: driving the command
through a pty perturbs keystroke dispatch here (a ~1 s delay appears in about a
third of runs, for a trivial palette command too), so it cannot gate the run.
The checks above are what this probe is for; they fail if the head goes back to
blocking, which is the regression that made the page slow to appear.

Usage: test/markdown_preview_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import http.client
import os
import re
import shutil
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 130, 32

# Rendered from the buffer by the editor, so finding it in a served page says the
# page came from this session rather than from anything on disk.
MARKER = "probe-heading-4c1"
# Typed into the buffer and never saved: only a live page can carry this.
EDIT = "live-edit-7c3"

DOC = "# %s\n\nsome prose\n\n```python\nprint(1)\n```\n" % MARKER

# Every tag a browser has to look at, in document order.
TAG = re.compile(r"<(script|link)\b[^>]*>", re.IGNORECASE)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_workspace(root: str) -> str:
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    path = os.path.join(root, "note.md")
    with open(path, "w") as fh:
        fh.write(DOC)
    return path


def write_config(cfg: str, port: int) -> None:
    os.makedirs(os.path.join(cfg, "configs"), exist_ok=True)
    with open(os.path.join(cfg, "configs", "settings.conf"), "w") as fh:
        fh.write("markdown_preview_port = %d\n" % port)
        fh.write("markdown_preview_browser = none\n")


def fetch(port: int, path: str = "/", timeout: float = 1.0):
    """GET one path; returns (status, body_text). 0 is "not answering yet"."""
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read().decode("utf-8", "replace")
        connection.close()
        return response.status, body
    except Exception:  # connection refused while the server is starting/stopping
        return 0, ""


def remote_blockers(page: str):
    """The tags before the document that a browser must fetch before painting.

    The walk a browser does, done on the bytes the server sent: the HTML parser
    stops at a script with a src and no async/defer, so nothing after it - the
    body included - exists until that fetch finishes, and a stylesheet in the
    head is render blocking however it is spelled. Returns None when the page has
    no document in it at all, which is its own failure.
    """
    content_at = page.find('id="content"')
    if content_at < 0:
        return None
    blockers = []
    for match in TAG.finditer(page[:content_at]):
        tag = match.group(0)
        if match.group(1).lower() == "script":
            if re.search(r"\ssrc\s*=", tag, re.I) and not re.search(r"\b(async|defer)\b", tag, re.I):
                blockers.append(tag)
        elif re.search(r"rel\s*=\s*\"stylesheet\"", tag, re.I):
            if not re.search(r"\smedia\s*=", tag, re.I):
                blockers.append(tag)
    return blockers


class Poller(threading.Thread):
    """Knocks until the preview answers, then keeps sampling what it serves.

    The pty session owns the terminal, so the observations have to be taken from
    another thread while it runs. Every body the server serves is kept, not just
    the first: the edit the session types arrives as a *later* body, and a set of
    samples is what says the served page really followed the buffer.
    """

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port
        self.stop_flag = threading.Event()
        self.ready = threading.Event()
        self.first_at = 0.0
        self.bodies: list[str] = []

    @property
    def page(self) -> str:
        return self.bodies[0] if self.bodies else ""

    def run(self) -> None:
        deadline = time.time() + 20.0
        while time.time() < deadline and not self.stop_flag.is_set():
            status, body = fetch(self.port, "/", timeout=0.5)
            if status == 200:
                self.first_at = time.time()
                self.bodies.append(body)
                self.ready.set()
                break
            time.sleep(0.005)
        if not self.ready.is_set():
            return
        while not self.stop_flag.is_set():
            status, body = fetch(self.port, "/", timeout=1.0)
            if status == 200 and body != self.bodies[-1]:
                self.bodies.append(body)
                if len(self.bodies) > 16:
                    break
            time.sleep(0.05)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"markdown preview probe: SKIP - no binary at {binary}")
        return 2

    root = "/tmp/jot_markdown_preview_probe"
    cfg = "/tmp/jot_markdown_preview_probe_cfg"
    shutil.rmtree(cfg, ignore_errors=True)
    port = free_port()
    path = write_workspace(root)
    write_config(cfg, port)

    failures: list[str] = []
    poller = Poller(port)
    poller.start()

    sent: dict[str, float] = {}

    def mark(_screen) -> bool:
        # Runs immediately before the key that runs the command.
        sent["at"] = time.time()
        return True

    # The file is the argument, so the session opens on it; `:MarkdownPreview`
    # goes through the command palette (Ctrl+Shift+P), which stays up after
    # running a command until Escape closes it. Then `i` types an edit into the
    # buffer that is never saved.
    pal = b"\x1b[112;6u"
    run_in_pty(binary, [path], b"", settle=5.0, after=0.5, cols=COLS, rows=ROWS,
               cfg=cfg, cwd=root,
               phases=[(0.6, pal), (0.6, b"MarkdownPreview"), (0.4, mark), (0.0, b"\r"),
                       (0.5, b"\x1b"), (1.0, b"i" + EDIT.encode() + b"\x1b"), (2.5, b"")])
    poller.stop_flag.set()

    if not poller.ready.is_set():
        print("markdown preview probe: FAIL - the preview server never answered "
              "on the configured port")
        return 1
    if dump:
        print("-" * 70)
        print(poller.page)

    print("scene 1: the served page is the rendered document")
    page = poller.page
    if MARKER not in page:
        failures.append("the served page does not carry the rendered document")
    if "<style>" not in page:
        failures.append("the built-in stylesheet is not inline in the served page")

    print("scene 2: the first paint does not wait on the network")
    blockers = remote_blockers(page)
    if blockers is None:
        failures.append("the served page has no document in it")
    elif blockers:
        failures.append("the page must fetch %r before its document can paint" % (blockers,))
    # Non-vacuity: the libraries are still shipped, just off the critical path.
    if "highlight.min.js" not in page:
        failures.append("the enabled CDN library is gone from the page")
    elif '<script async src="https://' not in page:
        failures.append("the CDN library is still a parser-blocking script")

    print(f"scene 3: the buffer's edits reach the page ({len(poller.bodies)} bodies seen)")
    if not any(EDIT in body for body in poller.bodies):
        failures.append("an edit that was never saved never reached the served page")
    with open(path) as fh:
        if EDIT in fh.read():
            failures.append("the probe saved the edit itself, so it proves nothing")

    # Reported, not asserted: the harness perturbs keystroke dispatch here (see
    # the module docstring), so a budget on this number would flake rather than
    # catch anything.
    print("  start: %.3f s (informational)" % (poller.first_at - sent.get("at", poller.first_at)))

    if failures:
        for failure in failures:
            print(f"markdown preview probe: FAIL - {failure}")
        return 1
    print("markdown preview probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
