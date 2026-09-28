-- cmd.exe primitives for the Windows install renderers.
--
-- Windows has no /bin/sh: the POSIX manager scripts cannot run there at all.
-- Every manager that supports Windows therefore renders a second script out of
-- these helpers, and the host writes it to a batch file and runs it with
-- `cmd /c <file>` (see LspInstall::wrap_script). Being a batch file is what
-- makes fail-fast possible: a step that fails aborts the whole script with
-- `exit /b 1`, `cmd /c` reports that status, and the host's success marker
-- cannot lie about a half-finished install.
--
-- Only what Windows itself provides is used: curl.exe (Windows 10 1803+) and
-- tar.exe (17063+, a bsdtar that unpacks zip as well as tar) plus the package
-- manager the entry already names (npm, python). Nothing is downloaded to get
-- the downloader, and no POSIX tool (ln, unzip, chmod, find) is assumed.
--
-- NOTE: these lines end up in a batch file, so `for` variables are spelled
-- with two percents (%%f), and every non-zero step is followed by `|| exit /b 1`.

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
-- `<bin_dir>\name<its own extension>`, and fails the script when none matched.
-- Patterns are tried in order, so a caller can put the runnable extension first
-- (.cmd before the extensionless sh script npm writes beside it). The search is
-- recursive because an archive keeps its own directory level
-- (clangd_22.1.8\bin\clangd.exe).
--
-- The extension is kept because the editor resolves a managed bin by looking
-- for exactly <name>, <name>.exe, <name>.cmd and <name>.bat: a copy without one
-- would never be found. The final existence check is what makes the guard real
-- -- a `for` loop over no matches simply does nothing, and would otherwise let
-- the install report success for a binary that is not there.
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
