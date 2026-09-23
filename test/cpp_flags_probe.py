#!/usr/bin/env python3
"""Probe: C++ diagnostics are judged against the project's flags.

A language server without compile flags parses with the compiler's default
standard, and a C++20 project then reads as a syntax error instead of as
"unconfigured": `concept` is an unknown type, a constrained member is a missing
';'. That is invisible to a unit test of the flag search, so this drives the
real binary over three workspaces and reads the rendered screen:

  * a workspace with no database at all -- the case the editor has to generate
    one for (and it must land under the data directory, never in the project),
  * the same workspace's C file, which must not be handed the C++ standard (an
    `-std=gnu++20` on a `.c` file is a hard "invalid argument" error, and it
    shares the one clangd process with the C++ file),
  * a workspace whose database lives in `build/`, where clangd's own search
    never looks -- the usual CMake layout.

Usage: test/cpp_flags_probe.py [path-to-jot] [--dump]
Exit codes: 0 pass, 1 fail, 2 skipped (no binary or no clangd).
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_screen import run_in_pty  # noqa: E402

# Note the constrained member functions: in the default standard clang reports
# them as "expected ';' at end of declaration list", on lines that already end
# with a semicolon.
MODERN = """#include <concepts>
#include <string>
#include <vector>

template <typename T>
concept Sized = requires(T v) { v.size(); };

template <typename T>
struct Holder {
  void reset() requires std::default_initializable<T>;
  int count() const requires (sizeof(T) > 0);
};

template <Sized T>
std::string describe(const T &value) { return std::to_string(value.size()); }

int main() {
  std::vector<int> values{1, 2, 3};
  Holder<int> h;
  h.reset();
  return (int)describe(values).size() + (h.count() > 0 ? 0 : 1);
}
"""

# C, and deliberately not C++: `malloc` into a `struct point *` is an implicit
# conversion from void*, which a C++ standard rejects.
LEGACY = """#include <stdlib.h>

struct point {
  int x;
  int y;
};

static int sum(struct point p) { return p.x + p.y; }

int main(void) {
  struct point *p = malloc(sizeof(struct point));
  if (!p) return 1;
  p->x = 1;
  p->y = 2;
  int total = sum(*p);
  free(p);
  return total;
}
"""

# What a configure writes, and what clangd never looks in.
BUILD_DATABASE = """[
  {
    "directory": "%(root)s/build",
    "file": "%(root)s/modern.cpp",
    "command": "c++ -std=gnu++20 -c %(root)s/modern.cpp"
  }
]
"""

# What the editor says when it reads a file with the wrong flags.
FALSE_ERRORS = ["Expected", "Unknown type name", "expected ';'", "no matching function",
                "does not refer to a value"]
# ...and what a C file says when it is handed a C++ standard.
C_LEAKS = ["invalid argument", "cannot initialize", "Expected", "Unknown"]


def write_workspace(root: str, with_build_database: bool) -> str:
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "modern.cpp"), "w") as fh:
        fh.write(MODERN)
    with open(os.path.join(root, "legacy.c"), "w") as fh:
        fh.write(LEGACY)
    if with_build_database:
        os.makedirs(os.path.join(root, "build"), exist_ok=True)
        with open(os.path.join(root, "build", "compile_commands.json"), "w") as fh:
            fh.write(BUILD_DATABASE % {"root": root})
    return os.path.join(root, "modern.cpp")


def false_rows(screen, needles) -> list[str]:
    return [line.rstrip() for line in screen.text().split("\n")
            if any(needle in line for needle in needles)]


def run(binary: str, path: str, root: str, cfg: str):
    return run_in_pty(binary, [path], b"", settle=9.0, after=2.0, cols=150, rows=32,
                      cfg=cfg, cwd=root)


def generated_databases(data_home: str) -> list[str]:
    directory = os.path.join(data_home, "jot", "lsp", "clangd-cdb")
    if not os.path.isdir(directory):
        return []
    return sorted(os.listdir(directory))


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "build/apps/jot/jot"
    dump = "--dump" in sys.argv
    if not os.path.exists(binary):
        print(f"cpp flags probe: SKIP - no binary at {binary}")
        return 2
    if not shutil.which("clangd") and not os.path.exists(
            os.path.expanduser("~/.local/share/jot/lsp/bin/clangd")):
        print("cpp flags probe: SKIP - no clangd")
        return 2

    # The generated databases go under the data directory, so point that at a
    # scratch one: nothing of the user's is touched, and the probe can check
    # what was written.
    data_home = "/tmp/jot_cpp_flags_probe_data"
    shutil.rmtree(data_home, ignore_errors=True)
    os.makedirs(data_home, exist_ok=True)
    os.environ["XDG_DATA_HOME"] = data_home
    os.environ.pop("LOCALAPPDATA", None)

    failures = []
    root = "/tmp/jot_cpp_flags_probe"

    # Scene 1: no database anywhere. The editor has to supply the flags.
    path = write_workspace(root, with_build_database=False)
    screen = run(binary, path, root, "/tmp/jot_cpp_flags_probe_cfg")
    if dump:
        print(screen.text())
        print("-" * 70)
    for row in false_rows(screen, FALSE_ERRORS):
        failures.append(f"generated flags: valid C++ still reported: {row.strip()!r}")
    if not generated_databases(data_home):
        failures.append("no database was generated under the data directory")
    else:
        cdb = os.path.join(data_home, "jot", "lsp", "clangd-cdb",
                           generated_databases(data_home)[0], "compile_commands.json")
        text = open(cdb).read()
        if "modern.cpp" not in text:
            failures.append("the generated database does not list the file that was opened")
        if "-std=gnu++20" not in text:
            failures.append("the generated database carries no C++ standard")
    if os.path.exists(os.path.join(root, "compile_commands.json")):
        failures.append("the project was written to (a database appeared at its root)")

    # Scene 2: the C file in the same workspace. One clangd serves both, so a
    # C++ standard leaking into the C entry is a hard error rather than a
    # warning -- the reason the generated database is per file.
    c_path = os.path.join(root, "legacy.c")
    screen = run(binary, c_path, root, "/tmp/jot_cpp_flags_probe_cfg_c")
    if dump:
        print(screen.text())
        print("-" * 70)
    for row in false_rows(screen, C_LEAKS):
        failures.append(f"C file judged with C++ flags: {row.strip()!r}")

    # Scene 3: the database is in build/, which clangd's own search misses.
    build_root = "/tmp/jot_cpp_flags_probe_build"
    path = write_workspace(build_root, with_build_database=True)
    screen = run(binary, path, build_root, "/tmp/jot_cpp_flags_probe_cfg_build")
    if dump:
        print(screen.text())
        print("-" * 70)
    for row in false_rows(screen, FALSE_ERRORS):
        failures.append(f"build-directory database ignored: {row.strip()!r}")

    if failures:
        for failure in failures:
            print(f"cpp flags probe: FAIL - {failure}")
        return 1
    print("cpp flags probe: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
