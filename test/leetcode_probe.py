#!/usr/bin/env python3
"""Exercise the LeetCode feature against a local GraphQL fixture.

Exit codes: 0 pass, 1 fail, 2 skip (no built binary).
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

COLS, ROWS = 120, 32
ROOT = "/tmp/jot_leetcode_probe"
CFG = "/tmp/jot_leetcode_probe_cfg"
SLUG = "two-sum"
TITLE = "Two Sum Local Fixture"
REQUESTS: list[dict] = []
RESPONDED: list[bool] = []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        REQUESTS.append({"path": self.path, "cookie": self.headers.get("Cookie"), "body": raw})
        query = json.loads(raw).get("query", "")
        if "questionOfToday" in query:
            payload = {"data": {"today": {"question": {"title_slug": SLUG}}}}
        elif "questionData" in query:
            payload = {"data": {"question": {
                "id": "1", "frontend_id": "1", "title": TITLE, "title_slug": SLUG,
                "difficulty": "Easy", "content": "<p>Return indices of two values.</p>",
                "testcase_list": "[2,7,11,15]\n9", "topic_tags": [{"name": "Array"}],
                "code_snippets": [{"lang": "C++", "lang_slug": "cpp", "code": "class Solution { public: int twoSum(vector<int>& nums, int target) {} };"}],
            }}}
        elif "globalData" in query:
            payload = {"data": {"userStatus": {"id": None, "name": "", "is_signed_in": False, "is_verified": False}}}
        else:
            payload = {"errors": [{"message": "fixture has no such query"}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        RESPONDED.append(True)

    def do_GET(self):
        REQUESTS.append({"path": self.path, "cookie": self.headers.get("Cookie"), "body": b""})
        if self.path.startswith("/api/problems/algorithms/"):
            pairs = [{"stat": {"question_id": 1, "frontend_question_id": "1", "question__title": TITLE,
                                "question__title_slug": SLUG, "question__hide": False,
                                "total_acs": 100, "total_submitted": 120},
                      "difficulty": {"level": 1}, "status": None, "paid_only": False}]
            data = json.dumps({"stat_status_pairs": pairs}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            RESPONDED.append(True)
            return
        data = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def command_phase(command: str):
    return [(0.5, b"\x1b[112;6u"), (0.6, command.encode()), (0.4, b"\r")]


def on_screen(fragment: str):
    return lambda screen: fragment in screen.text()


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build-tests/apps/jot/jot"
    if not os.path.exists(binary):
        print("leetcode probe: SKIP - no binary at %s" % binary)
        return 2

    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.rmtree(CFG, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.makedirs(os.path.join(CFG, "configs"), exist_ok=True)
    with open(os.path.join(CFG, "configs", "settings.conf"), "w") as fh:
        fh.write("lsp_enabled=false\nleetcode_api_base=http://127.0.0.1:%d\n" % port)
    os.makedirs(ROOT, exist_ok=True)
    env = {"JOT_CONFIG_HOME": CFG, "JOT_CACHE_HOME": CFG}

    screen = run_in_pty(binary, [ROOT], b"", settle=3.5, after=0.4, cols=COLS, rows=ROWS,
                        cfg=CFG, cwd=ROOT, env=env,
                        phases=command_phase("LeetList") + [(4.0, on_screen(TITLE))])
    if not RESPONDED:
        print("leetcode probe: FAIL - HTTP request did not complete")
        server.shutdown()
        return 1
    if TITLE not in screen.text():
        print("leetcode probe: FAIL - local problem fixture was not listed")
        print(screen.text())
        print("requests:", REQUESTS)
        print("responses:", RESPONDED)
        server.shutdown()
        return 1
    if not REQUESTS or REQUESTS[-1]["cookie"]:
        print("leetcode probe: FAIL - public list request missing or carried a cookie")
        server.shutdown()
        return 1

    request_start = len(REQUESTS)
    screen = run_in_pty(binary, [ROOT], b"", settle=3.5, after=0.4, cols=COLS, rows=ROWS,
                        cfg=CFG, cwd=ROOT, env=env,
                        phases=command_phase("LeetDaily") + [(4.0, on_screen("C++"))])
    if "C++" not in screen.text():
        print("leetcode probe: FAIL - daily question did not reach language selection")
        print(screen.text())
        server.shutdown()
        return 1
    recent = REQUESTS[request_start:]
    if len(recent) < 2 or any(request["cookie"] for request in recent):
        print("leetcode probe: FAIL - daily/question requests were missing or carried credentials")
        print("requests:", recent)
        server.shutdown()
        return 1

    if os.name == "nt" or sys.platform == "darwin":
        screen = run_in_pty(binary, [ROOT], b"", settle=3.5, after=0.4, cols=COLS, rows=ROWS,
                            cfg=CFG, cwd=ROOT, env=env,
                            phases=command_phase("LeetCookie") + [(0.8, on_screen("Paste the LeetCode session cookie"))])
        if "Paste the LeetCode session cookie" not in screen.text():
            print("leetcode probe: FAIL - credential prompt did not open")
            print(screen.text())
            server.shutdown()
            return 1
    else:
        print("credential prompt: SKIP - Linux Secret Service has no session bus")

    if any(request["cookie"] for request in REQUESTS):
        print("leetcode probe: FAIL - a public request carried credentials")
        server.shutdown()
        return 1
    server.shutdown()
    print("leetcode probe: PASS - problem list and cookie-free public requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
