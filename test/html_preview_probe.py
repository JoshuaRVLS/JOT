#!/usr/bin/env python3
"""Probe: the HTML preview serves the real tree and reloads on an edit.

A unit test can pin the route table, but the point of this feature is what
actually arrives in a browser, and only a live session can answer that: jot is
driven in a pty (open the file, `:HtmlPreview`), and while it keeps running this
probe speaks HTTP to the port it bound.

What that buys, per scene:

  * `GET /index.html` returns the buffer's text with the live-reload client
    injected before `</body>` - the page the browser gets,
  * `GET /styles/site.css` returns the file from disk, unmodified: the relative
    reference in the page resolves, which is the whole reason the server is
    rooted at the tree instead of holding one page,
  * `GET /../secret.txt` is refused (the file root is a boundary, not a prefix),
  * an edit that was never saved shows up in the served page - the buffer
    overrides the file on disk - and the reload event reaches the open page.

The port is fixed through the config file so the probe knows where to knock;
`open_browser = false` keeps the pty session from trying to launch one.

Usage: test/html_preview_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import http.client
import os
import shutil
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 130, 32

PAGE = """<!doctype html>
<html>
<head>
<link rel="stylesheet" href="styles/site.css">
</head>
<body>
<h1 class="hero-band-9f3">hello</h1>
</body>
</html>
"""

STYLE = ".hero-band-9f3 { color: #d92a76; }\n"

SECRET = "not part of the preview\n"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_workspace(root: str, port: int) -> None:
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "styles"), exist_ok=True)
    with open(os.path.join(root, "index.html"), "w") as fh:
        fh.write(PAGE)
    with open(os.path.join(root, "styles", "site.css"), "w") as fh:
        fh.write(STYLE)
    # Outside the previewed file's directory but inside the workspace, so the
    # traversal case is a real file the server could otherwise hand out.
    with open(os.path.join(root, "secret.txt"), "w") as fh:
        fh.write(SECRET)


def write_config(cfg: str, port: int) -> None:
    os.makedirs(os.path.join(cfg, "configs"), exist_ok=True)
    with open(os.path.join(cfg, "configs", "settings.conf"), "w") as fh:
        fh.write("html_preview_port = %d\n" % port)
        fh.write("html_preview_open_browser = false\n")


def fetch(port: int, path: str, timeout: float = 3.0):
    """GET one path; returns (status, body_text).

    The request target is sent verbatim -- `http.client` will not quietly
    resolve a `..` segment out of the path the way a URL-shaped client can, and
    the whole point of the traversal case is what the *server* does with the raw
    target. Body text is for text types.
    """
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read().decode("utf-8", "replace")
        connection.close()
        return response.status, body
    except Exception as error:  # connection refused while the server is starting/stopping
        return 0, str(error)


class Poller(threading.Thread):
    """Knocks until the preview answers, then keeps sampling what it serves.

    The pty session owns the terminal, so the observations have to be taken from
    another thread while it runs. Every page body the server serves is kept, not
    just the first: the edit the session types arrives as a *later* body, and a
    set of samples is what says the served document really followed the buffer.
    """

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port
        self.stop_flag = threading.Event()
        self.pages: list[str] = []
        self.style = ""
        # Every status the traversal path answered with while the server ran.
        self.traversal_statuses: set[int] = set()
        self.ready = threading.Event()

    @property
    def page(self) -> str:
        return self.pages[0] if self.pages else ""

    def run(self) -> None:
        deadline = time.time() + 20.0
        while time.time() < deadline and not self.stop_flag.is_set():
            status, body = fetch(self.port, "/index.html", timeout=1.0)
            if status == 200:
                if not self.pages:
                    self.pages.append(body)
                self.ready.set()
                break
            time.sleep(0.2)
        if not self.ready.is_set():
            return
        while not self.stop_flag.is_set():
            status, body = fetch(self.port, "/index.html", timeout=1.0)
            if status == 200 and body not in self.pages:
                self.pages.append(body)
            status, body = fetch(self.port, "/styles/site.css", timeout=1.0)
            if status == 200 and "hero-band-9f3" in body:
                self.style = body
            # `/..` climbs out of the served root, so the server has to answer
            # the path itself rather than resolve it. `%2e%2e` is the encoded
            # spelling of the same attempt, which the request parser decodes
            # before the route sees it.
            for attempt in ("/../secret.txt", "/%2e%2e/secret.txt"):
                status, _ = fetch(self.port, attempt, timeout=1.0)
                self.traversal_statuses.add(status)
            time.sleep(0.15)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"html preview probe: SKIP - no binary at {binary}")
        return 2

    root = "/tmp/jot_html_preview_probe"
    cfg = "/tmp/jot_html_preview_probe_cfg"
    shutil.rmtree(cfg, ignore_errors=True)
    port = free_port()
    write_workspace(root, port)
    write_config(cfg, port)

    failures: list[str] = []
    poller = Poller(port)
    poller.start()

    # The session: open the file, start the preview, then make an unsaved edit.
    # `:e` and `:HtmlPreview` both go through the command palette (Ctrl+Shift+P),
    # which stays up after running a command until Escape closes it.
    pal = b"\x1b[112;6u"
    run_in_pty(binary, [root], b"", settle=5.0, after=0.5, cols=COLS, rows=ROWS,
               cfg=cfg, cwd=root,
               phases=[(0.6, pal), (0.6, b"e index.html"), (0.6, b"\r"), (0.5, b"\x1b"),
                       (0.8, pal), (0.8, b"HtmlPreview"), (0.8, b"\r"), (0.5, b"\x1b"),
                       # Then type into the body: an edit that never reaches disk.
                       (1.5, b"xx"), (5.0, b"")])
    poller.stop_flag.set()
    poller.join(timeout=3.0)

    if not poller.ready.is_set():
        failures.append("the preview server never answered on the configured port")
        for failure in failures:
            print(f"html preview probe: FAIL - {failure}")
        return 1

    print("scene 1: GET /index.html")
    if "hero-band-9f3" not in poller.page:
        failures.append("the served page is not the HTML buffer's text")
    if "<link rel=\"stylesheet\" href=\"styles/site.css\">" not in poller.page:
        failures.append("the served page lost its own markup")
    if "/events" not in poller.page or "EventSource" not in poller.page:
        failures.append("the live-reload client was not injected into the page")
    if poller.page.find("EventSource") > poller.page.find("</body>"):
        failures.append("the reload client landed outside the document body")

    print("scene 2: GET /styles/site.css (a relative reference from the page)")
    if "hero-band-9f3" not in poller.style:
        failures.append("the sibling stylesheet was not served from the file root")
    elif "EventSource" in poller.style:
        failures.append("the reload client was injected into a stylesheet")

    print("scene 3: GET /../secret.txt must be refused")
    statuses = poller.traversal_statuses - {0}  # 0 = the server was already down
    if not statuses:
        failures.append("the traversal attempt was never observed while the server ran")
    if 200 in statuses:
        failures.append("a path climbing out of the served root was handed the file")
    if 404 not in statuses:
        failures.append(f"the traversal attempt answered {sorted(statuses)}, not 404")

    print(f"scene 4: an unsaved edit is what the page carries ({len(poller.pages)} bodies seen)")
    # The session typed `xx` into the document and never saved. The file on disk
    # still holds the original, so a page that carries the edit can only have
    # come from the buffer override -- and it has to be one of the *later*
    # bodies, or the page was never reloaded after the keystroke.
    if len(poller.pages) < 2:
        failures.append("the served page never changed after the edit")
    if not any("xx" in page for page in poller.pages):
        failures.append("the unsaved edit never reached the served page")
    with open(os.path.join(root, "index.html")) as fh:
        if "xx" in fh.read():
            failures.append("the probe saved the edit itself, so it proves nothing")

    if dump:
        print("-" * 70)
        for index, page in enumerate(poller.pages):
            print(f"--- body {index} ---")
            print(page)

    if failures:
        for failure in failures:
            print(f"html preview probe: FAIL - {failure}")
        return 1
    print("html preview probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
