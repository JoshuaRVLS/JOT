#ifndef SAVE_HYGIENE_H
#define SAVE_HYGIENE_H

#include <string>

// What a save cleans up on its way to the disk, and what it has to leave
// alone. The rules live here, free of the buffer and of the filesystem, so they
// can be asserted directly; file.cpp asks and does the writing.
class SaveHygiene
{
public:
  // Whether `line` ends in a space or a tab, so a save has something to drop.
  static bool has_trailing_whitespace(const std::string &line);

  // `line` without its trailing spaces and tabs. A line that is nothing but
  // whitespace comes back empty, which is the blank line it meant to be.
  static std::string trim_trailing_whitespace(const std::string &line);

  // Whether trailing whitespace in this file is content rather than stray
  // blanks. Markdown reads two of them as a hard line break, so a save has to
  // leave those exactly as they are; `:trim` still cleans them on request.
  static bool preserves_trailing_whitespace(const std::string &path);
};

#endif
