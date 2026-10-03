local json = require("jot_lc.json")
local config = require("jot_lc.config")
local M = {}

local function cache_root()
  local override = os.getenv("JOT_CACHE_HOME")
  if override and override ~= "" then return override .. package.config:sub(1, 1) .. "leetcode" end
  if package.config:sub(1, 1) == "\\" then
    return (os.getenv("LOCALAPPDATA") or ((os.getenv("APPDATA") or ".") .. "\\jot")) .. "\\jot\\leetcode-cache"
  end
  return (os.getenv("XDG_CACHE_HOME") or ((os.getenv("HOME") or ".") .. "/.cache")) .. "/jot/leetcode"
end

local function join(root, name)
  return root .. package.config:sub(1, 1) .. name
end

local function safe_slug(slug)
  return type(slug) == "string" and slug:match("^[%w%-]+$") ~= nil
end

function M.get(slug)
  if not safe_slug(slug) then return nil end
  local text = jot.file.read(join(cache_root(), "question-" .. slug .. ".json"))
  if not text then return nil end
  local value = json.decode(text)
  if type(value) ~= "table" or type(value.saved_at) ~= "number" or type(value.question) ~= "table" then return nil end
  if os.time() - value.saved_at > config.get("cache_ttl") then return nil end
  return value.question
end

function M.put(slug, question)
  if not safe_slug(slug) or type(question) ~= "table" then return false end
  return jot.file.write(join(cache_root(), "question-" .. slug .. ".json"),
                        json.encode({saved_at=os.time(), question=question}))
end

function M.clear()
  local files = jot.file.list(cache_root()) or {}
  local cleared = 0
  for _, entry in ipairs(files) do
    if entry.name:match("^question%-[%w%-]+%.json$") and jot.file.remove(entry.path) then
      cleared = cleared + 1
    end
  end
  return cleared
end

return M
