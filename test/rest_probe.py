#!/usr/bin/env python3
"""Probe: a `.http` request file runs end to end (variables -> curl -> response).

The unit tests pin the request-file grammar, the variable resolution and the
response formatting without a network. What only a real session proves is the
whole loop at once: `:rest` picks the request under the cursor out of the open
buffer, `{{base}}` is substituted from the file's own `@var`, a real curl
reaches a real server on a real port, and the answer lands in the `[Response]`
tab -- pretty-printed and reported. A named request (`:rest echo`) must reach
its own request with its body, and an unresolved `{{var}}` must refuse the run
rather than send it wrong.

Usage: test/rest_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary, or no curl).
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 150, 34

# Ctrl+Shift+P opens the command palette; Enter runs what is typed in it. A run
# leaves the response tab focused, so every scene is its own session.
PALETTE = b"\x1b[112;6u"
ENTER = b"\r"
ESC = b"\x1b"

# Unlike anything the editor would invent on its own: seeing it on screen can
# only mean the body came back from this probe's server.
TAG = "jot-rest-9f3"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the probe's own output readable
        pass

    def _reply(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply({"status": "pong", "tag": TAG})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        got = json.loads(self.rfile.read(length) or b"{}")
        self._reply({"status": "echo", "tag": TAG, "got": got})


def write_workspace(root: str, port: int) -> None:
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    with open(os.path.join(root, "api.http"), "w") as fh:
        fh.write(
            "@base = http://127.0.0.1:%d\n"
            "\n"
            "### ping\n"
            "GET {{base}}/ping\n"
            "Accept: application/json\n"
            "\n"
            "### echo\n"
            "POST {{base}}/echo\n"
            "Content-Type: application/json\n"
            "\n"
            '{"hello": "%s"}\n'
            "\n"
            "### broken\n"
            "GET {{nope}}/x\n" % (port, TAG)
        )


def run(binary: str, root: str, cfg: str, keys: bytes, tail: float = 3.0):
    """Opens the workspace and api.http, then types `keys` into the palette.

    The caret sits at 0:0 -- in the vars preamble, which runs the request it
    feeds. The trailing settle is the point of the shape: the request is a
    worker-thread curl, so the screen is only read once the answer has landed.
    `tail` is shorter for a run that never reaches the network, whose report is
    a toast (they live three seconds) rather than a tab's content.
    """
    return run_in_pty(binary, [root], b"", settle=5.0, after=0.6, cols=COLS,
                      rows=ROWS, cfg=cfg, cwd=root,
                      phases=[(0.6, PALETTE), (0.6, b"e api.http"), (0.6, ENTER),
                              (0.5, ESC), (1.0, PALETTE), (0.6, keys),
                              (0.6, ENTER), (tail, b"")])


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"rest probe: SKIP - no binary at {binary}")
        return 2
    if shutil.which("curl") is None:
        print("rest probe: SKIP - curl is not on PATH")
        return 2

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    root = "/tmp/jot_rest_probe"
    write_workspace(root, port)
    failures: list[str] = []

    # Scene 1: `:rest` with the caret in the preamble -- the request below it,
    # with `{{base}}` resolved from the file's `@base`.
    screen = run(binary, root, "/tmp/jot_rest_probe_cfg1", b"rest")
    if dump:
        print(screen.text())
        print("-" * 70)
    view = screen.text()
    print("scene 1: :rest at the cursor (the request below the vars preamble)")
    if f"GET http://127.0.0.1:{port}/ping" not in view:
        failures.append("the cursor's request did not run with {{base}} resolved")
    if '"status": "pong"' not in view:
        failures.append("the ping body never came back from the server")
    if TAG not in view:
        failures.append("the response tab does not show the served body")

    # Scene 2: a named request, with a JSON body of its own.
    screen = run(binary, root, "/tmp/jot_rest_probe_cfg2", b"rest echo")
    if dump:
        print(screen.text())
        print("-" * 70)
    view = screen.text()
    print("scene 2: :rest echo (a named request with a JSON body)")
    if f"POST http://127.0.0.1:{port}/echo" not in view:
        failures.append("the named request did not run")
    if '"status": "echo"' not in view:
        failures.append("the echo body never came back from the server")

    # Scene 3: an unresolved variable refuses the run -- nothing is sent.
    screen = run(binary, root, "/tmp/jot_rest_probe_cfg3", b"rest broken", tail=1.0)
    if dump:
        print(screen.text())
        print("-" * 70)
    view = screen.text()
    print("scene 3: :rest broken (an unresolved {{var}} must refuse the run)")
    if "unresolved" not in view or "{{nope}}" not in view:
        failures.append("the missing variable was not reported")
    if '"status"' in view or "200 OK" in view:
        failures.append("a request with an unresolved variable was sent anyway")

    server.shutdown()

    if failures:
        for failure in failures:
            print(f"rest probe: FAIL - {failure}")
        return 1
    print("rest probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
