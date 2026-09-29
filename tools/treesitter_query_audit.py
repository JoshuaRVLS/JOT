#!/usr/bin/env python3
"""Audit the bundled tree-sitter highlight queries against their grammars.

A tree-sitter query compiles as one unit: a single node name the grammar does
not define fails the whole file, and the editor then quietly drops that language
to the regex fallback while :tsstatus still reports its parser as installed.
That is how `(preproc_endif)` in the bundled `c` query disabled highlighting for
every .c and .h file.

This walks runtime/lua/treesitter/queries/<language>/highlights.scm, pulls the
node names each query references, and compares them with the named node types of
the grammar the registry points at (its generated src/node-types.json). A name
the grammar does not define is reported; names starting with `_` are supertypes
and wildcards, and ERROR / MISSING are query built-ins, so they are ignored.

The grammar files are downloaded once and cached under the cache directory, so a
second run needs no network. A grammar whose node-types.json cannot be fetched
is reported as unchecked rather than guessed at.

Usage:
    tools/treesitter_query_audit.py [--queries DIR] [--registry FILE]
                                    [--cache DIR] [--json] [--jobs N]

Exit codes: 0 clean (or nothing checkable), 1 findings, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_QUERIES = os.path.join(REPO_ROOT, "runtime", "lua", "treesitter", "queries")
DEFAULT_REGISTRY = os.path.join(REPO_ROOT, "runtime", "lua", "treesitter", "registry.lua")
DEFAULT_CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", "/tmp"), "jot_ts_query_audit")

# `{name, "url", {".ext", ...}, "subdir", {"alias"}}` - one registry entry.
ENTRY = re.compile(
    r'\{\s*"([a-z0-9_]+)"\s*,\s*"([^"]+)"\s*,\s*\{([^}]*)\}\s*'
    r'(?:,\s*"([^"]*)"\s*)?(?:,\s*\{[^}]*\}\s*)?\}'
)

# The first identifier after a `(`, i.e. the node reference. `(#pred ...)` and
# string tokens never match because `(` is not followed by a letter or `_`.
NODE_REF = re.compile(r"\(([A-Za-z_][A-Za-z0-9_]*)")

BUILTINS = {"ERROR", "MISSING"}


def parse_registry(path: str) -> dict[str, dict]:
    text = open(path, encoding="utf-8").read()
    languages: dict[str, dict] = {}
    for match in ENTRY.finditer(text):
        name, url, extensions, subdir = match.group(1), match.group(2), match.group(3), match.group(4)
        languages[name] = {
            "url": url,
            "subdir": (subdir or "").strip(),
            "extensions": re.findall(r'"([^"]+)"', extensions),
        }
    return languages


def strip_comments_and_strings(text: str) -> str:
    """Query source without `;` comments or quoted strings.

    Both carry parentheses that are not node references: a predicate's regex
    (`(#match? @x "_(mean|sum)")`) and a literal token (`"(key)"`). Leaving them
    in reports dozens of phantom node names.
    """
    out = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == ";":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if char in ('"', "'"):
            quote = char
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


def node_names(path: str) -> set[str]:
    """Named node types a highlights.scm references."""
    text = open(path, encoding="utf-8").read()
    stripped = strip_comments_and_strings(text)
    return {name for name in NODE_REF.findall(stripped) if not name.startswith("_")}


def raw_candidates(url: str, name: str, subdir: str) -> list[str]:
    match = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not match:
        return []
    owner, repo = match.group(1), match.group(2)
    # Grammars lay their generated node types out differently: at the repo root,
    # beside a language directory, in a tree-sitter-<name> subdir (markdown,
    # typescript), under grammars/ (ocaml), or under the bare language name
    # (php, csv, xml). Try them all before giving up on a language.
    paths = []
    if subdir:
        paths.append(f"{subdir}/src/node-types.json")
    paths.extend(
        [
            "src/node-types.json",
            f"tree-sitter-{name}/src/node-types.json",
            f"{name}/src/node-types.json",
            f"grammars/{name}/src/node-types.json",
        ]
    )
    out = []
    for branch in ("master", "main", "develop"):
        for path in paths:
            out.append(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}")
    return out


def fetch_node_types(name: str, url: str, subdir: str, cache_dir: str) -> tuple[str, set[str] | None]:
    """The grammar's named node types, or None when they could not be fetched."""
    cache = os.path.join(cache_dir, f"{name}.json")
    if os.path.exists(cache):
        try:
            data = json.load(open(cache, encoding="utf-8"))
            return name, {entry["type"] for entry in data if entry.get("named")}
        except (ValueError, KeyError):
            os.remove(cache)

    for candidate in raw_candidates(url, name, subdir):
        try:
            with urllib.request.urlopen(candidate, timeout=20) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, "wb") as fh:
            fh.write(raw)
        return name, {entry["type"] for entry in data if entry.get("named")}
    return name, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", default=DEFAULT_QUERIES)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()

    for path in (args.queries, args.registry):
        if not os.path.exists(path):
            print(f"treesitter query audit: missing {path}", file=sys.stderr)
            return 2

    languages = parse_registry(args.registry)
    report = {"checked": 0, "unchecked": [], "findings": {}, "clean": []}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(fetch_node_types, name, meta["url"], meta["subdir"], args.cache): name
            for name, meta in languages.items()
        }
        for future in concurrent.futures.as_completed(futures):
            name, known = future.result()
            if known is None:
                report["unchecked"].append(name)
                continue
            query = os.path.join(args.queries, name, "highlights.scm")
            if not os.path.exists(query):
                continue
            used = node_names(query)
            unknown = sorted(used - known - BUILTINS)
            report["checked"] += 1
            if unknown:
                report["findings"][name] = unknown
            else:
                report["clean"].append(name)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["findings"] else 0

    for name in sorted(report["findings"]):
        print(f"{name}: {', '.join(report['findings'][name])}")
    print(
        f"\nchecked {report['checked']} grammars, "
        f"{len(report['findings'])} with unknown node names, "
        f"{len(report['unchecked'])} not checkable"
    )
    if report["unchecked"]:
        print("not checkable (no node-types.json): " + ", ".join(sorted(report["unchecked"])))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
