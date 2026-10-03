local M = {}

local windows = package.config:sub(1, 1) == "\\"
local data_home = windows and (os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or ".")
                         or ((os.getenv("XDG_DATA_HOME") or ((os.getenv("HOME") or ".") .. "/.local/share")) .. "/jot")

M.defaults = {
  endpoint = "leetcode.com",
  language = "cpp",
  api_base = "",
  solution_dir = windows and (data_home .. "\\jot\\leetcode") or (data_home .. "/leetcode"),
  cache_ttl = 604800,
  -- The list query clamps one page at 100 rows server-side; the picker's
  -- "Load more" row fetches further pages with this step.
  list_limit = 100,
  timeout = 20,
}

function M.get(name)
  local fallback = M.defaults[name]
  if type(fallback) == "number" then
    return jot.config.get_number("leetcode_" .. name, fallback)
  end
  return jot.config.get("leetcode_" .. name, fallback)
end

function M.setup(values)
  if type(values) ~= "table" then return end
  for name, value in pairs(values) do
    if M.defaults[name] ~= nil then
      M.defaults[name] = value
      jot.config.set("leetcode_" .. name, value)
    end
  end
end

function M.graphql_url()
  return M.get("endpoint") == "leetcode.cn" and "https://leetcode.cn/graphql" or "https://leetcode.com/graphql"
end

function M.problem_url(slug)
  local host = M.get("endpoint") == "leetcode.cn" and "leetcode.cn" or "leetcode.com"
  return "https://" .. host .. "/problems/" .. slug .. "/"
end

return M
