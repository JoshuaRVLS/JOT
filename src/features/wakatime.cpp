// The pure half of WakaTime tracking (see features/wakatime.h for what the
// integration is and which half this is).
#include "features/wakatime.h"
#include "tools/shell_util.h"
#include "tools/string_util.h"

#include <cstdio>
#include <cstdlib>
#include <filesystem>

namespace jot_wakatime
{
  namespace
  {
    // Reads one JSON string starting at `pos` (which must be the opening
    // quote) and moves past it. Only the escapes a `text` field can carry are
    // decoded; a \u escape outside ASCII is encoded as UTF-8, which is what a
    // locale-dependent "1 hr 24 mins" would arrive as.
    bool read_json_string(const std::string &text, size_t &pos, std::string &out)
    {
      if (pos >= text.size() || text[pos] != '"')
      {
        return false;
      }
      pos++;
      out.clear();
      while (pos < text.size())
      {
        const char c = text[pos++];
        if (c == '"')
        {
          return true;
        }
        if (c != '\\')
        {
          out.push_back(c);
          continue;
        }
        if (pos >= text.size())
        {
          return false;
        }
        const char escape = text[pos++];
        switch (escape)
        {
        case 'n':
          out.push_back('\n');
          break;
        case 't':
          out.push_back('\t');
          break;
        case 'r':
          out.push_back('\r');
          break;
        case 'b':
          out.push_back('\b');
          break;
        case 'f':
          out.push_back('\f');
          break;
        case 'u':
        {
          if (pos + 4 > text.size())
          {
            return false;
          }
          unsigned int codepoint = 0;
          for (int i = 0; i < 4; i++)
          {
            const char hex = text[pos++];
            codepoint <<= 4;
            if (hex >= '0' && hex <= '9')
            {
              codepoint |= (unsigned int)(hex - '0');
            }
            else if (hex >= 'a' && hex <= 'f')
            {
              codepoint |= (unsigned int)(10 + hex - 'a');
            }
            else if (hex >= 'A' && hex <= 'F')
            {
              codepoint |= (unsigned int)(10 + hex - 'A');
            }
            else
            {
              return false;
            }
          }
          if (codepoint <= 0x7F)
          {
            out.push_back((char)codepoint);
          }
          else if (codepoint <= 0x7FF)
          {
            out.push_back((char)(0xC0 | ((codepoint >> 6) & 0x1F)));
            out.push_back((char)(0x80 | (codepoint & 0x3F)));
          }
          else
          {
            out.push_back((char)(0xE0 | ((codepoint >> 12) & 0x0F)));
            out.push_back((char)(0x80 | ((codepoint >> 6) & 0x3F)));
            out.push_back((char)(0x80 | (codepoint & 0x3F)));
          }
          break;
        }
        default:
          out.push_back(escape); // " \\ / and anything else stands for itself
          break;
        }
      }
      return false;
    }

    // Moves past one JSON value. A container is followed by its brackets, and
    // the strings inside it are skipped as strings so a `}` in a name cannot
    // close it early -- that is what makes skipping a nested object enough to
    // ignore whatever `text` fields it holds.
    bool skip_json_value(const std::string &text, size_t &pos)
    {
      if (pos >= text.size())
      {
        return false;
      }
      const char first = text[pos];
      if (first == '"')
      {
        std::string ignored;
        return read_json_string(text, pos, ignored);
      }
      if (first == '{' || first == '[')
      {
        int depth = 0;
        while (pos < text.size())
        {
          const char c = text[pos];
          if (c == '"')
          {
            std::string ignored;
            if (!read_json_string(text, pos, ignored))
            {
              return false;
            }
            continue;
          }
          if (c == '{' || c == '[')
          {
            depth++;
          }
          else if (c == '}' || c == ']')
          {
            depth--;
            if (depth <= 0)
            {
              pos++;
              return true;
            }
          }
          pos++;
        }
        return false;
      }
      // A bare scalar (number, true, false, null) runs to the next structural
      // character.
      while (pos < text.size() && text[pos] != ',' && text[pos] != '}')
      {
        pos++;
      }
      return true;
    }

    std::string format_epoch_seconds(long long time_ms)
    {
      char buf[32];
      std::snprintf(buf, sizeof(buf), "%lld.%03lld", time_ms / 1000, time_ms % 1000);
      return buf;
    }
  } // namespace

  bool exit_is_offline(int exit_code)
  {
    return exit_code == 102 || exit_code == 112;
  }

  bool should_send(long long now_ms,
                   long long last_sent_ms,
                   const std::string &entity,
                   const std::string &last_entity,
                   bool is_write)
  {
    if (entity.empty())
    {
      return false;
    }
    if (is_write)
    {
      return true;
    }
    if (entity != last_entity)
    {
      return true;
    }
    return now_ms - last_sent_ms >= kHeartbeatRateLimitMs;
  }

  std::vector<std::string> heartbeat_args(const Heartbeat &heartbeat, const Options &options)
  {
    std::vector<std::string> args;
    if (heartbeat.entity.empty())
    {
      return args;
    }
    args.push_back("--entity");
    args.push_back(shell_util::shell_quote(heartbeat.entity));
    if (heartbeat.time_ms > 0)
    {
      args.push_back("--time");
      args.push_back(format_epoch_seconds(heartbeat.time_ms));
    }
    if (!options.plugin.empty())
    {
      args.push_back("--plugin");
      args.push_back(shell_util::shell_quote(options.plugin));
    }
    // Zero means "not known here" for all three, which is the cli's cue to
    // detect them itself rather than being told a wrong number.
    if (heartbeat.lineno > 0)
    {
      args.push_back("--lineno");
      args.push_back(std::to_string(heartbeat.lineno));
    }
    if (heartbeat.cursorpos > 0)
    {
      args.push_back("--cursorpos");
      args.push_back(std::to_string(heartbeat.cursorpos));
    }
    if (heartbeat.lines_in_file > 0)
    {
      args.push_back("--lines-in-file");
      args.push_back(std::to_string(heartbeat.lines_in_file));
    }
    if (!options.api_key.empty())
    {
      args.push_back("--key");
      args.push_back(shell_util::shell_quote(options.api_key));
    }
    if (!options.api_url.empty())
    {
      args.push_back("--api-url");
      args.push_back(shell_util::shell_quote(options.api_url));
    }
    if (heartbeat.is_write)
    {
      args.push_back("--write");
    }
    return args;
  }

  std::vector<std::string> today_args(const Options &options)
  {
    std::vector<std::string> args;
    args.push_back("--today");
    args.push_back("--output");
    args.push_back("json");
    // The answer lands on a status bar the user is watching, so it does not
    // get the cli's 120 second patience with a network that is not answering.
    args.push_back("--timeout");
    args.push_back("15");
    if (!options.plugin.empty())
    {
      args.push_back("--plugin");
      args.push_back(shell_util::shell_quote(options.plugin));
    }
    if (!options.api_key.empty())
    {
      args.push_back("--key");
      args.push_back(shell_util::shell_quote(options.api_key));
    }
    if (!options.api_url.empty())
    {
      args.push_back("--api-url");
      args.push_back(shell_util::shell_quote(options.api_url));
    }
    return args;
  }

  std::string join_args(const std::vector<std::string> &args)
  {
    std::string line;
    for (const std::string &arg : args)
    {
      if (!line.empty())
      {
        line += ' ';
      }
      line += arg;
    }
    return line;
  }

  std::string today_text(const std::string &cli_stdout)
  {
    const size_t root = cli_stdout.find('{');
    if (root == std::string::npos)
    {
      return "";
    }
    size_t pos = root + 1;
    while (pos < cli_stdout.size())
    {
      while (pos < cli_stdout.size()
             && (string_util::is_space(cli_stdout[pos]) || cli_stdout[pos] == ','))
      {
        pos++;
      }
      if (pos >= cli_stdout.size() || cli_stdout[pos] == '}')
      {
        return "";
      }
      std::string key;
      if (!read_json_string(cli_stdout, pos, key))
      {
        return "";
      }
      while (pos < cli_stdout.size() && string_util::is_space(cli_stdout[pos]))
      {
        pos++;
      }
      if (pos >= cli_stdout.size() || cli_stdout[pos] != ':')
      {
        return "";
      }
      pos++;
      while (pos < cli_stdout.size() && string_util::is_space(cli_stdout[pos]))
      {
        pos++;
      }
      if (key == "text")
      {
        std::string value;
        if (!read_json_string(cli_stdout, pos, value))
        {
          return "";
        }
        return string_util::trim_copy(value);
      }
      if (!skip_json_value(cli_stdout, pos))
      {
        return "";
      }
    }
    return "";
  }

  std::string activity_label(const std::string &today, const std::string &fallback)
  {
    return today.empty() ? fallback : today;
  }

  std::string cfg_api_key(const std::string &cfg_text)
  {
    bool in_settings = false;
    for (std::string_view raw : string_util::lines(cfg_text))
    {
      const std::string_view line = string_util::trim_view(raw);
      if (line.empty() || line.front() == '#' || line.front() == ';')
      {
        continue;
      }
      if (line.front() == '[')
      {
        // Only [settings] is read: it is where the cli keeps api_key, and a
        // key under another section is not the one it would use either.
        in_settings = string_util::trim_copy(line) == "[settings]";
        continue;
      }
      if (!in_settings)
      {
        continue;
      }
      const size_t equals = line.find('=');
      if (equals == std::string_view::npos)
      {
        continue;
      }
      if (string_util::trim_view(line.substr(0, equals)) != "api_key")
      {
        continue;
      }
      return string_util::trim_copy(line.substr(equals + 1));
    }
    return "";
  }

  std::string cfg_path()
  {
    std::string home;
    if (const char *wakatime_home = std::getenv("WAKATIME_HOME"); wakatime_home && *wakatime_home)
    {
      home = wakatime_home;
    }
    else if (const char *env_home = std::getenv("HOME"); env_home && *env_home)
    {
      home = env_home;
    }
#ifdef _WIN32
    else if (const char *profile = std::getenv("USERPROFILE"); profile && *profile)
    {
      home = profile;
    }
#endif
    if (home.empty())
    {
      return "";
    }
    return (std::filesystem::path(home) / ".wakatime.cfg").string();
  }
} // namespace jot_wakatime
