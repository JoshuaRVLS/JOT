#!/usr/bin/env python3
"""Probe: the WakaTime integration, against a stub wakatime-cli on PATH.

The integration's whole contract is with wakatime-cli: the editor spawns it and
reads what it prints. So the probe puts a stub `wakatime-cli` first on PATH that
appends the argv it was handed to a file, and answers `--today` with a fixed
payload. What the editor actually asked for is then read back from the file the
stub wrote -- the production path (the worker queue, the shell, the plugin spec's
rule), not a seam.

Scenes:

  * an edit and a save put two heartbeats in the record: the first without
    `--write`, the second with it and on the absolute path of the file in the
    buffer. The spec's rule is what keeps it at two and not one per keystroke;
  * the record holds a `--today --output json` run, and the bar shows the total
    the stub answered with -- so the fetch, the parse and the chip are one
    chain, on a real screen;
  * with `wakatime=false` nothing is spawned at all and the chip falls back to
    the local total (features/coding_time.h);
  * with no cli on PATH the editor says so instead of tracking nothing quietly.

Usage: test/wakatime_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary).
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

ROOT = "/tmp/jot_wakatime_probe"
BIN = "/tmp/jot_wakatime_probe_bin"
# A plain text file on purpose: no language server and no formatter-on-save, so
# the bar has room for the chip and the scene is about the heartbeat rather than
# about clangd's own tray of chips.
FILE = "notes.txt"
FILE_BODY = "alpha\nbeta\ngamma\n"

TODAY_TEXT = "1 hr 30 mins"
# Nerd Fonts codepoints the coding chip leads with: nf-md-clock_outline is
# WakaTime's answer, nf-md-timer_outline is the local store's total.
CLOCK_GLYPH = "\U000F0150"
TIMER_GLYPH = "\U000F051B"

# The stub: one line per invocation with the argv it was given, and the payload
# vscode-wakatime reads for today (`text` at the top level).
STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$JOT_WAKATIME_RECORD"
for arg in "$@"; do
  if [ "$arg" = "--today" ]; then
    printf '%s\\n' '{"has_team_features":false,"text":"1 hr 30 mins","total_seconds":5400}'
    exit 0
  fi
done
exit 0
"""


def write_workspace() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.rmtree(BIN, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(BIN, exist_ok=True)
    with open(os.path.join(ROOT, FILE), "w") as fh:
        fh.write(FILE_BODY)
    stub = os.path.join(BIN, "wakatime-cli")
    with open(stub, "w") as fh:
        fh.write(STUB)
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def write_settings(cfg: str, lines: list[str]) -> None:
    os.makedirs(os.path.join(cfg, "configs"), exist_ok=True)
    with open(os.path.join(cfg, "configs", "settings.conf"), "w") as fh:
        for line in lines:
            fh.write(line + "\n")


def recorded(record: str, want: int, timeout: float = 2.0):
    """The stub's argv lines, once there are at least `want` of them."""
    deadline = time.time() + timeout
    lines: list[str] = []
    while True:
        lines = []
        if os.path.exists(record):
            with open(record) as fh:
                lines = [line.rstrip("\n") for line in fh if line.strip()]
        if len(lines) >= want or time.time() >= deadline:
            return lines
        time.sleep(0.05)


def run(binary: str, cfg: str, record: str, keys: bytes = b"", phases=None,
        env_path: str = None, settle: float = 2.5, after: float = 0.5,
        until_timeout: float = 8.0, sink: list = None):
    env = {
        "PATH": env_path if env_path is not None else BIN + ":/usr/bin:/bin",
        "JOT_WAKATIME_RECORD": record,
    }
    # `sink` collects every byte the child wrote. A toast is painted once and
    # fades, so the report can be gone from the final screen while its text is
    # still the record of what the editor said -- which is what is being asked.
    on_output = None
    if sink is not None:
        on_output = lambda _fd, data: sink.append(data)
    # Wide enough that no chip is dropped for space: what is being read here is
    # the chip's content, not the bar's priority order (status_clock_probe.py
    # covers the drop).
    return run_in_pty(binary, [os.path.join(ROOT, FILE)], keys, settle=settle,
                      after=after, cols=150, rows=30, cfg=cfg, cwd=ROOT, env=env,
                      phases=phases, until_timeout=until_timeout, on_output=on_output)


def today_runs(lines) -> list[str]:
    return [line for line in lines if "--today" in line]


def heartbeat_runs(lines) -> list[str]:
    return [line for line in lines if "--entity" in line]


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"wakatime probe: SKIP - no binary at {binary}")
        return 2

    write_workspace()
    failures: list[str] = []

    # ── Scene 1: tracking on, one edit and one save ──────────────────────────
    cfg = "/tmp/jot_wakatime_probe_cfg_on"
    record = "/tmp/jot_wakatime_probe_record_on"
    if os.path.exists(record):
        os.remove(record)
    # `x` deletes a character, which is a keystroke (a heartbeat without
    # --write) and leaves the buffer modified; the auto-save interval then
    # writes it out, which is the spec's is_write heartbeat. Both come from the
    # real paths -- the input dispatch and Editor::save_buffer_at -- so nothing
    # here is a seam the editor does not already have.
    write_settings(cfg, ["wakatime=true", "wakatime_api_key=probe-key",
                         "auto_save=true", "auto_save_interval_ms=200"])
    screen = run(binary, cfg, record, keys=b"x", phases=[
        (0.5, lambda _s, r=record: len(heartbeat_runs(recorded(r, 1, 0.05))) >= 1),
        (0.5, lambda _s, r=record: len(heartbeat_runs(recorded(r, 2, 0.05))) >= 2),
    ])
    if dump:
        print(screen.text())
        print("-" * 70)

    lines = recorded(record, 2, 1.0)
    hearts = heartbeat_runs(lines)
    today = today_runs(lines)
    entity = os.path.join(ROOT, FILE)
    print(f"wakatime probe: {len(hearts)} heartbeat(s), {len(today)} today run(s)")
    for line in lines:
        print(f"    {line}")

    if len(today) < 1:
        failures.append("the editor never asked the cli for today's total")
    elif "--output json" not in today[0]:
        failures.append(f"today's total was not asked for as json: {today[0]!r}")
    for line in lines:
        if "--key 'probe-key'" not in line and "--key probe-key" not in line:
            failures.append(f"a run is missing the configured api key: {line!r}")
        if "jot-wakatime" not in line:
            failures.append(f"a run is missing the plugin id: {line!r}")

    if not hearts:
        failures.append("the edit and the save produced no heartbeat at all")
    else:
        keystrokes = [line for line in hearts if "--write" not in line]
        writes = [line for line in hearts if "--write" in line]
        if not keystrokes:
            failures.append(f"the keystroke produced no heartbeat: {hearts!r}")
        if not writes:
            failures.append("the save produced no --write heartbeat")
        for line in hearts:
            if entity not in line:
                failures.append(f"a heartbeat is not for {entity}: {line!r}")
            if "--time " not in line:
                failures.append(f"a heartbeat has no timestamp: {line!r}")

    # The heartbeat rule is what keeps this from being one spawn per keystroke,
    # so the count has to stay at the events that earned one: the keystroke and
    # the save. A third would mean the rule let a duplicate through.
    if len(hearts) > 3:
        failures.append(f"{len(hearts)} heartbeats from one keystroke and one save")

    # The other half of the chain: what the cli answered has to reach the bar.
    if TODAY_TEXT not in screen.text():
        failures.append(f"the bar does not show the cli's total ({TODAY_TEXT})")
    elif CLOCK_GLYPH not in screen.text():
        failures.append("the chip shows WakaTime's total without its glyph")
    if TIMER_GLYPH in screen.text():
        failures.append("the local-store glyph is up while WakaTime has an answer")

    # ── Scene 2: tracking off, nothing spawned and the local total shown ─────
    cfg = "/tmp/jot_wakatime_probe_cfg_off"
    record = "/tmp/jot_wakatime_probe_record_off"
    if os.path.exists(record):
        os.remove(record)
    write_settings(cfg, ["wakatime=false", "wakatime_api_key=probe-key"])
    off = run(binary, cfg, record, keys=b"x", after=1.5)
    if dump:
        print(off.text())
        print("-" * 70)
    if recorded(record, 1, 0.5):
        failures.append(f"the cli ran with the extension off: {recorded(record, 1)}")
    if TIMER_GLYPH not in off.text():
        failures.append("the local total is not on the bar with WakaTime off")
    if TODAY_TEXT in off.text():
        failures.append("the bar shows a WakaTime total with the extension off")
    if not re.search(r"\s\d+(s|m|h \d+m)\s", off.text()):
        failures.append("the chip carries no local total with WakaTime off")

    # ── Scene 3: the cli is not installed ────────────────────────────────────
    # Only meaningful when the host really has none; a machine with WakaTime
    # installed would find its own and this scene would be about that instead.
    if shutil.which("wakatime-cli"):
        print("wakatime probe: note - wakatime-cli is installed here, "
              "skipping the missing-cli scene")
    else:
        cfg = "/tmp/jot_wakatime_probe_cfg_missing"
        record = "/tmp/jot_wakatime_probe_record_missing"
        if os.path.exists(record):
            os.remove(record)
        write_settings(cfg, ["wakatime=true", "wakatime_api_key=probe-key"])
        sink: list = []
        missing = run(binary, cfg, record, keys=b"x", env_path="/usr/bin:/bin",
                      after=1.5, sink=sink)
        said = b"".join(sink).decode("utf-8", "replace")
        if dump:
            print(missing.text())
            print("-" * 70)
        if "wakatime-cli not found" not in said:
            failures.append("a missing cli was not reported to the user")
        if TIMER_GLYPH not in missing.text():
            failures.append("the chip lost its local fallback with no cli")
        if TODAY_TEXT in missing.text():
            failures.append("the chip shows a WakaTime total with no cli")
        if os.path.exists(record):
            failures.append(f"a spawn was attempted with no cli on PATH: {record}")

    if failures:
        for failure in failures:
            print(f"wakatime probe: FAIL - {failure}")
        return 1
    print("wakatime probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
