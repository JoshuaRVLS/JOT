#!/usr/bin/env python3
"""Probe: the Windows installer renders cmd.exe scripts, and only on Windows.

Windows has no POSIX shell, so every package that installs from an external link
used to fail there: `:lspinstall` reported "not supported by the Windows
installer yet" for all but three catalog entries, and the ones it did accept
were handed to `/bin/sh` in a cmd.exe terminal. This drives the real binary down
both install paths and reads back what they produced.

The installer's platform is what selects the script language, so the Windows
path is reachable from any host: JOT_INSTALL_PLATFORM=win makes the Lua registry
render cmd.exe steps and the native wrapper write them to a batch file. The
probe never needs a Windows machine, or a working install, to tell the two
renderers apart.

Run 1 (no platform override) dumps the plan for `cpp` through
jot.lsp.installer.plan_install and asserts it is the POSIX script.
Run 2 (JOT_INSTALL_PLATFORM=win) dumps the same plan, asserts it is a cmd.exe
script, and then really runs `:lspinstall cpp` through the native command:

  * the plan contains curl.exe/tar.exe/findstr and none of ln, chmod, unzip;
  * the host wrote that same body to a .cmd batch file, which is what cmd.exe
    needs in order to fail fast (`exit /b` is only available to a batch);
  * the job reported failure rather than success, because there is no cmd.exe on
    this host to run the batch -- and no receipt was written. A marker that said
    "installed" here would be exactly the lie the batch guards against.

Usage: test/windows_install_probe.py [path-to-jot-binary]
Exit codes: 0 pass, 1 fail, 2 binary missing.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

DATA = "/tmp/jot_win_install_probe_data"
CFG_POSIX = "/tmp/jot_win_install_probe_cfg_posix"
CFG_WIN = "/tmp/jot_win_install_probe_cfg_win"
WORK = "/tmp/jot_win_install_probe_work"
# Empty PATH for the Windows run: `cmd /c ...` then cannot resolve to anything
# on this host, so the run cannot reach the network or run a batched step, and
# the only thing left to observe is what the editor itself produced.
SHIM = "/tmp/jot_win_install_probe_path"

# What each renderer must produce, and what must never appear in the other's.
POSIX_MARKERS = ("ln -sfn", "unzip -oq", "/bin/sh")
WIN_MARKERS = ("curl -fsSL", "tar -xf", "findstr", "for /r", "|| exit /b 1",
               "setlocal", "echo name=cpp")
WIN_MUST_NOT = ("ln -sfn", "chmod", "unzip", "XDG_DATA_HOME", "/bin/sh")


def write_init(cfg: str, dump: str, install: bool) -> None:
    """init.lua is how a user asks for an install without a keybinding."""
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "init.lua"), "w") as fh:
        fh.write("local plan = jot.lsp.installer.plan_install('cpp')\n"
                 "local out = io.open(%r, 'w')\n"
                 "out:write(plan and plan.script or '')\n"
                 "out:close()\n" % dump)
        if install:
            fh.write("jot.lsp.install('cpp')\n")


def run(binary: str, cfg: str, dump: str, install: bool, env: dict) -> None:
    write_init(cfg, dump, install)
    run_in_pty(binary, [os.path.join(WORK, "probe.cpp")], b"", settle=4.0, after=5.0,
               cfg=cfg, cwd=WORK, env=env)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    if not os.path.exists(binary):
        print(f"windows install probe: SKIP - no binary at {binary}")
        return 2
    binary = os.path.abspath(binary)

    for path in (DATA, CFG_POSIX, CFG_WIN, WORK, SHIM):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(WORK)
    os.makedirs(SHIM)
    with open(os.path.join(WORK, "probe.cpp"), "w") as fh:
        fh.write("int main() { return 0; }\n")

    failures = 0

    def check(name, ok, detail):
        nonlocal failures
        if not ok:
            failures += 1
        print(f"windows install probe: {name} {'ok' if ok else 'FAIL'} - {detail}")

    base_env = {
        "XDG_DATA_HOME": DATA,
        # No payload in sight: the plan has to come from the download manager,
        # which is where the renderers live.
        "JOT_LSP_PAYLOAD_DIR": "/nonexistent/jot-payload",
    }

    # Run 1: the POSIX renderer, so run 2's result is a switch and not the only
    # thing the code can produce.
    posix_dump = os.path.join(WORK, "plan-posix.txt")
    run(binary, CFG_POSIX, posix_dump, False, dict(base_env))
    posix = open(posix_dump, errors="replace").read() if os.path.exists(posix_dump) else ""
    check("posix plan uses the POSIX shell",
          all(marker in posix for marker in POSIX_MARKERS),
          repr(posix[:200]) if not posix else "shell script rendered")

    # Run 2: the Windows path, with the real install command on top of the plan.
    win_dump = os.path.join(WORK, "plan-win.txt")
    run(binary, CFG_WIN, win_dump, True,
        dict(base_env, JOT_INSTALL_PLATFORM="win", PATH=SHIM))
    win = open(win_dump, errors="replace").read() if os.path.exists(win_dump) else ""

    missing = [m for m in WIN_MARKERS if m not in win]
    check("windows plan is a cmd.exe script", not missing,
          "missing " + repr(missing) if missing else "curl/tar/findstr steps rendered")
    leaked = [m for m in WIN_MUST_NOT if m in win]
    check("no POSIX script reaches Windows", not leaked,
          "leaked " + repr(leaked) if leaked else "nothing POSIX in the plan")

    # The body rides in a batch file: cmd.exe cannot fail fast on a command
    # line, so `exit /b` (batch-only) is what aborts a step that failed.
    scripts = glob.glob(os.path.join(DATA, "jot", "lsp", "scripts", "*.cmd"))
    check("batch file written for cmd.exe", len(scripts) == 1, scripts or "(none)")
    if scripts:
        batch = open(scripts[0], errors="replace").read()
        # CRLF, because the batch parser is line oriented.
        check("batch body is the rendered plan",
              batch.replace("\r\n", "\n").strip() == win.strip(),
              f"{len(batch)} bytes vs {len(win)} bytes of plan")
        check("batch aborts on a failed step", "exit /b 1" in batch,
              batch.splitlines()[:2])
    else:
        failures += 2

    # The install really ran: the job log carries the lifecycle markers, and it
    # says failed. This host has no cmd.exe, so success would mean the marker is
    # not tied to the install actually working.
    logs = glob.glob(os.path.join(CFG_WIN, "logs", "install_lsp-cpp_*.log"))
    log_text = ""
    for path in logs:
        with open(path, "r", errors="replace") as fh:
            log_text += fh.read()
    check("install job reported a result", "[jot:lsp] start cpp" in log_text
          and "[jot:lsp] failed cpp" in log_text,
          log_text.strip() or "(no log)")
    check("no success reported without an install", "[jot:lsp] success cpp" not in log_text,
          log_text.strip()[:120] or "(no log)")
    receipt = os.path.join(DATA, "jot", "lsp", "cpp", "receipt")
    check("no receipt for a failed install", not os.path.exists(receipt), receipt)

    # Nothing POSIX may be produced on the Windows path either: the batch file
    # is the whole artifact.
    posix_scripts = glob.glob(os.path.join(DATA, "jot", "lsp", "scripts", "*.sh"))
    check("no shell script alongside it", not posix_scripts, posix_scripts or "(none)")

    print("windows install probe: " + ("FAIL" if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
