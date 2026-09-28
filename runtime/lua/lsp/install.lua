-- LSP / language-tooling installer orchestrator (mason.nvim-inspired).
--
-- The package catalog (registry.lua) is generated from the mason registry by
-- tools/mason_import.py. Each manager module (managers/*.lua) renders the
-- install shell steps for one package-manager family. This module assembles
-- the steps into one script that runs in a silent background job: isolated
-- package dir + binaries/wrappers under <root>/bin + a receipt file, so
-- uninstall and status stay trivial.
--
-- The native host wraps the returned script with [jot:lsp] markers, spawns
-- it and polls the log. Pure Lua except `jot_lsp_root`/`jot_lsp_platform` and
-- the `jot_lsp_bundled` payload lookup, which the host sets before loading.

local registry = dofile(_G.jot_lsp_lua_root .. "/registry.lua")

local managers = {}
for _, name in ipairs({ "npm", "pypi", "golang", "cargo", "gem", "nuget",
                        "github", "generic", "openvsx", "luarocks", "composer", "opam",
                        "payload" }) do
  managers[name] = dofile(_G.jot_lsp_lua_root .. "/managers/" .. name .. ".lua")
end

-- cmd.exe step builders shared by the Windows renderers (managers/*_win).
-- Windows has no POSIX shell, so a package that should install there needs a
-- second renderer rather than a translation of the first one.
local win = dofile(_G.jot_lsp_lua_root .. "/managers/win.lua")

local M = {}

local ROOT = _G.jot_lsp_root or ""
local PLATFORM = _G.jot_lsp_platform or "linux"

local function sh_quote(v)
  return "'" .. tostring(v):gsub("'", "'\\''") .. "'"
end

local function package_dir(id)
  return ROOT .. "/" .. id
end

-- A package that ships the server under share/jot/payload/<bin> (the release
-- vendors clangd that way) is installed from that copy instead of the entry's
-- manager: no network, and no unpacking either. The host answers with nil when
-- this package carries no payload for the binary, which keeps every unbundled
-- server on its normal manager.
local function bundled_dir(entry)
  if type(jot_lsp_bundled) ~= "function" then
    return nil
  end
  local bin = entry.bin and entry.bin[1]
  if not bin then
    return nil
  end
  local dir = jot_lsp_bundled(bin)
  if type(dir) == "string" and dir ~= "" then
    return dir
  end
  return nil
end

-- Shared POSIX preamble: $PDIR (package dir), $BIN (managed bin dir) and a
-- _jot_bin helper that locates a produced file inside $PDIR and links it (or
-- writes an exec wrapper) under $BIN. `kind` selects the wrapper:
--   "" symlink, jar -> java -jar, node/python/php/ruby/dotnet -> exec runtime,
--   gem -> exec with GEM_HOME/GEM_PATH pinned to the package dir.
-- NOTE: %%s inside the wrapper printf format strings stays %s after the
-- outer string.format below.
local PREAMBLE = [[
set -eu
PDIR='%s'
BIN='%s'
mkdir -p "$PDIR" "$BIN"
_jot_bin() {
  _name="$1"; _kind="$2"; _pat="$3"
  if [ "${_pat#*/}" != "$_pat" ]; then
    _found=$(find "$PDIR" -path "$PDIR/$_pat" 2>/dev/null | head -n1)
  else
    _found=""
  fi
  if [ -z "$_found" ]; then
    _found=$(find "$PDIR" \( -type f -o -type l \) -name "$_pat" 2>/dev/null | head -n1)
  fi
  if [ -z "$_found" ]; then
    echo "install: binary $_name ($_pat) not found under $PDIR" >&2
    exit 1
  fi
  chmod +x "$_found" 2>/dev/null || true
  case "$_kind" in
    # Wrappers bake the concrete path at write time: the generated file is a
    # standalone launcher, so install-script variables are out of scope later.
    jar) printf '#!/bin/sh\nexec java -jar "%%s" "$@"\n' "$_found" > "$BIN/$_name" ;;
    node) printf '#!/bin/sh\nexec node "%%s" "$@"\n' "$_found" > "$BIN/$_name" ;;
    python) printf '#!/bin/sh\nexec python3 "%%s" "$@"\n' "$_found" > "$BIN/$_name" ;;
    php) printf '#!/bin/sh\nexec php "%%s" "$@"\n' "$_found" > "$BIN/$_name" ;;
    ruby) printf '#!/bin/sh\nexec ruby "%%s" "$@"\n' "$_found" > "$BIN/$_name" ;;
    dotnet) printf '#!/bin/sh\nexec dotnet "%%s" "$@"\n' "$_found" > "$BIN/$_name" ;;
    gem) printf '#!/bin/sh\nexec env GEM_HOME=%%s GEM_PATH=%%s "%%s" "$@"\n' "$PDIR" "$PDIR" "$_found" > "$BIN/$_name" ;;
    pypi) if [ -f "$PDIR/bin/$_name" ]; then
      # pip --target console script: needs the package dir importable.
      printf '#!/bin/sh\nexec env PYTHONPATH=%%s python3 "%%s" "$@"\n' "$PDIR" "$PDIR/bin/$_name" > "$BIN/$_name"
    else
      # Compiled/data-file wheel (no console script): link it directly.
      ln -sfn "$_found" "$BIN/$_name"
    fi ;;
    *) ln -sfn "$_found" "$BIN/$_name" ;;
  esac
  chmod +x "$BIN/$_name" 2>/dev/null || true
}
]]

-- Emits a _jot_bin call for every public binary. `runs` (from the catalog)
-- carries {kind, hint} when the binary needs an interpreter; the hint path's
-- basename is what we search for.
local function link_lines(entry)
  local out = {}
  local runs = entry.runs or {}
  for _, b in ipairs(entry.bin or {}) do
    local spec = runs[b]
    local kind = spec and spec.kind or ""
    local hint = (spec and spec.hint ~= "") and spec.hint or b
    local pat = hint:match("([^/]+)$") or b
    out[#out + 1] = ("_jot_bin %s %s %s"):format(sh_quote(b), sh_quote(kind), sh_quote(pat))
  end
  return out
end

-- Manager and entry for one install. A package that ships the server under
-- share/jot/payload/<bin> (a release vendors clangd that way) is installed from
-- that copy instead of the entry's manager: no network, and no unpacking
-- either. The host answers nil when this package carries no payload for the
-- binary, which keeps every unbundled server on its normal manager.
local function select_manager(entry)
  local payload = bundled_dir(entry)
  if payload then
    local copy = {}
    for k, v in pairs(entry) do
      copy[k] = v
    end
    copy.payload_dir = payload
    -- The payload manager produces the links itself; the shared _jot_bin step
    -- would look for the binaries under $PDIR, where a payload install puts
    -- nothing.
    copy.no_bin_link = true
    return copy, managers.payload
  end
  return entry, managers[entry.manager]
end

-- The package dir, bin dir and download dir for one id. The separator follows
-- the platform being rendered, not the host: a Windows script has to speak
-- cmd.exe path syntax wherever it was generated.
local function package_dirs(id, sep)
  local dir = ROOT .. sep .. id
  return { root = ROOT, dir = dir, bin_dir = ROOT .. sep .. "bin",
           dl_dir = dir .. sep .. "dl" }
end

local function build_install_script(entry)
  local dirs = package_dirs(entry.id, "/")
  local manager
  entry, manager = select_manager(entry)
  if not manager or not manager.install_lines then
    return nil
  end
  local lines = manager.install_lines(entry, dirs, PLATFORM)
  if not lines then
    return nil
  end
  local script = {
    PREAMBLE:format(dirs.dir, dirs.bin_dir),
  }
  for _, l in ipairs(lines) do
    script[#script + 1] = l
  end
  if not entry.no_bin_link then
    for _, l in ipairs(link_lines(entry)) do
      script[#script + 1] = l
    end
  end
  -- A receipt is only written after every step succeeded (set -e).
  script[#script + 1] = "printf 'name=%s\\n' " .. sh_quote(entry.id) .. " > "
    .. sh_quote(dirs.dir .. "/receipt")
  return table.concat(script, "\n") .. "\n"
end

-- Windows script. The steps come from each manager's install_lines_win and the
-- host runs them as a batch file (LspInstall::wrap_script): there is no POSIX
-- shell on Windows, and inside a batch a failed step can abort with `exit /b`,
-- which is what keeps the receipt below and the success marker honest.
--
-- A catalog win_cmd installs globally instead (a launcher on PATH that is not a
-- managed bin), so the renderers supersede it; the field stays for packagers.
local function build_install_script_win(entry)
  local dirs = package_dirs(entry.id, "\\")
  dirs.win = win
  local manager
  entry, manager = select_manager(entry)
  if not manager or not manager.install_lines_win then
    return nil
  end
  local lines = manager.install_lines_win(entry, dirs)
  if not lines then
    return nil
  end
  -- setlocal keeps the scratch variables out of the shell the user is looking
  -- at: the fallback transport runs this in an integrated terminal.
  local script = { "setlocal" }
  for _, l in ipairs(win.ensure_dirs(dirs)) do
    script[#script + 1] = l
  end
  for _, l in ipairs(lines) do
    script[#script + 1] = l
  end
  -- Written last, so it can only exist once every step ran. Leading
  -- redirection keeps the file name off echo's argument line.
  script[#script + 1] = "> " .. win.quote(dirs.dir .. "\\receipt") .. " echo name=" .. entry.id
  return table.concat(script, "\n") .. "\n"
end

local function build_remove_script(entry)
  local lines = { "set -u" }
  for _, b in ipairs(entry.bin or {}) do
    lines[#lines + 1] = "rm -f " .. sh_quote(ROOT .. "/bin/" .. b)
  end
  lines[#lines + 1] = "rm -rf " .. sh_quote(package_dir(entry.id))
  return table.concat(lines, "\n") .. "\n"
end

-- Windows counterpart: a managed bin can be the bare name, a .exe or a
-- launcher script, and every one of them has to go before the package dir.
local function build_remove_script_win(entry)
  local dirs = package_dirs(entry.id, "\\")
  local lines = { "setlocal" }
  for _, b in ipairs(entry.bin or {}) do
    for _, ext in ipairs({ "", ".exe", ".cmd", ".bat" }) do
      lines[#lines + 1] = win.remove(dirs.bin_dir .. "\\" .. b .. ext)
    end
  end
  lines[#lines + 1] = "if exist " .. win.quote(dirs.dir) .. " rd /s /q "
    .. win.quote(dirs.dir)
  return table.concat(lines, "\n") .. "\n"
end

local function catalog_list()
  local out = {}
  for _, e in ipairs(registry.entries) do
    out[#out + 1] = { id = e.id, display = e.display, detail = e.detail }
  end
  return out
end

---@param name string user-supplied id / alias
function M.plan_install(name)
  local entry = registry.resolve(name)
  if not entry then
    return nil
  end
  local base = { id = entry.id }
  -- Not the `a and b() or c()` idiom: a renderer that declines this package
  -- returns nil, and that must read as "unsupported", not as "try the POSIX
  -- script on Windows".
  local script
  if PLATFORM == "win" then
    script = build_install_script_win(entry)
  else
    script = build_install_script(entry)
  end
  if not script then
    base.script = ""
    base.message = entry.display .. (PLATFORM == "win"
      and " has no Windows installer yet"
      or " is not supported on this platform yet")
    return base
  end
  base.script = script
  base.message = "LSP install started: " .. entry.id
  if bundled_dir(entry) then
    base.message = base.message .. " (bundled copy)"
  end
  return base
end

function M.plan_remove(name)
  local entry = registry.resolve(name)
  if not entry then
    return nil
  end
  local base = { id = entry.id }
  if PLATFORM == "win" then
    base.script = build_remove_script_win(entry)
  else
    base.script = build_remove_script(entry)
  end
  base.message = "LSP remove started: " .. entry.id
  return base
end

-- Globals the native host calls (see api_lsp_install.cpp).
jot_lsp_plan_install = M.plan_install
jot_lsp_plan_remove = M.plan_remove
jot_lsp_list = catalog_list

-- Plugin-facing surface: jot.lsp.installer.*
local jot = _G.jot
if jot then
  if not jot.lsp then
    jot.lsp = {}
  end
  jot.lsp.installer = M
  jot.lsp.installer.list = catalog_list
  jot.lsp.installer.resolve = registry.resolve
end

return M