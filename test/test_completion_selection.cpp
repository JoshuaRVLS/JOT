// The completion popup's selected row: which one Up/Down put there, and which
// one the next frame keeps there.
//
// The list is re-filtered on every frame (render_lsp_completion runs the same
// pass a keystroke does, so the popup always shows the word as it is now), and
// that pass used to re-derive the selection from the *label* of the row that was
// selected. A server's overloads share one label -- every `std::includes` row is
// labeled "std::includes" -- so the lookup landed on the family's first row
// again on the very next frame and the arrow keys could never get past it: the
// highlight and the preview stayed on row 1 for every press. The selection now
// carries across an unchanged list by index, and a genuinely new list follows
// the symbol (label plus the parameter list in labelDetails) rather than the
// label alone.
#include "editor.h"
#include <catch2/catch_test_macros.hpp>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

namespace
{
  void seed_config_home()
  {
    char home[] = "/tmp/jot_completion_selection_XXXXXX";
    mkdtemp(home);
    setenv("JOT_CONFIG_HOME", home, 1);
    setenv("JOT_CACHE_HOME", home, 1);
  }

  // One overload row: the label clangd sends, the parameter list it carries in
  // labelDetails (the one thing that tells one overload from another), and what
  // accepting inserts. No filterText, like most clangd items -- the typed word
  // is then matched against the insert text.
  LSPCompletionItem overload(const std::string &label,
                             const std::string &parameter_list,
                             const std::string &insert_text)
  {
    LSPCompletionItem it;
    it.label = label;
    it.label_detail = parameter_list;
    it.insert_text = insert_text;
    it.kind = 3;
    return it;
  }

  std::string open_with_caret(Editor &e, const std::string &text, int line, int col)
  {
    static int counter = 0;
    const std::string path = "/tmp/jot_completion_selection_" + std::to_string(::getpid())
                             + "_" + std::to_string(counter++) + ".cpp";
    std::ofstream out(path);
    out << text;
    out.close();
    e.load_file(path);
    e.apply_resize_for_test(110, 30);
    e.scroll_cursor_to_for_test(line, col);
    return path;
  }
} // namespace

TEST_CASE("Completion selection: Down survives the frame's re-filter", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  open_with_caret(e, "int main() {\n  incl\n}\n", 1, 6);

  // The shape clangd really answers `incl` with: a family of overloads that all
  // read `std::includes` and differ only in their parameter lists.
  const std::vector<LSPCompletionItem> family = {
      overload("std::includes", "(ExecutionPolicy &&policy, ...)", "includes"),
      overload("std::includes", "(InputIt1 first1, InputIt1 last1, ...)", "includes"),
      overload("std::includes", "(InputIt1 first1, InputIt1 last1, ...)", "includes"),
      overload("std::inclusive_scan", "(InputIt first, InputIt last, ...)", "inclusive_scan"),
  };
  REQUIRE(e.seed_lsp_completion_for_test(family));
  REQUIRE(e.lsp_completion_visible_for_test());
  REQUIRE(e.lsp_completion_selected_for_test() == 0);

  // Down, then the frame that follows it (the render path re-filters): the row
  // the key picked has to still be the selected one.
  e.raw_key_for_test(1009);
  e.refresh_lsp_completion_for_test();
  REQUIRE(e.lsp_completion_selected_for_test() == 1);

  e.raw_key_for_test(1009);
  e.refresh_lsp_completion_for_test();
  REQUIRE(e.lsp_completion_selected_for_test() == 2);
  // ...and the preview follows the highlight: accepting the `includes` overload
  // writes `includes`, so the rest of the word is what the caret previews.
  REQUIRE(e.lsp_completion_ghost_for_test() == "udes");

  // Up walks back and is preserved the same way.
  e.raw_key_for_test(1008);
  e.refresh_lsp_completion_for_test();
  REQUIRE(e.lsp_completion_selected_for_test() == 1);

  // The bottom of the list stays the bottom: three more Downs leave it on the
  // last row rather than wrapping back to the top.
  e.raw_key_for_test(1009);
  e.raw_key_for_test(1009);
  e.raw_key_for_test(1009);
  e.refresh_lsp_completion_for_test();
  REQUIRE(e.lsp_completion_selected_for_test() == 3);
  REQUIRE(e.lsp_completion_ghost_for_test() == "usive_scan");
}

TEST_CASE("Completion selection: a new list follows the selected overload", "[jot]")
{
  seed_config_home();
  Editor e;
  e.set_home_menu_visible(false);
  open_with_caret(e, "int main() {\n  val\n}\n", 1, 5);

  const LSPCompletionItem first = overload("value", "(int)", "value");
  const LSPCompletionItem second = overload("value", "(double)", "value");
  const LSPCompletionItem third = overload("value", "(char)", "value");
  REQUIRE(e.seed_lsp_completion_for_test({first, second, third}));
  REQUIRE(e.lsp_completion_selected_for_test() == 0);

  // Select the `double` overload, then let the server answer again -- a
  // re-request re-ranks the family, so the same row can come back at another
  // index. The selection follows the parameter list, not the label (which every
  // row shares) and not the index.
  e.raw_key_for_test(1009);
  REQUIRE(e.lsp_completion_selected_for_test() == 1);
  REQUIRE(e.seed_lsp_completion_for_test({third, first, second}));
  REQUIRE(e.lsp_completion_selected_for_test() == 2);

  // An answer where that overload is gone falls back to the family's first row.
  REQUIRE(e.seed_lsp_completion_for_test({third, first}));
  REQUIRE(e.lsp_completion_selected_for_test() == 0);
}
