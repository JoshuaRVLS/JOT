-- pypi manager: pip installs into the isolated package dir (console scripts
-- land in <dir>/bin) and those entries are linked into <root>/bin.
-- Console scripts are wrapped to run with PYTHONPATH=$PDIR: pip --target
-- scripts cannot import their own package otherwise. Wheels that ship a
-- compiled binary instead (no <dir>/bin/<name>) fall back to a plain link.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

---@param entry table registry entry (manager = "pypi")
---@param dirs table { root, dir, bin_dir, dl_dir }
function M.install_lines(entry, dirs)
  local spec = entry.pkg
  local extra = (entry.extras or {}).extra
  if extra and extra ~= "" then
    spec = spec .. "[" .. extra .. "]"
  end
  if entry.version and entry.version ~= "" then
    spec = spec .. "==" .. entry.version
  end
  local line = "python3 -m pip install --quiet --disable-pip-version-check --target "
    .. sh_quote(dirs.dir) .. " " .. sh_quote(spec)
  local runs = entry.runs or {}
  for _, b in ipairs(entry.bin or {}) do
    local existing = runs[b]
    if not existing or existing.kind == "" then
      runs[b] = { kind = "pypi", hint = "bin/" .. b }
    end
  end
  entry.runs = runs
  return { line }
end

-- Windows renderer. The interpreter is `python` there, not `python3`, and
-- --target writes the console scripts into <dir>\bin as .exe launchers: there is
-- nothing to wrap, so the launcher is published as is.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local spec = entry.pkg
  local extra = (entry.extras or {}).extra
  if extra and extra ~= "" then
    spec = spec .. "[" .. extra .. "]"
  end
  if entry.version and entry.version ~= "" then
    spec = spec .. "==" .. entry.version
  end
  local line = "python -m pip install --quiet --disable-pip-version-check --target "
    .. win.quote(dirs.dir) .. " " .. win.quote(spec)
  local lines = { win.fail(line) }
  for _, b in ipairs(entry.bin or {}) do
    for _, l in ipairs(win.publish(dirs.dir .. "\\bin", { b .. ".exe", b },
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
