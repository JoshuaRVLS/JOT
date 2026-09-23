-- The chat: a transcript in a scratch buffer, one provider turn at a time.
--
-- The interface is a real buffer, so tabs, the mouse, scrolling, undo and every
-- editor key work on it like anywhere else. Its shape:
--
--   # Chat · openai · gpt-4o-mini · ready
--
--   ## user
--   > why does the fold index rescan on every click
--
--   ## assistant
--   The index is validated by ...
--
--   ## user
--   >
--
-- You type after the `>` marker under the last `## user`, and sending takes
-- everything from that marker to the end of the buffer, so a prompt can be
-- several lines. The transcript (`session.messages`) is what goes to the
-- provider; the buffer is its rendering, rebuilt at the start of every turn -
-- which is also what repairs a transcript the user edited by hand.
--
-- A reply is appended as it arrives, but only while the chat is focused and
-- the caret sits on the last line. Appending under someone reading the middle
-- of the transcript would drag their cursor to the bottom every tick; when
-- they are elsewhere the text waits and lands the moment they return.
local M = {}

local config = require("jot_ai.config")
local context = require("jot_ai.context")
local http = require("jot_ai.http")

local PROMPT_MARKER = "> "
local USER_HEADER = "## user"
local ASSISTANT_HEADER = "## assistant"
-- The name is what the tab shows and what picks the markdown ruleset; the
-- engine knows it as a rendered view, not a file (is_rendered_view_path).
local BUFFER_NAME = "[Chat].md"
local BUFFER_VAR = "cc_chat"
local FLUSH_MS = 250
-- Turns kept before the oldest pair is dropped, so a long session does not
-- grow the request without bound.
local MAX_MESSAGES = 60

local session = {
  buffer = 0,
  adapter = nil,
  model = nil,
  messages = {},
  handle = nil,
  answer = "",
  pending = "",
  status = "ready",
  timer = nil,
  dirty = false,
}

local function notify(message)
  jot.ui.show_message("[ai] " .. message)
end

local function split_lines(text)
  local out = {}
  for line in (text .. "\n"):gmatch("(.-)\n") do
    out[#out + 1] = line
  end
  if #out == 0 then
    out[1] = ""
  end
  -- A trailing newline ends the last line instead of adding a blank one, which
  -- a streamed answer ends with as often as not.
  if #out > 1 and out[#out] == "" then
    table.remove(out)
  end
  return out
end

local function adapter()
  return config.resolve({ adapter = session.adapter, model = session.model })
end

-- ---------------------------------------------------------------------------
-- The buffer
-- ---------------------------------------------------------------------------

local function find_buffer()
  for _, row in ipairs(jot.buffer.list() or {}) do
    if jot.buffer.get_var(BUFFER_VAR, row.index) == "1" then
      return row.index
    end
  end
  return 0
end

local function focused()
  return session.buffer ~= 0 and jot.buffer.current() == session.buffer
end

local function alive()
  if session.buffer == 0 then
    return false
  end
  session.buffer = find_buffer()
  return session.buffer ~= 0
end

local function header_line(status)
  local parts = { "# Chat", adapter().name, adapter().model }
  if status and status ~= "" then
    parts[#parts + 1] = status
  end
  return table.concat(parts, " · ")
end

-- The buffer text for a transcript: the header, the turns, and then either a
-- prompt region to type the next turn in, or - while a turn is running
-- (`prompt_text == false`) - a fresh assistant section for the reply to land
-- in, so the appends below have somewhere to go.
function M.render_lines(messages, status, prompt_text)
  local out = { header_line(status) }
  for _, message in ipairs(messages) do
    out[#out + 1] = ""
    out[#out + 1] = message.role == "user" and USER_HEADER or ASSISTANT_HEADER
    local lines = split_lines(message.display or message.content or "")
    if message.role == "user" then
      out[#out + 1] = PROMPT_MARKER .. (lines[1] or "")
      for i = 2, #lines do
        out[#out + 1] = lines[i]
      end
    else
      for _, line in ipairs(lines) do
        out[#out + 1] = line
      end
    end
  end
  out[#out + 1] = ""
  if prompt_text == false then
    out[#out + 1] = ASSISTANT_HEADER
    out[#out + 1] = ""
    return out
  end
  out[#out + 1] = USER_HEADER
  if prompt_text and prompt_text ~= "" then
    local lines = split_lines(prompt_text)
    out[#out + 1] = PROMPT_MARKER .. (lines[1] or "")
    for i = 2, #lines do
      out[#out + 1] = lines[i]
    end
  else
    out[#out + 1] = PROMPT_MARKER
  end
  return out
end

-- The typed prompt: everything from the first `>` line under the last
-- `## user` header to the end of the buffer.
function M.prompt_from_lines(lines)
  local header = 0
  for i = #lines, 1, -1 do
    if lines[i]:match("^##%s+user%s*$") then
      header = i
      break
    end
  end
  local start = header + 1
  if header == 0 then
    -- The headers were edited away: take the whole buffer as the prompt rather
    -- than silently sending nothing.
    start = lines[1] and lines[1]:sub(1, 1) == "#" and 2 or 1
  end
  local out = {}
  local started = false
  for i = start, #lines do
    local line = lines[i]
    if not started then
      if line:sub(1, 1) == ">" then
        started = true
        out[#out + 1] = line:sub(line:sub(2, 2) == " " and 3 or 2)
      end
    else
      out[#out + 1] = line
    end
  end
  return (table.concat(out, "\n"):gsub("%s+$", ""))
end

-- Replaces the buffer with a fresh rendering and parks the caret at the end.
-- Only called while the chat is focused: set_text acts on the current buffer.
local function render(prompt_text)
  if not focused() then
    session.dirty = true
    return
  end
  local lines = M.render_lines(session.messages, session.status, prompt_text)
  jot.buffer.set_text(table.concat(lines, "\n"))
  local last = lines[#lines] or ""
  jot.cursor.set(#lines, #last + 1)
  session.dirty = false
end

-- Appends what has streamed since the last tick, without disturbing a reader
-- who is elsewhere in the transcript.
local function append(text)
  if not alive() or not focused() then
    return false
  end
  local lines = jot.buffer.lines(session.buffer) or {}
  local last = lines[#lines] or ""
  if select(1, jot.cursor.get()) ~= #lines then
    return false
  end
  jot.buffer.apply_edit(#lines, #last + 1, #lines, #last + 1, text)
  return true
end

-- ---------------------------------------------------------------------------
-- The turn
-- ---------------------------------------------------------------------------

local function stop_timer()
  if session.timer then
    jot.timer.clear(session.timer)
    session.timer = nil
  end
end

-- Keeps the buffer in step with the stream; the only thing that touches the
-- buffer while a turn runs.
local function tick()
  if not session.handle and session.pending == "" and not session.dirty then
    stop_timer()
    return
  end
  if not alive() or not focused() then
    return
  end
  if session.dirty then
    render()
    return
  end
  if session.pending ~= "" and append(session.pending) then
    session.pending = ""
  end
end

local function start_timer()
  if not session.timer then
    session.timer = jot.timer.set_interval(FLUSH_MS, tick)
  end
end

local function trim_history()
  while #session.messages > MAX_MESSAGES do
    table.remove(session.messages, 1)
  end
  if session.messages[1] and session.messages[1].role ~= "user" then
    -- Both APIs require the first turn to be the user's.
    table.remove(session.messages, 1)
  end
end

-- Closes a turn: the answer joins the transcript, and the buffer ends with a
-- prompt region again. An empty answer puts the question back in that region,
-- so retrying an error is another send rather than a retyped paragraph.
local function finish_turn(error_message, status)
  session.handle = nil
  stop_timer()
  local answer = session.answer
  session.answer = ""
  local typed = nil
  if answer == "" then
    local last = session.messages[#session.messages]
    if last and last.role == "user" then
      typed = last.display
      table.remove(session.messages)
    end
  else
    session.messages[#session.messages + 1] = { role = "assistant", content = answer }
  end
  session.pending = ""
  session.status = status or (error_message and ("failed: " .. error_message) or "ready")
  if typed then
    render(typed)
  else
    session.dirty = true
    start_timer()
    tick()
  end
  if error_message then
    notify("request failed: " .. error_message)
  end
end

local function on_event(event)
  if event.delta then
    session.answer = session.answer .. event.delta
    session.pending = session.pending .. event.delta
    tick()
    return
  end
  if event.done then
    finish_turn(nil)
    return
  end
  if event.error then
    if event.error == "aborted" then
      finish_turn(nil, "stopped")
    else
      finish_turn(event.error)
    end
  end
end

local function begin_turn(expanded, typed)
  local resolved = adapter()
  local blocked = config.blocked(resolved)
  if blocked then
    session.status = "not configured"
    render(typed)
    notify(blocked)
    return false
  end
  session.messages[#session.messages + 1] =
    { role = "user", content = expanded, display = typed }
  trim_history()
  session.answer = ""
  session.pending = ""
  session.status = "answering…"
  render(false)
  local handle, message =
    http.request(resolved, session.messages, jot.workspace.path() or "", on_event)
  if not handle then
    finish_turn(message or "the request could not start")
    return false
  end
  session.handle = handle
  start_timer()
  return true
end

-- ---------------------------------------------------------------------------
-- Commands
-- ---------------------------------------------------------------------------

local function help_text()
  local lines = {
    "Chat commands (as the prompt, then send):",
    "",
    "  /help              this list",
    "  /status            the adapter, model and endpoint in use",
    "  /new  /clear       start a fresh chat",
    "  /stop              stop the answer being written",
    "  /model <name>      use another model for this chat",
    "  /adapter <name>    switch provider (persisted in ai_adapter)",
    "",
    "Context, attached to the prompt:",
    "",
    "  #buffer  #selection  #diagnostics  #file:<path>",
    "  /buffer  /selection  /diagnostics  /file <path>",
    "",
    "Adapters: " .. table.concat(config.names(), ", "),
  }
  return table.concat(lines, "\n")
end

M.commands = {}

M.commands["help"] = function()
  jot.ui.popup(help_text(), " AI chat ")
end

M.commands["status"] = function()
  local blocked = config.blocked(adapter())
  notify(config.describe(adapter()) .. (blocked and ("  (" .. blocked .. ")") or ""))
end

local function new_chat()
  M.reset()
  notify("new chat")
end

M.commands["new"] = new_chat
M.commands["clear"] = new_chat

M.commands["stop"] = function()
  M.stop()
end

M.commands["model"] = function(arg)
  if arg == nil or arg == "" then
    notify("model: " .. adapter().model)
    return
  end
  session.model = arg
  render()
  notify("model: " .. arg)
end

M.commands["adapter"] = function(arg)
  if arg == nil or arg == "" then
    notify("adapter: " .. adapter().name .. "  (" .. table.concat(config.names(), ", ") .. ")")
    return
  end
  if not M.set_adapter(arg) then
    notify("unknown adapter: " .. arg)
    return
  end
  notify("adapter: " .. arg .. "  " .. config.describe(adapter()))
end

-- Switches provider for this chat and persists the choice, which is also how
-- the actions picker and the settings row do it. False for an unknown name.
function M.set_adapter(name)
  if not config.entry(name) then
    return false
  end
  session.adapter = name
  session.model = nil -- the new entry brings its own model
  jot.config.set("ai_adapter", name)
  render()
  return true
end

-- One turn, read from the buffer as it stands (or from `prompt` for a keymap).
function M.send(prompt)
  if session.handle then
    notify("still answering (Alt+Shift+A X stops it)")
    return false
  end
  if not M.open() then
    return false
  end
  local typed = prompt
  if typed == nil then
    typed = M.prompt_from_lines(jot.buffer.lines(session.buffer) or {})
  end
  if typed == "" then
    notify("nothing to send")
    return false
  end
  local name, arg = typed:match("^/([%w_%-]+)%s*(.-)%s*$")
  if name and M.commands[name] then
    M.commands[name](arg)
    return true
  end
  local expanded, notes = context.expand(typed)
  if expanded == "" then
    notify(notes[1] or "nothing to send")
    return false
  end
  if #notes > 0 then
    notify(table.concat(notes, "; "))
  end
  return begin_turn(expanded, typed)
end

function M.stop()
  if not session.handle then
    notify("not answering")
    return false
  end
  session.handle.stop()
  return true
end

-- Starts over: the same buffer, an empty transcript.
function M.reset()
  if session.handle then
    session.handle.stop()
    session.handle = nil
  end
  stop_timer()
  session.messages = {}
  session.answer = ""
  session.pending = ""
  session.status = "ready"
  render()
end

-- Opens the chat (creating the buffer the first time) and focuses it.
function M.open()
  local index = find_buffer()
  if index == 0 then
    jot.file.new(BUFFER_NAME)
    index = jot.buffer.current()
    if not index or index == 0 then
      notify("could not open a chat buffer")
      return false
    end
    session.buffer = index
    jot.buffer.set_var(BUFFER_VAR, "1", index)
    session.dirty = true
  end
  session.buffer = index
  jot.buffer.switch(index)
  if session.dirty then
    render()
  end
  jot.editor.request_redraw()
  return true
end

-- The chat when it is focused; otherwise this opens it.
function M.toggle()
  if focused() then
    jot.file.close()
    return false
  end
  return M.open()
end

function M.is_open()
  return session.buffer ~= 0 and find_buffer() ~= 0
end

function M.is_focused()
  return focused()
end

function M.status_line()
  return config.describe(adapter())
end

function M.messages()
  return session.messages
end

function M.session()
  return session
end

M.render_lines = M.render_lines
M.split_lines = split_lines

return M
