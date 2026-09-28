-- openvsx manager: downloads a VS Code extension .vsix from open-vsx.org and
-- extracts it into the package dir; run hints point at the server inside.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

---@param entry table registry entry (manager = "openvsx")
---@param dirs table { root, dir, bin_dir, dl_dir }
function M.install_lines(entry, dirs)
  local o = entry.openvsx or {}
  if o.ns == "" then
    return nil
  end
  local version = o.version or ""
  local file = (o.file or ""):gsub("{{version}}", version):gsub("{{ version }}", version)
  if file == "" then
    file = o.ns .. "." .. o.ext .. "-" .. version .. ".vsix"
  end
  local url = "https://open-vsx.org/api/" .. o.ns .. "/" .. o.ext .. "/" .. version
    .. "/file/" .. file
  local L = {
    "mkdir -p " .. sh_quote(dirs.dl_dir),
    "curl -fsSL -o " .. sh_quote(dirs.dl_dir .. "/" .. file) .. " " .. sh_quote(url),
    "(cd " .. sh_quote(dirs.dir) .. " && unzip -oq " .. sh_quote(dirs.dl_dir .. "/" .. file) .. ")",
    "rm -f " .. sh_quote(dirs.dl_dir .. "/" .. file),
  }
  return L
end

-- Windows renderer. A .vsix is a zip, so Windows' own tar unpacks it, and the
-- server inside is a .js or .jar that the shared link step wraps in a launcher.
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local o = entry.openvsx or {}
  if o.ns == "" then
    return nil
  end
  local version = o.version or ""
  local file = (o.file or ""):gsub("{{version}}", version):gsub("{{ version }}", version)
  if file == "" then
    file = o.ns .. "." .. o.ext .. "-" .. version .. ".vsix"
  end
  local url = "https://open-vsx.org/api/" .. o.ns .. "/" .. o.ext .. "/" .. version
    .. "/file/" .. file
  local archive = dirs.dl_dir .. "\\" .. file
  local lines = { win.curl(url, archive), win.extract(archive, dirs.dir),
                  win.remove(archive) }
  for _, b in ipairs(win.native_bins(entry)) do
    for _, l in ipairs(win.publish(dirs.dir,
                                   { b .. ".exe", b .. ".cmd", b .. ".bat", b },
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
