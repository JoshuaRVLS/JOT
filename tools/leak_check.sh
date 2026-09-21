#!/usr/bin/env bash
# Memory-leak check: builds the JOT_SANITIZE=ON tree and runs the Catch2 suite
# under AddressSanitizer + LeakSanitizer, failing if anything leaks.
#
#   tools/leak_check.sh [build-dir] [catch2-filter...]
#
# LeakSanitizer only reports at process exit, so the suite has to run as a
# single process (`jot_tests [filter]`) rather than one process per test case
# the way ctest drives it -- a per-case run would hide every leak.
#
# Two things about the exit status: the sanitizer tree is unoptimised, which
# slows the clock-sensitive cases down, so "A slower period blinks more slowly"
# (test/test_cursor_blink_wiring.cpp) is expected to fail here while passing in
# a normal build. This script therefore checks the leak report, not the suite's
# status. A filter can narrow the run to a subsystem.
set -uo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
build=${1:-"$root/build-asan"}
if [ $# -gt 0 ]; then
  shift
fi
log=${TMPDIR:-/tmp}/jot_leak_report

generator=()
if command -v ninja >/dev/null 2>&1; then
  generator=(-G Ninja)
fi

# Reuse the dependencies an existing checkout already fetched, so a run needs
# no network when one is present.
dep_args=()
if [ -d "$root/build/_deps/catch2-src" ]; then
  dep_args+=("-DFETCHCONTENT_SOURCE_DIR_CATCH2=$root/build/_deps/catch2-src")
fi

cmake -S "$root" -B "$build" "${generator[@]}" \
  -DCMAKE_BUILD_TYPE=Debug \
  -DJOT_SANITIZE=ON \
  -DBUILD_TESTING=ON \
  "${dep_args[@]}" >/dev/null || exit 1
cmake --build "$build" -j "$(nproc)" || exit 1

rm -f "$log".*
ASAN_OPTIONS=detect_leaks=1:halt_on_error=0:fast_unwind_on_malloc=0:log_path="$log" \
  "$build/test/jot_tests" "$@" >/dev/null 2>&1
status=$?

reports=$(ls "$log".* 2>/dev/null)
if [ -n "$reports" ]; then
  cat $reports
  printf 'leak check: FAIL -- %s leak report(s), %s record(s)\n' \
    "$(printf '%s\n' $reports | wc -l)" \
    "$(cat $reports | grep -c 'leak of')"
  exit 1
fi

printf 'leak check: no leaks reported (suite exit %s)\n' "$status"
