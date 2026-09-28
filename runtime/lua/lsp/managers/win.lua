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
