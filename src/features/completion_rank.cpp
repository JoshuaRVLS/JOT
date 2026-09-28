// Ranking the completion popup by the user's own habits and by the file's own
// words (see completion_rank.h). Every number the ranking is built from lives
// here, so the tests can pin the model instead of describing it.
#include "features/completion_rank.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <system_error>

namespace fs = std::filesystem;

namespace
{
  // The alphabet a word is read with here: an identifier in every language the
  // popup completes for, which is all the context signal needs.
  bool is_word_char(char c)
  {
    const unsigned char uc = (unsigned char)c;
    return std::isalnum(uc) != 0 || c == '_';
  }

  char fold_ascii(char c)
  {
    return (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
  }

  std::string fold(const std::string &s)
  {
    std::string out = s;
    for (char &c : out)
    {
      c = fold_ascii(c);
    }
    return out;
  }

  // 30 points per acceptance, and a habit stops paying past ten: the eleventh
  // acceptance of the same name says nothing new about the next one, and the cap
  // keeps a name accepted a thousand times from burying every other row.
  constexpr int kPointsPerUse = 30;
  constexpr int kUsesThatCount = 10;
  // A word anywhere in the window is worth 90 points, one on the caret's own line
  // 150, so being in the file already is the stronger signal and being right here
  // the strongest. The gap is measured in lines.
  constexpr int kLocalPoints = 90;
  constexpr int kLocalPointsRange = 60;
  constexpr int kPointsPerLineAway = 4;
  // Held per language, and a window of lines either side of the caret, generous
  // enough to cover the function being edited in a large file.
  constexpr int kMaxEntriesPerLanguage = 400;
  constexpr int kScanHalfWindow = 2000;
  constexpr std::size_t kMaxWords = 2000;
  constexpr std::size_t kMinWordLength = 2;

  fs::path config_home()
  {
    const char *override_home = std::getenv("JOT_CONFIG_HOME");
    if (override_home && *override_home)
    {
      return fs::path(override_home);
    }
#ifdef _WIN32
    const char *app_data = std::getenv("APPDATA");
    if (app_data && *app_data)
    {
      return fs::path(app_data) / "jot";
    }
    const char *profile = std::getenv("USERPROFILE");
    if (profile && *profile)
    {
      return fs::path(profile) / ".config" / "jot";
    }
#else
    const char *home = std::getenv("HOME");
    if (home && *home)
    {
      return fs::path(home) / ".config" / "jot";
    }
#endif
    return {};
  }
} // namespace

namespace CompletionRank
{
  void Usage::record(const std::string &language, const std::string &name)
  {
    const std::string lang = fold(language);
    const std::string key = fold(name);
    if (key.empty())
    {
      return;
    }
    auto &table = counts_[lang];
    auto it = table.find(key);
    if (it == table.end() && (int)table.size() >= kMaxEntriesPerLanguage)
    {
      // Full: a new name only makes room for itself when the least-used entry is
      // as weak as it is -- picked once. Evicting a real habit to admit a first
      // sighting would trade the signal away for nothing.
      auto smallest = table.begin();
      for (auto candidate = table.begin(); candidate != table.end(); ++candidate)
      {
        if (candidate->second < smallest->second)
        {
          smallest = candidate;
        }
      }
      if (smallest->second > 1)
      {
        return;
      }
      table.erase(smallest);
    }
    table[key] += 1;
  }

  int Usage::count(const std::string &language, const std::string &name) const
  {
    auto table = counts_.find(fold(language));
    if (table == counts_.end())
    {
      return 0;
    }
    auto it = table->second.find(fold(name));
    return it == table->second.end() ? 0 : it->second;
  }

  int Usage::points(const std::string &language, const std::string &name) const
  {
    return usage_points_for_count(count(language, name));
  }

  bool Usage::empty() const
  {
    return counts_.empty();
  }

  void Usage::clear()
  {
    counts_.clear();
  }

  std::size_t Usage::entries() const
  {
    std::size_t total = 0;
    for (const auto &table : counts_)
    {
      total += table.second.size();
    }
    return total;
  }

  std::string Usage::serialize() const
  {
    std::ostringstream out;
    for (const auto &table : counts_)
    {
      for (const auto &entry : table.second)
      {
        if (entry.second <= 0)
        {
          continue;
        }
        out << table.first << '\t' << entry.first << '\t' << entry.second << '\n';
      }
    }
    return out.str();
  }

  Usage Usage::parse(const std::string &text)
  {
    Usage usage;
    std::istringstream lines(text);
    std::string line;
    while (std::getline(lines, line))
    {
      if (!line.empty() && line.back() == '\r')
      {
        line.pop_back();
      }
      const std::size_t first = line.find('\t');
      if (first == std::string::npos)
      {
        continue;
      }
      const std::size_t second = line.find('\t', first + 1);
      if (second == std::string::npos)
      {
        continue;
      }
      const std::string language = line.substr(0, first);
      const std::string name = line.substr(first + 1, second - first - 1);
      const std::string count_text = line.substr(second + 1);
      if (language.empty() || name.empty() || count_text.empty())
      {
        continue;
      }
      char *end = nullptr;
      const long count = std::strtol(count_text.c_str(), &end, 10);
      if (end == count_text.c_str() || *end != '\0' || count <= 0)
      {
        continue;
      }
      usage.counts_[fold(language)][fold(name)] = (int)std::min<long>(count, 1000000L);
    }
    return usage;
  }

  void Context::rebuild(const std::vector<std::string> &lines, int caret_line)
  {
    nearest_gap_.clear();
    if (lines.empty())
    {
      return;
    }
    const int caret = std::clamp(caret_line, 0, (int)lines.size() - 1);
    const int first = std::max(0, caret - kScanHalfWindow);
    const int last = std::min((int)lines.size(), caret + kScanHalfWindow + 1);
    for (int index = first; index < last; index++)
    {
      const std::string &line = lines[(std::size_t)index];
      const int gap = std::abs(index - caret);
      std::size_t i = 0;
      while (i < line.size())
      {
        if (!is_word_char(line[i]))
        {
          i++;
          continue;
        }
        const std::size_t start = i;
        while (i < line.size() && is_word_char(line[i]))
        {
          i++;
        }
        if (i - start < kMinWordLength)
        {
          continue;
        }
        const std::string word = fold(line.substr(start, i - start));
        auto it = nearest_gap_.find(word);
        if (it != nearest_gap_.end())
        {
          it->second = std::min(it->second, gap);
        }
        else if (nearest_gap_.size() < kMaxWords)
        {
          nearest_gap_.emplace(word, gap);
        }
      }
    }
  }

  int Context::points(const std::string &name) const
  {
    auto it = nearest_gap_.find(fold(name));
    return it == nearest_gap_.end() ? 0 : locality_points_for_gap(it->second);
  }

  bool Context::empty() const
  {
    return nearest_gap_.empty();
  }

  void Context::clear()
  {
    nearest_gap_.clear();
  }

  int relevance(const Usage &usage, const Context &context, const std::string &language,
                const std::string &name)
  {
    if (name.empty())
    {
      return 0;
    }
    return usage.points(language, name) + context.points(name);
  }

  int usage_points_for_count(int count)
  {
    if (count <= 0)
    {
      return 0;
    }
    return kPointsPerUse * std::min(count, kUsesThatCount);
  }

  int locality_points_for_gap(int gap_lines)
  {
    if (gap_lines < 0)
    {
      return 0;
    }
    const int nearer = std::max(0, kLocalPointsRange - kPointsPerLineAway * gap_lines);
    return kLocalPoints + nearer;
  }

  std::string usage_file_path()
  {
    const fs::path root = config_home();
    if (root.empty())
    {
      return "";
    }
    return (root / "configs" / "completion_usage.tsv").string();
  }

  bool load_usage(const std::string &path, Usage &out)
  {
    out.clear();
    if (path.empty())
    {
      return false;
    }
    std::ifstream file(path);
    if (!file.is_open())
    {
      return false;
    }
    std::ostringstream text;
    text << file.rdbuf();
    out = Usage::parse(text.str());
    return true;
  }

  bool save_usage(const std::string &path, const Usage &usage)
  {
    if (path.empty())
    {
      return false;
    }
    std::error_code ec;
    const fs::path output_path(path);
    fs::create_directories(output_path.parent_path(), ec);
    std::ofstream file(path);
    if (!file.is_open())
    {
      return false;
    }
    file << usage.serialize();
    return true;
  }
} // namespace CompletionRank
