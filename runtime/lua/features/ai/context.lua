-- What a prompt carries besides the words: the `#` references and the `/`
-- context commands of the chat's prompt line.
--
--   #buffer          the focused file
--   #selection       the focused buffer's selection
--   #diagnostics     the focused buffer's diagnostics
--   #file:<path>     a file, workspace-relative or absolute
--   /buffer  /selection  /diagnostics  /file <path>
--
-- A reference becomes a short label in the sentence and its content is
-- appended to the prompt under it, so a question stays readable while the
-- model still receives the whole file. A context command on its own line does
-- the same thing and is dropped from the text, which is how a long path is
-- attached without breaking the sentence:
--
--   /file src/render/frame.cpp
--   why is the row diff rebuilt here
--
-- An unknown `#word` or `/line` is left exactly as typed: a prompt full of
-- `#include` is a prompt, not three references.
local M = {}

-- One file is capped so a huge buffer cannot swallow the request; the prompt
-- says it was cut, so the model can ask for the rest.
M.MAX_BLOCK_LINES = 400

local function split_lines(text)
  local out = {}
  for line in (text .. "\n"):gmatch("(.-)\n") do
    out[#out + 1] = line
  end
  -- A trailing newline ends the last line rather than adding an empty one, so
  -- a file that ends with one is not attached with a blank line before the
  -- closing fence.
  if #out > 1 and out[#out] == "" then
    table.remove(out)
  end
  return out
end

local function fence(path)
  local ext = path and path:match("%.([%w]+)$")
  return ext and ext:lower() or ""
end

local function focused_index()
  local index = jot.buffer.current()
  return index and index ~= 0 and index or nil
end

-- The focused buffer's text.
local function buffer_ref()
  local index = focused_index()
  if not index then
    return nil
  end
  local text = jot.buffer.text(index)
  if text == nil then
    local lines = jot.buffer.lines(index)
    if lines then
      text = table.concat(lines, "\n")
    end
  end
  if not text then
    return nil
  end
  local meta = jot.buffer.meta(index) or {}
  local name = meta.path ~= "" and meta.path or (meta.name ~= "" and meta.name or "buffer")
  return { label = name, body = text, language = fence(name) }
end

local function selection_ref()
  local index = focused_index()
  if not index then
    return nil
  end
  local selection = jot.buffer.selection(index)
  if not selection or not selection.active or not selection.start_line then
    return nil
  end
  local lines = jot.buffer.lines(index) or {}
  local out = {}
  for line = selection.start_line, selection.end_line do
    local text = lines[line]
    if text then
      if selection.start_line == selection.end_line then
        text = text:sub(selection.start_col, selection.end_col - 1)
      elseif line == selection.start_line then
        text = text:sub(selection.start_col)
      elseif line == selection.end_line then
        text = text:sub(1, selection.end_col - 1)
      end
      out[#out + 1] = text
    end
  end
  local meta = jot.buffer.meta(index) or {}
  return {
    label = (meta.path ~= "" and meta.path or "buffer") .. " (selection)",
    body = table.concat(out, "\n"),
    language = fence(meta.path),
  }
end

local function diagnostics_ref()
  local index = focused_index()
  if not index then
    return nil
  end
  local items = jot.diagnostics.get(index) or {}
  if #items == 0 then
    return nil
  end
  local path = (jot.buffer.meta(index) or {}).path
  if not path or path == "" then
    path = "buffer"
  end
  local names = { [1] = "error", [2] = "warning", [3] = "info", [4] = "hint" }
  local out = {}
  for _, item in ipairs(items) do
    out[#out + 1] = ("%s:%d:%d: %s: %s"):format(path, item.line or 0, item.col or 0,
                                               names[item.severity] or "diagnostic",
                                               item.message or "")
  end
  return { label = "diagnostics", body = table.concat(out, "\n"), language = "" }
end

local function file_ref(path)
  if not path or path == "" then
    return nil, "/file needs a path"
  end
  local candidate = path
  if not path:match("^/") and not path:match("^%a:[/\\]") then
    local root = jot.workspace.path()
    if root and root ~= "" then
      candidate = root .. "/" .. path
    end
  end
  local text = jot.file.read(candidate)
  if text == nil then
    return nil, "cannot read " .. path
  end
  return { label = path, body = text, language = fence(path) }
end

-- Caps a block and says how much was dropped. The lines are always rebuilt
-- from the split, so the block text has no trailing newline of its own - a
-- file that ends with one would otherwise close its fence a line early.
local function cap(text)
  local lines = split_lines(text)
  local total = #lines
  if total <= M.MAX_BLOCK_LINES then
    return table.concat(lines, "\n"), nil
  end
  local kept = {}
  for i = 1, M.MAX_BLOCK_LINES do
    kept[i] = lines[i]
  end
  return table.concat(kept, "\n"),
         ("first %d of %d lines"):format(M.MAX_BLOCK_LINES, total)
end

-- The reference named by a `#token` or a context command, as (ref, message).
function M.reference(name, arg)
  local ref, message
  if name == "buffer" then
    ref = buffer_ref()
    message = ref and nil or "nothing to read for #buffer"
  elseif name == "selection" then
    ref = selection_ref()
    message = ref and nil or "nothing selected"
  elseif name == "diagnostics" then
    ref = diagnostics_ref()
    message = ref and nil or "no diagnostics in this buffer"
  elseif name == "file" then
    ref, message = file_ref(arg)
  end
  if not ref then
    return nil, message
  end
  if ref.body == nil or ref.body == "" then
    return nil, "nothing to read for " .. name
  end
  local body, cut = cap(ref.body)
  ref.body = body
  ref.cut = cut
  return ref
end

local function block_text(ref)
  local header = ref.label
  if ref.cut then
    header = header .. " (" .. ref.cut .. ")"
  end
  return ("--- %s ---\n```%s\n%s\n```"):format(header, ref.language or "", ref.body)
end

local function note_text(ref)
  local lines = 1
  for _ in ref.body:gmatch("\n") do
    lines = lines + 1
  end
  return ("%s: %d lines"):format(ref.label, lines)
end

-- The context commands, for the help text and the line parser.
M.commands = {
  buffer = "the focused file",
  selection = "the selection",
  diagnostics = "the buffer's diagnostics",
  file = "a file: /file <path>",
}

-- Expands every reference in `text`. Returns the prompt to send and a list of
-- one-line notes (what was attached, and how big).
function M.expand(text)
  local blocks, notes = {}, {}
  local kept = {}
  for _, line in ipairs(split_lines(text)) do
    local name, arg = line:match("^/([%w_%-]+)%s*(.-)%s*$")
    if name and M.commands[name] then
      local ref, message = M.reference(name, arg)
      if ref then
        blocks[#blocks + 1] = block_text(ref)
        notes[#notes + 1] = note_text(ref)
      else
        notes[#notes + 1] = message or ("nothing to attach for /" .. name)
      end
    else
      kept[#kept + 1] = line
    end
  end
  -- `:` is in the class for the `#file:<path>` spelling; without it the token
  -- stops at "file" and the reference resolves to no path at all.
  local expanded = (table.concat(kept, "\n")):gsub("#([%w%._%-/:]+)", function(token)
    local name, arg = token, nil
    if token:sub(1, 5) == "file:" then
      name, arg = "file", token:sub(6)
    end
    if not M.commands[name] then
      return "#" .. token -- not a reference this feature knows: as typed
    end
    local ref, message = M.reference(name, arg)
    if not ref then
      notes[#notes + 1] = message or ("cannot attach " .. token)
      return "#" .. token
    end
    blocks[#blocks + 1] = block_text(ref)
    notes[#notes + 1] = note_text(ref)
    return "(" .. ref.label .. ")"
  end)
  expanded = expanded:gsub("%s+$", "")
  if #blocks > 0 then
    local body = table.concat(blocks, "\n\n")
    expanded = expanded == "" and body or (expanded .. "\n\n" .. body)
  end
  return expanded, notes
end

M.cap = cap
M.split_lines = split_lines

return M
