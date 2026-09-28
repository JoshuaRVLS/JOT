-- github manager: downloads the release asset for the current platform. The
-- catalog pins a released version, but tag naming varies between repos
-- (v1.2.3 vs 1.2.3 vs clangd-…), so candidate tags are probed and the asset
-- name is matched against the catalog's per-platform regex instead of being
-- guessed. Extracted binaries are found by basename at link time.

local M = {}

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

---@param entry table registry entry (manager = "github")
---@param dirs table { root, dir, bin_dir, dl_dir }
---@param platform string "linux" | "mac" | "win"
function M.install_lines(entry, dirs, platform)
  local spec = entry.asset and entry.asset[platform]
  if not spec then
    return nil
  end
  local repo = entry.repo
  local version = entry.version or ""
  local bare = version:gsub("^v", "")

  -- Candidate release refs: pinned version (common), with/without 'v', then
  -- the repo's latest release as a final fallback.
  local refs = {}
  for _, v in ipairs({ version, bare, "v" .. bare }) do
    if v ~= "" and not refs[v] then
      refs[#refs + 1] = v
    end
  end
  local match = spec.match
  local primary = (entry.bin and entry.bin[1]) or "app"

  local L = {
    "mkdir -p " .. sh_quote(dirs.dl_dir),
    "_asset=''",
    "_tag=''",
    -- _probe <api-path> e.g. releases/tags/v1.2.3 or releases/latest
    "_probe() {",
    '  _r=$(curl -fsSL "https://api.github.com/repos/' .. repo .. '/$1" 2>/dev/null || true)',
    '  if [ -z "$_r" ]; then return 1; fi',
    '  _names=$(printf \'%s\' "$_r" | grep \'"name":\' | sed -E \'s/.*"name": *"([^"]*)".*/\\1/\')',
    "  _cand=$(printf '%s\\n' \"$_names\" | grep -E " .. sh_quote("^" .. match .. "$") .. " || true)",
    '  if [ -z "$_cand" ]; then return 1; fi',
    '  _asset=$(printf \'%s\\n\' "$_cand" | grep -E \'x86_64|x64|amd64|aarch64|arm64\' | head -n1)',
    "  if [ -z \"$_asset\" ]; then _asset=$(printf '%s\\n' \"$_cand\" | head -n1); fi",
    '  _tag=$(printf \'%s\' "$_r" | sed -n \'s/.*"tag_name": *"\\([^"]*\\)".*/\\1/p\' | head -n1)',
    "  return 0",
    "}",
  }

  local probes = {}
  for _, ref in ipairs(refs) do
    probes[#probes + 1] = "_probe releases/tags/" .. ref
  end
  probes[#probes + 1] = "_probe releases/latest"
  L[#L + 1] = table.concat(probes, " && [ -n \"$_asset\" ] || ") .. " || true"
  L[#L + 1] = 'if [ -z "$_asset" ]; then'
  L[#L + 1] = '  echo "github: no release asset for ' .. repo .. ' matching ' .. match .. '" >&2'
  L[#L + 1] = "  exit 1"
  L[#L + 1] = "fi"
  L[#L + 1] = "curl -fsSL -o " .. sh_quote(dirs.dl_dir .. "/$_asset")
    .. " \"https://github.com/" .. repo .. "/releases/download/$_tag/$_asset\""

  local archive = spec.archive
  local runs = entry.runs or {}
  if archive == "zip" then
    L[#L + 1] = "(cd " .. sh_quote(dirs.dir) .. " && unzip -oq " .. sh_quote(dirs.dl_dir .. "/$_asset") .. ")"
    L[#L + 1] = "rm -f " .. sh_quote(dirs.dl_dir .. "/$_asset")
  elseif archive == "tar.gz" or archive == "tar" then
    L[#L + 1] = "(cd " .. sh_quote(dirs.dir) .. " && tar -xzf " .. sh_quote(dirs.dl_dir .. "/$_asset") .. ")"
    L[#L + 1] = "rm -f " .. sh_quote(dirs.dl_dir .. "/$_asset")
  elseif archive == "gz" then
    L[#L + 1] = "gzip -dc " .. sh_quote(dirs.dl_dir .. "/$_asset") .. " > "
      .. sh_quote(dirs.dir .. "/" .. primary)
    L[#L + 1] = "rm -f " .. sh_quote(dirs.dl_dir .. "/$_asset")
    L[#L + 1] = "chmod +x " .. sh_quote(dirs.dir .. "/" .. primary)
  else
    -- Direct file (jar / phar / binary): keep it under the public bin name so
    -- the linker can find it by basename.
    L[#L + 1] = "mv -f " .. sh_quote(dirs.dl_dir .. "/$_asset") .. " " .. sh_quote(dirs.dir .. "/" .. primary)
    L[#L + 1] = "chmod +x " .. sh_quote(dirs.dir .. "/" .. primary) .. " 2>/dev/null || true"
    for _, b in ipairs(entry.bin or {}) do
      local rs = runs[b]
      if rs and rs.hint and rs.hint:find("{", 1, true) then
        runs[b] = { kind = rs.kind, hint = b }
      end
    end
    entry.runs = runs
  end

  return L
end

-- Windows renderer. None of the POSIX tools the steps above use exist there
-- (no grep/sed/unzip), and there is no JSON reader either, so the release asset
-- is located with findstr and its URL is taken straight out of the release
-- metadata: browser_download_url already carries the tag, which leaves nothing
-- to reconstruct. The transfer is curl.exe and the unpacking is tar.exe, both
-- shipped with Windows 10 (1803 and 17063 respectively).
function M.install_lines_win(entry, dirs)
  local win = dirs.win
  local spec = entry.asset and entry.asset["win"]
  if not spec then
    return nil
  end
  -- tar.exe unpacks zip, tar and tar.gz alike, so every archive kind but a
  -- bare .gz is covered. A bare .gz or a non-.exe single-file release has no
  -- Windows path yet, and saying so beats installing something unrunnable.
  local archive = spec.archive
  if archive == "gz" then
    return nil
  end
  if archive ~= "zip" and archive ~= "tar.gz" and archive ~= "tar"
    and not spec.match:find("%.exe") then
    return nil
  end
  local repo = entry.repo
  local version = entry.version or ""
  local bare = version:gsub("^v", "")
  local refs = {}
  for _, v in ipairs({ version, bare, "v" .. bare }) do
    if v ~= "" and not refs[v] then
      refs[#refs + 1] = v
    end
  end
  refs[#refs + 1] = "latest"

  local json = dirs.dl_dir .. "\\release.json"
  local rows = dirs.dl_dir .. "\\asset-rows.txt"
  local host_rows = dirs.dl_dir .. "\\asset-rows-host.txt"
  local asset = dirs.dl_dir .. "\\asset"

  -- findstr writes its output file whether or not there are matches, and the
  -- for /f that reads it relies on that.
  local function select(out, host_only)
    local cmd = "findstr /R /C:" .. win.quote("browser_download_url.*" .. spec.match)
      .. " " .. win.quote(json)
    if host_only then
      cmd = cmd .. " | findstr /I /R " .. win.quote("x86_64 x64 amd64 aarch64 arm64")
    end
    return cmd .. " > " .. win.quote(out)
  end

  -- The URL is the second whitespace-separated token on its JSON row (the value
  -- is quoted and comma-terminated, hence the two strips). _jot_url stays
  -- undefined until something is found, which is what makes `if not defined`
  -- mean "nothing found yet" on every step below.
  local function take(out)
    return {
      select(out, out == host_rows),
      "for /f \"usebackq tokens=2\" %%A in (" .. win.quote(out)
        .. ") do @if not defined _jot_url set \"_jot_url=%%A\"",
      'if defined _jot_url set _jot_url=%_jot_url:"=%',
      "if defined _jot_url if \"%_jot_url:~-1%\"==\",\" set _jot_url=%_jot_url:~0,-1%",
    }
  end

  local lines = {}
  -- Try the pinned refs in turn and then the repo's latest release, exactly like
  -- the POSIX renderer: tag naming varies between repos.
  for _, ref in ipairs(refs) do
    local api = "https://api.github.com/repos/" .. repo
      .. (ref == "latest" and "/releases/latest" or "/releases/tags/" .. ref)
    lines[#lines + 1] = "if not defined _jot_url curl -fsSL -o " .. win.quote(json)
      .. " " .. win.quote(api) .. " >NUL 2>NUL"
    -- Prefer the host architecture: a release often carries several builds and
    -- the pattern alone can match more than one.
    for _, l in ipairs(take(host_rows)) do
      lines[#lines + 1] = l
    end
    for _, l in ipairs(take(rows)) do
      lines[#lines + 1] = l
    end
  end
  lines[#lines + 1] = "if not defined _jot_url (echo github: no release asset for " .. repo
    .. " matching " .. spec.match .. " & exit /b 1)"
  lines[#lines + 1] = win.curl("%_jot_url%", asset)

  if archive == "zip" or archive == "tar.gz" or archive == "tar" then
    lines[#lines + 1] = win.extract(asset, dirs.dir)
  else
    local primary = (entry.bin and entry.bin[1]) or "app"
    lines[#lines + 1] = "copy /Y " .. win.quote(asset) .. " "
      .. win.quote(dirs.dir .. "\\" .. primary .. ".exe") .. " >NUL"
  end
  lines[#lines + 1] = win.remove(asset)

  for _, b in ipairs(entry.bin or {}) do
    for _, l in ipairs(win.publish(dirs.dir, { b .. ".exe", b .. ".cmd", b .. ".bat", b },
                                   dirs.bin_dir .. "\\" .. b)) do
      lines[#lines + 1] = l
    end
  end
  return lines
end

return M
