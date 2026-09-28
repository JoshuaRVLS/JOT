// Occurrence highlight: the identifier under the caret is tinted at every other
// place it shows in the viewport, with the caret's own word on the stronger
// band, so a rename's blast radius is visible before a keystroke of it is typed.
//
// What counts as an occurrence is the whole question. A plain substring match
// would light the `print` inside `printf`, and a caret parked one cell past a
// word is still on that word (the same rule the edit commands use), while a
// caret sitting in the middle of a run of whitespace is on nothing at all. The
// tint is a band only: the token keeps the colour its syntax gave it, which is
// why the theme's fg slot is -1 unless a theme asks otherwise.
#include "editor.h"
#include "render/gutter.h"
#include "jot/model/panes.h" // pane_content_top
#include "ui/ui.h"

#include <catch2/catch_test_macros.hpp>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

namespace
{
  // A fresh editor per case: the highlight is decided by the caret, the buffer
  // and the selection, and a shared editor would carry another case's state.
  void seed_config_home()
  {
    char home[] = "/tmp/jot_word_highlight_XXXXXX";
    REQUIRE(mkdtemp(home) != nullptr);
    setenv("JOT_CONFIG_HOME", home, 1);
    setenv("JOT_CACHE_HOME", home, 1);
  }

  // A file on disk the editor can load, one line per string, ending in a
  // newline. Names are unique per case: load_file focuses an already-open
  // buffer instead of re-reading it.
  void load_lines(Editor &e, const std::vector<std::string> &lines, const std::string &name)
  {
    const std::string path = "/tmp/jot_word_highlight_" + name + ".cpp";
    std::ofstream out(path);
    for (const auto &line : lines)
    {
      out << line << "\n";
    }
    out.close();
    e.load_file(path);
  }

  // The cell a buffer position paints on this frame: the pane's own mapping
  // (border, gutter, the pane's first text row) at the current scroll.
  const UICell *cell_at(Editor &e, int line, int col)
  {
    const SplitPane &pane = e.pane_for_test();
    const int x = pane.x + 1 + gutter::width(e.buffer_for_test().line_count()) + col;
    const int y = pane_content_top(pane) + line;
    return e.ui_for_test()->cell_at(x, y);
  }

  // Repaints on demand: moving the caret through scroll_cursor_to_for_test does
  // not mark the frame dirty, and render() leaves an undirtied frame as it is.
  void repaint(Editor &e)
  {
    e.request_redraw_for_test();
    e.render_for_test();
  }

  // Whether every one of `len` cells at `col` wears the band the highlight
  // promises for that occurrence, read off the frame that was just rendered.
  bool wears(Editor &e, int line, int col, int len, bool strong)
  {
    const Theme &theme = e.theme_for_test();
    const int want = strong ? theme.bg_word_highlight_strong : theme.bg_word_highlight;
    for (int i = 0; i < len; i++)
    {
      const UICell *cell = cell_at(e, line, col + i);
      if (cell == nullptr || cell->bg != want)
      {
        return false;
      }
    }
    return true;
  }

  // Whether any occurrence band sits on the cells at all, either one.
  bool tinted(Editor &e, int line, int col, int len)
  {
    return wears(e, line, col, len, true) || wears(e, line, col, len, false);
  }
} // namespace

TEST_CASE("Word highlight: every occurrence is tinted, the caret's own strongest", "[jot]")
{
  seed_config_home();
  Editor e;
  load_lines(e,
             {"int alpha = 1;", "int beta = alpha + 1;", "int gamma = alpha * beta;"},
             "occurrences");
  e.apply_resize_for_test(100, 30);

  e.scroll_cursor_to_for_test(0, 4); // on the first `alpha`
  repaint(e);
  REQUIRE(wears(e, 0, 4, 5, true));
  REQUIRE(wears(e, 1, 11, 5, false));
  REQUIRE(wears(e, 2, 12, 5, false));
  // Another identifier on the same rows is untouched.
  REQUIRE_FALSE(tinted(e, 1, 4, 4));  // beta
  REQUIRE_FALSE(tinted(e, 2, 20, 4)); // beta

  // Moving onto `beta` moves both bands with it: its own row is the strong one,
  // and `alpha` is back to plain ink everywhere.
  e.scroll_cursor_to_for_test(1, 4);
  repaint(e);
  REQUIRE(wears(e, 1, 4, 4, true));
  REQUIRE(wears(e, 2, 20, 4, false));
  REQUIRE_FALSE(tinted(e, 0, 4, 5));
  REQUIRE_FALSE(tinted(e, 2, 12, 5));
}

TEST_CASE("Word highlight: only a whole word is an occurrence", "[jot]")
{
  seed_config_home();
  Editor e;
  load_lines(e, {"int print = 1;", "int printf = 2;", "int sprint = 3;"}, "wholeword");
  e.apply_resize_for_test(100, 30);

  e.scroll_cursor_to_for_test(0, 4); // on `print`
  repaint(e);
  REQUIRE(wears(e, 0, 4, 5, true));
  // Both neighbours contain the word without being it.
  REQUIRE_FALSE(tinted(e, 1, 4, 6)); // printf
  REQUIRE_FALSE(tinted(e, 2, 4, 6)); // sprint
}

TEST_CASE("Word highlight: a caret on whitespace lights nothing", "[jot]")
{
  seed_config_home();
  Editor e;
  load_lines(e, {"int  value = 1;", "int  value = value;"}, "noword");
  e.apply_resize_for_test(100, 30);

  // The second of the two spaces after `int`: a caret one cell past a word
  // still answers for that word, so being on nothing means the middle of a run
  // of whitespace.
  e.scroll_cursor_to_for_test(0, 4);
  repaint(e);
  REQUIRE_FALSE(tinted(e, 0, 5, 5));
  REQUIRE_FALSE(tinted(e, 1, 5, 5));
  REQUIRE_FALSE(tinted(e, 1, 13, 5));

  // One cell to the right is the word, and every occurrence lights from there.
  e.scroll_cursor_to_for_test(0, 5);
  repaint(e);
  REQUIRE(wears(e, 0, 5, 5, true));
  REQUIRE(wears(e, 1, 5, 5, false));
  REQUIRE(wears(e, 1, 13, 5, false));
}

TEST_CASE("Word highlight: the setting turns it off", "[jot]")
{
  seed_config_home();
  Editor e;
  load_lines(e, {"int alpha = 1;", "int beta = alpha + 1;"}, "setting");
  e.apply_resize_for_test(100, 30);

  e.scroll_cursor_to_for_test(0, 4);
  repaint(e);
  REQUIRE(wears(e, 1, 11, 5, false));

  e.set_config_for_test("word_highlight", "false");
  e.apply_config_live_for_test();
  repaint(e);
  REQUIRE_FALSE(tinted(e, 0, 4, 5));
  REQUIRE_FALSE(tinted(e, 1, 11, 5));
}

TEST_CASE("Word highlight: a selection steps aside", "[jot]")
{
  seed_config_home();
  Editor e;
  load_lines(e, {"int alpha = 1;", "int beta = alpha + 1;"}, "selection");
  e.apply_resize_for_test(100, 30);

  // The other occurrence lights while the caret is bare...
  e.scroll_cursor_to_for_test(0, 4);
  repaint(e);
  REQUIRE(wears(e, 1, 11, 5, false));

  // ...and goes quiet once a selection covers the word the caret is answering
  // for: which text is the user's answer while a selection is up, not ours.
  REQUIRE(e.select_word_at_cursor());
  repaint(e);
  REQUIRE_FALSE(tinted(e, 0, 4, 5));
  REQUIRE_FALSE(tinted(e, 1, 11, 5));
}
