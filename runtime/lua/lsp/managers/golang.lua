-- golang manager: `go install` with GOBIN pointed at the isolated package
-- dir, then the built binary is linked into <root>/bin. Registry pins are
-- already canonical module versions (vX.Y.Z).

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

-- `go install` wants the package path, not the module path: the catalog carries
-- that as a "#cmd/dlv" suffix on the version, which go rejects inline.
local function install_target(entry)
  local pkg = entry.pkg
  local version = entry.version or ""
  local sub = version:match("#(.+)$")
  if sub then
    version = version:sub(1, #version - #sub - 1)
    pkg = pkg .. "/" .. sub
  end
  return pkg .. "@" .. (version ~= "" and version or "latest")
end

---@param entry table registry entry (manager = "golang")
---@param dirs table { root, dir, bin_dir, dl_dir }
function M.install_lines(entry, dirs)
  local target = install_target(entry)
  local line = "GOBIN=" .. sh_quote(dirs.dir .. "/bin") .. " go install " .. sh_quote(target)
  local runs = entry.runs or {}
  for _, b in ipairs(entry.bin or {}) do
    if not runs[b] then
      runs[b] = { kind = "", hint = "bin/" .. b }
    end
  end
  entry.runs = runs
  return { line }
end

-- Windows renderer. GOBIN is what decides where the built .exe lands, and it
-- is set with cmd's own syntax instead of an inline assignment prefix.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local gobin = dirs.dir .. "\\bin"
  local lines = {
    win.setenv("GOBIN", gobin),
    win.mkdir(gobin),
    win.fail("go install " .. win.quote(install_target(entry))),
  }
  for _, b in ipairs(win.native_bins(entry)) do
    for _, l in ipairs(win.publish(gobin, { b .. ".exe", b },
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
