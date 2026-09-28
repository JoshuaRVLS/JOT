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

Run 2 also dumps the plans of one entry per manager family.

Run 3 is the only part that needs Windows itself, and it is skipped when wine is
not installed. Wine's cmd.exe executes the very batch text the editor produced,
with the package managers stubbed on PATH (a stub `gem.cmd` writes the .bat shim
RubyGems would write, a stub `node.cmd` prints its arguments), which is how the
batch semantics get checked on a Linux host:

  * `:lspinstall erb-lint` runs to completion, writes the receipt, and leaves a
    launcher whose GEM_HOME points at the package dir; running that launcher
    forwards its arguments and shows the gem running from the managed copy;
  * the openvsx plan's link lines produce a launcher for the interpreter bin
    that starts the .js file the catalog names, not a stray file with the same
    basename elsewhere in the package tree;
  * a step that fails aborts the batch (`exit /b`) instead of continuing.

Usage: test/windows_install_probe.py [path-to-jot-binary]
Exit codes: 0 pass, 1 fail, 2 binary missing.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
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
# Manager families whose plans are dumped in run 2 and executed in run 3.
FAMILY_IDS = ("erb-lint", "haxe-language-server", "luacheck")
# Wine's cmd.exe is the only Windows shell available on a Linux host, and its
# builtins (if/for/set/mkdir/copy/echo) are what the generated batches use.
WINE_STUBS = {
    "node.cmd": "@echo off\necho node-shim %*\n",
    # RubyGems writes a bat shim beside the ruby script; the launcher the
    # renderer generates must call it with GEM_HOME pointed at the package dir.
    "gem.cmd": "@echo off\n"
               "setlocal\n"
               "set \"bindir=\"\n"
               ":loop\n"
               "if \"%~1\"==\"\" goto done\n"
               "if \"%~1\"==\"--bindir\" (set \"bindir=%~2\" & shift & shift & goto loop)\n"
               "shift\n"
               "goto loop\n"
               ":done\n"
               "if not defined bindir exit /b 1\n"
               "mkdir \"%bindir%\" 2>NUL\n"
               "> \"%bindir%\\erblint.bat\" echo @echo off\n"
               ">> \"%bindir%\\erblint.bat\" echo echo gem-shim %%*\n"
               ">> \"%bindir%\\erblint.bat\" echo echo GEM_HOME=%%GEM_HOME%%\n",
}


def wine_cmd() -> str:
    return shutil.which("wine") or ""


def windows_path(path: str) -> str:
    """The Z: spelling of a host path, which is what wine's cmd.exe reads."""
    return "Z:" + os.path.abspath(path).replace("/", "\\")


def write_batch(path: str, body: str) -> None:
    """cmd.exe parses a batch line by line, so the endings have to be CRLF."""
    with open(path, "wb") as fh:
        fh.write(body.replace("\r\n", "\n").replace("\n", "\r\n").encode())


def run_batch(cmd: str, shim: str, batch: str, args: str = "") -> tuple[int, str]:
    env = dict(os.environ)
    # WINEPATH is what wine folds into the Windows PATH, so the stubs resolve.
    env["WINEPATH"] = windows_path(shim)
    env["WINEDEBUG"] = "-all"
    proc = subprocess.run([cmd, "cmd", "/c", windows_path(batch) + (" " + args if args else "")],
                          env=env, cwd=WORK,
                          capture_output=True, text=True, timeout=180)
    return proc.returncode, proc.stdout.replace("\r\n", "\n")


def is_transfer(line: str) -> bool:
    """Download/unpack steps, which need the network and a real curl.exe.

    Run 3 replaces them with a pre-created payload: what is under test there is
    the publish and link half of the plan, which is the part that has to be
    spelled differently on Windows.
    """
    stripped = line.lstrip()
    if stripped.startswith(("curl ", "tar ")):
        return True
    return stripped.startswith("if exist ") and "\\dl\\" in stripped

# What each renderer must produce, and what must never appear in the other's.
POSIX_MARKERS = ("ln -sfn", "unzip -oq", "/bin/sh")
WIN_MARKERS = ("curl -fsSL", "tar -xf", "findstr", "for /r", "|| exit /b 1",
               "setlocal", "echo name=cpp")
WIN_MUST_NOT = ("ln -sfn", "chmod", "unzip", "XDG_DATA_HOME", "/bin/sh")


def write_init(cfg: str, dump: str, install: bool, plans: dict | None = None) -> None:
    """init.lua is how a user asks for an install without a keybinding."""
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "init.lua"), "w") as fh:
        fh.write("local plan = jot.lsp.installer.plan_install('cpp')\n"
                 "local out = io.open(%r, 'w')\n"
                 "out:write(plan and plan.script or '')\n"
                 "out:close()\n" % dump)
        if plans:
            table = ", ".join("{ %r, %r }" % (name, path) for name, path in plans.items())
            fh.write("for _, pair in ipairs({ %s }) do\n"
                     "  local p = jot.lsp.installer.plan_install(pair[1])\n"
                     "  local f = io.open(pair[2], 'w')\n"
                     "  f:write(p and p.script or '')\n"
                     "  f:close()\n"
                     "end\n" % table)
        if install:
            fh.write("jot.lsp.install('cpp')\n")


def run(binary: str, cfg: str, dump: str, install: bool, env: dict,
        plans: dict | None = None) -> None:
    write_init(cfg, dump, install, plans)
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
    # The family plans are dumped by the same run, so run 3 executes exactly what
    # the editor produced rather than a reconstruction of it.
    win_dump = os.path.join(WORK, "plan-win.txt")
    family_plans = {name: os.path.join(WORK, f"plan-{name}.txt") for name in FAMILY_IDS}
    run(binary, CFG_WIN, win_dump, True,
        dict(base_env, JOT_INSTALL_PLATFORM="win", PATH=SHIM), family_plans)
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

    def read_plan(name: str) -> str:
        path = os.path.join(WORK, f"plan-{name}.txt")
        return open(path, errors="replace").read() if os.path.exists(path) else ""

    # The install tree in both spellings: os.path for this filesystem, and the
    # one the plan carries, where install.lua joins the root and id with a "\\".
    root = os.path.join(DATA, "jot", "lsp")
    win_root = DATA + "/jot/lsp"

    def bin_dir() -> str:
        return os.path.join(root, "bin")

    def package_dir(name: str) -> str:
        return os.path.join(root, name)

    # Run 3: wine's cmd.exe runs the batch text the editor produced, with the
    # package managers stubbed on PATH. Skipped when no Windows shell is around.
    wine = wine_cmd()
    if not wine:
        print("windows install probe: run 3 SKIP - wine is not installed")
    else:
        for name, body in WINE_STUBS.items():
            with open(os.path.join(SHIM, name), "w") as fh:
                fh.write(body)

        # A gem bin is not a file this install produces: the renderer finds
        # RubyGems' own shim and pins GEM_HOME to the isolated package dir.
        gem_batch = os.path.join(WORK, "run-erb-lint.cmd")
        write_batch(gem_batch, read_plan("erb-lint"))
        rc, out = run_batch(wine, SHIM, gem_batch)
        check("gem install runs to completion under cmd.exe", rc == 0,
              f"exit {rc}: {out.strip()[-200:]}")
        check("gem install wrote its receipt",
              os.path.exists(os.path.join(package_dir("erb-lint"), "receipt")),
              "(no receipt)")
        gem_home = win_root + "\\erb-lint"
        launcher = os.path.join(bin_dir(), "erblint.cmd")
        body = open(launcher, errors="replace").read() if os.path.exists(launcher) else ""
        check("gem launcher pins GEM_HOME to the package dir",
              "GEM_HOME=" + gem_home in body and "GEM_PATH=" + gem_home in body,
              body.strip() or "(no launcher)")
        check("gem launcher calls the installed shim",
              'call "' + gem_home + '\\bin\\erblint.bat"' in body,
              body.strip() or "(no launcher)")
        out = ""
        if os.path.exists(launcher):
            _, out = run_batch(wine, SHIM, launcher, "--stdio")
        else:
            failures += 1
        check("gem launcher forwards args and runs from the managed copy",
              "--stdio" in out and "GEM_HOME=" + gem_home in out,
              out.strip() or "(nothing ran)")

        # An interpreter bin (a .js inside a .vsix) is linked by a launcher that
        # names the file the catalog points at, not a same-named stray one.
        package = package_dir("haxe-language-server")
        os.makedirs(os.path.join(package, "extension", "bin"), exist_ok=True)
        with open(os.path.join(package, "extension", "bin", "server.js"), "w") as fh:
            fh.write("// server\n")
        with open(os.path.join(package, "server.js"), "w") as fh:
            fh.write("// stray\n")
        wanted = win_root + "\\haxe-language-server\\extension\\bin\\server.js"
        link_batch = os.path.join(WORK, "run-haxe-link.cmd")
        keep = [l for l in read_plan("haxe-language-server").splitlines() if not is_transfer(l)]
        write_batch(link_batch, "\n".join(keep) + "\n")
        rc, out = run_batch(wine, SHIM, link_batch)
        check("interpreter link lines run under cmd.exe", rc == 0,
              f"exit {rc}: {out.strip()[-200:]}")
        launcher = os.path.join(bin_dir(), "haxe-language-server.cmd")
        body = open(launcher, errors="replace").read() if os.path.exists(launcher) else ""
        check("interpreter launcher names the hint path, not the stray file",
              wanted in body and "node " in body, body.strip() or "(no launcher)")
        out = ""
        if os.path.exists(launcher):
            _, out = run_batch(wine, SHIM, launcher, "--stdio")
        else:
            failures += 1
        check("interpreter launcher forwards its arguments",
              "node-shim" in out and wanted in out and "--stdio" in out,
              out.strip() or "(nothing ran)")

        # A package manager that is not there has to abort the batch: the
        # receipt is written last, so a half-run install cannot look finished.
        missing_batch = os.path.join(WORK, "run-luacheck.cmd")
        write_batch(missing_batch, read_plan("luacheck"))
        rc, out = run_batch(wine, SHIM, missing_batch)
        check("a failed step aborts the batch", rc != 0, f"exit {rc}")
        check("no receipt for a failed install",
              not os.path.exists(os.path.join(package_dir("luacheck"), "receipt")),
              "(receipt written)")

    print("windows install probe: " + ("FAIL" if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
