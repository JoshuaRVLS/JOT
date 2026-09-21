-- HTML preview configuration.
--
-- Every option is stored under the `html_preview_*` keys of the normal jot
-- config store, so `:settings`, `config.lua` and `jot.html_preview.setup{}` all
-- edit the same values and edits apply live.
local M = {}

M.defaults = {
  auto_start = false,       -- open the preview when an HTML buffer opens
  auto_close = true,        -- close it when the last HTML buffer closes
  refresh_interval = 150,   -- ms to coalesce edits into one reload
  html_ext = "html,htm,xhtml",
  port = 0,                 -- 0 = pick a free port
  host = "127.0.0.1",
  -- "" = the workspace root (so `../styles/site.css` resolves the way it does
  -- over any other static server); "dir" = the HTML file's own directory.
  root = "",
  browser = "",             -- "", firefox, chromium, google-chrome, brave, ...
  echo_preview_url = false, -- show the preview URL in the message area
  open_browser = true,      -- false = serve it and print the URL only
}

local function config_key(name)
  return "html_preview_" .. name
end

-- Reads one option: the config store first (live, and visible in `:settings`),
-- then the built-in default.
function M.get(name)
  local default = M.defaults[name]
  if type(default) == "boolean" then
    return jot.config.get_bool(config_key(name), default)
  elseif type(default) == "number" then
    return jot.config.get_number(config_key(name), default)
  end
  return jot.config.get(config_key(name), default)
end

-- Applies a `jot.html_preview.setup{ ... }` table.
function M.setup(opts)
  if type(opts) ~= "table" then
    return
  end
  for key, value in pairs(opts) do
    if M.defaults[key] ~= nil then
      jot.config.set(config_key(key), value)
    end
  end
end

-- True when `path` carries a configured HTML extension.
function M.is_html_path(path)
  if not path or path == "" then
    return false
  end
  local ext = path:match("%.([%w_%-%.]+)$")
  if not ext then
    return false
  end
  ext = ext:lower()
  for candidate in tostring(M.get("html_ext")):gmatch("[^,%s]+") do
    if ext == candidate:lower() then
      return true
    end
  end
  return false
end

return M
