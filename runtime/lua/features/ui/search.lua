-- Search - part of the Lua UI kit.
-- Split out of features/ui.lua so each surface stays small and
-- focused; features/ui.lua is the orchestrator that requires
-- every module and registers the handlers.
local h = require("jot_ui.helpers")
local close = h.close
local cell_len = h.cell_len
local trunc_cells = h.trunc_cells
local pad_cells = h.pad_cells
local present_panel = h.present_panel

-- One labelled input row: "Find" / "Replace" padded to the label column the
-- native caret is placed at, then the field padded to `input_w` cells so a
-- focused field's background fills the whole box instead of stopping at the
-- last typed character.
local function search_field_row(p, label, text, focused, input_w, colors)
  local bg = colors.panel_bg or colors.bg or 0
  local comment = colors.comment or 8
  local fg = colors.fg or 7
  local selection_fg = colors.selection_fg or 0
  local selection_bg = colors.selection_bg or 6
  local line = pad_cells(label, 8)
  local field_start = #line -- byte offset of the input area
  local width = math.max(1, input_w)
  local shown = pad_cells(trunc_cells(text or "", width), width)
  line = line .. shown
  local spans = {
    { start = 0, len = 65535, fg = fg, bg = bg },
  }
  if focused then
    spans[#spans + 1] = { start = field_start, len = #shown, fg = selection_fg, bg = selection_bg }
  end
  return { text = line, spans = spans }
end

-- The toggle buttons and the match count, right-aligned on the find row. The
-- live toggles take the accent so the row reads as three buttons, not a legend.
local function search_chips(p, colors)
  local bg = colors.panel_bg or colors.bg or 0
  local comment = colors.comment or 8
  local accent = colors.accent or colors.fg or 7
  local segs = {
    { "Aa", p.case_sensitive and accent or comment },
    { "W", p.whole_word and accent or comment },
    { ".*", p.regex and accent or comment },
  }
  if p.scoped_to_selection then
    segs[#segs + 1] = { "Sel", accent }
  end
  local text = ""
  local spans = {}
  for i, seg in ipairs(segs) do
    if i > 1 then
      text = text .. " "
    end
    local start = #text
    text = text .. seg[1]
    spans[#spans + 1] = { start = start, len = #seg[1], fg = seg[2], bg = bg }
  end
  local count = p.count or "0/0"
  text = text .. "  " .. count
  spans[#spans + 1] = { start = #text - #count, len = #count, fg = comment, bg = bg }
  return text, spans
end

local function search_panel(p)
  if not p then
    close("search_panel")
    return true
  end
  local colors = p.colors or {}
  local bg = colors.panel_bg or colors.bg or 0
  local accent = colors.accent or colors.fg or 7

  local inner_w = math.max(1, (p.w or 2) - 2)
  local chips, chip_spans = search_chips(p, colors)
  local cluster_w = cell_len(chips)
  local input_w = math.max(1, inner_w - 8 - cluster_w - 1)

  local rows = {}
  local focus_replace = p.replace_visible and p.focus_replace
  local find_row = search_field_row(p, "Find", p.query, not focus_replace, input_w, colors)
  -- Right-align the cluster after the field: pad the row up to where it starts,
  -- then append it and carry its spans along by the prefix's byte length.
  local prefix = pad_cells(find_row.text, math.max(0, inner_w - cluster_w))
  local base = #prefix
  find_row.text = prefix .. chips
  for _, sp in ipairs(chip_spans) do
    sp.start = sp.start + base
    find_row.spans[#find_row.spans + 1] = sp
  end
  rows[#rows + 1] = find_row
  if p.replace_visible then
    rows[#rows + 1] = search_field_row(p, "Replace", p.replace_text, focus_replace, input_w, colors)
  end
  return present_panel("search_panel",
                       p,
                       rows,
                       {
                         border = "single",
                         title = (p.scoped_to_selection and " Find in Selection" or " Find"),
                         title_fg = accent,
                       })
end


return {
  search_panel = search_panel,
  search_field_row = search_field_row,
}
