#!/usr/bin/env python3
"""Probe: a mouse hover only arms when the pointer rests on a line of text.

Hovering the blank rows below a short file's last line used to raise the
diagnostic/hover card somewhere the pointer was not. The row mapping feeds it:
a row past the end of the buffer is stepped back to the last visible line so a
*click* down there still lands on the file, and the hover path reused that line
as the position to ask about -- through a stale second mapping of the same row
it asked about line 0, with the last line's column. Either way the pointer is on
no text, and no request may go out.

This drives the real binary against a scripted `clangd` (a shim on PATH) over a
pty. The shim publishes a warning on line 0 and answers every hover with the
position it was asked about, so the request is readable in its log and the
card's text on the screen. A pointer below the last line, on the same column as
a token, must leave both empty; the same pointer on the token must fill both,
warning leading the card -- that is the "Warning:" UI the report saw over empty
space.

Usage: test/mouse_hover_probe.py [path-to-jot] [--dump]
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

COLS, ROWS = 110, 24
WARN = "WARN-AT-TOP"

# Line 0 and the last line carry a token at the same columns, so a hover armed
# on the wrong row still finds a word to ask about.
SOURCE = """int alpha_top = 1;
int beta = 2;
int gamma = 3;
int delta = 4;
int omega_bottom = 5;
"""

# The scripted server: a handshake, a warning on line 0, and a hover answer that
# names the position it was asked about, so the editor's request is visible both
# in the log and in the popup.
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


def publish(uri, version):
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
          "params": {"uri": uri, "version": version, "diagnostics": [{
              "range": {"start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 32}},
              "severity": 2,
              "message": "WARN-AT-TOP",
          }]}})


EMPTY = {
    "textDocument/completion": {"isIncomplete": False, "items": []},
    "textDocument/documentSymbol": [],
    "textDocument/inlayHint": [],
    "textDocument/codeAction": [],
    "textDocument/references": [],
    "textDocument/formatting": [],
}

def read_more():
    # os.read, not buffered read(n): the latter blocks until it collects n
    # bytes, and a quiet client never sends that much.
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
            "hoverProvider": True,
        }}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
    elif method == "exit":
        sys.exit(0)
    elif method == "textDocument/didOpen":
        td = msg["params"]["textDocument"]
        log({"method": "didOpen", "version": td["version"]})
        publish(td["uri"], td["version"])
    elif method == "textDocument/hover":
        pos = msg["params"]["position"]
        log({"method": "hover", "line": pos["line"], "character": pos["character"]})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "contents": {"kind": "plaintext",
                         "value": "HOVER-%d-%d" % (pos["line"], pos["character"])}}})
    elif method == "textDocument/didChange":
        log({"method": "didChange"})
    elif method == "textDocument/didSave":
        log({"method": "didSave"})
    elif "id" in msg:
        send({"jsonrpc": "2.0", "id": msg["id"], "result": EMPTY.get(method)})
'''


def workspace(tmp: str) -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "bin"))
    os.makedirs(os.path.join(tmp, "data"))
    shim = os.path.join(tmp, "bin", "clangd")
    with open(shim, "w") as fh:
        fh.write(FAKE_CLANGD)
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IEXEC)
    path = os.path.join(tmp, "hover.cpp")
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


def until_log(log_path: str, want: str):
    """A phase that holds until the server log holds a `want` entry."""
    def phase(_screen):
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if any(e["method"] == want for e in read_log(log_path)):
                return True
            time.sleep(0.05)
        return False
    return phase


def motion(col: int, row: int) -> bytes:
    """SGR motion with no button on a 0-based screen cell (plain hover)."""
    return b"\x1b[<35;%d;%dM" % (col + 1, row + 1)


def cell_of(screen, needle: str):
    """The 0-based (screen_col, row) of `needle`, or None."""
    for row, line in enumerate(screen.text().split("\n")):
        idx = line.find(needle)
        if idx >= 0:
            return idx, row
    return None


def logical_col(screen, needle: str) -> int:
    """`needle`'s logical column: its cell minus the code area's first cell.

    The code area starts where the row's `int` does -- the gutter in front of it
    carries the line number -- so this reads the same column the buffer holds.
    """
    for line in screen.text().split("\n"):
        idx = line.find(needle)
        if idx >= 0:
            return idx - line.find("int")
    return -1


def run_scene(binary: str, tmp: str, phases, dump: bool):
    path = workspace(tmp)
    log_path = os.path.join(tmp, "server.log")
    backup = {
        "PATH": os.environ.get("PATH", ""),
        "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME", ""),
        "JOT_FAKE_LSP_LOG": os.environ.get("JOT_FAKE_LSP_LOG", ""),
    }
    os.environ["PATH"] = os.path.join(tmp, "bin") + os.pathsep + backup["PATH"]
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "data")
    os.environ["JOT_FAKE_LSP_LOG"] = log_path
    try:
        screen = run_in_pty(binary, [path], b"", settle=4.0, after=1.0, cfg=tmp,
                            cwd=tmp, cols=COLS, rows=ROWS, phases=phases,
                            until_timeout=20.0)
    finally:
        for key, value in backup.items():
            os.environ[key] = value
    entries = read_log(log_path)
    if dump:
        print(screen.text())
        print("-" * 70)
        for entry in entries:
            print(entry)
        print("-" * 70)
    return screen, entries, path


def hovers(entries: list):
    return [e for e in entries if e["method"] == "hover"]


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("mouse hover probe: SKIP - no binary at %s" % binary)
        return 2

    failures = []

    # Map the rendered grid first (the sidebar and gutter decide where the code
    # area is, so nothing can be assumed), and read the empty rows off the
    # picture: the renderer paints `~` on the rows past the file's last line.
    tmp = "/tmp/jot_mouse_hover_probe_base"
    screen, entries, _path = run_scene(
        binary, tmp,
        [(0.0, until_log(os.path.join(tmp, "server.log"), "didOpen"))],
        dump)
    cell = cell_of(screen, "omega_bottom")
    top_cell = cell_of(screen, "alpha_top")
    if cell is None or top_cell is None:
        print("mouse hover probe: FAIL - the probe file is not on screen")
        return 1
    token_col, token_row = cell
    token_line_col = logical_col(screen, "omega_bottom")
    top_line_col = logical_col(screen, "alpha_top")
    blank_row = token_row + 4
    rows = screen.text().split("\n")
    if blank_row >= len(rows) or "~" not in rows[blank_row]:
        failures.append("the base screen has no blank row at %d to hover "
                        "(row %r)" % (blank_row, rows[blank_row] if blank_row < len(rows) else ""))
    print("mouse hover probe: token at cell (%d,%d) logical col %d, blank row %d"
          % (token_col, token_row, token_line_col, blank_row))

    # Scene 1: the pointer rests in the blank rows on the token's column. The
    # row resolves to a line for the click's sake, but the pointer is on no
    # text: no hover request may go out, so nothing can pop up.
    tmp = "/tmp/jot_mouse_hover_probe_blank"
    screen, entries, _path = run_scene(
        binary, tmp,
        [(0.0, until_log(os.path.join(tmp, "server.log"), "didOpen")),
         (0.0, motion(token_col, blank_row)),
         (2.0, b"")],
        dump)
    got = hovers(entries)
    text = screen.text()
    print("mouse hover probe: blank rows     -> requests %s, popup %s"
          % ([(e["line"], e["character"]) for e in got],
             "yes" if "HOVER-" in text else "no"))
    if got:
        failures.append("scene 1: hovering the blank rows asked for %s"
                        % [(e["line"], e["character"]) for e in got])
    if "HOVER-" in text:
        failures.append("scene 1: a hover popup is on screen over the blank rows")
    # The renderer paints the diagnostic's message inline next to its line, so
    # the raw text is always somewhere on screen. The card is the copy that
    # leads with the severity (`Warning: ...`), which only the hover path makes.
    if "Warning: " + WARN in text:
        failures.append("scene 1: the warning card is on screen over the blank rows")

    # Scene 2: the same pointer on the file's own token. The request must name
    # that line's column, and the card must carry the warning the server
    # published there -- the text the report saw over empty space.
    tmp = "/tmp/jot_mouse_hover_probe_token"
    screen, entries, _path = run_scene(
        binary, tmp,
        [(0.0, until_log(os.path.join(tmp, "server.log"), "didOpen")),
         (0.0, motion(top_cell[0], top_cell[1])),
         (2.0, b"")],
        dump)
    got = hovers(entries)
    text = screen.text()
    print("mouse hover probe: the token      -> requests %s, popup %s, warning %s"
          % ([(e["line"], e["character"]) for e in got],
             "yes" if "HOVER-0-%d" % top_line_col in text else "no",
             "yes" if WARN in text else "no"))
    if not any(e["line"] == 0 and e["character"] == top_line_col for e in got):
        failures.append("scene 2: resting on the token did not ask about it "
                        "(got %s, want (0,%d))"
                        % ([(e["line"], e["character"]) for e in got], top_line_col))
    if "HOVER-0-%d" % top_line_col not in text:
        failures.append("scene 2: the token's hover card is not on screen")
    if "Warning: " + WARN not in text:
        failures.append("scene 2: the warning does not lead the token's card")

    # Scene 3: the last line's token, so the correct row is pinned for a line
    # other than 0 -- line 0 is where the stale mapping had sent everything.
    tmp = "/tmp/jot_mouse_hover_probe_last"
    screen, entries, _path = run_scene(
        binary, tmp,
        [(0.0, until_log(os.path.join(tmp, "server.log"), "didOpen")),
         (0.0, motion(token_col, token_row)),
         (2.0, b"")],
        dump)
    got = hovers(entries)
    text = screen.text()
    print("mouse hover probe: last line      -> requests %s, popup %s"
          % ([(e["line"], e["character"]) for e in got],
             "yes" if "HOVER-%d-%d" % (4, token_line_col) in text else "no"))
    if not any(e["line"] == 4 and e["character"] == token_line_col for e in got):
        failures.append("scene 3: resting on the last line's token did not ask "
                        "about it (got %s, want (4,%d))"
                        % ([(e["line"], e["character"]) for e in got], token_line_col))
    if "HOVER-%d-%d" % (4, token_line_col) not in text:
        failures.append("scene 3: the last line's hover card is not on screen")

    if failures:
        for failure in failures:
            print("mouse hover probe: FAIL - %s" % failure)
        return 1
    print("mouse hover probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
