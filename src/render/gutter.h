#ifndef RENDER_GUTTER_H
#define RENDER_GUTTER_H

// How wide the line-number gutter is.
//
// The number is left-aligned at the gutter's first cell and one space follows
// it, and the field is exactly as wide as the file's largest line number: the
// digits sit against the pane's left edge with nothing in front of them, and
// the code starts one cell after the longest number the file can show. A file
// that is five lines long therefore has a two-cell gutter, and a file that
// crosses a thousand gets one more cell - the code column shifts, the numbers
// do not move.
//
// This is one number in one place on purpose: the buffer renderer draws against
// it, frame.cpp places the caret with it, the completion and signature popups
// hang off it, and the mouse and the LSP hover map a screen column back to a
// buffer column with it. Each of those used to carry its own `7`, which is the
// kind of constant that stays right until the first time it changes.

#include <cstddef>

namespace gutter
{
  // Cells the gutter occupies in a buffer of `line_count` lines: the digits of
  // that count, plus the space before the code.
  inline int width(std::size_t line_count)
  {
    int digits = 1;
    for (std::size_t rest = line_count; rest >= 10; rest /= 10)
    {
      ++digits;
    }
    return digits + 1;
  }
} // namespace gutter

#endif
