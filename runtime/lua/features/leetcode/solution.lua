local config = require("jot_lc.config")
local M = {}

local extensions = {
  cpp="cpp", c="c", java="java", python="py", python3="py", csharp="cs",
  javascript="js", typescript="ts", php="php", swift="swift", kotlin="kt",
  dart="dart", golang="go", ruby="rb", scala="scala", rust="rs", racket="rkt",
  erlang="erl", elixir="ex", bash="sh",
}

local function slugify(value)
  value = tostring(value or ""):lower():gsub("[^%w%-]", "-"):gsub("%-+", "-"):gsub("^%-", ""):gsub("%-$", "")
  return value ~= "" and value or "question"
end

local function write_solution(path, text)
  local ok, err = jot.file.write(path, text)
  if not ok then return false, err end
  return true
end

function M.path(question, language)
  return config.get("solution_dir") .. "/" .. slugify(question.title_slug) .. "." .. (extensions[language] or language)
end

function M.open(question, language)
  local snippet
  for _, candidate in ipairs(question.code_snippets or {}) do
    if candidate.lang_slug == language or candidate.lang == language then
      snippet = candidate.code
      break
    end
  end
  if not snippet then return false, "no code template for " .. language end
  local injector = jot.config.get("leetcode_injector_" .. language, "")
  if type(injector) == "string" and injector ~= "" then
    snippet = injector .. "\n" .. snippet
  end
  local path = M.path(question, language)
  local existing = jot.file.read(path)
  if not existing then
    local saved = write_solution(path, snippet .. (snippet:sub(-1) == "\n" and "" or "\n"))
    if not saved then return false, "cannot create or save the solution file" end
  else
    jot.file.open(path)
  end
  question.lang_slug = language
  question.solution_path = path
  return true, path
end

function M.reset(question, language)
  local snippet
  for _, candidate in ipairs(question.code_snippets or {}) do
    if candidate.lang_slug == language or candidate.lang == language then snippet = candidate.code; break end
  end
  if not snippet then return false, "no code template for " .. language end
  local injector = jot.config.get("leetcode_injector_" .. language, "")
  if type(injector) == "string" and injector ~= "" then snippet = injector .. "\n" .. snippet end
  local path = M.path(question, language)
  local saved = write_solution(path, snippet .. (snippet:sub(-1) == "\n" and "" or "\n"))
  if not saved then return false, "cannot reset or save the solution file" end
  return true
end

function M.restore_latest(question, callback)
  require("jot_lc.client").latest(question, function(submission, err)
    if err then callback(false, err); return end
    if type(submission) ~= "table" or type(submission.code) ~= "string" or submission.code == "" then
      callback(false, "no latest submission was returned for this problem")
      return
    end
    local path = M.path(question, submission.lang or question.lang_slug or config.get("language"))
    local saved = write_solution(path, submission.code)
    if not saved then callback(false, "cannot restore or save the latest submission"); return end
    callback(true, path)
  end)
end

function M.submission_code(question, language, source)
  local injector = jot.config.get("leetcode_injector_" .. language, "")
  if type(injector) ~= "string" or injector == "" then return source end
  local prefix = injector .. "\n"
  if source:sub(1, #prefix) == prefix then return source:sub(#prefix + 1) end
  return source
end

function M.testcases(question)
  local raw = question.testcase_list or ""
  local cases = {}
  for block in (raw .. "\n\n"):gmatch("(.-)\n%s*\n") do
    if block:match("%S") then cases[#cases + 1] = block end
  end
  return cases
end

return M
