// Settings menu model + input (cell-based; see render/settings.cpp for the
// paint pass). The menu enumerates config.keys(), so every setting -- the
// built-in defaults, settings.conf overrides and Lua-registered keys from
// jot.config.set -- appears with its current value. Each row is edited the
// way its type asks to be: booleans toggle, integers step (and take a typed
// value), enumerated settings cycle or open their choices, strings open an
// inline input row (type the new value, Enter applies, Esc cancels). A
// search bar above the list filters rows by label or key. Changes flow
// through apply_settings_value -> config.set + apply_config_live + save, the
// same live-apply pipeline Lua uses.
#include "editor.h"
#include "ui/gui/gui.h"

#include <algorithm>
#include <cctype>
#include <climits>
#include <cstdlib>
#include <string>
#include <vector>

namespace
{
// Friendly labels + types for the built-in settings. Keys absent from this
// table (Lua-registered, plugin-owned) fall back to a string entry using
// the raw key as the label, so the menu stays complete and Lua-extensible.
struct KnownSetting
{
  const char *key;
  const char *label;
  SettingsEntry::Type type;
};

const KnownSetting kKnownSettings[] = {
    {"auto_detect_indent", "Auto-detect indent", SettingsEntry::Type::Bool},
    {"auto_indent", "Auto indent", SettingsEntry::Type::Bool},
    {"auto_save", "Auto save", SettingsEntry::Type::Bool},
    {"auto_save_interval_ms", "Auto save interval (ms)", SettingsEntry::Type::Int},
    {"clang_format_on_save", "Clang-format on save", SettingsEntry::Type::Bool},
    {"color_scheme", "Color scheme", SettingsEntry::Type::String},
    {"colorizer", "Color preview", SettingsEntry::Type::Bool},
    {"colorizer_mode", "Color preview mode", SettingsEntry::Type::String},
    {"emmet", "Emmet abbreviations", SettingsEntry::Type::Bool},
    {"colorizer_hex", "Color preview: hex", SettingsEntry::Type::Bool},
    {"colorizer_hex_alpha", "Color preview: 8-digit hex", SettingsEntry::Type::Bool},
    {"colorizer_hex_qml", "Color preview: #AARRGGBB", SettingsEntry::Type::Bool},
    {"colorizer_hex_no_hash", "Color preview: hex without #", SettingsEntry::Type::Bool},
    {"colorizer_hex_0x", "Color preview: 0x hex", SettingsEntry::Type::Bool},
    {"colorizer_names", "Color preview: named", SettingsEntry::Type::Bool},
    {"colorizer_tailwind", "Color preview: Tailwind", SettingsEntry::Type::Bool},
    {"colorizer_xcolor", "Color preview: xcolor", SettingsEntry::Type::Bool},
    {"colorizer_functions", "Color preview: CSS functions", SettingsEntry::Type::Bool},
    {"colorizer_xterm", "Color preview: terminal codes", SettingsEntry::Type::Bool},
    {"colorizer_ls_colors", "Color preview: LS_COLORS", SettingsEntry::Type::Bool},
    {"colorizer_css_vars", "Color preview: CSS variables", SettingsEntry::Type::Bool},
    {"colorizer_sass", "Color preview: Sass variables", SettingsEntry::Type::Bool},
    {"colorizer_only_in_strings", "Color preview: strings only", SettingsEntry::Type::Bool},
    {"colorizer_exclude_filetypes", "Color preview: skip extensions", SettingsEntry::Type::String},
    {"truecolor", "24-bit color (auto/on/off)", SettingsEntry::Type::String},
    {"completion_rich_labels", "Completion: rich labels", SettingsEntry::Type::Bool},
    {"completion_align_type", "Completion: align type", SettingsEntry::Type::Bool},
    {"completion_dim_arguments", "Completion: dim arguments", SettingsEntry::Type::Bool},
    {"cursor_blink_ms", "Cursor blink (ms)", SettingsEntry::Type::Int},
    {"cursor_style", "Cursor style", SettingsEntry::Type::String},
    {"debugger_height", "Debugger panel height", SettingsEntry::Type::Int},
    {"decorations_inline_diagnostics", "Inline diagnostics", SettingsEntry::Type::Bool},
    {"diagnostics_virtual_text", "Diagnostic messages inline", SettingsEntry::Type::Bool},
    {"cpp_dim_inactive", "Dim inactive #ifdef branches", SettingsEntry::Type::Bool},
    {"cpp_definitions", "C++ definition checks", SettingsEntry::Type::Bool},
    {"discord_rpc", "Discord presence", SettingsEntry::Type::Bool},
    {"discord_app_id", "Discord app id", SettingsEntry::Type::String},
    {"discord_details_editing", "Discord details (editing)", SettingsEntry::Type::String},
    {"discord_details_idling", "Discord details (idling)", SettingsEntry::Type::String},
    {"discord_details_debugging", "Discord details (debugging)", SettingsEntry::Type::String},
    {"discord_lower_details_editing", "Discord state (editing)", SettingsEntry::Type::String},
    {"discord_lower_details_idling", "Discord state (idling)", SettingsEntry::Type::String},
    {"discord_large_image", "Discord large image text", SettingsEntry::Type::String},
    {"discord_large_image_idling",
     "Discord large image text (idling)",
     SettingsEntry::Type::String},
    {"discord_small_image", "Discord small image text", SettingsEntry::Type::String},
    {"discord_idle_timeout", "Discord idle timeout (s)", SettingsEntry::Type::Int},
    {"discord_swap_images", "Discord swap images", SettingsEntry::Type::Bool},
    {"discord_remove_details", "Discord hide details", SettingsEntry::Type::Bool},
    {"discord_remove_lower_details", "Discord hide state", SettingsEntry::Type::Bool},
    {"discord_remove_timestamp", "Discord hide elapsed time", SettingsEntry::Type::Bool},
    {"discord_remove_repository_button", "Discord hide repo button", SettingsEntry::Type::Bool},
    {"discord_show_status", "Discord status chip", SettingsEntry::Type::Bool},
    {"explorer_width", "Explorer width", SettingsEntry::Type::Int},
    {"gui_font_family", "GUI font family", SettingsEntry::Type::String},
    {"gui_font_size", "GUI font size (px)", SettingsEntry::Type::Int},
    {"highlight_cursor_line", "Highlight cursor line", SettingsEntry::Type::Bool},
    {"idle_fps", "Idle FPS", SettingsEntry::Type::Int},
    {"image_viewer_backend", "Image viewer backend", SettingsEntry::Type::String},
    {"lsp_change_debounce_ms", "LSP change debounce (ms)", SettingsEntry::Type::Int},
    {"lsp_completion_ghost_delay_ms", "LSP ghost text delay (ms)", SettingsEntry::Type::Int},
    {"html_preview_auto_close", "HTML preview: auto close", SettingsEntry::Type::Bool},
    {"html_preview_auto_start", "HTML preview: auto start", SettingsEntry::Type::Bool},
    {"html_preview_browser", "HTML preview: browser", SettingsEntry::Type::String},
    {"html_preview_echo_preview_url", "HTML preview: echo URL", SettingsEntry::Type::Bool},
    {"html_preview_host", "HTML preview: host", SettingsEntry::Type::String},
    {"html_preview_html_ext", "HTML preview: extensions", SettingsEntry::Type::String},
    {"html_preview_open_browser", "HTML preview: open browser", SettingsEntry::Type::Bool},
    {"html_preview_port", "HTML preview: port", SettingsEntry::Type::Int},
    {"html_preview_refresh_interval",
     "HTML preview: reload debounce (ms)",
     SettingsEntry::Type::Int},
    {"html_preview_root", "HTML preview: root", SettingsEntry::Type::String},
    {"lsp_completion_ghost_text", "LSP ghost text", SettingsEntry::Type::Bool},
    {"lsp_completion_max_items", "LSP completion max items", SettingsEntry::Type::Int},
    {"lsp_completion_nerd_icons", "LSP completion icons", SettingsEntry::Type::Bool},
    {"lsp_inlay_hints", "LSP parameter hints", SettingsEntry::Type::Bool},
    {"lsp_inlay_type_hints", "LSP type hints", SettingsEntry::Type::Bool},
    {"markdown_preview_auto_close", "Markdown preview: auto close", SettingsEntry::Type::Bool},
    {"markdown_preview_auto_start", "Markdown preview: auto start", SettingsEntry::Type::Bool},
    {"markdown_preview_browser", "Markdown preview: browser", SettingsEntry::Type::String},
    {"markdown_preview_custom_css", "Markdown preview: custom CSS", SettingsEntry::Type::String},
    {"markdown_preview_echo_preview_url",
     "Markdown preview: echo URL",
     SettingsEntry::Type::Bool},
    {"markdown_preview_host", "Markdown preview: host", SettingsEntry::Type::String},
    {"markdown_preview_images_path", "Markdown preview: images path", SettingsEntry::Type::String},
    {"markdown_preview_markdown_ext", "Markdown preview: extensions", SettingsEntry::Type::String},
    {"markdown_preview_open_timeout_ms",
     "Markdown preview: open timeout (ms)",
     SettingsEntry::Type::Int},
    {"markdown_preview_option_code_copy", "Markdown preview: code copy", SettingsEntry::Type::Bool},
    {"markdown_preview_option_echarts", "Markdown preview: ECharts", SettingsEntry::Type::Bool},
    {"markdown_preview_option_emoji", "Markdown preview: emoji", SettingsEntry::Type::Bool},
    {"markdown_preview_option_flowchart", "Markdown preview: flowcharts", SettingsEntry::Type::Bool},
    {"markdown_preview_option_highlightjs",
     "Markdown preview: highlight.js",
     SettingsEntry::Type::Bool},
    {"markdown_preview_option_katex", "Markdown preview: KaTeX math", SettingsEntry::Type::Bool},
    {"markdown_preview_option_mermaid", "Markdown preview: Mermaid", SettingsEntry::Type::Bool},
    {"markdown_preview_option_plantuml", "Markdown preview: PlantUML", SettingsEntry::Type::Bool},
    {"markdown_preview_option_source_map",
     "Markdown preview: source map",
     SettingsEntry::Type::Bool},
    {"markdown_preview_option_toc", "Markdown preview: TOC panel", SettingsEntry::Type::Bool},
    {"markdown_preview_option_vega", "Markdown preview: Vega charts", SettingsEntry::Type::Bool},
    {"markdown_preview_page_title", "Markdown preview: page title", SettingsEntry::Type::String},
    {"markdown_preview_port", "Markdown preview: port", SettingsEntry::Type::Int},
    {"markdown_preview_refresh_interval",
     "Markdown preview: refresh (ms)",
     SettingsEntry::Type::Int},
    {"markdown_preview_theme", "Markdown preview: theme", SettingsEntry::Type::String},
    {"minimap_width", "Minimap width", SettingsEntry::Type::Int},
    {"prettier_on_save", "Prettier on save", SettingsEntry::Type::Bool},
    {"relative_line_numbers", "Relative line numbers", SettingsEntry::Type::Bool},
    {"render_fps", "Render FPS", SettingsEntry::Type::Int},
    {"right_panel_width", "Right panel width", SettingsEntry::Type::Int},
    {"show_explorer", "Show explorer", SettingsEntry::Type::Bool},
    {"show_indent_guides", "Indent guides", SettingsEntry::Type::Bool},
    {"show_line_numbers", "Line numbers", SettingsEntry::Type::Bool},
    {"show_minimap", "Show minimap", SettingsEntry::Type::Bool},
    {"smart_paste_indent", "Smart paste indent", SettingsEntry::Type::Bool},
    {"smooth_scroll", "Smooth scrolling", SettingsEntry::Type::Bool},
    {"smooth_scroll_duration_multiplier",
     "Smooth scrolling: duration multiplier",
     SettingsEntry::Type::String},
    {"smooth_scroll_easing", "Smooth scrolling: easing", SettingsEntry::Type::String},
    {"snippet_auto_expand", "Snippets: auto expand", SettingsEntry::Type::Bool},
    {"snippet_backtab_key", "Snippets: jump back key", SettingsEntry::Type::String},
    {"snippet_choice_next_key", "Snippets: next choice key", SettingsEntry::Type::String},
    {"snippet_choice_prev_key", "Snippets: previous choice key", SettingsEntry::Type::String},
    {"snippet_enabled", "Snippets: enabled", SettingsEntry::Type::Bool},
    {"snippet_filetypes", "Snippets: extension overrides", SettingsEntry::Type::String},
    {"snippet_highlight", "Snippets: highlight placeholders", SettingsEntry::Type::Bool},
    {"snippet_history", "Snippets: remember history", SettingsEntry::Type::Bool},
    {"snippet_history_size", "Snippets: history size", SettingsEntry::Type::Int},
    {"snippet_load_snipmate", "Snippets: load snipMate packs", SettingsEntry::Type::Bool},
    {"snippet_load_vscode", "Snippets: load VSCode packs", SettingsEntry::Type::Bool},
    {"snippet_paths", "Snippets: extra paths", SettingsEntry::Type::String},
    {"winbar", "Breadcrumbs: auto / on / off", SettingsEntry::Type::String},
    {"snippet_tab_key", "Snippets: expand/jump key", SettingsEntry::Type::String},
    {"status_clock", "Statusline: local time", SettingsEntry::Type::Bool},
    {"status_session_time", "Statusline: session time", SettingsEntry::Type::Bool},
    {"tab_size", "Tab size", SettingsEntry::Type::Int},
    {"terminal_height", "Terminal panel height", SettingsEntry::Type::Int},
    {"toast.duration_ms", "Toast duration (ms)", SettingsEntry::Type::Int},
    {"toast.fade_ms", "Toast fade (ms)", SettingsEntry::Type::Int},
    {"toast.gap", "Toast gap", SettingsEntry::Type::Int},
    {"toast.margin", "Toast margin", SettingsEntry::Type::Int},
    {"toast.max_visible", "Toast max visible", SettingsEntry::Type::Int},
    {"toast.max_width", "Toast max width", SettingsEntry::Type::Int},
    {"treesitter_language_overrides",
     "Tree-sitter language overrides",
     SettingsEntry::Type::String},
    {"treesitter_library_paths", "Tree-sitter library paths", SettingsEntry::Type::String},
    {"treesitter_query_paths", "Tree-sitter query paths", SettingsEntry::Type::String},
    {"update.build_dir", "Update build dir", SettingsEntry::Type::String},
    {"update.check_on_startup", "Check updates on startup", SettingsEntry::Type::Bool},
    {"word_wrap", "Word wrap", SettingsEntry::Type::Bool},
    {"zen_content_width", "Zen content width", SettingsEntry::Type::Int},
};

// The selectable rows: a fixed set of choices, so Left/Right walks them and
// Enter lists them. An overlay table so the type table above stays one line
// per setting.
struct EnumChoices
{
  const char *key;
  const char *const *options;
  int count;
};

const char *const kAutoOnOff[] = {"auto", "on", "off"};
// The names the image viewer's own parser answers to (imageviewer.cpp):
// `cell` is the half-block preview, `off` shows no picture at all.
const char *const kImageBackends[] = {"auto", "kitty", "sixel", "cell", "off"};
const char *const kColorizerModes[] = {"background", "foreground", "virtualtext"};
// The shapes the renderer spells out (render/frame.cpp); anything else is
// read as the bar.
const char *const kCursorStyles[] = {"block", "bar"};
// Upstream neoscroll's easing names (see features/smooth_scroll.h).
const char *const kEasings[] = {"linear",   "quadratic", "cubic",  "quartic",
                                "quintic",  "circular",  "sine"};

const EnumChoices kEnumChoices[] = {
    {"truecolor", kAutoOnOff, 3},
    {"winbar", kAutoOnOff, 3},
    {"cursor_style", kCursorStyles, 2},
    {"colorizer_mode", kColorizerModes, 3},
    {"image_viewer_backend", kImageBackends, 5},
    {"smooth_scroll_easing", kEasings, 7},
};

// What a numeric row's steppers move by, and the range a stepped or typed
// value lands in. The ranges mirror the clamps the rest of the app already
// applies (apply_config_live and the settings' own readers); a row that is
// absent steps by one and cannot go below zero, which is what every count,
// size and millisecond budget here wants.
struct IntRange
{
  const char *key;
  int step;
  int min;
  int max;
};

const IntRange kIntRanges[] = {
    {"auto_save_interval_ms", 100, 100, 600000},
    {"cursor_blink_ms", 50, 0, 5000},
    {"debugger_height", 1, 6, 24},
    {"discord_idle_timeout", 5, 5, 3600},
    {"explorer_width", 1, 16, 200},
    {"gui_font_size", 1, 8, 40},
    {"html_preview_port", 1, 1, 65535},
    {"html_preview_refresh_interval", 50, 50, 60000},
    {"idle_fps", 5, 5, 240},
    {"lsp_change_debounce_ms", 10, 25, 1000},
    {"lsp_completion_ghost_delay_ms", 10, 0, 5000},
    {"lsp_completion_max_items", 5, 1, 1000},
    {"markdown_preview_open_timeout_ms", 100, 100, 60000},
    {"markdown_preview_port", 1, 1, 65535},
    {"markdown_preview_refresh_interval", 50, 50, 60000},
    {"minimap_width", 1, 4, 40},
    {"render_fps", 10, 30, 240},
    {"right_panel_width", 1, 28, 80},
    {"snippet_history_size", 5, 1, 500},
    {"tab_size", 1, 1, 16},
    {"terminal_height", 1, 5, 20},
    {"toast.duration_ms", 250, 500, 60000},
    {"toast.fade_ms", 50, 0, 5000},
    {"toast.gap", 1, 0, 10},
    {"toast.margin", 1, 0, 20},
    {"toast.max_visible", 1, 1, 20},
    {"toast.max_width", 5, 20, 500},
    {"zen_content_width", 10, 40, 400},
};

// The search bar's matcher: every character of the query has to appear in the
// row's label or key, in order, ignoring case and the punctuation between
// words -- so "autosave" finds "Auto save" and "lspghost" finds
// "lsp_completion_ghost_delay_ms". The list keeps the config's own order
// rather than re-ranking: a menu that reshuffles under the cursor is worse
// than one that only filters.
std::string match_key_of(const std::string &text)
{
  std::string out;
  out.reserve(text.size());
  for (char c : text)
  {
    if (std::isalnum((unsigned char)c))
      out.push_back((char)std::tolower((unsigned char)c));
  }
  return out;
}

bool matches_query(const std::string &needle, const std::string &haystack)
{
  size_t at = 0;
  for (char c : haystack)
  {
    if (at < needle.size() && needle[at] == c)
      ++at;
  }
  return at == needle.size();
}

// A typed or stepped number, and the range it has to land in.
int clamp_setting_int(const SettingsEntry &e, int value)
{
  const int lo = e.has_range ? e.min_value : 0;
  const int hi = e.has_range ? e.max_value : INT_MAX;
  return std::clamp(value, lo, hi);
}

bool parse_setting_int(const std::string &text, int &out)
{
  if (text.empty())
    return false;
  errno = 0;
  char *end = nullptr;
  const long value = std::strtol(text.c_str(), &end, 10);
  if (errno != 0 || end == text.c_str() || *end != '\0')
    return false;
  if (value > INT_MAX || value < INT_MIN)
    return false;
  out = (int)value;
  return true;
}

SettingsEntry::Type infer_type(const std::string &key, const std::string &value)
{
  for (const KnownSetting &k : kKnownSettings)
  {
    if (key == k.key)
      return k.type;
  }
  // Unknown (Lua-registered) keys: infer from the stored value so the menu
  // still toggles booleans set from Lua.
  if (value == "true" || value == "false")
    return SettingsEntry::Type::Bool;
  if (!value.empty())
  {
    bool numeric = true;
    for (char c : value)
    {
      if (!std::isdigit((unsigned char)c) && c != '-' && c != '.')
      {
        numeric = false;
        break;
      }
    }
    if (numeric)
      return SettingsEntry::Type::Int;
  }
  return SettingsEntry::Type::String;
}
} // namespace

void Editor::rebuild_settings_entries()
{
  settings_entries.clear();
  for (const std::string &key : config.keys())
  {
    const std::string value = config.get(key, "");
    SettingsEntry e;
    e.key = key;
    e.type = infer_type(key, value);
    e.value = value;
    e.label = key; // fallback label; replaced below when known
    for (const KnownSetting &k : kKnownSettings)
    {
      if (key == k.key)
      {
        e.label = k.label;
        break;
      }
    }
    for (const EnumChoices &choices : kEnumChoices)
    {
      if (key != choices.key)
        continue;
      e.type = SettingsEntry::Type::Enum;
      e.options.assign(choices.options, choices.options + choices.count);
      break;
    }
    // The themes are not a constant -- they depend on what is installed --
    // so the one enumerated row whose choices are discovered is filled from
    // the live registry here.
    if (key == "color_scheme")
    {
      e.type = SettingsEntry::Type::Enum;
      e.options = list_available_themes();
    }
    for (const IntRange &range : kIntRanges)
    {
      if (key != range.key)
        continue;
      e.step = range.step;
      e.has_range = true;
      e.min_value = range.min;
      e.max_value = range.max;
      break;
    }
    settings_entries.push_back(std::move(e));
  }
  refresh_settings_filter();
  needs_redraw = true;
}

void Editor::refresh_settings_filter()
{
  settings_filtered.clear();
  const std::string needle = match_key_of(settings_query);
  for (int i = 0; i < (int)settings_entries.size(); i++)
  {
    SettingsEntry &e = settings_entries[(size_t)i];
    // The label is what the row reads as, the key is what it is called in a
    // config file; a query may name either.
    const bool shown =
        needle.empty() || matches_query(needle, match_key_of(e.label + e.key));
    e.row_pos = shown ? (int)settings_filtered.size() : -1;
    if (shown)
      settings_filtered.push_back(i);
  }
  settings_selected =
      std::clamp(settings_selected, 0, std::max(0, (int)settings_filtered.size() - 1));
  // A new query reads from the top; keeping the old window over a shorter
  // list would leave it past the end.
  settings_scroll = 0;
  needs_redraw = true;
}

SettingsEntry *Editor::settings_selected_entry()
{
  if (settings_filtered.empty())
    return nullptr;
  const int pos = std::clamp(settings_selected, 0, (int)settings_filtered.size() - 1);
  const int idx = settings_filtered[(size_t)pos];
  if (idx < 0 || idx >= (int)settings_entries.size())
    return nullptr;
  return &settings_entries[(size_t)idx];
}

void Editor::toggle_settings_menu()
{
  if (show_settings_menu)
  {
    close_settings_menu();
    return;
  }
  // A fresh menu opens unfiltered: the search bar is per-visit, so the list
  // always starts whole rather than on whatever was typed last time.
  settings_query.clear();
  settings_selected = 0;
  settings_scroll = 0;
  rebuild_settings_entries();
  show_settings_menu = true;
  needs_redraw = true;
}

void Editor::close_settings_menu()
{
  show_settings_menu = false;
  settings_entries.clear();
  settings_filtered.clear();
  settings_query.clear();
  settings_dropdown_open = false;
  settings_selected = 0;
  settings_scroll = 0;
  needs_redraw = true;
}

void Editor::open_settings_dropdown()
{
  SettingsEntry *e = settings_selected_entry();
  if (!e || e->type != SettingsEntry::Type::Enum || e->options.empty())
    return;
  settings_dropdown_open = true;
  settings_dropdown_index = 0;
  for (int i = 0; i < (int)e->options.size(); i++)
  {
    if (e->options[(size_t)i] == e->value)
    {
      settings_dropdown_index = i;
      break;
    }
  }
  settings_dropdown_scroll = 0;
  needs_redraw = true;
}

bool Editor::step_settings_value(bool increase)
{
  SettingsEntry *e = settings_selected_entry();
  if (!e)
    return true;
  if (e->type == SettingsEntry::Type::Bool)
  {
    apply_settings_value(e->key, e->value == "true" ? "false" : "true");
    return true;
  }
  if (e->type == SettingsEntry::Type::Int)
  {
    int current = 0;
    if (!parse_setting_int(e->value, current))
    {
      // Nothing sensible to step from (empty or hand-edited): Enter opens the
      // editor, which is where the value can be typed instead.
      return true;
    }
    const int next = clamp_setting_int(*e, current + (increase ? e->step : -e->step));
    apply_settings_value(e->key, std::to_string(next));
    return true;
  }
  if (e->type == SettingsEntry::Type::Enum && !e->options.empty())
  {
    const int count = (int)e->options.size();
    int at = 0;
    for (int i = 0; i < count; i++)
    {
      if (e->options[(size_t)i] == e->value)
      {
        at = i;
        break;
      }
    }
    const int next = ((at + (increase ? 1 : -1)) % count + count) % count;
    apply_settings_value(e->key, e->options[(size_t)next]);
    return true;
  }
  return true;
}

void Editor::apply_settings_value(const std::string &key, const std::string &value)
{
  config.set(key, value);
  // Font changes need the GUI's live re-fit (same path Ctrl+= uses);
  // everything else applies through the shared live-config pipeline.
  if (key == "gui_font_size")
  {
#ifdef JOT_GUI
    if (auto *gui = gui_ui())
    {
      gui->apply_font_size(std::clamp(config.get_int("gui_font_size", 16), 8, 40));
    }
#endif
  }
  else if (key == "gui_font_family")
  {
    // One place handles rejecting an unknown name, and writes back the family
    // that actually took effect so the file cannot keep a name that resolves
    // to nothing.
    apply_gui_font_family(config.get("gui_font_family", ""));
  }
  apply_config_live();
  config.save();
  // Keep the menu's model in sync with the applied value.
  for (SettingsEntry &e : settings_entries)
  {
    if (e.key == key)
    {
      e.value = config.get(key, "");
      e.editing = false;
      break;
    }
  }
  needs_redraw = true;
}

bool Editor::handle_settings_dropdown_input(int ch)
{
  SettingsEntry *e = settings_selected_entry();
  if (!e || e->options.empty())
  {
    settings_dropdown_open = false;
    return true;
  }
  const int count = (int)e->options.size();
  if (ch == 1008 || ch == 14)
  {
    settings_dropdown_index = std::max(0, settings_dropdown_index - 1);
    needs_redraw = true;
    return true;
  }
  if (ch == 1009 || ch == 16)
  {
    settings_dropdown_index = std::min(count - 1, settings_dropdown_index + 1);
    needs_redraw = true;
    return true;
  }
  if (ch == 1012 || ch == 1013)
  {
    settings_dropdown_index = ch == 1012 ? 0 : count - 1;
    needs_redraw = true;
    return true;
  }
  if (ch == '\n' || ch == 13)
  {
    settings_dropdown_open = false;
    apply_settings_value(e->key, e->options[(size_t)settings_dropdown_index]);
    return true;
  }
  // The open list owns the keyboard: a stray letter must not silently edit
  // the row behind it.
  return true;
}

bool Editor::handle_settings_input(int ch)
{
  if (!show_settings_menu)
    return false;

  // Esc unwinds one layer at a time: the choices drop-down, then a row being
  // edited, then the search text, and only then the menu itself.
  if (ch == 27)
  {
    if (settings_dropdown_open)
    {
      settings_dropdown_open = false;
      needs_redraw = true;
      return true;
    }
    SettingsEntry *editing = settings_selected_entry();
    if (editing && editing->editing)
    {
      editing->editing = false;
      needs_redraw = true;
      return true;
    }
    if (!settings_query.empty())
    {
      settings_query.clear();
      refresh_settings_filter();
      return true;
    }
    close_settings_menu();
    return true;
  }

  // The drop-down is modal over the panel while it is up.
  if (settings_dropdown_open)
    return handle_settings_dropdown_input(ch);

  SettingsEntry *cur = settings_selected_entry();

  if (cur && cur->editing)
  {
    if (ch == '\n' || ch == 13)
    {
      // A typed number has to land in the row's range, and empty /
      // non-numeric input cancels the edit rather than corrupting the config.
      if (cur->type == SettingsEntry::Type::Int)
      {
        int value = 0;
        if (!parse_setting_int(cur->edit_input, value))
        {
          cur->editing = false;
          needs_redraw = true;
          return true;
        }
        cur->editing = false;
        apply_settings_value(cur->key, std::to_string(clamp_setting_int(*cur, value)));
        return true;
      }
      if (cur->edit_input.empty())
      {
        // Empty string input: treat as cancel (no meaningful change).
        cur->editing = false;
        needs_redraw = true;
        return true;
      }
      apply_settings_value(cur->key, cur->edit_input);
      return true;
    }
    if (ch == 127 || ch == 8)
    {
      if (!cur->edit_input.empty())
        cur->edit_input.pop_back();
      needs_redraw = true;
      return true;
    }
    if (ch >= 32 && ch < 1000)
    {
      cur->edit_input.push_back((char)ch);
      needs_redraw = true;
    }
    return true;
  }

  // Navigation walks the filtered list; Ctrl+P / Ctrl+N do what the arrows
  // do, since plain letters belong to the search bar now.
  const int matches = (int)settings_filtered.size();
  const auto clamp_selected = [&](int to)
  { return std::max(0, std::min(matches - 1, to)); };
  if (ch == 1008 || ch == 14)
  {
    settings_selected = clamp_selected(settings_selected - 1);
    needs_redraw = true;
    return true;
  }
  if (ch == 1009 || ch == 16)
  {
    settings_selected = clamp_selected(settings_selected + 1);
    needs_redraw = true;
    return true;
  }
  if (ch == 1015)
  {
    settings_selected = clamp_selected(settings_selected - 8);
    needs_redraw = true;
    return true;
  }
  if (ch == 1016)
  {
    settings_selected = clamp_selected(settings_selected + 8);
    needs_redraw = true;
    return true;
  }
  if (ch == 1012)
  {
    settings_selected = 0;
    needs_redraw = true;
    return true;
  }
  if (ch == 1013)
  {
    settings_selected = clamp_selected(matches - 1);
    needs_redraw = true;
    return true;
  }

  // Right/Left: the row's own way of moving a notch (1010 is the right
  // arrow, 1011 the left, the same codes the buffer's own movement uses).
  if (ch == 1010 || ch == 1011)
    return step_settings_value(ch == 1010);

  // Enter: toggle booleans, list an enum's choices, start the inline editor
  // for everything else.
  if (ch == '\n' || ch == 13)
  {
    if (!cur)
      return true;
    if (cur->type == SettingsEntry::Type::Bool)
    {
      apply_settings_value(cur->key, cur->value == "true" ? "false" : "true");
    }
    else if (cur->type == SettingsEntry::Type::Enum)
    {
      open_settings_dropdown();
    }
    else
    {
      // Fresh input: type the new value from scratch (empty + Enter
      // cancels the edit). Seeding with the old value would force
      // backspacing over it for every change.
      cur->editing = true;
      cur->edit_input.clear();
      needs_redraw = true;
    }
    return true;
  }

  if (ch == 127 || ch == 8)
  {
    if (!settings_query.empty())
    {
      settings_query.pop_back();
      settings_selected = 0;
      refresh_settings_filter();
    }
    return true;
  }

  // Typing filters: every printable character lands in the search bar (there
  // is no other text field in the panel to compete with it), the way the
  // palette and the quick pick behave.
  if (ch >= 32 && ch < 1000)
  {
    settings_query.push_back((char)ch);
    settings_selected = 0;
    refresh_settings_filter();
    return true;
  }

  return true;
}

bool Editor::handle_settings_mouse(int x, int y, bool is_click)
{
  if (!show_settings_menu)
    return false;

  // An open choices drop-down owns the pointer over its own box; anywhere
  // else dismisses it (a motion just puts it away, a click also does what it
  // would have done).
  if (settings_dropdown_open)
  {
    const bool inside = x >= settings_dropdown_x && x < settings_dropdown_x + settings_dropdown_w
                        && y >= settings_dropdown_y && y < settings_dropdown_y + settings_dropdown_h;
    if (inside)
    {
      // Interior rows start one below the top border (the same arithmetic the
      // painter uses to place them).
      const int row = y - (settings_dropdown_y + 1) + settings_dropdown_scroll;
      SettingsEntry *e = settings_selected_entry();
      const int count = e ? (int)e->options.size() : 0;
      if (row >= 0 && row < count && e)
      {
        settings_dropdown_index = row;
        needs_redraw = true;
        if (is_click)
        {
          settings_dropdown_open = false;
          apply_settings_value(e->key, e->options[(size_t)row]);
        }
      }
      return true;
    }
    settings_dropdown_open = false;
    needs_redraw = true;
    if (!is_click)
      return true;
  }

  for (int i = 0; i < (int)settings_entries.size(); i++)
  {
    SettingsEntry &e = settings_entries[(size_t)i];
    if (e.row_y != y || x < e.row_x || x >= e.row_x + e.row_w)
      continue;
    // The steppers and chevrons sit inside the value column: a press on one
    // moves the value instead of opening the editor (which is what a press
    // anywhere else on the row does).
    if (e.step_down_x >= 0 && x == e.step_down_x)
    {
      if (e.row_pos >= 0)
        settings_selected = e.row_pos;
      return step_settings_value(false);
    }
    if (e.step_up_x >= 0 && x == e.step_up_x)
    {
      if (e.row_pos >= 0)
        settings_selected = e.row_pos;
      return step_settings_value(true);
    }
    if (e.row_pos >= 0 && settings_selected != e.row_pos)
    {
      settings_selected = e.row_pos;
      needs_redraw = true;
    }
    if (is_click)
    {
      return handle_settings_input('\n');
    }
    return true;
  }

  const bool inside_panel = x >= settings_panel_x && x < settings_panel_x + settings_panel_w
                            && y >= settings_panel_y && y < settings_panel_y + settings_panel_h;
  if (inside_panel)
    return true;
  if (!is_click)
    return true;
  close_settings_menu();
  return false;
}