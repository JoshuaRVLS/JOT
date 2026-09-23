#include "ui/input_reader.h"

#include <algorithm>
#include <poll.h>
#include <unistd.h>

namespace jot_ui
{
namespace
{
// A bracketed paste's terminator. A paste body is opaque: how long it is can
// only be known from its terminator, so these bytes are matched one at a time
// and anything that does not carry the match on is body text.
const char kPasteEnd[] = "\x1b[201~";
constexpr int kPasteBodyTimeoutMs = 1000;
constexpr int kPasteEndTimeoutMs = 20;
} // namespace

InputReader::InputReader(int fd, int claim_ms) : fd_(fd), claim_ms_(claim_ms) {}

bool InputReader::read(char &out, int timeout_ms)
{
  if (!pending_.empty())
  {
    out = pending_.front();
    pending_.pop_front();
    return true;
  }

  if (timeout_ms >= 0)
  {
    struct pollfd pfd;
    pfd.fd = fd_;
    pfd.events = POLLIN;
    pfd.revents = 0;
    const int ready = poll(&pfd, 1, timeout_ms);
    if (ready <= 0 || !(pfd.revents & POLLIN))
      return false;
  }
  return ::read(fd_, &out, 1) == 1;
}

void InputReader::unread(char c)
{
  if (pending_.size() >= kPendingMax)
    return;
  pending_.push_front(c);
}

void InputReader::unread(const char *bytes, std::size_t len)
{
  if (pending_.size() + len > kPendingMax)
    return;
  for (std::size_t i = len; i-- > 0;)
    pending_.push_front(bytes[i]);
}

bool InputReader::accepts(char c, bool &is_final)
{
  const unsigned char u = static_cast<unsigned char>(c);
  is_final = false;

  if (claim_ == Claim::Fixed)
  {
    if (fixed_at_ >= fixed_len_ || c != fixed_[fixed_at_])
      return false;
    fixed_at_++;
    is_final = fixed_at_ >= fixed_len_;
    return true;
  }

  if (owed_ == OwedTail::MouseReport)
  {
    // SGR is `ESC [ < buttons ; x ; y M|m`; the `<` was the head's last byte.
    if ((u >= '0' && u <= '9') || u == ';' || u == '?')
      return true;
    if (u != 'M' && u != 'm')
      return false;
    is_final = true;
    return true;
  }

  if (owed_ == OwedTail::FinalByte)
  {
    if (u < 0x40 || u > 0x7e)
      return false;
    is_final = true;
    return true;
  }

  if (u >= 0x20 && u <= 0x3f)
    return true;
  if (u < 0x40 || u > 0x7e)
    return false;
  is_final = true;
  return true;
}

void InputReader::open_claim()
{
  claim_ = claim_ == Claim::Fixed ? Claim::Fixed : Claim::Grammar;
  deadline_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(claim_ms_);
}

void InputReader::abandon(OwedTail owed, int per_byte_ms, int budget_ms)
{
  owed_ = owed;
  claim_ = Claim::Grammar;
  fixed_len_ = 0;
  fixed_at_ = 0;

  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(budget_ms);
  char c = 0;
  bool is_final = false;
  for (int i = 0; i < 64; i++)
  {
    const auto now = std::chrono::steady_clock::now();
    if (now >= deadline)
      break;
    const int remain =
        (int)std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count();
    if (!read(c, std::min(per_byte_ms, std::max(1, remain))))
      break;
    if (!accepts(c, is_final))
    {
      unread(c);
      break;
    }
    if (is_final)
    {
      claim_ = Claim::None; // the tail was there after all
      return;
    }
  }
  open_claim();
}

void InputReader::claim_remaining(const char *bytes, std::size_t consumed, std::size_t len)
{
  fixed_len_ = std::min(len, sizeof(fixed_));
  fixed_at_ = std::min(consumed, fixed_len_);
  for (std::size_t i = 0; i < fixed_len_; i++)
    fixed_[i] = bytes[i];
  claim_ = Claim::Fixed;
  open_claim();
}

bool InputReader::read_paste(std::string &out)
{
  out.clear();
  bool any_body = false;
  std::size_t matched = 0;
  while (matched < sizeof(kPasteEnd) - 1)
  {
    char c = 0;
    if (!read(c, matched == 0 ? kPasteBodyTimeoutMs : kPasteEndTimeoutMs))
    {
      // The body stopped before its terminator arrived. Keep what it did send,
      // it is the user's paste, and claim the terminator's remaining bytes,
      // which are still owed and must not be read back as typing.
      if (matched > 0)
        claim_remaining(kPasteEnd, matched, sizeof(kPasteEnd) - 1);
      return any_body;
    }
    if (c == kPasteEnd[matched])
    {
      matched++;
      continue;
    }
    out.append(kPasteEnd, matched);
    out.push_back(c);
    any_body = true;
    matched = 0;
  }
  return true;
}

void InputReader::settle()
{
  char c = 0;
  bool is_final = false;
  while (claim_ != Claim::None)
  {
    if (std::chrono::steady_clock::now() >= deadline_)
    {
      claim_ = Claim::None;
      return;
    }
    if (!read(c, kTailPerByteMs))
      return; // nothing has arrived yet; the claim stays open
    if (!accepts(c, is_final))
    {
      unread(c); // someone's keystroke: still owed, so keep the claim
      return;
    }
    if (is_final)
      claim_ = Claim::None;
  }
}

} // namespace jot_ui
