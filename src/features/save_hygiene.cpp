#include "save_hygiene.h"

#include <algorithm>
#include <cctype>

namespace
{
// The extensions where a line ending in spaces is a line break rather than an
// accident: markdown is the one format a stray blank is not stray in.
bool is_line_break_sensitive(const std::string &extension)
{
  return extension == ".md" || extension == ".markdown" || extension == ".mdown"
         || extension == ".mkd" || extension == ".mdx";
}

// The extension of a path, lowercased, or empty when it has none. A dot in a
// directory name (`~/.config/jot/a`) is not an extension.
std::string extension_of(const std::string &path)
{
  const size_t slash = path.find_last_of("/\\");
  const size_t dot = path.find_last_of('.');
  if (dot == std::string::npos || (slash != std::string::npos && dot < slash))
  {
    return {};
  }
  std::string extension = path.substr(dot);
  std::transform(extension.begin(),
                 extension.end(),
                 extension.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return extension;
}
} // namespace

bool SaveHygiene::has_trailing_whitespace(const std::string &line)
{
  return !line.empty() && (line.back() == ' ' || line.back() == '\t');
}

std::string SaveHygiene::trim_trailing_whitespace(const std::string &line)
{
  size_t end = line.size();
  while (end > 0 && (line[end - 1] == ' ' || line[end - 1] == '\t'))
  {
    end--;
  }
  return line.substr(0, end);
}

bool SaveHygiene::preserves_trailing_whitespace(const std::string &path)
{
  return is_line_break_sensitive(extension_of(path));
}
