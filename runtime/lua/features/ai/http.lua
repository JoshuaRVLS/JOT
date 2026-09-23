-- The assistant's transport: one HTTP request (or one CLI) per turn, streamed
-- back into the chat as it arrives.
--
-- Everything here is scratch files and a shell job, because that is what the
-- Lua host has: jot.job.capture runs a command on a worker thread and hands
-- back its whole output when it finishes, and there is no Lua socket. So the
-- request body goes into a file, curl is told to write the reply into another
-- one as it arrives (--no-buffer), and a timer reads the growing tail - which
-- is what makes a reply appear token by token instead of all at once.
--
-- The reply format is per adapter kind:
--   "openai"    SSE `data: {...}` frames, the text in choices[1].delta.content
--   "anthropic" SSE frames with named events, the text in delta.text
--   "cmd"       whatever the CLI prints, taken as-is
local M = {}

local json = require("jot_ai.json")

local WINDOWS = package.config:sub(1, 1) == "\\"
local counter = 0

local function temp_dir()
  for _, name in ipairs({ "TMPDIR", "TEMP", "TMP" }) do
    local dir = os.getenv(name)
    if dir and dir ~= "" then
      return dir
    end
  end
  return "/tmp"
end

-- Quotes a path for the shell jot.job.capture runs the command through. POSIX
-- shells take single quotes; cmd.exe does not know them.
local function shell_quote(value)
  if WINDOWS then
    return '"' .. value:gsub('"', '""') .. '"'
  end
  return "'" .. value:gsub("'", "'\\''") .. "'"
end

local function scratch_path()
  counter = counter + 1
  return ("%s/jot-ai-%d-%d"):format(temp_dir(), os.time(), counter)
end

local function write_file(path, text)
  local handle = io.open(path, "wb")
  if not handle then
    return false
  end
  handle:write(text)
  handle:close()
  return true
end

local function remove_files(...)
  for _, path in ipairs({ ... }) do
    os.remove(path)
  end
end

-- The bytes appended to `path` since `offset`, plus the new offset.
local function read_appended(path, offset)
  local handle = io.open(path, "rb")
  if not handle then
    return "", offset
  end
  handle:seek("set", offset)
  local data = handle:read("a") or ""
  handle:close()
  return data, offset + #data
end

-- The messages array both HTTP kinds take: role/content pairs, system prompt
-- left out (Anthropic carries it beside the array, and OpenAI accepts it as a
-- leading message, so each builder adds its own).
local function wire_messages(messages)
  local out = {}
  for _, message in ipairs(messages) do
    if message.content ~= nil and message.content ~= "" then
      out[#out + 1] = { role = message.role, content = message.content }
    end
  end
  return out
end

-- The request body for a kind, or nil when the kind takes none (a CLI gets the
-- whole turn on stdin instead).
function M.payload(adapter, messages)
  if adapter.kind == "anthropic" then
    local body = {
      model = adapter.model,
      messages = wire_messages(messages),
      max_tokens = adapter.max_tokens > 0 and adapter.max_tokens or 4096,
      temperature = adapter.temperature,
      system = adapter.system,
      stream = adapter.stream and true or false,
    }
    if body.system == "" then
      body.system = nil
    end
    return body
  end
  if adapter.kind == "openai" then
    local wire = wire_messages(messages)
    if adapter.system ~= "" then
      table.insert(wire, 1, { role = "system", content = adapter.system })
    end
    local body = {
      model = adapter.model,
      messages = wire,
      temperature = adapter.temperature,
      stream = adapter.stream and true or false,
    }
    if adapter.max_tokens > 0 then
      body.max_tokens = adapter.max_tokens
    end
    return body
  end
  return nil
end

-- The whole conversation as one prompt, for the adapters that take text.
function M.prompt_text(adapter, messages)
  local parts = {}
  if adapter.system ~= "" then
    parts[#parts + 1] = adapter.system
  end
  for _, message in ipairs(messages) do
    if message.content and message.content ~= "" then
      parts[#parts + 1] = (message.role == "assistant" and "Assistant:\n" or "User:\n")
          .. message.content
    end
  end
  return table.concat(parts, "\n\n") .. "\n"
end

-- The URL one kind posts to. The bases above are endpoint roots, so the path
-- is appended (`.../v1` + `/chat/completions`), and a user who pasted a full
-- URL keeps whatever path they wrote.
function M.url(adapter)
  local base = adapter.base:gsub("/+$", "")
  if adapter.kind == "anthropic" then
    return base .. "/messages"
  end
  if base:find("/chat/completions", 1, true) then
    return base
  end
  return base .. "/chat/completions"
end

-- Pulls the answer text out of one decoded frame. `finish` is true for the
-- last envelope of a non-streamed body (the whole message in one object).
local function text_of(kind, payload)
  if kind == "openai" then
    local choice = payload.choices and payload.choices[1]
    if not choice then
      return nil
    end
    if type(choice.delta) == "table" then
      return type(choice.delta.content) == "string" and choice.delta.content or nil
    end
    if type(choice.message) == "table" then
      return type(choice.message.content) == "string" and choice.message.content or nil
    end
    return nil
  end
  -- anthropic
  if type(payload.delta) == "table" and type(payload.delta.text) == "string" then
    return payload.delta.text
  end
  if type(payload.content) == "table" then
    local text = {}
    for _, block in ipairs(payload.content) do
      if block.type == "text" and type(block.text) == "string" then
        text[#text + 1] = block.text
      end
    end
    return #text > 0 and table.concat(text) or nil
  end
  return nil
end

-- A provider error, as the message a human should read.
local function error_of(payload)
  if type(payload) ~= "table" then
    return nil
  end
  local err = payload.error
  if type(err) == "table" then
    return err.message or err.type or "request failed"
  end
  if type(err) == "string" then
    return err
  end
  return nil
end

-- Starts one turn.
--
--   adapter   a resolved adapter (features/ai/config.lua)
--   messages  { {role="user"|"assistant", content=...}, ... }
--   cwd       the directory the CLI runs in (a provider sees only the request)
--   on_event  {delta = text} | {done = true} | {error = message}
--
-- Returns a handle whose `stop()` stops delivering (the request itself runs to
-- the end in the background; jot.job.capture cannot be asked to kill it).
function M.request(adapter, messages, cwd, on_event)
  local stem = scratch_path()
  local out_path = stem .. ".out"
  local err_path = stem .. ".err"
  local body_path = stem .. ".req"
  local aborted = false
  local finished = false
  local handle = {}

  local function finish(error_message)
    if finished then
      return
    end
    finished = true
    if handle.timer then
      jot.timer.clear(handle.timer)
      handle.timer = nil
    end
    remove_files(out_path, err_path, body_path)
    if aborted then
      on_event({ error = "aborted" })
    elseif error_message then
      on_event({ error = error_message })
    else
      on_event({ done = true })
    end
  end

  local command
  if adapter.kind == "cmd" then
    if not write_file(body_path, M.prompt_text(adapter, messages)) then
      return nil, "cannot write a scratch file in " .. temp_dir()
    end
    command = ("%s < %s > %s 2> %s"):format(
      adapter.command, shell_quote(body_path), shell_quote(out_path), shell_quote(err_path))
  else
    local body = M.payload(adapter, messages)
    if not write_file(body_path, json.encode(body)) then
      return nil, "cannot write a scratch file in " .. temp_dir()
    end
    local headers = {
      "-H " .. shell_quote("Content-Type: application/json"),
    }
    if adapter.kind == "anthropic" then
      if adapter.key ~= "" then
        headers[#headers + 1] = "-H " .. shell_quote("x-api-key: " .. adapter.key)
      end
      headers[#headers + 1] = "-H " .. shell_quote("anthropic-version: 2023-06-01")
    elseif adapter.key ~= "" then
      headers[#headers + 1] = "-H " .. shell_quote("Authorization: Bearer " .. adapter.key)
    end
    command = ("curl -sS --no-buffer -o %s -w '%%{http_code}' -X POST %s %s --data-binary @%s"):format(
      shell_quote(out_path), shell_quote(M.url(adapter)), table.concat(headers, " "),
      shell_quote(body_path))
  end

  local pending = ""
  local data_lines = {}
  local delivered = false
  local offset = 0

  local function deliver(payload)
    local text = text_of(adapter.kind, payload)
    if text and text ~= "" then
      delivered = true
      on_event({ delta = text })
    end
  end

  -- One SSE event: the `data:` lines accumulated since the last blank line.
  local function flush_event()
    if #data_lines > 0 then
      local payload = json.decode(table.concat(data_lines, "\n"))
      data_lines = {}
      if payload then
        local message = error_of(payload)
        if message then
          finish(message)
          return true
        end
        deliver(payload)
      end
    end
    return false
  end

  local function consume_sse(chunk)
    pending = pending .. chunk
    while true do
      local newline = pending:find("\n")
      if not newline then
        return false
      end
      local line = pending:sub(1, newline - 1)
      pending = pending:sub(newline + 1)
      line = line:gsub("\r$", "")
      if line == "" then
        if flush_event() then
          return true
        end
      elseif line:sub(1, 5) == "data:" then
        local payload = line:sub(6):gsub("^ ", "")
        if payload == "[DONE]" then
          return true
        end
        data_lines[#data_lines + 1] = payload
      end
      -- event:/id:/retry: lines and comments carry nothing this needs.
    end
  end

  local function pump()
    if aborted or finished then
      return
    end
    local chunk
    chunk, offset = read_appended(out_path, offset)
    if chunk ~= "" then
      if adapter.kind == "cmd" then
        if not aborted then
          delivered = true
          on_event({ delta = chunk })
        end
      elseif consume_sse(chunk) then
        -- A terminating frame arrived; the job callback still owns the end.
      end
    end
  end

  local function on_job(result)
    if finished then
      return
    end
    if aborted then
      finish()
      return
    end
    -- Whatever curl wrote after the last pump, then the status code it
    -- printed through -w (the last run of digits in the output).
    pump()
    if adapter.kind ~= "cmd" then
      consume_sse("\n\n")
      local status = tonumber(result.output:match("(%d%d%d)%s*$") or "")
      if status and status >= 400 then
        local body = read_appended(out_path, 0)
        local payload = json.decode(body)
        finish(error_of(payload)
               or ("the provider answered " .. status))
        return
      end
      if not status and not result.ok then
        local message = (result.output:gsub("%s+$", ""))
        finish(#message > 0 and message or "the request failed")
        return
      end
      if not delivered and adapter.kind ~= "cmd" then
        -- A provider that ignored `stream`, or a body that arrived in one
        -- piece: the whole answer is sitting in the file.
        local body = read_appended(out_path, 0)
        local payload = json.decode(body)
        if payload then
          local message = error_of(payload)
          if message then
            finish(message)
            return
          end
          deliver(payload)
        end
      end
    elseif not result.ok and not delivered then
      local err = read_appended(err_path, 0)
      local message = (err:gsub("%s+$", ""))
      finish(#message > 0 and message or "the command failed")
      return
    end
    finish()
  end

  if not jot.job.capture(command, cwd or "", on_job) then
    remove_files(out_path, err_path, body_path)
    return nil, "the job queue is unavailable"
  end
  handle.timer = jot.timer.set_interval(110, pump)
  handle.stop = function()
    aborted = true
    finish()
  end
  return handle
end

-- Exposed for tests: the pieces that decide bytes rather than move them.
M.wire_messages = wire_messages
M.text_of = text_of
M.error_of = error_of
M.shell_quote = shell_quote
M.temp_dir = temp_dir

return M
