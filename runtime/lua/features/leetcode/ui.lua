local config = require("jot_lc.config")
local client = require("jot_lc.client")
local solution = require("jot_lc.solution")
local M = {current=nil, busy=false, cache_hit=false, testcase=1, testcases={}}

local function notify(text)
  jot.ui.show_message("[leet] " .. text)
end

local function popup(title, text)
  jot.ui.popup(text, title)
end

local function plain_description(html)
  local text = tostring(html or "")
  text = text:gsub("<br%s*/?>", "\n")
  text = text:gsub("</p%s*>", "\n\n"):gsub("</pre%s*>", "\n\n")
  text = text:gsub("</li%s*>", "\n"):gsub("<li[^>]*>", "  - ")
  text = text:gsub("<[^>]->", "")
  text = text:gsub("&nbsp;", " "):gsub("&lt;", "<"):gsub("&gt;", ">")
  text = text:gsub("&amp;", "&"):gsub("&quot;", '\"'):gsub("&#39;", "'")
  text = text:gsub("\n[ \t]+\n", "\n\n"):gsub("\n\n\n+", "\n\n")
  return text
end

local function language_picker(question)
  local rows = {}
  for _, snippet in ipairs(question.code_snippets or {}) do
    rows[#rows + 1] = {label=snippet.lang, value=snippet.lang_slug, detail=snippet.lang_slug}
  end
  if #rows == 0 then notify("This question has no language templates"); return end
  jot.ui.picker("LeetCode language", function() return rows end, function(lang)
    local ok, value = solution.open(question, lang)
    if not ok then popup("LeetCode error", tostring(value)); return end
    M.current = question
    M.testcases = solution.testcases(question)
    M.testcase = 1
    notify("Opened " .. value)
  end)
end

function M.open_question(slug)
  if M.busy then notify("A question request is already running"); return end
  M.busy = true
  notify("Loading question " .. slug .. "...")
  client.question(slug, function(question, err, cached)
    M.busy = false
    if err then popup("LeetCode error", "Question failed to load.\n\n" .. err); return end
    if type(question) ~= "table" then popup("LeetCode", "No question data was returned."); return end
    M.current = question
    M.cache_hit = cached == true
    M.testcases = solution.testcases(question)
    M.testcase = 1
    notify(tostring(question.frontend_id or "") .. ". " .. tostring(question.title or slug)
           .. " · " .. tostring(question.difficulty or "Unknown"))
    language_picker(question)
  end)
end

local function open_rows(rows, title)
  local items = {}
  for _, question in ipairs(rows) do
    items[#items + 1] = {label=string.format("%s. %s", question.frontend_id or "?", question.title or question.title_slug or ""),
                         value=question.title_slug,
                         detail=tostring(question.difficulty or "") .. (question.status and " · " .. question.status or "")}
  end
  if #items == 0 then notify("No problems in this result"); return end
  jot.ui.picker(title, function() return items end, function(slug) M.open_question(slug) end)
end

function M.description()
  if not M.current then notify("Open a question first"); return end
  local question = M.current
  local lines = {
    tostring(question.frontend_id or "") .. ". " .. tostring(question.title or question.title_slug),
    "Difficulty: " .. tostring(question.difficulty or "Unknown"),
    "",
    "Language templates: " .. tostring(#(question.code_snippets or {})),
    "Examples: " .. tostring(#M.testcases),
    M.cache_hit and "Question data: cached" or "Question data: refreshed",
  }
  if type(question.topic_tags) == "table" and #question.topic_tags > 0 then
    local tags = {}
    for _, tag in ipairs(question.topic_tags) do tags[#tags + 1] = tag.name end
    lines[#lines + 1] = "Topics: " .. table.concat(tags, ", ")
  end
  lines[#lines + 1] = ""
  lines[#lines + 1] = plain_description(question.content)
  popup("LeetCode problem", table.concat(lines, "\n"))
end

function M.list()
  if M.busy then notify("A request is already running"); return end
  M.busy = true
  notify("Loading problem list...")
  client.list(0, function(rows, err)
    M.busy = false
    notify("List response: " .. (err or tostring(rows and #rows or "nil")))
    if err then
      jot.picker.close()
      popup("LeetCode error", "Problem list failed to load.\n\n" .. err)
      return
    end
    if not rows or #rows == 0 then
      jot.picker.close()
      popup("LeetCode", "The problem-list response contained no visible problems.")
      return
    end
    open_rows(rows, "LeetCode problems")
  end)
end

function M.daily()
  if M.busy then notify("A request is already running"); return end
  M.busy = true
  notify("Loading today's problem...")
  client.daily(function(question, err)
    M.busy = false
    if err then popup("LeetCode error", "Daily problem failed to load.\n\n" .. err); return end
    M.current = question
    M.testcases = solution.testcases(question)
    M.testcase = 1
    notify(tostring(question.title) .. " · " .. tostring(question.difficulty or "Unknown"))
    language_picker(question)
  end)
end

function M.random()
  if M.busy then notify("A request is already running"); return end
  M.busy = true
  notify("Choosing a random problem...")
  client.random(function(question, err)
    M.busy = false
    if err then popup("LeetCode error", "Random problem failed.\n\n" .. err); return end
    M.current = question
    M.testcases = solution.testcases(question)
    M.testcase = 1
    notify(tostring(question.title) .. " · " .. tostring(question.difficulty or "Unknown"))
    language_picker(question)
  end)
end

function M.profile()
  popup("LeetCode profile", "Loading profile statistics...")
  client.profile(function(data, err)
    if err then popup("LeetCode error", "Profile request failed.\n\n" .. err); return end
    local rows = {}
    for _, item in ipairs((data and data.submit_stats and data.submit_stats.acSubmissionNum) or {}) do
      rows[#rows + 1] = tostring(item.difficulty) .. ": " .. tostring(item.count)
    end
    popup("LeetCode profile", #rows > 0 and table.concat(rows, "\n") or "No profile statistics were returned.")
  end)
end

function M.run(kind)
  local question = M.current
  if not question then notify("Open a question first"); return end
  local path = question.solution_path or solution.path(question, question.lang_slug or config.get("language"))
  local code = jot.file.read(path)
  if not code then notify("Save the solution file before running it"); return end
  code = solution.submission_code(question, question.lang_slug or config.get("language"), code)
  local case = M.testcases[M.testcase] or ""
  popup("LeetCode judge", kind == "submit" and "Submitting solution..." or "Running selected example...")
  client.judge(kind, question, code, case, function(result, err)
    if err then popup("LeetCode error", "Judge request failed.\n\n" .. err); return end
    local lines = {
      tostring(result.status_msg or "Judge completed"),
      "Correct cases: " .. tostring(result.total_correct or "?"),
      "Total cases: " .. tostring(result.total_testcases or "?"),
      "Runtime: " .. tostring(result.runtime or "?") .. " ms",
      "Memory: " .. tostring(result.memory or "?"),
    }
    popup("LeetCode result", table.concat(lines, "\n"))
  end)
end

function M.next_case()
  if #M.testcases == 0 then notify("This question has no examples"); return end
  M.testcase = M.testcase % #M.testcases + 1
  popup("LeetCode testcase", "Example " .. M.testcase .. " of " .. #M.testcases .. "\n\n" .. M.testcases[M.testcase])
end

function M.change_language()
  if not M.current then notify("Open a question first"); return end
  language_picker(M.current)
end

function M.reset()
  if not M.current then notify("Open a question first"); return end
  local ok, err = solution.reset(M.current, M.current.lang_slug or config.get("language"))
  notify(ok and "Solution reset to the platform template" or err)
end

function M.restore_latest()
  if not M.current then notify("Open a question first"); return end
  solution.restore_latest(M.current, function(ok, value)
    notify(ok and ("Restored " .. value) or value)
  end)
end

function M.open_browser()
  if not M.current then notify("Open a question first"); return end
  local url = config.problem_url(M.current.title_slug)
  local opened = require("jot_md.browser").open(url, jot.config.get("leetcode_browser", ""))
  if not opened then notify("Browser launch is disabled; URL: " .. url) end
end

function M.auth_status()
  local result = jot.leetcode.credential_get()
  if not result.available then notify(result.error); return end
  if not result.ok then notify("Session store error: " .. result.error); return end
  if result.value == "" then notify("No LeetCode session stored"); return end
  client.auth(function(user, err)
    if err then notify(err); return end
    notify("Signed in as " .. tostring(user.name or "LeetCode user"))
  end)
end

function M.sign_out()
  local result = jot.leetcode.credential_delete()
  notify(result.ok and "Session removed from the OS credential store" or result.error)
end

function M.status()
  local title = M.current and M.current.title or "No active problem"
  popup("LeetCode", table.concat({
    title,
    "Endpoint: " .. config.get("endpoint"),
    "Language: " .. config.get("language"),
    "Solution: " .. tostring(M.current and M.current.solution_path or "not opened"),
    "On disk: " .. tostring(M.current and jot.file.read(M.current.solution_path or "") ~= nil or false),
    "Examples: " .. tostring(#M.testcases),
    "Cache: " .. tostring(config.get("cache_ttl")) .. " seconds",
    "",
    "Use :Leet list, :Leet daily, or :Leet random to choose a problem.",
  }, "\n"))
end

function M.actions()
  local items = {
    {label="Browse problem list", detail="Filter and open a problem"},
    {label="Daily problem", detail="Load today's question"},
    {label="Random problem", detail="Choose an algorithm problem"},
    {label="Profile statistics", detail="Requires a stored session"},
    {label="Session status", detail="Check OS credential store"},
    {label="Sign out", detail="Remove stored session"},
    {label="Clear question cache", detail="Remove public cached questions"},
    {label="Refresh active question", detail="Fetch question data again"},
    {label="Reset solution", detail="Restore the platform template"},
    {label="Show active problem status", detail="Endpoint, language and testcase"},
  }
  if M.current then
    items[#items + 1] = {label="Run selected example", detail="Send code and selected testcase"}
    items[#items + 1] = {label="Submit solution", detail="Send code to LeetCode judge"}
    items[#items + 1] = {label="Show testcase", detail="Cycle to the next example"}
    items[#items + 1] = {label="Restore latest submission", detail="Open the latest accepted code"}
    items[#items + 1] = {label="Open problem in browser", detail="Open the official problem page"}
    items[#items + 1] = {label="Show description", detail="Display statement and examples"}
    items[#items + 1] = {label="Change language", detail="Open a solution template in another language"}
  end
  jot.ui.picker("LeetCode", function() return items end, function(action)
    if action == "Browse problem list" then M.list()
    elseif action == "Daily problem" then M.daily()
    elseif action == "Random problem" then M.random()
    elseif action == "Profile statistics" then M.profile()
    elseif action == "Session status" then M.auth_status()
    elseif action == "Sign out" then M.sign_out()
    elseif action == "Clear question cache" then notify("Removed " .. require("jot_lc.cache").clear() .. " cached question(s)")
    elseif action == "Refresh active question" and M.current then M.open_question(M.current.title_slug, true)
    elseif action == "Reset solution" then M.reset()
    elseif action == "Show active problem status" then M.status()
    elseif action == "Run selected example" then M.run("run")
    elseif action == "Submit solution" then M.run("submit")
    elseif action == "Show testcase" then M.next_case()
    elseif action == "Restore latest submission" then M.restore_latest()
    elseif action == "Open problem in browser" then M.open_browser()
    elseif action == "Show description" then M.description()
    elseif action == "Change language" then M.change_language() end
  end)
end

function M.open_cookie_prompt()
  local result = jot.leetcode.credential_get()
  if not result.available then notify(result.error); return end
  local value = ""
  local function render()
    local display = string.rep("*", math.min(#value, 48))
    local width = 58
    local x = math.max(0, math.floor((jot.viewport.info().window.width - width) / 2))
    local win = M.cookie_win
    local lines = {"Paste the LeetCode session cookie.", "", "Session: " .. display, "", "Enter saves it. Esc cancels."}
    if not win or not jot.ui.float.is_valid(win) then
      local buffer = jot.ui.buffer.create(false, true)
      jot.ui.buffer.set_lines(buffer, 0, -1, false, lines)
      win = jot.ui.float.open(buffer, true, {relative="editor", col=x, row=4, width=width, height=9, border="rounded", title=" LeetCode sign-in ", focusable=true})
      M.cookie_win = win
      jot.ui.float.on_key(win, function(event)
        local key = event.key
        if key == 27 then
          jot.ui.float.close(win, true); M.cookie_win=nil; return true
        elseif key == 13 or key == 10 then
          local saved = jot.leetcode.credential_set(value)
          jot.ui.float.close(win, true); M.cookie_win=nil
          if not saved.ok then notify(saved.error); return true end
          M.auth_status(); return true
        elseif key == 127 or key == 8 then
          value = value:sub(1, -2)
        elseif type(key) == "number" and key >= 32 and key <= 126 and not event.ctrl and not event.alt then
          value = value .. string.char(key)
        end
        jot.ui.float.set_lines(win, {"Paste the LeetCode session cookie.", "", "Session: " .. string.rep("*", math.min(#value, 48)), "", "Enter saves it. Esc cancels."})
        return true
      end)
    else
      jot.ui.float.set_lines(win, lines)
    end
  end
  render()
end

function M.setup(options)
  config.setup(options)
end

M.notify = notify

return M
