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

local function build_rows()
  local colors = palette()
  local s = state()
  local console = s.console or {}
  local question = s.question
  local rows = {}

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
