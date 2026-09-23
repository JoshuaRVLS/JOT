# Report 001, 2026-09-23

Follow-up to `audit-001-2026-09-23.md`. Mode: **fix what the audit found**,
except finding 5, which is measured below and only started.

## Fixed

### Finding 1, the em dash in what the editor says (R-02)

All 19 strings replaced with a comma, colon or parentheses. No behaviour
change; the tests that assert them pass.

### Findings 2 and 3, theme contrast (R-25, R-34)

Every text pairing in all four bundled themes now clears 4.5:1. One hex was
moved per failing *role*: a fix is computed against every background the hex is
text on, and applied only where it is text, because a hex can be ink in one
group and a fill in another (flexoki's LineNr ink is also SidebarSel's fill).
Where the roles collide they stay two shades; where unification was safe the
value was applied file-wide.

| Theme | Before | After |
| --- | --- | --- |
| jot-dark | 1 pairing under 4.5:1 | 0 |
| jot-light | 26 | 0 |
| flexoki-dark | 12 | 0 |
| flexoki-light | 27 | 0 |

The `Comment` value the owner picked (`#8d7f8b` to `#7a6b78`) is in, and it is
the shared ink for `Comment` and `WinbarBreadcrumb`, so it is measured against
both surfaces: 4.53:1 on the winbar's `#fdf1f2`, 4.82:1 on the paper.

Not moved, on purpose: the border and separator fills (finding 7). They are
shapes, not text, and the audit recorded them as an explicit choice. They still
read 1.16:1 to 2.16:1.

`docs/THEMES.md` was updated to the new light-theme inks, and the pinned values
in `test/test_themes.cpp` and `test/theme_probe.py` follow.

### Finding 4, the settings panel on a narrow terminal (R-03, R-35)

The panel's width floor is the screen now, the margin shrinks to nothing and
the columns take what is left, so the right border, the divider and the values
stay on screen at 30 and 44 columns.

### Finding 6, the em dash outside the messages (R-02)

337 characters in 100 files, comments and docs, replaced with a spaced hyphen,
which stays grammatical in every shape the character appeared in here (the
aside, the definition label, the lone table cell that means "none").
`antislop.md`, `.claude/`, `.commandcode/` and `anti-slop/` are out of scope:
the first two document the rule and quote the character as evidence, and the
last two are this audit's own numbers.

Remaining U+2014 in `src/`, `runtime/`, `test/`: 8, all of them a literal in a
rule-documenting or probe-output string.

## Started, not finished

### Finding 5, comment blocks over two lines (R-31)

The cap is the skill's: one line, two at most, and a blank `//` still counts.

| Scope `src/ runtime/ test/` | Blocks | Lines |
| --- | --- | --- |
| before | 2110 | 10238 |
| after | 2081 | 9904 |

29 blocks and 334 lines, concentrated in the files this session's work added
to: the settings surface (`src/jot/surfaces/settings.cpp`,
`src/render/settings.cpp`, `runtime/lua/features/ui/settings.lua`), the winbar
(`src/features/winbar.*`, `src/render/winbar.cpp`,
`runtime/lua/features/ui/winbar.lua`), the shared state and model structs
(`src/jot/state/surface_state.h`, `src/jot/model/quick_pick.h`), plus
`folding.h`, `smooth_scroll.h`, `terminal.h`, `terminal.cpp`, `buffer.cpp`,
`dispatcher.cpp`, `api.h` and `preview_server.h`. A box-drawn section banner in
`preview_server.h` went with them (decorative separator, R-31).

What is left is the repo's house style, and compacting it is a ~9900-line job
over roughly 2000 blocks in nearly every file. Two exclusions are still the
owner's call: `runtime/lua/luals/jot_api.lua`, which is type documentation for
the Lua surface and mirrors `docs/LUA_API.md`, and the file-header block
itself, which is what a module says it is for.

Reproduce the count:

```
python3 - <<'PY'
import os
SKIP = {'.git','build','build-tests','build-asan','.configs','.claude','.commandcode','anti-slop','third_party'}
tot = lines = 0
for sub in ('src','runtime','test'):
    for root, dirs, files in os.walk(sub):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if not f.endswith(('.cpp','.h','.hpp','.c','.lua')): continue
            run = 0
            for line in open(os.path.join(root,f), encoding='utf-8', errors='replace').read().splitlines() + ['']:
                s = line.strip()
                if s.startswith(('//','--')) or (s.startswith('*') and not s.startswith('*/')): run += 1
                else:
                    if run >= 3: tot += 1; lines += run
                    run = 0
print(tot, lines)
PY
```

## Recorded, no change

Finding 7 (border and separator fills under 3:1) and finding 8 (the purpose of
each panel affordance) stay as the audit filed them, in
`audit-001-2026-09-23.md`.

## Verification

- `ctest`: 590/590.
- `test/theme_probe.py` against the real binary: PASS, all four themes and both
  legacy aliases, with the retuned accent inks read out of the escape stream.
- `test/settings_probe.py`: PASS, 10 scenes.
- The settings panel dumped at 30 and 44 columns: no clipping.
