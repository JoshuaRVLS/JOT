-- cmd.exe primitives for the Windows install renderers.
--
-- Windows has no /bin/sh, so a POSIX manager script cannot run there at all:
-- every manager that supports Windows renders a second script out of these,
-- which the host writes to a batch file and runs with `cmd /c <file>`. Only
-- what Windows itself carries is used (curl.exe, tar.exe, and the package
-- manager the entry names), and only batch lines, so `for` variables are
-- spelled with two percents and every step is followed by `|| exit /b 1`.

local M = {}

-- Quotes for cmd.exe. Paths travel through exactly one parser (the batch file
-- is read directly by cmd, never re-split by another shell), so doubling the
-- quotes is enough.
function M.quote(v)
  return '"' .. tostring(v):gsub('"', '""') .. '"'
end

-- Appends the fail-fast abort to a step.
function M.fail(cmd)
  return cmd .. " || exit /b 1"
end

-- Runs a package manager. npm, gem, composer and luarocks are batch shims on
-- Windows, and a batch started from a batch without `call` never comes back.
function M.run(cmd)
  return "call " .. cmd
end

function M.mkdir(path)
  return M.fail("if not exist " .. M.quote(path) .. " mkdir " .. M.quote(path))
end

function M.remove(path)
  return "if exist " .. M.quote(path) .. " del /f /q " .. M.quote(path)
end

function M.curl(url, out)
  return M.fail("curl -fsSL -o " .. M.quote(out) .. " " .. M.quote(url))
end

-- tar.exe unpacks zip and tar archives both, so one step covers every archive
-- kind the catalog uses.
function M.extract(archive, into)
  return M.fail("tar -xf " .. M.quote(archive) .. " -C " .. M.quote(into))
end

-- The package dir, the managed bin dir and the download dir.
function M.ensure_dirs(dirs)
  return { M.mkdir(dirs.dir), M.mkdir(dirs.bin_dir), M.mkdir(dirs.dl_dir) }
end

-- `set "NAME=value"` form: keeps a trailing space out of the value, and the
-- generated script's setlocal keeps the variable out of the user's shell.
function M.setenv(name, value)
  return 'set "' .. name .. "=" .. tostring(value) .. '"'
end

-- The command a catalog run kind needs, or nil when what the manager produces
-- starts on its own (pip's .exe launchers, the .bat shim gem installs).
local INTERPRETERS = {
  node = "node",
  jar = "java -jar",
  php = "php",
  ruby = "ruby",
  python = "python",
  dotnet = "dotnet",
}

function M.interpreter(kind)
  return INTERPRETERS[kind]
end

-- The bins a manager publishes as files: the ones the catalog does not route
-- through an interpreter, which the link step turns into launcher scripts.
function M.native_bins(entry)
  local out = {}
  for _, b in ipairs(entry.bin or {}) do
    local spec = (entry.runs or {})[b]
    if not spec or not M.interpreter(spec.kind) then
      out[#out + 1] = b
    end
  end
  return out
end

-- The names a manager looks for when publishing a native bin: the catalog's
-- own hint first (a release asset is not always named after the bin), then the
-- bin with each launcher extension a Windows package can carry.
function M.bin_patterns(entry, b)
  local out = {}
  local hint = (entry.runs or {})[b] and (entry.runs or {})[b].hint
  if hint and hint ~= "" and hint ~= b and not hint:find("{", 1, true) then
    out[#out + 1] = hint:match("([^/\\]+)$") or hint
  end
  for _, ext in ipairs({ ".exe", ".cmd", ".bat", "" }) do
    local candidate = b .. ext
    local seen = false
    for _, existing in ipairs(out) do
      seen = seen or existing == candidate
    end
    if not seen then
      out[#out + 1] = candidate
    end
  end
  return out
end

-- Leaves the path of the first file named like one of `patterns` in
-- %_jot_found%, and fails the script when there is none: a `for` loop over no
-- matches runs zero times, so the check is what turns a missing payload into a
-- failed install. `exact` is the path the catalog named, tried first so a stray
-- file with the same basename elsewhere in the tree cannot win the search.
function M.find(dir, patterns, exact)
  local lines = { 'set "_jot_found="' }
  if exact then
    local path = dir .. "\\" .. exact:gsub("/", "\\")
    lines[#lines + 1] = "if exist " .. M.quote(path) .. " set \"_jot_found=" .. path .. "\""
  end
  for _, pattern in ipairs(patterns) do
    lines[#lines + 1] = "for /r " .. M.quote(dir) .. " %%f in (" .. pattern
      .. ") do @if not defined _jot_found set \"_jot_found=%%f\""
  end
  lines[#lines + 1] = "if not defined _jot_found (echo install: no "
    .. table.concat(patterns, " / ") .. " under the package dir & exit /b 1)"
  return lines
end

-- Writes `dest` as a launcher script: line one silences cmd's echo and the rest
-- run. Lines are written as cmd reads them, so a percent meant for the file is
-- doubled (%%*) while %_jot_found% expands as the file is written.
function M.launcher(dest, lines)
  local out = { "> " .. M.quote(dest) .. " echo @echo off" }
  for _, line in ipairs(lines) do
    out[#out + 1] = ">> " .. M.quote(dest) .. " echo " .. line
  end
  return out
end

-- Publishes the first file matching one of `patterns` under `dir` as
-- `<bin_dir>\name<its own extension>`: the extension is what the editor
-- resolves a managed bin by, and a `for` loop over no matches does nothing, so
-- the existence check at the end is what fails the script on a missing binary.
function M.publish(dir, patterns, name)
  local lines = {}
  local dest = M.quote(name .. "%%~xf")
  for _, pattern in ipairs(patterns) do
    lines[#lines + 1] = "for /r " .. M.quote(dir) .. " %%f in (" .. pattern
      .. ") do @if not exist " .. M.quote(name .. ".*") .. " copy /Y \"%%f\" " .. dest
      .. " >NUL"
  end
  lines[#lines + 1] = "if not exist " .. M.quote(name .. "*") .. " (echo install: no "
    .. table.concat(patterns, " / ") .. " under the package dir & exit /b 1)"
  return lines
end

return M
