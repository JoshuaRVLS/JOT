-- The winbar: the Lua painter for the breadcrumb rows the panes spend above
-- their text, one cell short on the right like the pane leaves to the
-- scrollbar. Every pane's row arrives in one payload (render/winbar.cpp), and
-- this keeps one float per row -- keyed by the row's pane -- so a split paints
-- every pane. A row this frame does not carry is closed, the way the crumb
-- cascade closes the levels it stepped back out of.
local h = require("jot_ui.helpers")
local close = h.close
local cell_len = h.cell_len
local pad_cells = h.pad_cells
local surfaces = h.surfaces

-- The same kind -> color mapping the outline panel uses, read from the colors
-- table the native payload carries.
local function symbol_color(colors, kind)
  kind = (kind or ""):lower()
  if kind == "function" or kind == "method" or kind == "constructor" or kind == "macro" then
    -- `function` is a Lua keyword, so the colors table is indexed as a string.
    return colors.function_method or colors["function"] or colors.accent
  end
  if kind == "class" or kind == "struct" or kind == "union" or kind == "interface"
      or kind == "enum" or kind == "type" or kind == "typedef" or kind == "type_alias"
      or kind == "enum_member" then
    return colors.type
  end
  if kind == "namespace" or kind == "module" or kind == "package" then
    return colors.namespace or colors.module
  end
  if kind == "variable" or kind == "property" or kind == "field" or kind == "parameter" then
    return colors.variable
  end
  if kind == "constant" then
    return colors.constant_macro or colors.constant
  end
  return colors.accent
end

-- The float name for one pane's row. Stable per pane, so the next frame
-- re-configures that row's own float instead of opening another one.
local function row_name(pane)
  return "winbar:" .. tostring(pane or 0)
end

-- Closes every row float except the ones in `keep` (the panes this frame drew).
local function close_rows(keep)
  local stale = {}
  for name in pairs(surfaces) do
    if name:sub(1, 7) == "winbar:" and not (keep and keep[name]) then
      stale[#stale + 1] = name
    end
  end
  for _, name in ipairs(stale) do
    close(name)
  end
end

local function paint_row(p, row)
  local colors = p.colors or {}
  local row_fg = colors.winbar_fg or colors.default_fg or 7
  local row_bg = colors.winbar_bg or colors.default_bg or 0
  local crumb_fg = colors.winbar_crumb_fg or row_fg
  local sep_fg = colors.winbar_separator or colors.comment or 8
  local hover_fg = colors.winbar_hover_fg or colors.selection_fg or 0
  local hover_bg = colors.winbar_hover_bg or colors.selection_bg or 6
  local sel_fg = colors.selection_fg or 0
  local sel_bg = colors.selection_bg or 6

  -- The row stops before the last column of the pane, the one the text leaves
  -- to the scrollbar.
  local w = math.max(1, (row.w or 1) - 1)
  local text = ""
  local spans = {}
  local function emit(s, fg, bg, bold)
    if s == nil or s == "" then
      return
    end
    local at = #text
    text = text .. s
    spans[#spans + 1] = { start = at, len = #s, fg = fg or row_fg, bg = bg or row_bg, bold = bold }
  end

  -- The crumbs carry absolute screen columns (that is what the hit-test reads);
  -- this float starts at the row's own x, so the coupons are converted here.
  local origin = row.x or 0
  local cursor = 0 -- column already painted, relative to the float
  for i, c in ipairs(row.crumbs or {}) do
    -- Honoring `x` keeps this painter aligned with the hit-test if the layout
    -- ever adds a gap between crumbs.
    local x = (c.x or cursor) - origin
    if x > cursor then
      emit(string.rep(" ", x - cursor), row_fg, row_bg)
      cursor = x
    end
    if c.ellipsis then
      emit(c.label or "\u{2026}", sep_fg, row_bg)
    else
      local fg = crumb_fg
      local bg = row_bg
      if c.current or c.kind == "file" then
        fg = row_fg
      end
      if c.hovered then
        fg, bg = hover_fg, hover_bg
      end
      if c.active then
        fg, bg = sel_fg, sel_bg
      end
      -- The first crumb on the row is its start: no separator before it.
      emit(i > 1 and "\u{203A}" or " ", sep_fg, bg)
      local icon_fg = c.icon_fg
      if c.kind == "folder" then
        icon_fg = colors.sidebar_dir or icon_fg
      elseif c.kind == "symbol" then
        icon_fg = symbol_color(colors, c.symbol_kind)
      end
      if c.icon and c.icon ~= "" then
        emit(c.icon .. " ", icon_fg or fg, bg)
      end
      emit(c.label or "", fg, bg, c.current or c.kind == "file")
    end
    cursor = (c.end_x or c.x or cursor) - origin
  end
  local used = cell_len(text)
  if used < w then
    emit(string.rep(" ", w - used), row_fg, row_bg)
  end
  text = pad_cells(text, w)
  -- The row background first, so anything the spans above did not cover is the
  -- winbar's own band rather than whatever was under it.
  table.insert(spans, 1, { start = 0, len = 65535, fg = row_fg, bg = row_bg })

  local name = row_name(row.pane)
  local s = surfaces[name]
  local buf, win
  if s then
    buf, win = s.buf, s.win
    jot.ui.float.configure(win, {
      col = row.x or 0,
      row = row.y or 0,
      width = w,
      height = 1,
      relative = "editor",
      anchor = "NW",
      border = "none",
      strip = true,
      fg = row_fg,
      bg = row_bg,
    })
  else
    buf = jot.ui.buffer.create(false, true)
    win = jot.ui.float.open(buf, {
      col = row.x or 0,
      row = row.y or 0,
      width = w,
      height = 1,
      relative = "editor",
      anchor = "NW",
      border = "none",
      focusable = false,
      mouse = false,
      hide = false,
      strip = true,
      fg = row_fg,
      bg = row_bg,
    })
    if not win or win == 0 then
      jot.ui.buffer.delete(buf)
      return false
    end
    surfaces[name] = { win = win, buf = buf }
  end
  jot.ui.buffer.set_lines(buf, 0, -1, true, { text })
  jot.ui.float.set_spans(win, 1, spans)
  return true
end

local function winbar(p)
  if not p or not p.rows or #p.rows == 0 then
    close_rows(nil)
    return true
  end
  local keep = {}
  local ok = true
  for _, row in ipairs(p.rows) do
    keep[row_name(row.pane)] = true
    if not paint_row(p, row) then
      ok = false
    end
  end
  close_rows(keep)
  return ok
end

return {
  winbar = winbar,
}
