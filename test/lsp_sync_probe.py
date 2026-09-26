#!/usr/bin/env python3
"""Probe: the editor's document sync keeps a language server's draft honest.

Phantom diagnostics -- the server swearing a `;` is missing from a line that
plainly has one, until the user retypes a character -- are a document-sync bug,
not a parser bug: the server is parsing text the editor never sent, or the
findings that would clear the error are thrown away on arrival. Three rules
keep that honest, and none of them is visible in a unit test of the parsers:

  * a save flushes the edits the change debounce had not sent yet (didSave's
    text field is informational; only didChange updates a server's document),
  * a clearing publishDiagnostics one version behind the document is still
    applied -- servers coalesce parses, so it can be the freshest word there is,
  * a save with no edits since sends no didChange at all.

This drives the real binary against a scripted `clangd` (a shim on PATH) that
logs everything it receives and publishes findings one version behind the
document, the way a coalescing server does. `XDG_DATA_HOME` points inside the
workspace so a managed clangd install cannot shadow the shim.

Scenes:
  * type, then save inside the debounce window: the server's draft must end up
    holding the text the file on disk holds,
  * the phantom finding must clear by itself when the one-version-behind
    clearing publish lands -- no retyping a character,
  * a save with no edits must put nothing on the wire but didSave.

Usage: test/lsp_sync_probe.py [path-to-jot] [--dump]
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
PHANTOM = "PHANTOM-MISSING-SEMI"
# The edit is typed in characters that trigger no completion request: a
# completion request flushes the document before it goes out, which would
# mask the save path under test. `;` is exactly the character from the bug
# report -- the phantom "missing ;" lands where the user just typed one.
MARKER = ";="

# The scripted server: a full LSP handshake, a log of every document
# notification, and the one behavior under test -- findings published a
# version behind the document they describe.
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


def publish(uri, version, message):
    diags = []
    if message:
        diags = [{
            "range": {"start": {"line": 0, "character": 0},
                      "end": {"line": 0, "character": 1}},
            "severity": 1,
            "message": message,
        }]
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
          "params": {"uri": uri, "version": version, "diagnostics": diags}})


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
        }}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
    elif method == "exit":
        sys.exit(0)
    elif method == "textDocument/didOpen":
        td = msg["params"]["textDocument"]
        log({"method": "didOpen", "version": td["version"], "text": td["text"]})
        publish(td["uri"], td["version"], "PHANTOM-MISSING-SEMI")
    elif method == "textDocument/didChange":
        td = msg["params"]["textDocument"]
        text = msg["params"]["contentChanges"][0]["text"]
        log({"method": "didChange", "version": td["version"], "text": text})
        # Findings for the *previous* draft: what a server that coalesced its
        # parses publishes -- a version behind the document it describes.
        publish(td["uri"], td["version"] - 1, "")
    elif method == "textDocument/didSave":
        log({"method": "didSave",
             "text": msg["params"].get("text")})
    elif "id" in msg:
        send({"jsonrpc": "2.0", "id": msg["id"],
              "result": EMPTY.get(method)})
'''


def workspace(tmp: str, name: str = "sync.cpp") -> str:
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "configs"))
    os.makedirs(os.path.join(tmp, "bin"))
    os.makedirs(os.path.join(tmp, "data"))
    with open(os.path.join(tmp, "configs", "settings.conf"), "w") as fh:
        # A wide debounce makes the save race deterministic: the save always
        # lands before the change flush would have fired.
        fh.write("# jot configuration file\n\nlsp_change_debounce_ms=1000\n")
    shim = os.path.join(tmp, "bin", "clangd")
    with open(shim, "w") as fh:
        fh.write(FAKE_CLANGD)
    os.chmod(shim, os.stat(shim).st_mode | stat.S_IEXEC)
    path = os.path.join(tmp, name)
    with open(path, "w") as fh:
        fh.write("int x = 1;\n")
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


def draft(entries: list) -> str:
    """The text the server's document holds: the last didOpen/didChange wins.

    didSave's text does not count -- a server's document is only ever updated
    by didChange, and that distinction is the whole point of the scenes.
    """
    text = None
    for entry in entries:
        if entry["method"] in ("didOpen", "didChange"):
            text = entry["text"]
    return text if text is not None else ""


def until_log(log_path: str, predicate):
    """A phase that holds until the server log reaches the wanted state."""
    def phase(_screen):
        deadline = time.time() + 25.0
        while time.time() < deadline:
            if predicate(read_log(log_path)):
                return True
            time.sleep(0.05)
        return False
    return phase


def logged(name: str):
    return lambda entries: any(e["method"] == name for e in entries)


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
        screen = run_in_pty(binary, [path], b"", settle=3.0, after=1.5,
                            cfg=tmp, cwd=tmp, cols=COLS, rows=ROWS,
                            phases=phases, until_timeout=25.0)
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


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("lsp sync probe: SKIP - no binary at %s" % binary)
        return 2

    failures = []

    # Scene 1: an edit saved inside the debounce window must reach the server's
    # document. The save sent didSave only, and didSave's text is
    # informational: the server kept parsing the pre-edit draft and reported a
    # missing `;` at the line just typed. The edit and the save travel in one
    # burst, and the debounce is a wide 1000ms, so the save deterministically
    # overtakes the change flush.
    #
    # The typed marker deliberately avoids the completion-trigger characters:
    # a completion request flushes the document on its way out, and that path
    # would carry the edit even when the save path does not.
    tmp = "/tmp/jot_lsp_sync_probe_1"
    screen, entries, path = run_scene(
        binary, tmp,
        [(0.0, until_log(os.path.join(tmp, "server.log"), logged("didOpen"))),
         (0.0, MARKER.encode() + b"\x13"),  # type the edit, save at once
         (0.0, until_log(os.path.join(tmp, "server.log"), logged("didSave")))],
        dump)
    with open(path) as fh:
        on_disk = fh.read()
    if MARKER not in draft(entries):
        failures.append("scene 1: the server's draft never got the saved edit")
    # Trailing newlines aside -- the buffer's text is its lines joined, with
    # no final newline, while the file on disk ends in one -- the two must be
    # the same document.
    if draft(entries).rstrip("\n") != on_disk.rstrip("\n"):
        failures.append("scene 1: the server's draft disagrees with the file "
                        "on disk (draft %r vs %r)" % (draft(entries), on_disk))
    if not logged("didSave")(entries):
        failures.append("scene 1: the save never reached the server")
    changes = [i for i, e in enumerate(entries) if e["method"] == "didChange"]
    saves = [i for i, e in enumerate(entries) if e["method"] == "didSave"]
    if changes and saves and changes[0] > saves[0]:
        failures.append("scene 1: didSave went out before the didChange that "
                        "carries the edit")

    # Scene 2: the phantom finding must clear by itself. The server publishes
    # the clear one version behind the document; the old exact-version rule
    # discarded it and the error stayed on screen until the user retyped.
    tmp = "/tmp/jot_lsp_sync_probe_2"
    screen, entries, path = run_scene(
        binary, tmp,
        [(0.0, lambda s: PHANTOM in s.text()),  # the finding is on screen
         (0.0, b";"),                           # an edit: the fix for it
         (0.0, until_log(os.path.join(tmp, "server.log"), logged("didChange"))),
         (0.0, lambda s: PHANTOM not in s.text())],
        dump)
    if not logged("didChange")(entries):
        failures.append("scene 2: the edit never reached the server")
    if PHANTOM in screen.text():
        failures.append("scene 2: the phantom finding is still on screen "
                        "after the clearing publish landed")

    # Scene 3: a save with no edits since puts didSave on the wire and nothing
    # else -- no invented changes.
    tmp = "/tmp/jot_lsp_sync_probe_3"
    screen, entries, path = run_scene(
        binary, tmp,
        [(0.0, until_log(os.path.join(tmp, "server.log"), logged("didOpen"))),
         (0.0, b"\x13"),
         (0.0, until_log(os.path.join(tmp, "server.log"), logged("didSave")))],
        dump)
    changes = [e for e in entries if e["method"] == "didChange"]
    if changes:
        failures.append("scene 3: a save with no edits sent %d didChange(s)"
                        % len(changes))
    if not logged("didSave")(entries):
        failures.append("scene 3: the save never reached the server")

    if failures:
        for line in failures:
            print("lsp sync probe: FAIL - %s" % line)
        return 1
    print("lsp sync probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
