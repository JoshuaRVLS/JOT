-- Highlights TODO-style comments - a port of folke/todo-comments.nvim, rebuilt
-- on jot's own APIs: decorations for the paint, the syntax highlighter for the
-- "only in comments" gate, the workspace walk for the search.
--
-- Disable with:   jot.config.set("todo_comments", false)
-- Extra keywords: jot.config.set("todo_comments_keywords", "SECURITY,REVIEW")
--                 (comma separated; each takes the default colour)
--
-- Alt+] t / Alt+[ t jump to the next/previous comment (the chord joins the
-- Alt+]/Alt+[ jump family, but is registered here because :TodoNext/:TodoPrev
-- are this feature's own commands).
-- :Todo lists them across the workspace; `keywords=TODO,FIX` filters it the way
-- upstream's `:TodoTelescope keywords=...` does.
--
-- The matched keyword wears a band in its family's colour and the text after
-- the colon takes the same ink, upstream's default "wide" highlight. The
-- colours are resolved from the live theme at paint time, so a colourscheme
-- switch re-inks the bands instead of leaving the old palette on screen.
--
-- The comment gate uses jot.syntax.highlight, the regex fallback the renderer
-- keeps for languages without a tree-sitter grammar, so a keyword is only
-- painted when that line's own spans say comment. Two limits come with it: an
-- extension the fallback has no rules for fails open (painted anywhere, which
-- is where upstream stands without a parser), and a multi-line block comment's
-- continuation lines are not recognized, because the fallback sees one line at
-- a time.

local jot = jot

-- This feature deletes only the decorations it created: jot.decoration.clear()
-- wipes every decoration on the buffer, which would take the diagnostics'
-- squiggles (or, the other way round, wipe these bands the moment the language
-- server publishes).
local owned = {}

-- Buffers already painted. CursorMoved fires on every key without the text
-- changing, so the first sight of a buffer is worth a paint and the rest are
-- left to BufChange (or to a config change, which forces).
local seen = {}

-- The last (enabled, keywords) pair the feature painted for. CursorMoved only
-- re-reads a buffer when this moved: the settings menu re-applies config live
-- but fires no event of its own, so the next cursor move is where a toggle
-- takes effect.
local last_signature = nil

-- Buffer -> last path painted, so a theme switch can repaint the buffers that
-- already carry bands without waiting for each one to be touched.
local paths = {}

local ENABLED_KEY = "todo_comments"
local KEYWORDS_KEY = "todo_comments_keywords"

-- Upstream's default keywords, each family mapping to a colour name. A custom
-- keyword added with todo_comments_keywords joins the `default` family.
local FAMILIES = {
  { key = "FIX", color = "error", alt = { "FIXME", "BUG", "FIXIT", "ISSUE" } },
  { key = "TODO", color = "info" },
  { key = "HACK", color = "warning" },
  { key = "WARN", color = "warning", alt = { "WARNING", "XXX" } },
  { key = "PERF", color = "default", alt = { "OPTIM", "PERFORMANCE", "OPTIMIZE" } },
  { key = "NOTE", color = "hint", alt = { "INFO" } },
  { key = "TEST", color = "test", alt = { "TESTING", "PASSED", "FAILED" } },
}

-- Colour name -> jot theme group. The diagnostic groups are the theme's own
-- ink for error/warning/info/hint; `default` (upstream's violet, what a custom
-- keyword gets) and `test` (upstream's magenta) take the two syntax inks that
-- stand nearest, so every family still comes from the theme.
local FAMILY_GROUP = {
  error = "diagnostic_error",
  warning = "diagnostic_warning",
  info = "diagnostic_info",
  hint = "diagnostic_hint",
  default = "type",
  test = "keyword",
}

-- Upstream ignores very long lines (highlight.max_line_len): a minified file
-- is one 50k line, and scanning it per keystroke is the whole cost for nothing.
local MAX_LINE_LEN = 400

-- Above the C/C++ dim (priority 1), below the diagnostic squiggle (10).
local BAND_PRIORITY = 8
local TEXT_PRIORITY = 7

local function enabled()
  return jot.config.get(ENABLED_KEY, "true") ~= "false"
end

local function extra_keywords()
  return tostring(jot.config.get(KEYWORDS_KEY, "") or "")
end

local function signature()
  return tostring(enabled()) .. "|" .. extra_keywords()
end

-- The keyword -> colour family map: upstream's defaults, plus whatever
-- todo_comments_keywords adds (merge_keywords, upstream calls it).
local function configured_keywords()
  local map = {}
  for _, family in ipairs(FAMILIES) do
    map[family.key] = family.color
    for _, alt in ipairs(family.alt or {}) do
      map[alt] = family.color
    end
  end
  for name in extra_keywords():gmatch("[^,%s]+") do
    if map[name] == nil then
      map[name] = "default"
    end
  end
  return map
end

-- Keywords are matched as Lua patterns, so any magic character in a custom
-- keyword (unlikely, but the setting is free text) has to be escaped.
local function pattern_escape(text)
  return (tostring(text or ""):gsub("([%%%^%$%(%)%.%[%]%*%+%-%?])", "%%%1"))
end

-- The keyword set as a matcher. Lua patterns have no alternation: the `(A|B)`
-- upstream's regex uses matches a literal `|`, so a pattern built by joining
-- the keywords would never match anything. Instead the line is scanned for
-- `word%f...:` candidates and the word is looked up in `names` - one pass per
-- line, and `%f[%w_]` is the boundary upstream's `<` spells, so `mytodo:` is
-- not a hit. A custom keyword with characters outside `[%w_]` (the setting is
-- free text) cannot be captured that way, so it gets a pattern of its own and
-- both kinds compete for the earliest match.
local function compile_matcher(keywords)
  local names, special, count = {}, {}, 0
  for keyword in pairs(keywords) do
    count = count + 1
    if keyword:match("^[%w_]+$") then
      names[keyword] = true
    else
      special[#special + 1] = {
        pattern = "%f[%w_]" .. pattern_escape(keyword) .. "%s*:",
        keyword = keyword,
      }
    end
  end
  if count == 0 then
    return nil
  end
  -- Longest first so FIXME is preferred over FIX when both sit at one start.
  table.sort(special, function(a, b)
    if #a.keyword ~= #b.keyword then
      return #a.keyword > #b.keyword
    end
    return a.keyword < b.keyword
  end)
  return { names = names, special = special }
end

-- The first whole word on the line that is a keyword and is followed by a
-- colon, as `start1, finish1, keyword`. `finish1` is the 1-based end of the
-- colon (whitespace before it included), which is what the band length needs.
local function find_word(line, names, at)
  local start1, finish1, word = line:find("%f[%w_]([%w_]+)%s*:", at)
  while start1 do
    if names[word] then
      return start1, finish1, word
    end
    start1, finish1, word = line:find("%f[%w_]([%w_]+)%s*:", start1 + 1)
  end
  return nil
end

local function find_match(line, matcher)
  if not matcher then
    return nil
  end
  local start1, finish1, keyword = find_word(line, matcher.names, 1)
  for _, entry in ipairs(matcher.special) do
    local s, f = line:find(entry.pattern)
    if s and (not start1 or s < start1 or (s == start1 and f > finish1)) then
      start1, finish1, keyword = s, f, entry.keyword
    end
  end
  return start1, finish1, keyword
end

-- Returns keyword, colour family, keyword start (0-based) and the band's end
-- (0-based, exclusive: through the colon, plus one plain byte when the line
-- has one there, the way upstream paints the space after the colon).
local function match_line(line, matcher, keywords)
  if not matcher or line == "" or #line > MAX_LINE_LEN then
    return nil
  end
  local start1, finish1, keyword = find_match(line, matcher)
  if not start1 then
    return nil
  end
  local band_end = finish1
  local next_byte = line:sub(finish1 + 1, finish1 + 1)
  -- Only a single-byte character is folded in: extending one byte into a
  -- multi-byte glyph would hand the renderer half a rune.
  if next_byte ~= "" and next_byte:byte() < 128 then
    band_end = band_end + 1
  end
  return keyword, keywords[keyword] or "default", start1 - 1, band_end
end

-- The byte offset the comment holding `col` starts at, or false when the line
-- has spans and none of them is a comment there. nil means the editor cannot
-- tell (no rules for the extension); callers fail open, as upstream does with
-- no tree-sitter parser. The comment's own start is returned because that is
-- where the band should begin: `int x; // TODO:` must not band the code.
local function comment_start_at(ext, line, col)
  if ext == "" or type(jot.syntax) ~= "table" or type(jot.syntax.highlight) ~= "function" then
    return nil
  end
  local ok, spans = pcall(jot.syntax.highlight, ext, line)
  if not ok or type(spans) ~= "table" then
    return nil
  end
  if spans.rules == false then
    return nil
  end
  for _, span in ipairs(spans) do
    if span.kind == "comment" and col >= span.start and col < span.start + span.len then
      return span.start
    end
  end
  return false
end

local function extension_of(path)
  local ext = tostring(path or ""):match("(%.[%w_+]+)$")
  return ext or ""
end

--------------------------------------------------------------------- colours

-- "#rrggbb" -> r, g, b, or nil when the text is not a colour.
local function hex_rgb(text)
  local r, g, b = tostring(text or ""):match("^#(%x%x)(%x%x)(%x%x)$")
  if not r then
    return nil
  end
  return tonumber(r, 16), tonumber(g, 16), tonumber(b, 16)
end

local function relative_luminance(r, g, b)
  local function channel(value)
    local c = value / 255
    if c <= 0.03928 then
      return c / 12.92
    end
    return ((c + 0.055) / 1.055) ^ 2.4
  end
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
end

-- The ink a band's text is written in: whichever of the theme's normal text
-- and background contrasts more with the band (upstream's maximize_contrast).
local function readable_ink(band_hex, normal)
  local r, g, b = hex_rgb(band_hex)
  if not r then
    return nil
  end
  local band_luminance = relative_luminance(r, g, b)
  local candidates = {
    normal and normal.fg or "#ffffff",
    normal and normal.bg or "#000000",
  }
  local best = candidates[1]
  local best_ratio = -1
  for _, candidate in ipairs(candidates) do
    local cr, cg, cb = hex_rgb(candidate)
    if cr then
      local candidate_luminance = relative_luminance(cr, cg, cb)
      local low, high = band_luminance, candidate_luminance
      if low > high then
        low, high = high, low
      end
      local ratio = (high + 0.05) / (low + 0.05)
      if ratio > best_ratio then
        best_ratio = ratio
        best = candidate
      end
    end
  end
  return best
end

-- Colour family -> { fg = band ink, ink = text written on the band }, resolved
-- from the live theme. A family whose group the theme does not name is left
-- out rather than painted with an invented colour.
local function family_accents()
  local normal = nil
  local accents = {}
  for family, group in pairs(FAMILY_GROUP) do
    local ok, resolved = pcall(jot.theme.get, group)
    if ok and type(resolved) == "table" and type(resolved.fg) == "string" then
      if not normal then
        local nok, colors = pcall(jot.theme.get, "normal")
        if nok and type(colors) == "table" then
          normal = colors
        end
      end
      accents[family] = { fg = resolved.fg, ink = readable_ink(resolved.fg, normal) }
    end
  end
  return accents
end

---------------------------------------------------------------- decorations

local function release(buffer)
  local ids = owned[buffer]
  if not ids then
    return
  end
  for _, id in ipairs(ids) do
    jot.decoration.delete(buffer, id)
  end
  owned[buffer] = {}
end

local function keep(buffer, id)
  if type(id) == "number" and id ~= 0 then
    local ids = owned[buffer]
    if not ids then
      ids = {}
      owned[buffer] = ids
    end
    ids[#ids + 1] = id
  end
end

-- Turns the lines of one buffer into decoration specs. Pure but for the comment
-- gate, which asks the syntax highlighter per matching line.
local function scan_lines(lines, ext, keywords, matcher, accents)
  local specs = {}
  -- The accent a match carries into the following comment lines (upstream's
  -- multiline): set by a painted keyword, cleared when the run or the comment
  -- ends.
  local carried = nil
  for index, line in ipairs(lines) do
    local keyword, family, start0, band_end = match_line(line, matcher, keywords)
    if keyword then
      local comment_start = comment_start_at(ext, line, start0)
      local accent = accents[family]
      if comment_start ~= false and accent then
        local band_start = comment_start or start0
        specs[#specs + 1] = {
          row = index,
          col = band_start + 1,
          width = band_end - band_start,
          bg = accent.fg,
          fg = accent.ink,
          priority = BAND_PRIORITY,
        }
        if band_end < #line then
          specs[#specs + 1] = {
            row = index,
            col = band_end + 1,
            width = #line - band_end,
            fg = accent.fg,
            priority = TEXT_PRIORITY,
          }
        end
        carried = { accent = accent, col = start0 }
      else
        carried = nil
      end
    elseif carried and line ~= "" and comment_start_at(ext, line, carried.col) ~= false then
      specs[#specs + 1] = {
        row = index,
        col = 1,
        width = #line,
        fg = carried.accent.fg,
        priority = TEXT_PRIORITY,
      }
    else
      carried = nil
    end
  end
  return specs
end

--------------------------------------------------------------- buffer reads

-- jot.buffer.get_line is 1-based and takes the buffer. The buffer text is
-- empty while a file is still being opened (BufOpen fires before the text is
-- in), so the caller falls back to reading the file from disk.
-- jot.buffer.get_text reads the whole buffer in one crossing, but only when it
-- is the buffer on screen; the per-line fallback costs one crossing per line.
local function current_id()
  local ok, value = pcall(jot.buffer.current)
  if not ok or value == nil then
    return nil
  end
  if type(value) == "number" then
    return value
  end
  if type(value) == "table" then
    return value.id or value.buffer or value.index
  end
  return nil
end

local function current_path()
  local candidates = {}
  if jot.buffer and type(jot.buffer.current_file) == "function" then
    candidates[#candidates + 1] = jot.buffer.current_file
  end
  if type(jot.current_file) == "function" then
    candidates[#candidates + 1] = jot.current_file
  end
  if jot.editor and type(jot.editor.current_file) == "function" then
    candidates[#candidates + 1] = jot.editor.current_file
  end
  for _, fn in ipairs(candidates) do
    local ok, value = pcall(fn)
    if ok and type(value) == "string" and value ~= "" then
      return value
    end
  end
  return ""
end

local function whole_buffer_text(buffer)
  if type(jot.buffer.get_text) ~= "function" or current_id() ~= buffer then
    return nil
  end
  local ok, text = pcall(jot.buffer.get_text)
  if not ok or type(text) ~= "string" or text == "" then
    return nil
  end
  return text
end

local function split_lines(text)
  local lines = {}
  for line in (text .. "\n"):gmatch("([^\n]*)\n") do
    lines[#lines + 1] = line
  end
  return lines
end

local function buffer_lines(buffer)
  local whole = whole_buffer_text(buffer)
  if whole then
    local lines = split_lines(whole)
    if #lines > 0 then
      return lines
    end
  end
  local lines = {}
  for index = 1, 200000 do
    local text = jot.buffer.get_line(index, buffer)
    if text == nil then
      break
    end
    lines[#lines + 1] = tostring(text)
  end
  return lines
end

------------------------------------------------------------ the pending jump

-- A :Todo pick opens a file that may still be on the worker thread, so the
-- target is remembered and applied when its BufOpen arrives. A file that was
-- already open fires no BufOpen, so the jump is attempted at once as well. The
-- walk's path is spelled through the workspace root while the opened buffer's
-- is canonical, so the relative path is kept as the fallback comparison.
local pending_jump = nil

local function same_file(current, pending)
  if current == "" then
    return false
  end
  if current == pending.path then
    return true
  end
  local rel = pending.relative or ""
  if rel == "" or #current < #rel or current:sub(-#rel) ~= rel then
    return false
  end
  local before = current:sub(-#rel - 1, -#rel - 1)
  return before == "/" or before == ""
end

local function land_pending_jump()
  if not pending_jump then
    return
  end
  if same_file(current_path(), pending_jump) then
    jot.cursor.set(pending_jump.line, pending_jump.column)
    pending_jump = nil
  end
end

------------------------------------------------------------------- the pass

local retries = 0
local retry = function() end

local function apply(info, force)
  local buffer = info and info.buffer
  if not buffer or buffer < 0 then
    retry()
    return
  end
  local sig = signature()
  if not force and seen[buffer] and sig == last_signature then
    return
  end
  local path = (info and (info.filepath or info.path)) or ""
  if path == "" then
    path = current_path()
  end
  if not enabled() then
    release(buffer)
    paths[buffer] = path
    seen[buffer] = true
    last_signature = sig
    return
  end
  local lines = buffer_lines(buffer)
  if #lines == 0 then
    -- BufOpen raced the load: read the file so the first paint still shows the
    -- comments, and retry shortly for the buffer's own text.
    if path ~= "" then
      local ok, text = pcall(jot.file.read, path)
      if ok and type(text) == "string" and text ~= "" then
        lines = split_lines(text)
      end
    end
  end
  if #lines == 0 then
    retry()
    return
  end
  retries = 0

  local keywords = configured_keywords()
  local specs = scan_lines(lines,
                           extension_of(path),
                           keywords,
                           compile_matcher(keywords),
                           family_accents())
  paths[buffer] = path
  seen[buffer] = true
  last_signature = sig
  release(buffer)
  for _, spec in ipairs(specs) do
    keep(buffer, jot.decoration.set(buffer, spec))
  end
end

retry = function()
  if retries >= 8 or type(jot.set_timeout) ~= "function" then
    return
  end
  retries = retries + 1
  local function run()
    local id = current_id()
    if id then
      apply({ buffer = id, path = current_path() }, false)
    end
  end
  if not pcall(jot.set_timeout, run, 120) then
    pcall(jot.set_timeout, 120, run)
  end
end

-- The text only moves on an edit, so a cursor move is worth a rescan once per
-- buffer (or after a config change) and never again.
jot.autocmd("CursorMoved", function(info)
  apply(info, false)
end)
jot.autocmd("BufChange", function(info)
  apply(info, true)
end)
-- A file arriving is the moment the bands should show up, without touching
-- anything, so this one is worth the retry loop. The pending jump from :Todo
-- lands here too when the file still had to be read.
jot.autocmd("BufOpen", function()
  apply({ buffer = current_id(), path = current_path() }, true)
  land_pending_jump()
  retry()
end)

------------------------------------------------------------------- commands

local function jump(direction)
  local buffer = current_id()
  if not buffer then
    return false
  end
  local lines = buffer_lines(buffer)
  if #lines == 0 then
    return false
  end
  local ext = extension_of(current_path())
  local keywords = configured_keywords()
  local matcher = compile_matcher(keywords)
  local ok, row = pcall(jot.cursor.get)
  if not ok or type(row) ~= "number" then
    row = 1
  end
  local first = direction > 0 and row + 1 or row - 1
  local last = direction > 0 and #lines or 1
  for index = first, last, direction do
    local line = lines[index] or ""
    local keyword, _, start0 = match_line(line, matcher, keywords)
    if keyword and comment_start_at(ext, line, start0) ~= false then
      jot.cursor.set(index, start0 + 1)
      return true
    end
  end
  jot.notify("No more todo comments to jump to")
  return false
end

jot.command("TodoNext", function()
  jump(1)
end, "Next todo comment")
jot.command("TodoPrev", function()
  jump(-1)
end, "Previous todo comment")

-- `keywords=TODO,FIX` (upstream's spelling), comma separated and
-- case-sensitive. Anything else in the argument is ignored.
local function keyword_filter(arg)
  local filter = nil
  for key, value in tostring(arg or ""):gmatch("([%w_]+)%s*=%s*([^%s]+)") do
    if key == "keywords" then
      filter = {}
      for name in value:gmatch("[^,]+") do
        filter[name] = true
      end
    end
  end
  return filter
end

local function workspace_items(filter)
  local keywords = configured_keywords()
  local matcher = compile_matcher(keywords)
  if not matcher then
    return {}
  end
  local items = {}
  local lines_seen = {}
  for keyword in pairs(keywords) do
    if not filter or filter[keyword] then
      local ok, results = pcall(jot.workspace.search, keyword .. ":", 1000)
      if ok and type(results) == "table" then
        for _, result in ipairs(results) do
          local line = tostring(result.text or "")
          -- The walk is case-insensitive and has no word boundary, so a line
          -- only becomes a row when the real highlight pattern (and the
          -- comment gate) accepts it. This is also what keeps a TODO inside a
          -- string out of the list.
          local hit, _, start0 = match_line(line, matcher, keywords)
          if hit and comment_start_at(extension_of(result.path), line, start0) ~= false then
            local dedupe = tostring(result.path) .. ":" .. tostring(result.line)
            if not lines_seen[dedupe] then
              lines_seen[dedupe] = true
              items[#items + 1] = {
                path = tostring(result.path),
                relative = tostring(result.relative_path or result.path),
                line = tonumber(result.line) or 1,
                column = start0 + 1,
                text = line,
              }
            end
          end
        end
      end
    end
  end
  table.sort(items, function(a, b)
    if a.relative ~= b.relative then
      return a.relative < b.relative
    end
    if a.line ~= b.line then
      return a.line < b.line
    end
    return a.column < b.column
  end)
  local cap = 500
  if #items > cap then
    local kept = {}
    for i = 1, cap do
      kept[i] = items[i]
    end
    return kept
  end
  return items
end

-- A picker row carries the location in `value` and the readable line in
-- `label`, so the callback does not have to parse its own display text.
local function picker_rows(items)
  local rows = {}
  for _, item in ipairs(items) do
    rows[#rows + 1] = {
      label = string.format("%s:%d  %s", item.relative, item.line, item.text),
      value = string.format("%s\t%d\t%d\t%s", item.path, item.line, item.column, item.relative),
    }
  end
  return rows
end

local function open_picked(value)
  local path, line, column, relative = tostring(value or ""):match("^(.-)\t(%d+)\t(%d+)\t(.*)$")
  if not path then
    return
  end
  pending_jump = {
    path = path,
    relative = relative,
    line = tonumber(line) or 1,
    column = tonumber(column) or 1,
  }
  jot.file.open(path)
  land_pending_jump()
end

jot.keymap.set("Alt+] t", ":TodoNext", "Next TODO comment")
jot.keymap.set("Alt+[ t", ":TodoPrev", "Previous TODO comment")

jot.command("Todo", function(arg)
  local items = workspace_items(keyword_filter(arg))
  if #items == 0 then
    jot.notify("No todo comments found")
    return
  end
  -- Rows are handed over as a function: show_picker's table branch refs the
  -- table without registering it as a callback, so only the function shape
  -- ever reaches the native item parser.
  jot.show_picker("Todo Comments", function()
    return picker_rows(items)
  end, open_picked)
end, "Search todo comments across the workspace")

-- A theme switch changes every band's ink, and the spans carry resolved
-- colours, so the painted buffers have to be repainted now: waiting for each
-- one's next edit would leave the old palette on screen.
if jot.events and type(jot.events.subscribe) == "function" then
  jot.events.subscribe("config.changed", function(info)
    local key = info and info.key
    if key == nil or key == ENABLED_KEY or key == KEYWORDS_KEY then
      apply({ buffer = current_id(), path = current_path() }, true)
    end
  end)
  jot.events.subscribe("theme.switched", function()
    for buffer, path in pairs(paths) do
      apply({ buffer = buffer, path = path }, true)
    end
  end)
end

-- Exposed so the keyword/matcher/colour logic can be exercised directly,
-- without an editor: test/test_lua_todo_comments.cpp drives these.
return {
  match_line = match_line,
  configured_keywords = configured_keywords,
  compile_matcher = compile_matcher,
  readable_ink = readable_ink,
  extension_of = extension_of,
  scan_lines = scan_lines,
  workspace_items = workspace_items,
  keyword_filter = keyword_filter,
  picker_rows = picker_rows,
  jump = jump,
}
