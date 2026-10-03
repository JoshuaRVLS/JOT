-- LeetCode right-dock panel: the question, its clickable actions and the
-- judge console, drawn as rows in the secondary sidebar instead of a floating
-- window. The dock's native hit-test routes a click on an action row back into
-- this module's callback as `event.action`, so a row is a real button.
local M = {}

-- Nerd Fonts glyphs (classic FontAwesome codepoints, present in every build).
local ICON_CHECK = "\u{F00C}"  -- check
local ICON_CROSS = "\u{F00D}"  -- times
local ICON_PLAY = "\u{F04B}"   -- play
local ICON_SEND = "\u{F1D8}"   -- paper-plane
local ICON_NEXT = "\u{F061}"   -- arrow-right
local ICON_CLOCK = "\u{F017}"  -- clock-o
local ICON_DOT = "\u{F111}"    -- circle, tinted by difficulty

-- Wired by ui.lua: a function returning the live state table, and the action
-- handlers the clickable rows run.
local state_provider = nil
local action_handlers = {}

function M.bind(provider, handlers)
  state_provider = provider
  action_handlers = handlers or {}
end

local function state()
  if type(state_provider) == "function" then
    local ok, value = pcall(state_provider)
    if ok and type(value) == "table" then return value end
  end
  return {}
end

local function palette()
  local theme = {}
  pcall(function() theme = jot.theme.palette() or {} end)
  local function pick(slot, field, fallback)
    local value = type(theme[slot]) == "table" and theme[slot][field]
    if type(value) == "number" then return value end
    return fallback
  end
  return {
    fg = pick("default", "fg", -1),
    comment = pick("comment", "fg", 8),
    accent = pick("keyword", "fg", 6),
    ok = pick("git_added", "fg", 2),
    bad = pick("git_deleted", "fg", 1),
    warn = pick("status_warning", "fg", 3),
    border = pick("panel_border", "fg", 8),
  }
end

-- Difficulty is the one piece of question metadata worth a colour: easy reads
-- green, hard red, everything else amber, the same vocabulary the problem list
-- uses for its statuses.
local function difficulty_fg(colors, difficulty)
  local value = tostring(difficulty or ""):lower()
  if value == "easy" then return colors.ok end
  if value == "hard" then return colors.bad end
  if value == "medium" then return colors.warn end
  return colors.comment
end

local function split_lines(text)
  local out = {}
  for line in (tostring(text) .. "\n"):gmatch("(.-)\n") do out[#out + 1] = line end
  return out
end

local function cell_len(text)
  local n = 0
  for _, cp in utf8.codes(text) do
    if cp >= 0x1100 and (cp <= 0x115F or (cp >= 0x2E80 and cp <= 0xA4CF)
        or (cp >= 0xAC00 and cp <= 0xD7A3) or (cp >= 0xF900 and cp <= 0xFAFF)
        or (cp >= 0xFE30 and cp <= 0xFE4F) or (cp >= 0xFF00 and cp <= 0xFF60)
        or (cp >= 0x1F300 and cp <= 0x1FAFF)) then
      n = n + 2
    else
      n = n + 1
    end
  end
  return n
end

-- Wraps text to at most `width` cells, preserving words where possible. The
-- statement arrives as HTML stripped to plain text, so this is what turns a
-- long paragraph into dock rows.
local function wrap(text, width)
  width = math.max(8, width)
  local out = {}
  for _, source in ipairs(split_lines(text)) do
    if source == "" then
      out[#out + 1] = ""
    else
      local line = ""
      for word in source:gmatch("%S+") do
        local candidate = line == "" and word or (line .. " " .. word)
        if cell_len(candidate) <= width then
          line = candidate
        else
          if line ~= "" then out[#out + 1] = line end
          -- A word longer than the width is broken rather than dropped.
          while cell_len(word) > width do
            local head = utf8.offset(word, width + 1)
            out[#out + 1] = word:sub(1, (head or #word + 1) - 1)
            word = word:sub(head or #word + 1)
          end
          line = word
        end
      end
      out[#out + 1] = line
    end
  end
  while #out > 0 and out[#out] == "" do table.remove(out) end
  return out
end

local function decode_entities(text)
  return (text:gsub("&nbsp;", " "):gsub("&lt;", "<"):gsub("&gt;", ">")
              :gsub("&amp;", "&"):gsub("&quot;", '\"'):gsub("&#39;", "'"))
end

-- The question's content HTML split into what the site shows: the statement
-- prose, and the example blocks (`<pre>` on the site) with their labelled
-- input / output / explanation lines. The API returns them in one field, so
-- this is the split that lets the panel border the examples like the page.
local function parse_content(html)
  local text = tostring(html or "")
  local statement = text:gsub("<pre[^>]*>.-</pre>", "\n\n")
  statement = statement:gsub("<br%s*/?>", "\n")
  statement = statement:gsub("</p%s*>", "\n\n"):gsub("</li%s*>", "\n")
  statement = statement:gsub("<li[^>]*>", "  - ")
  statement = statement:gsub("<[^>]->", "")
  statement = decode_entities(statement)
  statement = statement:gsub("\n[ \t]+\n", "\n\n"):gsub("\n\n\n+", "\n\n")

  local examples = {}
  for block in text:gmatch("<pre[^>]*>(.-)</pre>") do
    block = decode_entities(block:gsub("<br%s*/?>", "\n"):gsub("<[^>]->", ""))
    local lines = {}
    for _, raw in ipairs(split_lines(block)) do
      local line = raw:gsub("^%s+", ""):gsub("%s+$", "")
      if line ~= "" then lines[#lines + 1] = line end
    end
    if #lines > 0 then examples[#examples + 1] = lines end
  end
  return statement, examples
end

-- One labelled value in a bordered block, the way the site boxes Input and
-- Output: the label, a rule, the value, and a closing rule.
local function block_rows(colors, label, value, width)
  local rows = {}
  rows[#rows + 1] = {text=" " .. tostring(label), fg=colors.comment, bold=true}
  local rule = "\u{2500}"
  local corner = "\u{250C}"
  local bottom = "\u{2514}"
  local value_lines = wrap(value, width - 4)
  if #value_lines == 0 then value_lines = {""} end
  rows[#rows + 1] = {text=" " .. corner .. rule:rep(math.max(1, width - 3)),
                     fg=colors.border or colors.comment, kind="leet_rule"}
  for _, value_line in ipairs(value_lines) do
    rows[#rows + 1] = {text=" \u{2502} " .. value_line, fg=colors.fg, kind="leet_code"}
  end
  rows[#rows + 1] = {text=" " .. bottom .. rule:rep(math.max(1, width - 3)),
                     fg=colors.border or colors.comment, kind="leet_rule"}
  return rows
end

-- The selected example as bordered blocks. Its labels come from the content's
-- own lines (Input: / Output: / Explanation:); a continuation line belongs to
-- the label above it, which is how a multi-line input stays one block.
local function example_rows(colors, example, fallback, width)
  local rows = {}
  rows[#rows + 1] = {text=""}
  rows[#rows + 1] = {text="Example", fg=colors.accent, bold=true}
  local blocks = {}
  if type(example) == "table" then
    for _, line in ipairs(example) do
      local label, value = line:match("^(%a[%a ]*):%s*(.*)$")
      if label then
        blocks[#blocks + 1] = {label=label, value=value}
      elseif #blocks > 0 then
        blocks[#blocks].value = blocks[#blocks].value .. "\n" .. line
      else
        blocks[#blocks + 1] = {label="Input", value=line}
      end
    end
  end
  if #blocks == 0 then
    blocks[1] = {label="Input", value=tostring(fallback or "")}
  end
  for _, block in ipairs(blocks) do
    for _, row in ipairs(block_rows(colors, block.label, block.value, width)) do
      rows[#rows + 1] = row
    end
  end
  return rows
end

-- The judge console as rows: an icon and a colour per outcome, so a passing
-- run and a failing one are told apart at a glance instead of by reading the
-- status text.
local function console_rows(colors, console)
  if type(console) ~= "table" then return {} end
  if console.progress then
    return { {text=console.progress, icon=ICON_CLOCK, icon_fg=colors.comment, fg=colors.comment} }
  end
  if console.error then
    return {
      {text="Judge request failed", icon=ICON_CROSS, icon_fg=colors.bad, fg=colors.bad, bold=true},
      {text=tostring(console.error), fg=colors.comment},
    }
  end
  local result = console.result
  if type(result) ~= "table" then return {} end

  local status = tostring(result.status_msg or "judge finished without a status")
  local accepted = status == "Accepted"
  local rows = {
    {text=status, icon=accepted and ICON_CHECK or ICON_CROSS,
     icon_fg=accepted and colors.ok or colors.bad,
     fg=accepted and colors.ok or colors.bad, bold=true},
  }
  local correct = tonumber(result.total_correct)
  local total = tonumber(result.total_testcases)
  if correct ~= nil or total ~= nil then
    local passed = correct ~= nil and total ~= nil and correct >= total
    rows[#rows + 1] = {
      text=string.format("correct  %s / %s", tostring(correct or "?"), tostring(total or "?")),
      icon=passed and ICON_CHECK or ICON_CROSS,
      icon_fg=passed and colors.ok or colors.bad,
      fg=passed and colors.ok or colors.bad,
    }
  end
  -- The report's own body, minus the status line the row above already carries.
  local body = split_lines(console.report or "")
  if body[1] and body[1]:match("^Status:") then table.remove(body, 1) end
  for _, line in ipairs(body) do
    if line:match("%S") then
      local block = line:match(":%s*$") ~= nil
      local failed = line:match("^Runtime error:") ~= nil or line:match("^Compile error:") ~= nil
      rows[#rows + 1] = {
        text=line,
        icon=failed and ICON_CROSS or "",
        icon_fg=failed and colors.bad or nil,
        fg=failed and colors.bad or (block and colors.accent or colors.comment),
        bold=block,
      }
    end
  end
  return rows
end

-- The dock's inner width, so the statement and the example blocks wrap to
-- what is actually on screen. The panel is redrawn every frame, so reading
-- it here is how the rows follow a resize.
local function dock_width()
  local ok, info = pcall(function() return jot.viewport.info() end)
  if not ok or type(info) ~= "table" then return 42 end
  local panel = info.right_panel or {}
  local width = tonumber(panel.width) or 42
  return math.max(20, width - 2)
end

local function build_rows()
  local colors = palette()
  local s = state()
  local console = s.console or {}
  local question = s.question
  local rows = {}
  local width = dock_width()

  if type(question) ~= "table" then
    rows[#rows + 1] = {text="No problem open", icon=ICON_DOT, icon_fg=colors.comment, bold=true}
    rows[#rows + 1] = {text="Use :LeetList, :LeetDaily or :LeetRandom", fg=colors.comment}
    rows[#rows + 1] = {text="to choose a problem.", fg=colors.comment}
    return rows
  end

  rows[#rows + 1] = {
    text=tostring(question.frontend_id or "") .. ". " .. tostring(question.title or question.title_slug),
    detail=tostring(question.difficulty or ""),
    icon=ICON_DOT,
    icon_fg=difficulty_fg(colors, question.difficulty),
    bold=true,
  }
  local example = ""
  if type(s.testcases) == "table" and #s.testcases > 0 then
    example = " · example " .. tostring(s.testcase or 1) .. " of " .. tostring(#s.testcases)
  end
  rows[#rows + 1] = {
    text=tostring(question.lang_slug or "no language") .. example
         .. (s.cache_hit and " · cached" or ""),
    fg=colors.comment,
  }

  rows[#rows + 1] = {text=""}
  rows[#rows + 1] = {
    text="Run test", detail="example", icon=ICON_PLAY, icon_fg=colors.ok, action="run",
  }
  rows[#rows + 1] = {
    text="Submit", detail="judge", icon=ICON_SEND, icon_fg=colors.accent, action="submit",
  }
  rows[#rows + 1] = {
    text="Next example", detail=tostring(s.testcase or 1) .. "/" .. tostring(#(s.testcases or {})),
    icon=ICON_NEXT, icon_fg=colors.comment, action="next",
  }

  -- The statement and the selected example, the way the site shows them: the
  -- prose, then the example's input and output in bordered blocks. The
  -- content's own <pre> blocks carry the labels; the testcase list is the
  -- fallback for questions whose content has none.
  local statement, examples = parse_content(question.content)
  rows[#rows + 1] = {text=""}
  rows[#rows + 1] = {text="Problem", fg=colors.accent, bold=true}
  local statement_rows = wrap(statement, width - 2)
  if #statement_rows == 0 then
    rows[#rows + 1] = {text="No statement was returned for this problem", fg=colors.comment}
  else
    for _, line in ipairs(statement_rows) do
      rows[#rows + 1] = {text=" " .. line, fg=colors.fg, kind="leet_statement"}
    end
  end
  if type(s.testcases) == "table" and #s.testcases > 0 then
    local selected = math.max(1, math.min(tonumber(s.testcase) or 1, #s.testcases))
    local example = examples[selected] or examples[1]
    local fallback = s.testcases[selected]
    for _, row in ipairs(example_rows(colors, example, fallback, width - 2)) do
      rows[#rows + 1] = row
    end
  end

  rows[#rows + 1] = {text=""}
  rows[#rows + 1] = {text="Console", fg=colors.accent, bold=true}
  local console_lines = console_rows(colors, console)
  if #console_lines == 0 then
    rows[#rows + 1] = {text="No run or submit yet", icon=ICON_CLOCK,
                       icon_fg=colors.comment, fg=colors.comment}
    rows[#rows + 1] = {text="Run test sends the selected example,", fg=colors.comment}
    rows[#rows + 1] = {text="Submit sends the whole solution.", fg=colors.comment}
  else
    for _, line in ipairs(console_lines) do rows[#rows + 1] = line end
  end
  return rows
end

local function panel_callback(_name, event)
  if type(event) == "table" and type(event.action) == "string" then
    local handler = action_handlers[event.action]
    if type(handler) == "function" then handler(event) end
  end
  return build_rows()
end

function M.register()
  jot.ui.register_panel("LeetCode", panel_callback, "LeetCode")
end

-- Shows the dock panel (idempotent: the host activates the tab when it is
-- already open) and repaints it.
function M.open()
  jot.ui.panel("LeetCode")
  M.refresh()
end

function M.refresh()
  pcall(function() jot.editor.request_redraw() end)
end

function M.rows()
  return build_rows()
end

return M
