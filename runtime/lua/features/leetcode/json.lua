local M = {}
M.null = setmetatable({}, {__tostring=function() return "null" end})

local escapes = {['"']='"', ['\\']='\\', ['/']='/', b='\b', f='\f', n='\n', r='\r', t='\t'}

local function quote(value)
  return '"' .. value:gsub('[%z\1-\31"\\]', function(char)
    local map = {['"']='\\"', ['\\']='\\\\', ['\b']='\\b', ['\f']='\\f', ['\n']='\\n', ['\r']='\\r', ['\t']='\\t'}
    return map[char] or ("\\u%04x"):format(char:byte())
  end) .. '"'
end

local function is_array(value)
  if value[1] == nil then return false end
  local count = 0
  for key in pairs(value) do
    if type(key) ~= "number" then return false end
    count = count + 1
  end
  return count == #value
end

local function encode_value(value)
  if value == M.null or value == nil then return "null" end
  local kind = type(value)
  if kind == "boolean" then return value and "true" or "false" end
  if kind == "number" then
    if value ~= value or value == math.huge or value == -math.huge then return "null" end
    return tostring(value)
  end
  if kind == "string" then return quote(value) end
  if kind ~= "table" then return "null" end
  local out = {}
  if is_array(value) then
    for i = 1, #value do out[i] = encode_value(value[i]) end
    return "[" .. table.concat(out, ",") .. "]"
  end
  local keys = {}
  for key in pairs(value) do keys[#keys + 1] = tostring(key) end
  table.sort(keys)
  for _, key in ipairs(keys) do out[#out + 1] = quote(key) .. ":" .. encode_value(value[key]) end
  return "{" .. table.concat(out, ",") .. "}"
end

function M.encode(value)
  return encode_value(value)
end

local function skip_space(text, index)
  local matched = text:sub(index):match("^[ \t\r\n]*")
  return index + #(matched or "")
end

local parse_value

local function parse_string(text, index)
  index = index + 1
  local out = {}
  while index <= #text do
    local char = text:sub(index, index)
    if char == '"' then return table.concat(out), index + 1 end
    if char == "\\" then
      local escape = text:sub(index + 1, index + 1)
      if escape == "u" then
        local hex = text:sub(index + 2, index + 5)
        if #hex ~= 4 or not hex:match("^%x%x%x%x$") then return nil, "invalid unicode escape" end
        local code = tonumber(hex, 16)
        index = index + 6
        if code >= 0xD800 and code <= 0xDBFF then
          local low_hex = text:sub(index + 2, index + 5)
          local low = tonumber(low_hex, 16)
          if text:sub(index, index + 1) ~= "\\u" or not low or low < 0xDC00 or low > 0xDFFF then
            return nil, "invalid unicode surrogate pair"
          end
          code = 0x10000 + (code - 0xD800) * 0x400 + (low - 0xDC00)
          index = index + 6
        elseif code >= 0xDC00 and code <= 0xDFFF then
          return nil, "unexpected low unicode surrogate"
        end
        out[#out + 1] = utf8.char(code)
      else
        local decoded = escapes[escape]
        if not decoded then return nil, "invalid string escape" end
        out[#out + 1] = decoded
        index = index + 2
      end
    elseif char:byte() < 32 then
      return nil, "control character in string"
    else
      out[#out + 1] = char
      index = index + 1
    end
  end
  return nil, "unterminated string"
end

local function parse_number(text, index)
  local tail = text:sub(index)
  local token = tail:match("^%-?%d+%.?%d*[eE]?[%+%-]?%d*")
  if not token or token == "" or token == "-" or not tonumber(token) then return nil, "invalid number" end
  if token:match("^%-?0%d") or token:match("%.e") or token:match("[eE][%+%-]?$" ) then
    return nil, "invalid number"
  end
  return tonumber(token), index + #token
end

parse_value = function(text, index)
  index = skip_space(text, index)
  local char = text:sub(index, index)
  if char == "{" then
    local out = {}
    index = skip_space(text, index + 1)
    if text:sub(index, index) == "}" then return out, index + 1 end
    while true do
      if text:sub(index, index) ~= '"' then return nil, "invalid object key" end
      local key
      key, index = parse_string(text, index)
      if key == nil then return nil, index end
      index = skip_space(text, index)
      if text:sub(index, index) ~= ":" then return nil, "expected colon" end
      local value, next_index = parse_value(text, index + 1)
      if next_index == nil then return nil, value end
      out[key] = value
      index = skip_space(text, next_index)
      local separator = text:sub(index, index)
      if separator == "}" then return out, index + 1 end
      if separator ~= "," then return nil, "expected comma or closing brace" end
      index = skip_space(text, index + 1)
    end
  elseif char == "[" then
    local out = {}
    index = skip_space(text, index + 1)
    if text:sub(index, index) == "]" then return out, index + 1 end
    while true do
      local value, next_index = parse_value(text, index)
      if next_index == nil then return nil, value end
      out[#out + 1] = value
      index = skip_space(text, next_index)
      local separator = text:sub(index, index)
      if separator == "]" then return out, index + 1 end
      if separator ~= "," then return nil, "expected comma or closing bracket" end
      index = skip_space(text, index + 1)
    end
  elseif char == '"' then
    return parse_string(text, index)
  elseif text:sub(index, index + 3) == "true" then
    return true, index + 4
  elseif text:sub(index, index + 4) == "false" then
    return false, index + 5
  elseif text:sub(index, index + 3) == "null" then
    return M.null, index + 4
  elseif char == "-" or char:match("%d") then
    return parse_number(text, index)
  end
  return nil, "invalid JSON value"
end

function M.decode(text)
  if type(text) ~= "string" then return nil, "JSON input must be a string" end
  local ok, value, index = pcall(parse_value, text, 1)
  if not ok then return nil, "invalid JSON" end
  if type(index) ~= "number" then return nil, value or index or "invalid JSON" end
  if skip_space(text, index) <= #text then return nil, "trailing data after JSON" end
  return value
end

return M
