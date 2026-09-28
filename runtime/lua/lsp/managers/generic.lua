-- generic manager: direct URL downloads pinned by the catalog (no release
-- mechanics). Archive files are extracted into the package dir; plain files
-- are stored under their catalog filename.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

local function archive_kind(name)
  local low = name:lower()
  if low:match("%.zip$") or low:match("%.vsix$") then
    return "zip"
  end
  if low:match("%.tar%.gz$") or low:match("%.tgz$") or low:match("%.tar$") then
    return "tar.gz"
  end
  if low:match("%.gz$") then
    return "gz"
  end
  return ""
end

---@param entry table registry entry (manager = "generic")
---@param dirs table { root, dir, bin_dir, dl_dir }
---@param platform string
function M.install_lines(entry, dirs, platform)
  local spec = (entry.dl or {})[platform]
  if not spec then
    return nil
  end
  local L = { "mkdir -p " .. sh_quote(dirs.dl_dir) }
  for name, url in pairs(spec.files or {}) do
    local kind = archive_kind(name)
    if kind == "" then
      L[#L + 1] = "curl -fsSL -o " .. sh_quote(dirs.dir .. "/" .. name) .. " " .. sh_quote(url)
      L[#L + 1] = "chmod +x " .. sh_quote(dirs.dir .. "/" .. name) .. " 2>/dev/null || true"
    else
      L[#L + 1] = "curl -fsSL -o " .. sh_quote(dirs.dl_dir .. "/" .. name) .. " " .. sh_quote(url)
      if kind == "zip" then
        L[#L + 1] = "(cd " .. sh_quote(dirs.dir) .. " && unzip -oq " .. sh_quote(dirs.dl_dir .. "/" .. name) .. ")"
      else
        L[#L + 1] = "(cd " .. sh_quote(dirs.dir) .. " && tar -xzf " .. sh_quote(dirs.dl_dir .. "/" .. name) .. ")"
      end
      L[#L + 1] = "rm -f " .. sh_quote(dirs.dl_dir .. "/" .. name)
    end
  end
  -- Point linker patterns at the produced names when the catalog gave us an
  -- archive-internal bin path.
  local runs = entry.runs or {}
  if spec.bin ~= "" then
    for _, b in ipairs(entry.bin or {}) do
      if not runs[b] then
        runs[b] = { kind = "", hint = spec.bin:match("([^/]+)$") or b }
      end
    end
    entry.runs = runs
  end
  return L
end

-- Windows renderer. The catalog carries a per-platform download list, so the
-- files come from `dl.win`, transferred and unpacked by curl.exe and tar.exe.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local spec = (entry.dl or {})["win"]
  if not spec then
    return nil
  end
  local lines = {}
  -- Sorted so the plan is stable: an install script that reorders itself is
  -- hard to tell apart from one that changed.
  local names = {}
  for name in pairs(spec.files or {}) do
    names[#names + 1] = name
  end
  table.sort(names)
  for _, name in ipairs(names) do
    local kind = archive_kind(name)
    if kind == "" then
      lines[#lines + 1] = win.curl(spec.files[name], dirs.dir .. "\\" .. name)
    else
      local archive = dirs.dl_dir .. "\\" .. name
      lines[#lines + 1] = win.curl(spec.files[name], archive)
      lines[#lines + 1] = win.extract(archive, dirs.dir)
      lines[#lines + 1] = win.remove(archive)
    end
  end
  -- Same as the POSIX side: the catalog names the binary inside the archive
  -- for this platform, and the run hint is its basename.
  local runs = entry.runs or {}
  if spec.bin ~= "" then
    for _, b in ipairs(win.native_bins(entry)) do
      if not runs[b] then
        runs[b] = { kind = "", hint = spec.bin:match("([^/\\]+)$") or b }
      end
    end
    entry.runs = runs
  end
  for _, b in ipairs(win.native_bins(entry)) do
    for _, l in ipairs(win.publish(dirs.dir, win.bin_patterns(entry, b),
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
