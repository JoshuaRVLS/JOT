-- composer manager: installs a PHP package into an isolated composer project
-- dir; executables land in <dir>/vendor/bin.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

---@param entry table registry entry (manager = "composer")
---@param dirs table { root, dir, bin_dir, dl_dir }
function M.install_lines(entry, dirs)
  local lines = {
    "mkdir -p " .. sh_quote(dirs.dir),
    "printf '{\"name\": \"jot/install\", \"minimum-stability\": \"dev\", \"prefer-stable\": true}\\n' > "
      .. sh_quote(dirs.dir .. "/composer.json"),
    "composer require --working-dir=" .. sh_quote(dirs.dir) .. " --no-interaction --no-progress "
      .. sh_quote(entry.pkg),
  }
  local runs = entry.runs or {}
  for _, b in ipairs(entry.bin or {}) do
    if not runs[b] then
      runs[b] = { kind = "", hint = "vendor/bin/" .. b }
    end
  end
  entry.runs = runs
  return lines
end

-- Windows renderer. Same isolated project; composer's vendor\bin holds a .bat
-- shim beside the PHP script, and that shim is the one a console can start.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local lines = {
    "> " .. win.quote(dirs.dir .. "\\composer.json")
      .. ' echo {"name": "jot/install", "minimum-stability": "dev", "prefer-stable": true}',
    win.fail(win.run("composer require --working-dir=" .. win.quote(dirs.dir)
      .. " --no-interaction --no-progress " .. win.quote(entry.pkg))),
  }
  for _, b in ipairs(win.native_bins(entry)) do
    for _, l in ipairs(win.publish(dirs.dir .. "\\vendor\\bin",
                                   { b .. ".bat", b .. ".cmd", b .. ".exe", b },
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
