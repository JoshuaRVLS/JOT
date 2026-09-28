-- gem manager: `gem install --install-dir <dir>` installs into the isolated
-- package dir (executables in <dir>/bin). The run wrapper keeps GEM_HOME /
-- GEM_PATH pointed at the package dir so the gem's own wrapper works from
-- anywhere.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

---@param entry table registry entry (manager = "gem")
---@param dirs table { root, dir, bin_dir, dl_dir }
function M.install_lines(entry, dirs)
  local spec = entry.pkg
  if entry.version and entry.version ~= "" then
    spec = spec .. ":" .. entry.version
  end
  local line = "GEM_HOME=" .. sh_quote(dirs.dir)
    .. " gem install --no-user-install --no-document --no-format-executable"
    .. " --install-dir " .. sh_quote(dirs.dir)
    .. " --bindir " .. sh_quote(dirs.dir .. "/bin")
    .. " " .. sh_quote(spec)
  local runs = entry.runs or {}
  for _, b in ipairs(entry.bin or {}) do
    if not runs[b] then
      runs[b] = { kind = "gem", hint = "bin/" .. b }
    end
  end
  entry.runs = runs
  return { line }
end

-- Windows renderer. RubyGems' .bat shim activates its gem through GEM_HOME and
-- GEM_PATH, which the isolated package dir is not on, so both are baked in.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local bindir = dirs.dir .. "\\bin"
  local spec = entry.pkg
  if entry.version and entry.version ~= "" then
    spec = spec .. ":" .. entry.version
  end
  local lines = {
    win.setenv("GEM_HOME", dirs.dir),
    win.setenv("GEM_PATH", dirs.dir),
    win.fail(win.run("gem install --no-user-install --no-document --no-format-executable"
      .. " --install-dir " .. win.quote(dirs.dir)
      .. " --bindir " .. win.quote(bindir)
      .. " " .. win.quote(spec))),
  }
  for _, b in ipairs(entry.bin or {}) do
    for _, l in ipairs(win.find(bindir, { b .. ".bat", b .. ".cmd" })) do
      lines[#lines + 1] = l
    end
    for _, l in ipairs(win.launcher(dirs.bin_dir .. "\\" .. b .. ".cmd", {
      win.setenv("GEM_HOME", dirs.dir),
      win.setenv("GEM_PATH", dirs.dir),
      'call "%_jot_found%" %%*',
    })) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
