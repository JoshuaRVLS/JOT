#ifndef UI_INPUT_READER_H
#define UI_INPUT_READER_H

#include <chrono>
#include <cstddef>
#include <deque>
#include <string>

// The one reader of the input fd. A sequence whose head a reader consumes must
// come back for its tail; abandon() finishes the tail if it has arrived and
// claims it if it has not, every read settles the claim first, and a byte that
// was read but not claimed is handed back rather than lost. That is what stops
// the tail of a reply ("191R", ";12;3M") from being read back as typing.
//
// Kept free of Terminal so it is unit testable against a pipe.

namespace jot_ui
{

// The grammar of a tail still owed, so the bytes skipped are provably the
// sequence the head belonged to and never a keystroke that raced it.
enum class OwedTail
{
  CsiBody,     // parameter and intermediate bytes, then any final byte
  FinalByte,   // a single final byte: the last byte of an SS3 sequence
  MouseReport, // digits, `;` and `?`, then `M` or `m`: an SGR mouse report
};

class InputReader
{
public:
  // How long abandon() waits for a tail that may already be queued, and how
  // long the claim it leaves behind stays open when it is not.
  static constexpr int kTailPerByteMs = 20;
  static constexpr int kTailBudgetMs = 50;
  static constexpr int kClaimMs = 1000;

  // `claim_ms` is how long a claim outlives the drain; the default is the
  // editor's, and a test passes something short instead of waiting it out.
  explicit InputReader(int fd, int claim_ms = kClaimMs);

  // The next byte: one handed back, else one off the fd. A negative timeout
  // does not wait, and a handed-back byte never waits.
  bool read(char &out, int timeout_ms);

  // Gives bytes back: the next read takes them, in this order, ahead of the fd.
  // This is what lets a reader that has to pull bytes it cannot yet judge put
  // them back instead of dropping them.
  void unread(char c);
  void unread(const char *bytes, std::size_t len);

  // Whether bytes are waiting to be handed back.
  bool pending() const
  {
    return !pending_.empty();
  }

  // Eats the rest of a sequence whose head was consumed and dropped.
  void abandon(OwedTail owed, int per_byte_ms = kTailPerByteMs,
               int budget_ms = kTailBudgetMs);

  // The same, for a tail with a fixed shape rather than a grammar, such as the
  // `~` of a bracketed paste's terminator after its body stopped.
  void claim_remaining(const char *bytes, std::size_t consumed, std::size_t len);

  // Finishes a claimed tail with what has arrived: eats the bytes that belong
  // to it, hands back the first one that does not, and drops the claim at its
  // deadline. Called before every read.
  void settle();

  // Reads a bracketed paste body up to, and including, its terminator `ESC [
  // 201 ~`. A body that stops before its terminator keeps what it did send and
  // has that terminator's remaining bytes claimed; false means no body at all.
  bool read_paste(std::string &out);

  bool claiming() const
  {
    return claim_ != Claim::None;
  }

private:
  enum class Claim
  {
    None,
    Grammar,
    Fixed,
  };

  bool accepts(char c, bool &is_final);
  void open_claim();

  // More than any caller hands back at once: the size probe returns at most
  // its own 32-byte buffer, and a drain or a claim returns one byte.
  static constexpr std::size_t kPendingMax = 256;

  int fd_;
  int claim_ms_;
  std::deque<char> pending_;
  Claim claim_ = Claim::None;
  OwedTail owed_ = OwedTail::CsiBody;
  char fixed_[8] = {};
  std::size_t fixed_len_ = 0;
  std::size_t fixed_at_ = 0;
  std::chrono::steady_clock::time_point deadline_{};
};

} // namespace jot_ui

#endif
