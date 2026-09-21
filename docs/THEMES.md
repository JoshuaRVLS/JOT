# jot Theme Guide

Themes are native JSON data. The primary location is
`~/.config/jot/configs/colors/`; legacy `~/.config/jot/themes/` is also scanned.

jot ships four themes: its own pair, and a port of kepano's Flexoki.

| Theme | Look |
|---|---|
| `jot-dark` (default) — *yoru ramune* | indigo night (`#131024`) under neon sakura-pink keywords, mint-ramune strings, soda-cyan functions and tangerine numbers |
| `jot-light` — *mochi milk* | the same inks as strawberry jam, matcha and ramune blue on warm cream paper (`#fffaf4`) |
| `flexoki-dark` | Flexoki's ink palette: black (`#100f0f`) paper, base-200 text, green keywords, cyan strings |
| `flexoki-light` | the same scheme on Flexoki paper (`#fffcf0`) with the 600-step accents |

### jot-dark — "yoru ramune" (night soda)

A deep indigo night — not the usual neutral `#1c1c1c`, and not a grey-blue
terminal default — lit by a candy-stand palette: neon **sakura pink**
(`#ff79c0`) for keywords, tags and every accent; **mint ramune** (`#79e8bd`)
for strings; **soda cyan** (`#63d9ff`) for functions and builtins;
**tangerine** (`#ff9f6e`) for numbers and constants; **dango gold**
(`#ffd479`) for escapes, search hits and warnings; **taro purple**
(`#c09bff`) for types, namespaces and the explorer's folders. Every piece of
chrome is the same night sky lifted one step — sidebar `#181430`, status line
`#1a1630`, raised rows `#282147` — so the panels feel like rooms in one house
instead of three unrelated widgets.

### jot-light — "mochi milk"

Warm milk paper (`#fffaf4`) with the dark theme's inks re-pitched for daylight:
**strawberry jam** (`#d92a76`) keywords, **matcha** (`#0e8a6f`) strings,
**ramune blue** (`#1b7fb5`) functions, **taro** (`#7b4bd1`) types and
**persimmon** (`#c95a2b`) numbers. The surfaces (sidebar `#fdf1f2`, status line
`#fbe9e7`, selection `#f8d8e3`) are blush tints of the same cream, so the whole
window reads as one pastel confection rather than white boxes on white.

The sakura/strawberry accent is the jot palette's signature: keywords, tags, the
active pane border, the cursor, the cursor line number and the focused terminal
tab all carry it, set against mint strings and taro types on the indigo (or
cream, on paper) ground. The chrome reads as jot's own pastel scheme rather than
a neutral grey editor with a blue border.

The Flexoki pair is [Flexoki](https://stephango.com/flexoki) by kepano (MIT
licensed), ported slot by slot from the official VS Code and Helix themes: the
400-step accents on the dark surfaces, the 600-step accents on paper, and the
base steps (50-950) for text, line numbers, borders and every panel surface.
Accent roles follow the upstream themes rather than jot's -- keywords are green,
numbers purple, operators red -- which is what makes it recognisably Flexoki.

Every shipped theme is an exact 24-bit palette: every slot names a `#rrggbb`
colour, and jot paints it verbatim (SGR `38;2`/`48;2`, or the exact value in the
GUI) instead of rounding it to the nearest of 256 entries. A slot still accepts
an xterm 256 index for a theme written that way, and the two forms can be mixed
in one file. Tune any slot by copying a file to `~/.config/jot/configs/colors/`.
The names of the themes that used to be bundled (`dark` and `light`) still
resolve to the jot theme that replaced them, so a `color_scheme` written before
the change keeps working. Any other name now needs a file of its own.

Apply with `:colorscheme jot-light` or from Lua:

```lua
set_hl("Normal", { fg = "#e9e5fb", bg = "#131024" })
set_hl("Keyword", { fg = 215 }) -- an xterm index still works
```

Theme files contain highlight groups mapped to foreground/background colours:
an exact `"#rrggbb"` string, or an xterm 256 index as a number. A three-digit
`"#rgb"` is accepted and expanded, and an eight-digit `"#rrggbbaa"` drops its
alpha channel (every surface composites over an opaque background). A value that
is neither is ignored, leaving the slot at whatever it inherited. Use `-1` or
`null` for transparent/inherited values.

```json
{
  "Normal": {"fg": "#e9e5fb", "bg": "#131024"},
  "Comment": {"fg": "#8b83b0", "bg": "#131024"},
  "Keyword": {"fg": "#ff79c0", "bg": "#131024"},
  "Visual": {"fg": 231, "bg": 240}
}
```

The same two forms are accepted wherever the Lua API takes a colour — `set_hl`,
`jot.theme.set_color`, and a decoration's `fg`, `bg`, `underline_fg`, `virt_fg`
and `virt_bg` (`jot.decoration.set`) — so a plugin or a theme can name the exact
colour it means instead of hunting for the nearest index. `jot.decoration.list()`
hands an exact colour back as the `#rrggbb` string it was set with, and a palette
index back as a number; a value that is neither is ignored, leaving that colour
unset rather than painting a wrong one.

Exact colours need a terminal that understands 24-bit colour (`COLORTERM`
reports `truecolor`/`24bit`, or `TERM` ends in `-direct`); elsewhere jot folds
each one down to the nearest palette entry — text, background and underline
alike — so the theme still reads correctly on a 256-colour terminal. The
`truecolor` setting forces the answer either way.

## Group names

Group names accept a few spellings. Neovim-style group names (`"Normal"`,
`"StatusLineInfo"`, `"Pmenu"`, `"TerminalTabFocused"`) are translated to their
jot slot; tree-sitter capture style (`"@keyword.control"`, `"@property"`) drops
the `@` and maps to the same slot as the dotted/snake form below.

### Syntax (tree-sitter and regex) slots

Each capture produced by a query maps to one of these slots. Slots marked
*inherited* fall back to a base color when a theme does not set them; setting
one explicitly in a theme overrides the fallback.

| Slot | What it colors | Falls back to |
|---|---|---|
| `keyword` | generic keywords | — |
| `keyword.control` | `if`, `for`, `while`, `return`, `break` | `keyword` |
| `keyword.storage` | `static`, `const`, `class`, `struct` | `type` |
| `keyword.directive` | `#include`, `#define`, imports | `constant` |
| `string` | string literals | — |
| `string.escape` | `\n`, `\t` inside strings | `builtin` |
| `comment` | comments | — |
| `number` | numeric literals | — |
| `function` | function definitions and calls | — |
| `function.method` | method calls/definitions | `function` |
| `function.constructor` | `Foo()` constructions | `type` |
| `type` | type identifiers | — |
| `type.builtin` | `int`, `float`, `auto` | `builtin` |
| `variable` | plain identifiers | `default` |
| `parameter` | function parameters | `default` |
| `property` | `obj.field`, members (alias: `field`) | `default` |
| `constant` | constants | `number` |
| `constant_macro` | `#define NAME`, preprocessor constants | `constant` |
| `builtin` | `True`/`None`, builtin functions | `type` |
| `operator` | `+`, `==`, `->` | `keyword` |
| `tag` | markup tags (`<div>`) | `keyword` |
| `attribute` | markup tag attributes (`class=`) | `type` |
| `namespace` | `namespace`, package qualifiers | `default` |
| `module` | imported module names | `default` |
| `punctuation` | generic punctuation | `default` |
| `punctuation.bracket` | `() [] {}` | `punctuation` |
| `punctuation.delimiter` | `, ; .` | `punctuation` |

### UI slots

`Normal`, `NormalFloat`, `LineNr`, `Comment`, `Keyword`, `String`, `Number`,
`Function`, `Type`, `Cursor`, `CursorLine`, `CursorLineNr`, `Visual`,
`Search`, `CurSearch`, `StatusLine`,
`StatusLineMsg`, `StatusLineLogo`, `StatusLineFile`, `StatusLineInfo`,
`StatusLineWarn`, `StatusLineError`, `StatusLineMuted`, `FloatBorder`,
`WinSeparator`, `WinActiveBorder`, `TabLine`, `TabLineSel`, `TabLineFill`,
`TabClose`, `Sidebar`, `SidebarDir`, `SidebarSel`, `SidebarSelNC`,
`SidebarBorder`, `DiagnosticError`, `DiagnosticWarn`, `DiagnosticInfo`,
`DiagnosticHint`, `Pmenu`, `PmenuSel`, `TelescopeNormal`,
`TelescopeSelection`, `TelescopePreviewNormal`, `TelescopeQuery`,
`Terminal`, `TerminalTab`,
`TerminalTabActive`, `TerminalTabFocused`, `TerminalTabClose`,
`TerminalTabPlus`, `TerminalTabSeparator`.

`CursorLine` tints the row under the text cursor (`bg` only is enough) and
`CursorLineNr` colors its line number; `CurSearch` is the highlight of the
search result the cursor currently sits on (the next/previous match when
stepping), which reads brighter than the plain `Search` hits so the current
target never blends into the rest. The cursor-row tint can be turned off with
the `highlight_cursor_line` setting (`false`), and both `CurSearch` and
`CursorLine` fall back to sensible defaults when a theme omits them.

Git status colors for file rows (git view), file tabs, and the diff panel use
`git_modified`, `git_added`, `git_untracked`, `git_deleted`, `git_renamed`,
and `git_conflict` (`fg` text / `bg` row tint). Themes that omit them fall
back to the built-in ANSI defaults, which do not match the colorscheme.

The four `Telescope*` slots paint the file finder's two boxes: `TelescopeNormal`
is the result list (a panel, so its `bg` should match `Sidebar`/`Pmenu`),
`TelescopeSelection` the band on the selected and hovered row, and
`TelescopeQuery` the query field's own band while it has focus.
`TelescopePreviewNormal` is the file view on the right, which is meant to read
as a small editor, so its `bg` should be the editor's `Normal` background.

## Authoring a theme

### Inherit a base with `extends`

A theme only needs to list what it overrides. Start from any bundled theme and
keep its full look — file explorer, status bar, git colors, diagnostics, and
syntax — by extending it:

```json
{
  "extends": "jot-dark",
  "Comment": {"fg": 244},
  "keyword.control": {"fg": 214},
  "StatusLine": {"fg": 188, "bg": 236}
}
```

Any group the theme does not set is inherited from the base (`extends` chains
depth and cycles are guarded). Without `extends`, unlisted groups fall back to
the built-in ANSI defaults, which is why explorer and status bar colors would
otherwise not follow a minimal custom theme.

### Standalone theme

For a fully self-contained theme, copy a bundled theme as a starting point,
then tune the syntax slots:

```bash
mkdir -p ~/.config/jot/configs/colors
cp .configs/configs/colors/jot-dark.json ~/.config/jot/configs/colors/mine.json
```

A minimal annotated theme:

```json
{
  "Normal": {"fg": "#d7d0c4", "bg": "#131024"},         // plain text / editor background
  "Comment": {"fg": "#8b83b0", "bg": "#131024"},        // comments
  "keyword": {"fg": "#d7afd7", "bg": "#131024"},        // all keywords
  "keyword.control": {"fg": "#af87d7", "bg": "#131024"}, // if/for/while - override control
  "string": {"fg": "#79e8bd", "bg": "#131024"},         // string literals
  "number": {"fg": "#ff9f6e", "bg": "#131024"},         // numbers, constants fall back here
  "function": {"fg": "#63d9ff", "bg": "#131024"},       // function names
  "function.method": {"fg": "#63d9ff", "bg": "#131024"}, // method names (optional: keep = function)
  "type": {"fg": "#c09bff", "bg": "#131024"},           // type identifiers
  "property": {"fg": "#a3e084", "bg": "#131024"},       // obj.field members
  "punctuation": {"fg": "#8f86b8", "bg": "#131024"},    // dim the brackets/semicolons
  "tag": {"fg": "#ff79c0", "bg": "#131024"},            // HTML/JSX tags
  "attribute": {"fg": "#ffb877", "bg": "#131024"}       // HTML/JSX tag attributes
}
```

### Bringing in a VSCode pack

`tools/vscode_themes_import.py <pack-dir> <output-dir>` converts a VSCode theme
pack (colors + TextMate scopes) into jot schemes; point the output at
`~/.config/jot/configs/colors` so the imports stay yours. jot's own two themes
are never overwritten by an import. The source's colours are written through as
hex, so an imported scheme keeps the 24-bit values it was designed with instead
of landing on the nearest palette entry.

Rules:

- Colors are exact `"#rrggbb"` strings; an xterm 256 index (0-255) is also
  accepted and can be mixed with hex values in one file. `fg`/`bg` of `-1`
  leaves that side untouched so a group can change only one side.
- Any slot you omit falls back per the table above; base slots are
  `default`, `keyword`, `string`, `comment`, `number`, `function`, `type`.
- JSON keys are matched against slot names directly — dotted and snake forms
  both work (`"keyword.control"` / `"keyword_control"`,
  `"constant.macro"` / `"constant_macro"`, `"property"` / `"field"`), and a
  leading `@` is ignored so tree-sitter capture names can be used as-is.
- `:colorscheme <name>` switches live; the choice persists in the settings
  file (`color_scheme`). Names resolve case-insensitively.
- After editing a theme file, switch away and back (`:colorscheme jot-dark`,
  `:colorscheme mine`) or restart to reload it.
