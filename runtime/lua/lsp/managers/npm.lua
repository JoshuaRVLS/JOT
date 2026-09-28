-- npm manager: installs a package into the isolated package dir with pinned
-- version (mason registry pins CI-verified releases) and optional extra
-- packages.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

---@param entry table registry entry (manager = "npm")
---@param dirs table { root, dir, bin_dir, dl_dir }
function M.install_lines(entry, dirs)
  local spec = entry.pkg
  if entry.version and entry.version ~= "" then
    spec = spec .. "@" .. entry.version
  else
    spec = spec .. "@latest"
  end
  local line = "npm install --prefix " .. sh_quote(dirs.dir) .. " " .. sh_quote(spec)
  for _, extra in ipairs(entry.extra_pkgs or {}) do
    line = line .. " " .. sh_quote(extra)
  end
  -- Public bins live under node_modules/.bin inside the isolated prefix.
  local runs = entry.runs or {}
  for _, b in ipairs(entry.bin or {}) do
    if not runs[b] then
      runs[b] = { kind = "", hint = "node_modules/.bin/" .. b }
    end
  end
  entry.runs = runs
  return { line }
end

-- Windows renderer. npm is the same command there, but what it produces is
-- not: node_modules\.bin holds a .cmd launcher, a .ps1 and the extensionless sh
-- script the POSIX side links. Only the .cmd can be spawned, so the launcher is
-- what the managed bin keeps.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local spec = entry.pkg
  if entry.version and entry.version ~= "" then
    spec = spec .. "@" .. entry.version
  else
    spec = spec .. "@latest"
  end
  local line = "npm install --prefix " .. win.quote(dirs.dir) .. " " .. win.quote(spec)
  for _, extra in ipairs(entry.extra_pkgs or {}) do
    line = line .. " " .. win.quote(extra)
  end
  local lines = { win.fail(line) }
  for _, b in ipairs(entry.bin or {}) do
    for _, l in ipairs(win.publish(dirs.dir .. "\\node_modules\\.bin",
                                   { b .. ".cmd", b .. ".exe", b .. ".bat" },
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
