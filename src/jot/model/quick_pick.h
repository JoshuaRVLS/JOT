#ifndef JOT_MODEL_QUICK_PICK_H
#define JOT_MODEL_QUICK_PICK_H

#include <string>
#include <vector>
struct CommandPaletteSuggestion
{
  std::string insert_text;
  std::string label;
  std::string category;
  std::string detail;
  int score = 0;
  // 0-based byte offsets into `label` matched by the query; used to
  // emphasize matched characters while rendering. Empty when not computed.
  std::vector<int> match;
};

enum QuickPickKind
{
  QUICK_PICK_NONE,
  QUICK_PICK_PROJECT_SEARCH,
  QUICK_PICK_DIAGNOSTICS,
  QUICK_PICK_SYMBOLS,
  QUICK_PICK_REFERENCES,
  QUICK_PICK_CODE_ACTIONS,
  QUICK_PICK_PLUGIN,
  QUICK_PICK_FONT,
  QUICK_PICK_JUMPLIST,
  QUICK_PICK_WORKSPACE_SYMBOLS,
  QUICK_PICK_WORKSPACE_DIAGNOSTICS
};

struct QuickPickItem
{
  std::string label;
  std::string detail;
  std::string preview;
  std::string filepath;
  // The value the row stands for, when it is not a location: the font picker
  // puts the family name here so the label can carry the display text.
  std::string value;
  int line = 0;
  int col = 0;
  int severity = 0;
};

// One row of the cell-based settings menu (:settings / Ctrl+,). Each entry
// wraps a config key with its human label, current value, value type and
// edit state. Bool keys toggle on Enter; int/string keys open an inline
// input row. Lua-registered config keys (jot.config.set) appear here too
// as generic string entries, so the menu doubles as a config browser.
struct SettingsEntry
{
  enum class Type
  {
    Bool,
    Int,
    String,
    Enum // a fixed set of choices: cycled with Left/Right, listed by Enter
  };
  std::string key;    // config key
  std::string label;  // human-readable label
  std::string value;  // current string value (as stored in settings.conf)
  // The group the panel files the row under (see the sections table in
  // surfaces/settings.cpp). Entries are kept in section order, so the list
  // reads in groups without the render pass having to sort anything.
  std::string section;
  Type type = Type::String;
  // Enum rows: the choices, in the order they are offered. Empty otherwise.
  std::vector<std::string> options;
  // Int rows: what the steppers move by, and the range a stepped or typed
  // value is clamped to. `has_range` false keeps only a floor of zero, which
  // is what every count, size and millisecond setting wants.
  int step = 1;
  bool has_range = false;
  int min_value = 0;
  int max_value = 0;
  // While the row is being edited, its input text and the row's screen
  // position (set by the render pass, used by mouse hit-testing).
  bool editing = false;
  std::string edit_input;
  int row_x = 0;
  // -1 until the render pass places the row: a row that is filtered out,
  // scrolled past or simply not painted yet must never answer a hit test.
  int row_y = -1;
  int row_w = 0;
  // The row's position in the filtered list (set when the search bar's query
  // is applied), so a mouse hit names the row it landed on rather than an
  // index into the unfiltered list. -1 while the query hides the row.
  int row_pos = -1;
  // The value column, and the decrease/increase affordance inside it (an
  // int's steppers, an enum's cycling chevrons). The render pass records both
  // and the Lua surface paints into them, so the picture and the mouse hit
  // test can never disagree about where a button is. -1 means the row has no
  // affordance (every type but Enum/Int, and any unselected row).
  int value_x = 0;
  int value_w = 0;
  int step_down_x = -1;
  int step_up_x = -1;
};

// One painted row of the settings panel: either a section header or one of
// the entries. `entry_pos` is the row's position in the selectable list (what
// SettingsEntry::row_pos records too) and -1 on a header, so the picture, the
// scroll window and the mouse hit test all speak in the same units.
struct SettingsRow
{
  std::string header; // the title, on a header row
  int entry_pos = -1; // position in the selectable list; -1 on a header
};

#endif
