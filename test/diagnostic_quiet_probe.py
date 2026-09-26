#!/usr/bin/env python3
"""Probe: fresh findings wait for the typing to pause, and a save paints now.

A unit test can assert on the buffer's diagnostics, but this is a claim about
what the screen does while a hand is on the keyboard: the server's answer to a
keystroke must not paint under the hands, must paint once the typing pauses, and
must paint *at once* when the user saves -- Ctrl+S is the user asking for the
truth, not another thing to wait out.

This drives the real binary against a scripted `clangd` (a shim on PATH) that
publishes a fresh finding for every document it sees and logs what it sent, so
the probe can wait for the server to have *answered* before it looks at the
screen. The quiet window is set wide (6s) so "not yet" and "at once" are
unambiguous rather than a race.

Scenes:
  * open the file, type one character: the server's finding for that change is
    on the wire (the log says so) and still nowhere on screen,
  * keep typing nothing: the finding paints once the pause is over,
  * type, then save: the save's finding paints well inside the quiet window.

Usage: test/diagnostic_quiet_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 100, 24
QUIET_MS = 6000
OPEN_TEXT = "PROBE-OPEN-FINDING"
CHANGE_TEXT = "PROBE-CHANGE-FINDING"
SAVE_TEXT = "PROBE-SAVE-FINDING"

SOURCE = """int broken = 1;
int fine = 2;
int other = 3;
"""

# The scripted server: a full LSP handshake and one finding per notification
# kind, logged as it is published. The three messages sit on three lines -- so
# no finding ever replaces another on screen, and the distinct texts are what
# let the probe tell "the answer to my keystroke" from "the answer to my save".
FAKE_CLANGD = r'''#!/usr/bin/env python3
import json, os, sys

LOG = os.environ["JOT_FAKE_LSP_LOG"]

if "--version" in sys.argv:
    print("fake clangd 1.0.0")
    sys.exit(0)


def send(obj):
    body = json.dumps(obj).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()


def log(entry):
    with open(LOG, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


# A publishDiagnostics replaces the file's whole list, the way a real server's
# does, so every publish carries the findings published so far: the earlier
# lines' messages stay on screen instead of vanishing with the new answer.
findings = []


def publish(uri, line, message):
    log({"event": "publish", "message": message})
    findings.append((line, message))
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
          "params": {"uri": uri, "diagnostics": [
              {"range": {"start": {"line": ln, "character": 0},
                         "end": {"line": ln, "character": 1}},
               "severity": 1, "message": text}
              for ln, text in findings
          ]}})


EMPTY = {
    "textDocument/completion": {"isIncomplete": False, "items": []},
    "textDocument/documentSymbol": [],
    "textDocument/inlayHint": [],
    "textDocument/codeAction": [],
    "textDocument/references": [],
    "textDocument/formatting": [],
}

def read_more():
    chunk = os.read(0, 65536)
    if not chunk:
        sys.exit(0)
    return chunk


pending = b""
while True:
    while b"\r\n\r\n" not in pending:
        pending += read_more()
    header, pending = pending.split(b"\r\n\r\n", 1)
    length = 0
    for line in header.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    while len(pending) < length:
        pending += read_more()
    body, pending = pending[:length], pending[length:]
    msg = json.loads(body)
    method = msg.get("method")

    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {
            "textDocumentSync": {"openClose": True, "change": 1,
                                 "save": {"includeText": False}},
        }}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
    elif method == "exit":
        sys.exit(0)
    elif method == "textDocument/didOpen":
        publish(msg["params"]["textDocument"]["uri"], 0, "PROBE-OPEN-FINDING")
    elif method == "textDocument/didChange":
        publish(msg["params"]["textDocument"]["uri"], 1, "PROBE-CHANGE-FINDING")
    elif method == "textDocument/didSave":
        publish(msg["params"]["textDocument"]["uri"], 2, "PROBE-SAVE-FINDING")
    elif "id" in msg:
        send({"jsonrpc": "2.0", "id": msg["id"],
              "result": EMPTY.get(method)})
'''


class Wait:
    """A bounded wait, so a scene ends without racing the quiet window."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.t0 = None

    def __call__(self, _screen):
        if self.t0 is None:
            self.t0 = time.time()
        return time.time() - self.t0 >= self.seconds


def workspace(tmp: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "configs"))
    os.makedirs(os.path.join(tmp, "bin"))
    os.makedirs(os.path.join(tmp, "data"))
    with open(os.path.join(tmp, "configs", "settings.conf"), "w") as fh:
        fh.write("# jot configuration file\n\n"
                 "lsp_diagnostics_quiet_ms=%d\n" % QUIET_MS)
    shim = os.path.join(tmp, "bin", "clangd")
    with open(shim, "w") as fh:
        fh.write(FAKE_CLANGD)
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IEXEC)
    path = os.path.join(tmp, "probe.cpp")
    with open(path, "w") as fh:
        fh.write(SOURCE)
    return path


def read_log(log_path: str) -> list:
    entries = []
    if os.path.exists(log_path):
        with open(log_path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    return entries


def published(log_path: str, message: str):
    """A phase that holds until the server has put `message` on the wire.

    Waiting on the server's own log is what makes the screen assertion below a
    statement about the hold rather than about latency: the answer exists, and
    the screen still does not show it.
    """
    def phase(_screen):
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if any(e.get("message") == message for e in read_log(log_path)):
                return True
            time.sleep(0.05)
        return False
    return phase


def run_scene(binary: str, tmp: str, keys: bytes, phases, dump: bool, timeout: float = 12.0):
    path = os.path.join(tmp, "probe.cpp")
    # Every scene starts from the same file: a scene that saves would otherwise
    # leave its typed character in the next scene's document.
    with open(path, "w") as fh:
        fh.write(SOURCE)
    log_path = os.path.join(tmp, "server.log")
    if os.path.exists(log_path):
        os.remove(log_path)
    backup = {
        "PATH": os.environ.get("PATH", ""),
        "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME", ""),
        "JOT_FAKE_LSP_LOG": os.environ.get("JOT_FAKE_LSP_LOG", ""),
    }
    os.environ["PATH"] = os.path.join(tmp, "bin") + os.pathsep + backup["PATH"]
    # A managed clangd install must not shadow the shim.
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "data")
    os.environ["JOT_FAKE_LSP_LOG"] = log_path
    try:
        screen = run_in_pty(binary, [path], keys, settle=3.0, after=1.0,
                            cfg=tmp, cwd=tmp, cols=COLS, rows=ROWS,
                            phases=phases, until_timeout=timeout)
    finally:
        for key, value in backup.items():
            os.environ[key] = value
    if dump:
        print(screen.text())
        print("-" * 70)
        for entry in read_log(log_path):
            print(entry)
        print("-" * 70)
    return screen, read_log(log_path)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("diagnostic quiet probe: SKIP - no binary at %s" % binary)
        return 2

    tmp = "/tmp/jot_diagnostic_quiet_probe"
    workspace(tmp)
    failures = []

    # Scene 1: type one character. The server answers it -- the log says so --
    # and the answer must stay off the screen while the hand is still down.
    # The keystroke goes in a beat after the first finding paints: the
    # start-up handshake (terminal queries, the server attaching) is the one
    # window where a key can land before the buffer owns the keyboard.
    screen, log = run_scene(
        binary, tmp, b"",
        [(0.0, lambda s: OPEN_TEXT in s.text()),
         (0.8, b"x"),
         (0.0, published(os.path.join(tmp, "server.log"), CHANGE_TEXT)),
         (0.0, Wait(1.0))],
        dump)
    if OPEN_TEXT not in screen.text():
        failures.append("scene 1: the first finding never appeared (the server "
                        "or the attach is the problem, not the hold)")
    if not any(e.get("message") == CHANGE_TEXT for e in log):
        failures.append("scene 1: the keystroke never reached the server")
    if CHANGE_TEXT in screen.text():
        failures.append("scene 1: the finding for the keystroke painted under "
                        "the hands, %dms into a %dms quiet window"
                        % (1000, QUIET_MS))

    # Scene 2: the same keystroke, then no further input: the finding paints
    # once the pause is over.
    screen, log = run_scene(
        binary, tmp, b"",
        [(0.0, lambda s: OPEN_TEXT in s.text()),
         (0.8, b"x"),
         (0.0, lambda s: CHANGE_TEXT in s.text())],
        dump, timeout=20.0)
    if CHANGE_TEXT not in screen.text():
        failures.append("scene 2: the finding never painted after the typing "
                        "paused")

    # Scene 3: type, then save inside the quiet window: the save's own finding
    # paints well before the pause would have released it.
    screen, log = run_scene(
        binary, tmp, b"",
        [(0.0, lambda s: OPEN_TEXT in s.text()),
         (0.8, b"x"),
         (0.0, published(os.path.join(tmp, "server.log"), CHANGE_TEXT)),
         (0.0, b"\x13"),  # save, right behind the keystroke
         (0.0, Wait(1.2))],
        dump)
    if not any(e.get("message") == SAVE_TEXT for e in log):
        failures.append("scene 3: the save never reached the server")
    if SAVE_TEXT not in screen.text():
        failures.append("scene 3: the save's finding did not paint: a save must "
                        "not wait out the %dms quiet window" % QUIET_MS)

    if failures:
        for failure in failures:
            print("diagnostic quiet probe: FAIL - %s" % failure)
        return 1
    print("diagnostic quiet probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
