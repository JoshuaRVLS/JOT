-- nuget manager: `dotnet tool update --tool-path <dir>` installs a .NET tool
-- into the isolated package dir. The produced apphost carries the same name
-- as the package's bin entry.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

---@param entry table registry entry (manager = "nuget")
---@param dirs table { root, dir, bin_dir, dl_dir }
function M.install_lines(entry, dirs)
  local cmd = "dotnet tool update --tool-path " .. sh_quote(dirs.dir)
  if entry.version and entry.version ~= "" then
    cmd = cmd .. " --version " .. sh_quote(entry.version)
  end
  cmd = cmd .. " " .. sh_quote(entry.pkg)
  local runs = entry.runs or {}
  for _, b in ipairs(entry.bin or {}) do
    if not runs[b] then
      runs[b] = { kind = "", hint = b }
    end
  end
  entry.runs = runs
  return { cmd }
end

-- Windows renderer. `dotnet tool` is the same command there; the apphost it
-- writes next to the package metadata is a .exe carrying the bin's name.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local cmd = "dotnet tool update --tool-path " .. win.quote(dirs.dir)
  if entry.version and entry.version ~= "" then
    cmd = cmd .. " --version " .. win.quote(entry.version)
  end
  cmd = cmd .. " " .. win.quote(entry.pkg)
  local lines = { win.fail(cmd) }
  for _, b in ipairs(win.native_bins(entry)) do
    for _, l in ipairs(win.publish(dirs.dir, { b .. ".exe", b },
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
