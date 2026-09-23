#!/usr/bin/env python3
"""Probe: the AI assistant against a real provider on a real port.

test_ai.cpp pins the pieces that decide bytes (the JSON codec, the SSE frames,
the wire payload, the transcript<->buffer round trip) inside a stubbed Lua
state. What only a real session proves is the loop around them: the chat buffer
opens as a tab, the prompt typed into it is read back out, `jot.job.capture`
runs curl on a worker thread, the reply is appended while it streams in, and
the adapter's key and model are what actually left the process.

Three scenes:
  * chat: prompt -> send -> the answer streams into the buffer, and the server's
    request carried the configured model, the environment token and the prompt;
  * provider error: a 401 with a JSON body reports the provider's own message
    and leaves the prompt in place to retry;
  * inline: a selection rewritten through a diff preview, accepted with `y`,
    which replaces the line in the buffer.

Usage: test/ai_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary, or no curl).
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

COLS, ROWS = 160, 34

# Ctrl+Shift+P opens the command palette; Enter runs what is typed in it.
PALETTE = b"\x1b[112;6u"
ENTER = b"\r"
# Alt+Shift+A is the assistant's key family, S sends the prompt in the chat.
SEND = b"\x1bAS"
# Ctrl+Shift+L selects the line under the caret.
SELECT_LINE = b"\x1b[108;6u"

TOKEN = "probe-token-7c1"
MODEL = "probe-model"
# Unlike anything the editor would invent on its own: seeing it on screen can
# only mean these bytes came back from the probe's server.
ANSWER = "the fold index is validated by a checksum"
REPLACEMENT = "int replacement = 2;"
PROMPT = "why does the fold index rescan please"

REQUESTS: list[dict] = []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the probe's own output readable
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) or b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        prompt = ""
        for message in body.get("messages", []):
            if message.get("role") == "user":
                prompt = message.get("content", "")
        REQUESTS.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
                "prompt": prompt,
            }
        )

        if "failme" in prompt:
            payload = json.dumps({"error": {"message": "bad key, go away"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ANSWER.split(" "):
                frame = json.dumps({"choices": [{"delta": {"content": chunk + " "}}]})
                self.wfile.write(b"data: " + frame.encode() + b"\n\n")
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        # A non-streaming caller (the inline assistant) gets one whole answer.
        content = REPLACEMENT if "```" in prompt else ANSWER
        payload = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def write_workspace(root: str, cfg: str, port: int, source: str = "") -> str:
    """A workspace with the assistant pointed at the probe's server.

    The settings live under the config home the run is given (JOT_CONFIG_HOME
    + configs/settings.conf), not in the workspace: the editor reads one path
    and the workspace only holds the files being edited.
    """
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(cfg, ignore_errors=True)
    os.makedirs(os.path.join(cfg, "configs"))
    with open(os.path.join(cfg, "configs", "settings.conf"), "w") as fh:
        fh.write(
            "# jot configuration file\n\n"
            "ai_adapter=openai\n"
            "ai_model=%s\n"
            "ai_base_url=http://127.0.0.1:%d/v1\n" % (MODEL, port)
        )
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "a.cpp")
    with open(path, "w") as fh:
        fh.write(source or "int a = 1;\n")
    return path


def on_screen(fragment: str):
    """A phase that waits for `fragment` to be on the screen.

    The keys after it are typed only once the editor got there: the palette
    command has to have opened the chat before the prompt goes to the buffer,
    and a provider's answer has to have landed before the screen is read.
    """
    return lambda screen: fragment in screen.text()


def chat_session(binary: str, root: str, cfg: str, prompt: str, expect: str, dump: bool):
    """Opens the chat, types `prompt`, sends it, and waits for `expect`."""
    return run_in_pty(binary, [root], b"", settle=4.0, after=0.6, cols=COLS,
                      rows=ROWS, cfg=cfg, cwd=root,
                      phases=[(0.6, PALETTE), (0.8, b"CodeCompanionChat"), (0.6, ENTER),
                              (0.6, on_screen("# Chat")), (0.6, prompt.encode()),
                              (0.6, SEND), (0.6, on_screen(expect))])


def inline_session(binary: str, root: str, cfg: str, path: str, accept: bool, dump: bool):
    """Selects the line and runs the inline assistant.

    Without `accept` the run stops at the diff preview, which is what the
    screen is read for; with it the `y` that applies the rewrite is sent, and
    the preview is gone by the time the screen is read back.
    """
    # The line is selected, so the selection is on screen before the command
    # runs; the rewrite is waited for either way, since the preview is what the
    # diff scene reads and the applied text is what the accept scene reads.
    phases = [(0.6, SELECT_LINE), (0.4, PALETTE),
              (1.0, b"CodeCompanion rewrite this"), (0.6, ENTER),
              (0.6, on_screen(REPLACEMENT))]
    if accept:
        phases += [(0.6, b"y")]
    return run_in_pty(binary, [path], b"", settle=4.0, after=0.6, cols=COLS,
                      rows=ROWS, cfg=cfg, cwd=root, phases=phases)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print("ai probe: SKIP - no binary at %s" % binary)
        return 2
    if shutil.which("curl") is None:
        print("ai probe: SKIP - curl is not on PATH")
        return 2

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["OPENAI_API_KEY"] = TOKEN

    failures: list[str] = []

    # Scene 1: the chat streams an answer, and the request is what the config
    # said it should be.
    root = "/tmp/jot_ai_probe_chat"
    cfg = "/tmp/jot_ai_probe_cfg_chat"
    write_workspace(root, cfg, port)
    del REQUESTS[:]
    screen = chat_session(binary, root, cfg, PROMPT, ANSWER.split(" ")[-1], dump)
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()
    # The last word of the answer, in the buffer's own transcript: it is only
    # on the screen if the reply really came back over the socket and landed.
    for fragment in ("# Chat", "## user", "> " + PROMPT, ANSWER.split(" ")[-1]):
        if fragment not in text:
            failures.append("chat: %r missing from the screen" % fragment)
    if not REQUESTS:
        failures.append("chat: the provider saw no request")
    else:
        request = REQUESTS[-1]
        if request["path"] != "/v1/chat/completions":
            failures.append("chat: posted to %s" % request["path"])
        if request["body"].get("model") != MODEL:
            failures.append("chat: model was %r" % request["body"].get("model"))
        if request["body"].get("stream") is not True:
            failures.append("chat: stream was %r" % request["body"].get("stream"))
        if request["headers"].get("authorization") != "Bearer " + TOKEN:
            failures.append("chat: authorization was %r"
                            % request["headers"].get("authorization"))
        if request["prompt"] != PROMPT:
            failures.append("chat: the prompt sent was %r" % request["prompt"])
    print("chat:             %s" % ("ok" if not failures else "FAILED"))

    # Scene 2: a provider error reports its own message and keeps the prompt.
    root = "/tmp/jot_ai_probe_error"
    cfg = "/tmp/jot_ai_probe_cfg_error"
    write_workspace(root, cfg, port)
    del REQUESTS[:]
    screen = chat_session(binary, root, cfg, "failme now", "bad key, go away", dump)
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()
    before = len(failures)
    if "bad key, go away" not in text:
        failures.append("error: the provider's message is not on the screen")
    if "> failme now" not in text:
        failures.append("error: the prompt was not put back for a retry")
    if ANSWER.split(" ")[-1] in text:
        failures.append("error: an answer appeared for a failed request")
    print("provider error:   %s" % ("ok" if len(failures) == before else "FAILED"))

    # Scene 3: the inline assistant previews the rewrite as a diff.
    root = "/tmp/jot_ai_probe_inline"
    cfg = "/tmp/jot_ai_probe_cfg_inline"
    path = write_workspace(root, cfg, port, "int a = 1;\nint keep = 3;\n")
    del REQUESTS[:]
    screen = inline_session(binary, root, cfg, path, False, dump)
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()
    before = len(failures)
    if "- int a = 1;" not in text or "+ int replacement = 2;" not in text:
        failures.append("inline: the diff preview never appeared")
    if not REQUESTS or REQUESTS[-1]["body"].get("stream") is not False:
        failures.append("inline: the rewrite should be a non-streaming request")
    print("inline + diff:    %s" % ("ok" if len(failures) == before else "FAILED"))

    # Scene 4: `y` applies that rewrite to the selection it was shown for.
    root = "/tmp/jot_ai_probe_apply"
    cfg = "/tmp/jot_ai_probe_cfg_apply"
    path = write_workspace(root, cfg, port, "int a = 1;\nint keep = 3;\n")
    del REQUESTS[:]
    screen = inline_session(binary, root, cfg, path, True, dump)
    if dump:
        print(screen.text())
        print("-" * 70)
    text = screen.text()
    before = len(failures)
    if "int replacement = 2;" not in text:
        failures.append("apply: 'y' did not apply the rewrite")
    if "int a = 1;" in text:
        failures.append("apply: the original line is still there")
    if "int keep = 3;" not in text:
        failures.append("apply: the rewrite ate the line after the selection")
    print("inline apply:     %s" % ("ok" if len(failures) == before else "FAILED"))

    if failures:
        print("ai probe: FAIL")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("ai probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
