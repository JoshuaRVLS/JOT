// Save hygiene (src/features/save_hygiene.*, wired into Editor::save_buffer_at
// in src/jot/app/file.cpp), and the atomic write under it
// (src/tools/file_util.*).
//
// Three parts. The rules are pure, so each is asserted directly: what counts as
// trailing whitespace, what comes off, and which files have to keep theirs. The
// write helper is exercised on real files in /tmp, where the case that matters
// is the file that is already there: it is replaced, it keeps its mode, and a
// write that cannot happen leaves it exactly as it was. The editor cases pin
// the save itself, since the buffer and the bytes have to agree afterwards.

#include "editor.h"
#include "features/save_hygiene.h"
#include "tools/file_util.h"

#include <catch2/catch_test_macros.hpp>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;

namespace
{
  std::string scratch_path(const std::string &name)
  {
    static int counter = 0;
    return "/tmp/jot_save_hygiene_" + std::to_string(::getpid()) + "_"
           + std::to_string(counter++) + "_" + name;
  }

  void write_text(const std::string &path, const std::string &text)
  {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out << text;
  }

  std::string read_back(const std::string &path)
  {
    std::ifstream in(path, std::ios::binary);
    std::ostringstream out;
    out << in.rdbuf();
    return out.str();
  }

  unsigned int mode_of(const std::string &path)
  {
    struct stat st
    {
    };
    if (::stat(path.c_str(), &st) != 0)
    {
      return 0;
    }
    return static_cast<unsigned int>(st.st_mode & 07777);
  }

  // The text a buffer would be saved as, so a case can pin the buffer as well
  // as the file on disk.
  std::string buffer_text(const FileBuffer &buf)
  {
    std::string text;
    for (const std::string &line : buf.lines)
    {
      text += line;
      text += '\n';
    }
    return text;
  }

  Editor &probe_editor()
  {
    static bool seeded = false;
    if (!seeded)
    {
      char cfgdir[] = "/tmp/jot_save_test_XXXXXX";
      mkdtemp(cfgdir);
      setenv("JOT_CONFIG_HOME", cfgdir, 1);
      setenv("JOT_CACHE_HOME", cfgdir, 1);
      seeded = true;
    }
    static Editor e;
    return e;
  }

  // An editor that saves what it is given rather than what a formatter makes of
  // it: the two on-save formatters are off, so the bytes are this test's.
  Editor &saving_editor(bool trim_on_save)
  {
    Editor &e = probe_editor();
    e.set_home_menu_visible(false);
    e.set_config_for_test("clang_format_on_save", "false");
    e.set_config_for_test("prettier_on_save", "false");
    e.set_config_for_test("trim_trailing_whitespace_on_save", trim_on_save ? "true" : "false");
    return e;
  }

  int buffer_index_for(Editor &e, const std::string &path)
  {
    for (int i = 0; i < e.buffer_count_for_test(); i++)
    {
      if (e.buffer_for_test(i).filepath == path)
      {
        return i;
      }
    }
    return -1;
  }
} // namespace

TEST_CASE("Save hygiene: a line ends in spaces or tabs, and nothing else", "[jot][save]")
{
  REQUIRE(SaveHygiene::has_trailing_whitespace("int a = 1; "));
  REQUIRE(SaveHygiene::has_trailing_whitespace("int a = 1;\t"));
  REQUIRE(SaveHygiene::has_trailing_whitespace("   "));
  REQUIRE_FALSE(SaveHygiene::has_trailing_whitespace("int a = 1;"));
  REQUIRE_FALSE(SaveHygiene::has_trailing_whitespace(""));

  REQUIRE(SaveHygiene::trim_trailing_whitespace("int a = 1;  \t ") == "int a = 1;");
  REQUIRE(SaveHygiene::trim_trailing_whitespace("    ") == "");
  REQUIRE(SaveHygiene::trim_trailing_whitespace("\t\t") == "");
  // Whitespace inside the line is content, and stays.
  REQUIRE(SaveHygiene::trim_trailing_whitespace("a  =  b") == "a  =  b");
  REQUIRE(SaveHygiene::trim_trailing_whitespace("a") == "a");
}

TEST_CASE("Save hygiene: markdown keeps the spaces that mean a line break", "[jot][save]")
{
  REQUIRE(SaveHygiene::preserves_trailing_whitespace("notes.md"));
  REQUIRE(SaveHygiene::preserves_trailing_whitespace("docs/README.MARKDOWN"));
  REQUIRE(SaveHygiene::preserves_trailing_whitespace("/tmp/a/b.mdx"));
  REQUIRE_FALSE(SaveHygiene::preserves_trailing_whitespace("main.cpp"));
  REQUIRE_FALSE(SaveHygiene::preserves_trailing_whitespace("Makefile"));
  // A dot in a directory name is not an extension, and the nearest dot wins.
  REQUIRE_FALSE(SaveHygiene::preserves_trailing_whitespace("/tmp/a.md/README"));
  REQUIRE(SaveHygiene::preserves_trailing_whitespace("/tmp/a.md/notes.md"));
}

TEST_CASE("Atomic save: an existing file is replaced and keeps its mode", "[jot][save]")
{
  const std::string path = scratch_path("keeps-mode.txt");
  write_text(path, "old\n");
  ::chmod(path.c_str(), 0640);

  REQUIRE(file_util::write_file_atomic(path, "new\n").empty());
  REQUIRE(read_back(path) == "new\n");
  // The temporary a fresh file would have created is not allowed to turn a
  // group-readable file into a private one.
  REQUIRE(mode_of(path) == 0640);
  REQUIRE_FALSE(fs::exists(path + ".jot-saving"));

  // And the swap works again on the file it just created.
  REQUIRE(file_util::write_file_atomic(path, "one more\n").empty());
  REQUIRE(read_back(path) == "one more\n");
  std::remove(path.c_str());
}

TEST_CASE("Atomic save: a write that cannot happen leaves the file alone", "[jot][save]")
{
  // A path whose directory does not exist: nothing is created anywhere, and no
  // temporary is left behind beside a file that was never there.
  const std::string missing = scratch_path("no-such-dir/keep.txt");
  REQUIRE_FALSE(file_util::write_file_atomic(missing, "x\n").empty());
  REQUIRE_FALSE(fs::exists(missing));
  REQUIRE_FALSE(fs::exists(missing + ".jot-saving"));

  // A directory that refuses the temporary: the file that was there keeps every
  // byte rather than being truncated by the attempt. Root ignores the mode, so
  // this half only means something as an ordinary user.
  if (::geteuid() != 0)
  {
    const std::string dir = scratch_path("readonly-dir");
    fs::create_directory(dir);
    const std::string path = dir + "/keep.txt";
    write_text(path, "old\n");
    ::chmod(dir.c_str(), 0500);

    REQUIRE_FALSE(file_util::write_file_atomic(path, "new\n").empty());
    REQUIRE(read_back(path) == "old\n");
    REQUIRE_FALSE(fs::exists(path + ".jot-saving"));

    ::chmod(dir.c_str(), 0700);
    std::remove(path.c_str());
    fs::remove(dir);
  }
}

TEST_CASE("Save: trailing whitespace leaves both the buffer and the file", "[jot][save]")
{
  Editor &e = saving_editor(true);
  const std::string path = scratch_path("trim.cpp");
  write_text(path, "int a = 1;   \nint b = 2;\t\n");
  e.load_file(path);

  const int index = buffer_index_for(e, path);
  REQUIRE(index >= 0);
  REQUIRE(e.save_buffer_for_test(index, true));

  REQUIRE(read_back(path) == "int a = 1;\nint b = 2;\n");
  // What is on screen is what landed on disk: a save that wrote trimmed bytes
  // over an untrimmed buffer would leave the file and the window disagreeing.
  REQUIRE(buffer_text(e.buffer_for_test(index)) == "int a = 1;\nint b = 2;\n");
  std::remove(path.c_str());
}

TEST_CASE("Save: the setting off leaves every space where it was", "[jot][save]")
{
  Editor &e = saving_editor(false);
  const std::string path = scratch_path("keep.cpp");
  const std::string text = "int a = 1;   \n";
  write_text(path, text);
  e.load_file(path);

  const int index = buffer_index_for(e, path);
  REQUIRE(index >= 0);
  REQUIRE(e.save_buffer_for_test(index, true));

  REQUIRE(read_back(path) == text);
  std::remove(path.c_str());
}

TEST_CASE("Save: markdown keeps the trailing spaces even with the setting on", "[jot][save]")
{
  Editor &e = saving_editor(true);
  const std::string path = scratch_path("hard-break.md");
  // Two trailing spaces are a hard line break in markdown, so this is content.
  const std::string text = "first line  \nsecond line\n";
  write_text(path, text);
  e.load_file(path);

  const int index = buffer_index_for(e, path);
  REQUIRE(index >= 0);
  REQUIRE(e.save_buffer_for_test(index, true));

  REQUIRE(read_back(path) == text);
  std::remove(path.c_str());
}
