// The spawn half of a Windows LSP install: what to exec, and how to find a
// server that was installed outside the managed dir.
//
// Both are Windows-only mistakes that look fine on POSIX. A managed bin there
// can be a .cmd launcher (anything npm installed publishes one), and
// CreateProcess cannot start a launcher script: only cmd.exe can. And a Windows
// PATH is ';'-separated with drive-letter entries and executable extensions,
// so reading it the POSIX way both cut C:\... in half and looked for a name
// that never exists.
#include "jot/integrations/lsp/common.h"

#include <catch2/catch_test_macros.hpp>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace
{
  struct EnvGuard
  {
    explicit EnvGuard(const char *name) : name_(name)
    {
      const char *value = std::getenv(name);
      had_ = value != nullptr;
      if (had_)
      {
        old_ = value;
      }
    }
    ~EnvGuard()
    {
      if (had_)
      {
        setenv(name_.c_str(), old_.c_str(), 1);
      }
      else
      {
        unsetenv(name_.c_str());
      }
    }
    EnvGuard(const EnvGuard &) = delete;
    EnvGuard &operator=(const EnvGuard &) = delete;
    std::string name_;
    std::string old_;
    bool had_ = false;
  };

  void write_file(const fs::path &path)
  {
    std::ofstream(path) << "x";
  }
} // namespace

TEST_CASE("LSP spawn routes Windows launcher scripts through cmd.exe", "[lsp]")
{
  EnvGuard platform("JOT_INSTALL_PLATFORM");
  const std::vector<std::string> binary = {"clangd", "--stdio"};
  // An executable is spawned directly on every platform.
  REQUIRE(lsp_internal::launcher_argv(binary) == binary);

  setenv("JOT_INSTALL_PLATFORM", "win", 1);
  REQUIRE(lsp_internal::launcher_argv(binary) == binary);

  const std::vector<std::string> launcher = {"C:/data/lsp/bin/eslint-lsp.cmd", "--stdio"};
  const std::vector<std::string> wrapped = lsp_internal::launcher_argv(launcher);
  REQUIRE(wrapped.size() == 4);
  REQUIRE(wrapped[0] == "cmd");
  REQUIRE(wrapped[1] == "/c");
  REQUIRE(wrapped[2] == launcher[0]);
  REQUIRE(wrapped[3] == "--stdio");

  // Extensions are case-insensitive on Windows, and a .bat is the same kind of
  // script as a .cmd.
  const std::vector<std::string> bat = {"C:/x/thing.BAT"};
  const std::vector<std::string> bat_wrapped = lsp_internal::launcher_argv(bat);
  REQUIRE(bat_wrapped.size() == 3);
  REQUIRE(bat_wrapped[0] == "cmd");
}

TEST_CASE("LSP PATH lookup follows the platform separator and extensions", "[lsp]")
{
  EnvGuard platform("JOT_INSTALL_PLATFORM");
  EnvGuard path("PATH");
  EnvGuard data("XDG_DATA_HOME");
  const fs::path root = fs::temp_directory_path() / "jot_lsp_path_test";
  std::error_code ec;
  fs::remove_all(root, ec);
  fs::create_directories(root / "one", ec);
  fs::create_directories(root / "two", ec);
  // Nothing is installed in the managed dir, so every answer below comes from
  // PATH.
  fs::create_directories(root / "data", ec);
  setenv("XDG_DATA_HOME", (root / "data").string().c_str(), 1);

  setenv("JOT_INSTALL_PLATFORM", "win", 1);
  write_file(root / "two" / "eslint-lsp.cmd");
  // ';'-separated, as Windows writes it. A ':' split chopped the first entry
  // into a bare drive letter and a fragment, and then looked for a name with no
  // extension, so a server installed by npm was invisible.
  setenv("PATH", ((root / "one").string() + ";" + (root / "two").string()).c_str(), 1);
  REQUIRE(lsp_internal::lsp_bin_available("eslint-lsp"));
  REQUIRE_FALSE(lsp_internal::lsp_bin_available("no-such-tool"));

  unsetenv("JOT_INSTALL_PLATFORM");
  fs::remove(root / "two" / "eslint-lsp.cmd", ec);
  write_file(root / "one" / "pylsp");
  setenv("PATH", ((root / "one").string() + ":" + (root / "two").string()).c_str(), 1);
  REQUIRE(lsp_internal::lsp_bin_available("pylsp"));
  // The extension candidates are a Windows thing: a .cmd is not a POSIX
  // executable and must not be picked up.
  REQUIRE_FALSE(lsp_internal::lsp_bin_available("eslint-lsp"));

  fs::remove_all(root, ec);
}
