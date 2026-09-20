-- Menu — part of the Lua UI kit.
-- Split out of features/ui.lua so each surface stays small and
-- focused; features/ui.lua is the orchestrator that requires
-- every module and registers the handlers.
local h = require("jot_ui.helpers")
local close = h.close
local trunc_cells = h.trunc_cells
local pad_cells = h.pad_cells
local present_panel = h.present_panel

-- The most panels a cascade will stack; the native side caps the levels at the
-- same depth (kMaxMenuLevels in render/winbar.cpp), so this only bounds the
-- sweep that closes the panels of levels that are gone.
local MAX_LEVELS = 6
local function menu_rows(p, list, selected)
  -- Renders an item list as a bordered panel: enabled rows are selectable,
  -- disabled rows are dimmed and skip the selection bar.
  local colors = p.colors or {}
  local fg = colors.fg or 7
  local bg = colors.panel_bg or colors.bg or 0
  local selection_fg = colors.selection_fg or 0
  local selection_bg = colors.selection_bg or 6
  local comment = colors.comment or 8
  local inner_w = math.max(1, p.w - 2)
  local rows = {}
  for i, item in ipairs(list) do
    local enabled = item.enabled ~= false
    local sel = enabled and (i - 1) == (selected or 0)
    local label = trunc_cells(item.label or "", math.max(1, inner_w - 4))
    rows[#rows + 1] = {
      text = " " .. label,
      fg = sel and selection_fg or (enabled and fg or comment),
      bg = sel and selection_bg or bg,
    }
  end
  return rows
end

local function context_menu(p)
  if not p then
    close("context_menu")
    return true
  end
  local rows = menu_rows(p, p.items or {}, p.selected)
  return present_panel("context_menu", p, rows, {})
end

-- ---------------------------------------------------------------------------
-- Menu bar dropdown
-- ---------------------------------------------------------------------------

local function menu_dropdown(p)
  if not p then
    close("menu_dropdown")
    return true
  end
  local colors = p.colors or {}
  local accent = colors.accent or colors.fg or 7
  local rows = menu_rows(p, p.items or {}, p.selected)
  local label = (p.menu_label or "") ~= "" and (" " .. p.menu_label .. " ") or nil
  return present_panel("menu_dropdown",
                       p,
                       rows,
                       { border = "single", title = label, title_fg = accent })
end

-- ---------------------------------------------------------------------------
-- Winbar crumb drop-down
-- ---------------------------------------------------------------------------

-- The breadcrumb winbar's menu (render/winbar.cpp): a *cascade* of panels.
-- Level 0 is the crumb's siblings -- a folder's children, a file's folder, the
-- symbols sharing a scope -- with the row the chain is already on marked by a
-- dot, and every level below it is a folder opened from the selected row of
-- the level above, sitting beside it (dropbar's model). Rows carry an icon and
-- the absolute `index` the native selection keys on, so the click hit-test and
-- the highlighted row cannot drift apart; a folder row wears the chevron that
-- says the row opens another panel.
local function winbar_menu(p)
  if not p then
    -- Closed: every level's panel goes, not just level 0.
    for i = 1, MAX_LEVELS do
      close(i == 1 and "winbar_menu" or ("winbar_menu:" .. i))
    end
    return true
  end
  local colors = p.colors or {}
  local fg = colors.fg or 7
  local bg = colors.panel_bg or colors.bg or 0
  local selection_fg = colors.selection_fg or 0
  local selection_bg = colors.selection_bg or 6
  local accent = colors.winbar_crumb_fg or colors.accent or 6

  local levels = p.levels or {}
  for i, level in ipairs(levels) do
    local inner_w = math.max(1, (level.w or 1) - 2)
    local rows = {}
    for _, entry in ipairs(level.entries or {}) do
      local selected = (entry.index or 0) == (level.selected or 0)
      local text = " "
      if entry.current then
        text = text .. "\u{F111} "
      end
      if (entry.icon or "") ~= "" then
        text = text .. entry.icon .. " "
      end
      text = text .. (entry.label or "")
      if entry.is_dir then
        -- The chevron sits on the right edge; pad the row to the cell before it
        -- so the glyph lands where the native painter puts it.
        text = pad_cells(trunc_cells(text, math.max(1, inner_w - 3)), math.max(0, inner_w - 1))
                      .. "\u{F054}"
      else
        text = trunc_cells(text, math.max(1, inner_w - 2))
      end
      rows[#rows + 1] = {
        text = text,
        fg = selected and selection_fg or fg,
        bg = selected and selection_bg or bg,
      }
    end
    local title = (level.title or "") ~= "" and (" " .. level.title .. " ") or nil
    local name = i == 1 and "winbar_menu" or ("winbar_menu:" .. i)
    present_panel(name,
                  { x = level.x,
                    y = level.y,
                    w = level.w,
                    h = level.h,
                    colors = p.colors },
                  rows,
                  { border = "single", title = title, title_fg = accent })
  end

  -- A level that closed (the cascade shrank, or the whole menu went away) must
  -- take its float with it: a panel left behind has no state under it, so it
  -- can neither be clicked nor dismissed.
  for i = #levels + 1, MAX_LEVELS do
    close(i == 1 and "winbar_menu" or ("winbar_menu:" .. i))
  end
  return true
end


return {
  menu_rows = menu_rows,
  context_menu = context_menu,
  menu_dropdown = menu_dropdown,
  winbar_menu = winbar_menu,
}
