-- luarocks manager: `luarocks install --tree <dir>` installs a rock into the
-- isolated tree; executables land in <dir>/bin.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

---@param entry table registry entry (manager = "luarocks")
---@param dirs table { root, dir, bin_dir, dl_dir }
function M.install_lines(entry, dirs)
  local spec = entry.pkg
  if entry.version and entry.version ~= "" then
    spec = spec .. " " .. entry.version
  end
  local line = "luarocks install --tree " .. sh_quote(dirs.dir) .. " " .. sh_quote(spec)
  local runs = entry.runs or {}
  for _, b in ipairs(entry.bin or {}) do
    if not runs[b] then
      runs[b] = { kind = "", hint = "bin/" .. b }
    end
  end
  entry.runs = runs
  return { line }
end

-- Windows renderer. A rock installed into a tree puts a .bat launcher in
-- <tree>\bin, which is the file worth publishing on this platform.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local spec = entry.pkg
  if entry.version and entry.version ~= "" then
    spec = spec .. " " .. entry.version
  end
  local lines = { win.fail(win.run("luarocks install --tree " .. win.quote(dirs.dir)
    .. " " .. win.quote(spec))) }
  for _, b in ipairs(win.native_bins(entry)) do
    for _, l in ipairs(win.publish(dirs.dir .. "\\bin",
                                   { b .. ".bat", b .. ".cmd", b .. ".exe", b },
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
