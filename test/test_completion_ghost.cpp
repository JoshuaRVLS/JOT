// The completion popup's ghost text: the selected item's remaining insert text
// previewed inline at the caret while typing.
//
// Two rules decide whether a preview is worth showing, and both come from what
// the preview *is* -- an inline completion of the word being typed:
//
//   * only an item whose insert text really continues what is typed has
//     something to preview (a row that matched by fuzzy subsequence, or one left
//     over from the previous keystroke's response, would render a whole
//     identifier as if the user were typing it), and
//   * it is only painted where the caret owns the rest of the row (inside a call
//     the editor auto-closed, `printf(|)`, the text under the caret is the `)`
//     itself, and the preview would land after a character that accepting would
//     insert before).
//
// The cases drive a real Editor: the prefix comes out of the buffer's own token
// under the caret, through the same arm/filter path a real response runs, and
// the render cases read the cells the painter produced.
#include "editor.h"
#include "ui/ui.h"
#include <catch2/catch_test_macros.hpp>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

namespace
{
  void seed_config_home()
  {
    char home[] = "/tmp/jot_completion_ghost_XXXXXX";
    mkdtemp(home);
    setenv("JOT_CONFIG_HOME", home, 1);
    setenv("JOT_CACHE_HOME", home, 1);
  }

  LSPCompletionItem item(const std::string &label,
                         const std::string &insert_text,
                         int insert_text_format = 1)
  {
    LSPCompletionItem it;
    it.label = label;
    it.insert_text = insert_text;
    it.filter_text = label;
    it.insert_text_format = insert_text_format;
    it.kind = 3;
    return it;
  }

  // A file whose content is `lines`, opened and left with the caret at
  // `line`:`col` -- the typed prefix is whatever the token before the caret
  // holds, exactly as a real keystroke would leave it.
  std::string open_with_caret(Editor &e, const std::string &text, int line, int col)
  {
    static int counter = 0;
    const std::string path = "/tmp/jot_completion_ghost_" + std::to_string(::getpid())
                             + "_" + std::to_string(counter++) + ".cpp";
    std::ofstream out(path);
    out << text;
    out.close();
    e.load_file(path);
    e.apply_resize_for_test(110, 30);
    e.scroll_cursor_to_for_test(line, col);
    return path;
  }

  // Every italic cell on screen, per row: the ghost is drawn italic
  // (like the inlay hints), and nothing else in a test's screen is.
  std::vector<std::string> italic_rows(UI *ui)
  {
    std::vector<std::string> rows;
    for (int y = 0; y < ui->get_height(); y++)
    {
      std::string row;
      for (int x = 0; x < ui->get_width(); x++)
      {
        const UICell *cell = ui->cell_at(x, y);
        if (cell && cell->italic && !cell->ch.empty())
        {
          row += cell->ch;
        }
      }
      if (!row.empty())
      {
        rows.push_back(row);
      }
    }
    return rows;
  }
} // namespace

TEST_CASE("Completion ghost: the preview is the insert text minus what is typed", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  open_with_caret(e, "int main() {\n  pri\n}\n", 1, 5);

  REQUIRE(e.seed_lsp_completion_for_test(
      {item("printf", "printf(${1:const char *format, ...})", 2)}));
  REQUIRE(e.lsp_completion_prefix_for_test() == "pri");
  REQUIRE(e.lsp_completion_ghost_for_test() == "ntf(const char *format, ...)");
}

TEST_CASE("Completion ghost: a fuzzy match is not previewed", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  open_with_caret(e, "int main() {\n  wdt\n}\n", 1, 5);

  // `Widget` matches `wdt` as a subsequence, so the popup lists it -- but
  // `wdt` is not how `Widget` starts, so there is no continuation to preview.
  // Drawing the whole identifier there is the noise this rule is about.
  REQUIRE(e.seed_lsp_completion_for_test({item("Widget", "Widget")}));
  REQUIRE(e.lsp_completion_ghost_for_test().empty());

  // A row from the previous keystroke's response behaves the same way: it is
  // in the popup (it matches `wdt` as a subsequence too), and it is still not
  // what the caret is typing.
  REQUIRE(e.seed_lsp_completion_for_test({item("WidgetFactory", "WidgetFactory(w)")}));
  REQUIRE(e.lsp_completion_ghost_for_test().empty());
}

TEST_CASE("Completion ghost: nothing typed and nothing left to type preview nothing", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);

  // Triggered on `.`: the member list is up before a letter is typed, and the
  // preview would be the selected member's whole name.
  open_with_caret(e, "int main() {\n  w.\n}\n", 1, 4);
  REQUIRE(e.seed_lsp_completion_for_test({item("width", "width"), item("area", "area()")}));
  REQUIRE(e.lsp_completion_prefix_for_test().empty());
  REQUIRE(e.lsp_completion_ghost_for_test().empty());

  // Typed in full: the item is the prefix, so there is no remainder to show.
  open_with_caret(e, "int main() {\n  width\n}\n", 1, 7);
  REQUIRE(e.seed_lsp_completion_for_test({item("width", "width")}));
  REQUIRE(e.lsp_completion_ghost_for_test().empty());
}

TEST_CASE("Completion ghost: a snippet previews its first line", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  open_with_caret(e, "int main() {\n  i\n}\n", 1, 3);

  // A body spanning lines cannot be shown inline, and the newline's control byte
  // would paint as a fallback glyph on the row: only the first line is previewed.
  REQUIRE(e.seed_lsp_completion_for_test({item("if", "if (${1:cond})\n{\n\t$0\n}", 2)}));
  REQUIRE(e.lsp_completion_prefix_for_test() == "i");
  REQUIRE(e.lsp_completion_ghost_for_test() == "f (cond)");
}

TEST_CASE("Completion ghost: the prefix match ignores case", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  open_with_caret(e, "int main() {\n  PRI\n}\n", 1, 5);

  REQUIRE(e.seed_lsp_completion_for_test({item("printf", "printf(format)")}));
  REQUIRE(e.lsp_completion_ghost_for_test() == "ntf(format)");
}

TEST_CASE("Completion ghost: nothing is painted inside an auto-closed call", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  // The caret sits between the typed `p` and the `)` the editor auto-closed the
  // call with: the bracket owns the rest of the row.
  open_with_caret(e, "int main() {\n  printf(p)\n}\n", 1, 10);

  REQUIRE(e.seed_lsp_completion_for_test({item("printf", "printf(${1:format})", 2)}));
  // There is a preview to show -- the suppression is the paint rule, not the
  // match rule -- so this case fails if either half regresses.
  REQUIRE(e.lsp_completion_prefix_for_test() == "p");
  REQUIRE(e.lsp_completion_ghost_for_test() == "rintf(format)");

  e.request_redraw_for_test();
  e.render_for_test();
  const std::vector<std::string> painted = italic_rows(e.ui_for_test());
  REQUIRE(painted.empty());

  // The row itself is untouched: the bracket is still the bracket, and nothing
  // previews between them.
  std::string screen;
  UI *ui = e.ui_for_test();
  for (int y = 0; y < ui->get_height(); y++)
  {
    for (int x = 0; x < ui->get_width(); x++)
    {
      const UICell *cell = ui->cell_at(x, y);
      if (cell)
      {
        screen += cell->ch;
      }
    }
    screen += "\n";
  }
  REQUIRE(screen.find("printf(p)") != std::string::npos);
  REQUIRE(screen.find("printf(p)rintf") == std::string::npos);
}

TEST_CASE("Completion ghost: it is painted where the caret owns the row", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  open_with_caret(e, "int main() {\n  pri\n}\n", 1, 5);

  REQUIRE(e.seed_lsp_completion_for_test({item("printf", "printf(format)")}));
  REQUIRE(e.lsp_completion_ghost_for_test() == "ntf(format)");

  e.request_redraw_for_test();
  e.render_for_test();
  const std::vector<std::string> painted = italic_rows(e.ui_for_test());
  REQUIRE(painted.size() == 1);
  REQUIRE(painted.front() == "ntf(format)");
}

TEST_CASE("Completion ghost: only the pane that owns the popup previews", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  open_with_caret(e, "int main() {\n  pri\n}\n", 1, 5);

  REQUIRE(e.seed_lsp_completion_for_test({item("printf", "printf(format)")}));
  REQUIRE(e.lsp_completion_ghost_for_test() == "ntf(format)");

  // The split shows the same file with its own view, and its caret sits on the
  // same row. The popup -- and so the preview -- belongs to the focused pane
  // (render_lsp_completion anchors there), so the second pane must not draw the
  // same word at its own caret: two carets, one preview.
  e.split_pane_for_test(true);
  e.request_redraw_for_test();
  e.render_for_test();
  const std::vector<std::string> painted = italic_rows(e.ui_for_test());
  REQUIRE(painted.size() == 1);
  REQUIRE(painted.front() == "ntf(format)");
}
