// The input reader (src/ui/input_reader.*), which every read of the tty now
// goes through so a sequence whose head was consumed cannot leave its tail to
// be read back as typing.
//
// A pipe stands in for the tty: the reader only ever needs a byte at a time,
// and a pipe lets a case control exactly when each byte arrives. The cases
// below are the whole contract -- a tail already queued is eaten, a tail that
// arrives later is claimed and eaten when it does, and a keystroke in its place
// is handed back instead of eaten.

#include "ui/input_reader.h"

#include <catch2/catch_test_macros.hpp>

#include <string>
#include <unistd.h>

namespace
{

struct Pipe
{
  int fds[2] = {-1, -1};

  Pipe()
  {
    REQUIRE(::pipe(fds) == 0);
  }

  ~Pipe()
  {
    for (int fd : fds)
    {
      if (fd >= 0)
        ::close(fd);
    }
  }

  Pipe(const Pipe &) = delete;
  Pipe &operator=(const Pipe &) = delete;

  int read_fd() const
  {
    return fds[0];
  }

  void write(const std::string &bytes) const
  {
    REQUIRE(::write(fds[1], bytes.data(), bytes.size()) == (ssize_t)bytes.size());
  }
};

// A reader with a claim window short enough to wait out in a test.
jot_ui::InputReader reader_for(const Pipe &pipe, int claim_ms = 40)
{
  return jot_ui::InputReader(pipe.read_fd(), claim_ms);
}

std::string drain(jot_ui::InputReader &input)
{
  std::string out;
  char c = 0;
  while (input.read(c, 0))
    out.push_back(c);
  return out;
}

bool has_queued(jot_ui::InputReader &input)
{
  char c = 0;
  return input.read(c, 0);
}

} // namespace

TEST_CASE("Input reader delivers a handed-back byte before the tty", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);
  pipe.write("ab");
  input.push_back('z');

  std::string typed;
  char c = 0;
  while (input.read(c, 0))
    typed.push_back(c);

  REQUIRE(typed == "zab");
}

TEST_CASE("Input reader eats a cursor reply that is already queued", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);
  pipe.write("91R");

  input.abandon(jot_ui::OwedTail::CursorPos);

  REQUIRE_FALSE(input.claiming());
  REQUIRE_FALSE(has_queued(input));
}

TEST_CASE("Input reader claims a cursor reply that has not arrived yet", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);

  input.abandon(jot_ui::OwedTail::CursorPos);
  REQUIRE(input.claiming());

  pipe.write("91R");
  input.settle();

  REQUIRE_FALSE(input.claiming());
  REQUIRE_FALSE(has_queued(input));
}

TEST_CASE("Input reader hands back a keystroke where a reply tail was owed", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);

  input.abandon(jot_ui::OwedTail::CursorPos);
  pipe.write("z");

  input.settle();
  REQUIRE(input.claiming());
  REQUIRE(drain(input) == "z");
}

TEST_CASE("Input reader stops claiming once the reply tail is owed no more", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe, 30);

  input.abandon(jot_ui::OwedTail::CursorPos);
  REQUIRE(input.claiming());

  ::usleep(60 * 1000);
  pipe.write("91Rz");
  input.settle();

  REQUIRE_FALSE(input.claiming());
  // The window is gone, so the reply that came too late is the editor's
  // problem: it is past the point where a keystroke and a reply can be told
  // apart, and eating input for longer is worse than missing one.
  REQUIRE(drain(input) == "91Rz");
}

TEST_CASE("Input reader tells a mouse report's tail from a keystroke", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);

  input.abandon(jot_ui::OwedTail::MouseReport);
  pipe.write("123;37M");
  input.settle();
  REQUIRE_FALSE(input.claiming());
  REQUIRE_FALSE(has_queued(input));

  input.abandon(jot_ui::OwedTail::MouseReport);
  pipe.write("q");
  input.settle();
  REQUIRE(input.claiming());
  REQUIRE(drain(input) == "q");
}

TEST_CASE("Input reader finishes a fixed tail such as a paste terminator", "[jot][input]")
{
  static const char kPasteEnd[] = "\x1b[201~";
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);

  // The paste body stopped after `ESC [ 201`: only the `~` is still owed.
  input.claim_remaining(kPasteEnd, 5, sizeof(kPasteEnd) - 1);
  REQUIRE(input.claiming());

  pipe.write("~");
  input.settle();

  REQUIRE_FALSE(input.claiming());
  REQUIRE_FALSE(has_queued(input));
}

TEST_CASE("Input reader's reply reads never take a handed-back byte", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);
  input.push_back('r');

  // Parsing a reply this reader asked for must not steal a keystroke that a
  // drain handed back: the tty has nothing, so neither does read_fd.
  char c = 0;
  REQUIRE_FALSE(input.read_fd(c, 0));
  REQUIRE(input.read(c, 0));
  REQUIRE(c == 'r');
}

TEST_CASE("Input reader reads a whole bracketed paste", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);
  pipe.write("hello\x1b[201~");

  std::string body;
  REQUIRE(input.read_paste(body));
  REQUIRE(body == "hello");
  REQUIRE_FALSE(has_queued(input));
}

TEST_CASE("Input reader keeps body text that looks like a paste terminator", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);
  // `ESC [ 2` is body text when the next byte does not carry the match on.
  pipe.write("a\x1b[2b\x1b[201~");

  std::string body;
  REQUIRE(input.read_paste(body));
  REQUIRE(body == "a\x1b[2b");
}

TEST_CASE("Input reader delivers a paste whose terminator is still coming", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);
  // The body arrived and stopped inside the terminator: the paste is the
  // user's text and has to land, and the `~` still owed must not be typed.
  pipe.write("hi\x1b[201");

  std::string body;
  REQUIRE(input.read_paste(body));
  REQUIRE(body == "hi");
  REQUIRE(input.claiming());

  pipe.write("~");
  input.settle();
  REQUIRE_FALSE(input.claiming());
  REQUIRE_FALSE(has_queued(input));
}

TEST_CASE("Input reader reports no paste when nothing arrived", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);

  std::string body;
  // The body's own timeout is a second; that is the cost of an empty paste.
  REQUIRE_FALSE(input.read_paste(body));
  REQUIRE(body.empty());
  REQUIRE_FALSE(input.claiming());
}

TEST_CASE("Input reader keeps an unfinished SS3 sequence's final byte", "[jot][input]")
{
  Pipe pipe;
  jot_ui::InputReader input = reader_for(pipe);

  // `ESC O` was consumed and the key's final byte is still owed; `A` is it.
  input.abandon(jot_ui::OwedTail::FinalByte);
  pipe.write("A");
  input.settle();
  REQUIRE_FALSE(input.claiming());
  REQUIRE_FALSE(has_queued(input));

  // An SS3 final byte is a letter, so a letter typed where one was owed is
  // eaten -- the cost of the shape, bounded by the claim's window. What is not
  // a final byte, such as Escape, is handed back.
  input.abandon(jot_ui::OwedTail::FinalByte);
  pipe.write("\x1b");
  input.settle();
  REQUIRE(drain(input) == "\x1b");
}
