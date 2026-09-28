// The statusline's coding-time chip and the two sources behind it (see
// jot/editor/api/coding_time.h for the shape and features/coding_time.h /
// features/wakatime.h for the two pure halves).
//
// The two runs that leave this module are opposites, which is what the comments
// below keep repeating:
//
//   * a heartbeat is fire and forget. Nothing reads its output, so the spawn is
//     what goes on the worker queue -- the fork is the only part worth keeping
//     off the keystroke path -- and the shell backgrounds the cli, whose own log
//     file is where its complaints go.
//   * today's total is a value on screen, so it is read back: the cli's stdout
//     is captured on the worker queue and landed on the main thread, the same
//     shape as the REST client (jot/app/rest_client.cpp).
//   * the local store writes a file, so it is written on a timer of its own
//     rather than per keystroke, and flushed for good when the editor goes away.
#include "features/coding_time.h"
#include "editor.h"
#include "features/status_clock.h"
#include "features/wakatime.h"
#include "tools/shell_util.h"

#include <chrono>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <system_error>
#include <utility>

namespace
{
  // How stale today's total may get before it is asked for again, and how often
  // the local store is written out. A minute each: the same minute
  // vscode-wakatime refreshes its status bar at, and one small file write a
  // minute at worst while the user is actually coding.
  constexpr long long kTodayRefreshMs = 60000;
  constexpr long long kFlushIntervalMs = 60000;

  // A shell command's whole stdout, for the today run below: the cli may still
  // be printing when a short read would otherwise come back empty.
  std::string capture_command(const std::string &command)
  {
    std::string out;
    FILE *pipe = shell_util::open_command_pipe(command, "r");
    if (!pipe)
    {
      return out;
    }
    char chunk[8192];
    size_t read = 0;
    while ((read = fread(chunk, 1, sizeof(chunk), pipe)) > 0)
    {
      out.append(chunk, read);
    }
    shell_util::close_command_pipe(pipe);
    return out;
  }

  // Starts `command` and returns at once, for a heartbeat. The trailing `&`
  // (POSIX) lets popen's shell exit while the cli keeps running, and the
  // detached redirect keeps the child from holding our stdout open -- without it
  // a harness reading that pipe waits for a process that is still talking to the
  // API.
  void spawn_detached(const std::string &command)
  {
#ifdef _WIN32
    const std::string full = "start /B " + command + shell_util::detached_redirect();
#else
    const std::string full = command + shell_util::detached_redirect() + " &";
#endif
    FILE *pipe = shell_util::open_command_pipe(full, "r");
    if (pipe)
    {
      shell_util::close_command_pipe(pipe);
    }
  }

  std::string read_file_text(const std::string &path)
  {
    std::ifstream file(path);
    if (!file)
    {
      return "";
    }
    return std::string((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
  }

  long long wall_clock_ms()
  {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
  }
} // namespace

void Editor::note_editing_activity(bool is_write)
{
  const long long now_ms = wall_clock_ms();
  coding_time_tick(now_ms, true);
  wakatime_heartbeat(is_write, now_ms);
}

// ---------------------------------------------------------------------------
// WakaTime
// ---------------------------------------------------------------------------

jot_wakatime::Options Editor::wakatime_options()
{
  jot_wakatime::Options options;
  options.api_key = config.get("wakatime_api_key", "");
  options.api_url = config.get("wakatime_api_url", "");
  // The User-Agent WakaTime shows on the plugin page. The editor has no version
  // constant of its own, so the plugin id carries the version.
  options.plugin = "jot jot-wakatime/1.0.0";
  return options;
}

std::string Editor::wakatime_command(const std::vector<std::string> &args) const
{
  return "wakatime-cli " + jot_wakatime::join_args(args);
}

void Editor::sync_wakatime()
{
  const bool wanted = config.get_bool("wakatime", false);
  if (wanted != wakatime_enabled)
  {
    wakatime_enabled = wanted;
    // Turning it off forgets what the cli said, so turning it back on starts
    // from the local total rather than a WakaTime number from whenever it was
    // last on.
    wakatime_today_text.clear();
    wakatime_today_checked_ms = 0;
    wakatime_today_running = false;
    wakatime_last_sent_ms = 0;
    wakatime_last_entity.clear();
    wakatime_probed = false;
    wakatime_probe_key.clear();
    wakatime_cli_ready = false;
    needs_redraw = true;
  }
  if (!wakatime_enabled || !task_queue_)
  {
    return;
  }

  // The check is a PATH lookup and a config read, so it runs on the worker
  // queue, once -- and again if the key setting changes, so a key typed into
  // :settings starts tracking without a restart.
  const std::string configured_key = config.get("wakatime_api_key", "");
  if (wakatime_probed && wakatime_probe_key == configured_key)
  {
    return;
  }
  wakatime_probed = true;
  wakatime_probe_key = configured_key;

  task_queue_->submit_val<std::string>(
      [configured_key]() -> std::string
      {
        if (!shell_util::command_exists("wakatime-cli"))
        {
          return "WakaTime: wakatime-cli not found on PATH";
        }
        // No key here is fine as long as the cli has one of its own: that is
        // the whole point of driving it, so an existing WakaTime setup keeps
        // working with no JOT setting at all.
        if (!configured_key.empty())
        {
          return "";
        }
        if (jot_wakatime::cfg_api_key(read_file_text(jot_wakatime::cfg_path())).empty())
        {
          return "WakaTime: no API key (set wakatime_api_key)";
        }
        return "";
      },
      [this](std::string report)
      {
        if (!running)
        {
          return;
        }
        wakatime_cli_ready = report.empty();
        if (!report.empty())
        {
          set_message(report, true);
        }
        needs_redraw = true;
      });
}

void Editor::wakatime_heartbeat(bool is_write, long long now_ms)
{
  if (!wakatime_enabled || !wakatime_cli_ready || !task_queue_)
  {
    return;
  }
  FileBuffer &buf = get_buffer();
  // Nothing behind the name means nothing to report: an unsaved scratch buffer,
  // a rendered view ([Response], [Chat]), and an image tab, whose buffer holds a
  // placeholder rather than the file's text.
  if (buf.filepath.empty() || is_rendered_view_path(buf.filepath)
      || image_viewer.is_image_file(buf.filepath))
  {
    return;
  }

  std::error_code error;
  // The cli wants an absolute path, and asks for it rather than resolving one
  // itself so it cannot disagree with what is in the buffer.
  const std::string entity = std::filesystem::absolute(std::filesystem::path(buf.filepath), error)
                                 .lexically_normal()
                                 .string();
  if (error)
  {
    return;
  }
  if (!jot_wakatime::should_send(
          now_ms, wakatime_last_sent_ms, entity, wakatime_last_entity, is_write))
  {
    return;
  }
  // Recorded before the spawn, not after: the rule is about the heartbeats
  // actually sent, and a spawn that fails must not let every keystroke past it.
  wakatime_last_sent_ms = now_ms;
  wakatime_last_entity = entity;

  jot_wakatime::Heartbeat heartbeat;
  heartbeat.entity = entity;
  heartbeat.time_ms = now_ms;
  heartbeat.is_write = is_write;
  heartbeat.lineno = buf.cursor.y + 1;
  heartbeat.cursorpos = buf.cursor.x + 1;
  heartbeat.lines_in_file = (int)buf.line_count();

  const std::string command =
      wakatime_command(jot_wakatime::heartbeat_args(heartbeat, wakatime_options()));
  task_queue_->submit([command]() { spawn_detached(command); }, nullptr);
}

void Editor::wakatime_poll_today()
{
  if (!wakatime_enabled || !wakatime_cli_ready || !task_queue_ || wakatime_today_running)
  {
    return;
  }
  const long long now_ms = wall_clock_ms();
  if (wakatime_today_checked_ms != 0 && now_ms - wakatime_today_checked_ms < kTodayRefreshMs)
  {
    return;
  }
  // Asked for before the answer arrives, so a slow or unanswered request cannot
  // turn the once-a-minute poll into one spawn per frame.
  wakatime_today_checked_ms = now_ms;
  wakatime_today_running = true;

  const std::string command = wakatime_command(jot_wakatime::today_args(wakatime_options()));
  task_queue_->submit_val<std::string>([command]() { return capture_command(command); },
                                       [this](std::string output)
                                       {
                                         wakatime_today_running = false;
                                         if (!running)
                                         {
                                           return;
                                         }
                                         // Empty is the honest answer when the cli is offline or
                                         // refused: the chip falls back to the local total rather
                                         // than showing a zero the user never earned.
                                         const std::string text = jot_wakatime::today_text(output);
                                         if (text == wakatime_today_text)
                                         {
                                           return;
                                         }
                                         wakatime_today_text = text;
                                         needs_redraw = true;
                                       });
}

// ---------------------------------------------------------------------------
// The local store and the chip
// ---------------------------------------------------------------------------

std::string Editor::coding_time_current_key() const
{
  return coding_time::key_for(root_dir, coding_time::local_date(std::time(nullptr)));
}

void Editor::coding_time_tick(long long now_ms, bool activity)
{
  const std::string key = coding_time_current_key();
  if (key != coding_time_key)
  {
    // A workspace switch or a day boundary. What the previous key earned is
    // folded in and written out before the counter is pointed at the new one,
    // so nothing is carried over and nothing is lost.
    if (!coding_time_key.empty())
    {
      coding_time.advance(now_ms);
      coding_time::set_total(coding_time_totals, coding_time_key, coding_time.total_ms);
      flush_coding_time(now_ms, true);
    }
    coding_time_key = key;
    coding_time.total_ms = coding_time::total_for(coding_time_totals, key);
    coding_time.last_activity_ms = now_ms;
    coding_time.last_credit_ms = now_ms;
    return;
  }

  const long long before = coding_time.total_ms;
  if (activity)
  {
    coding_time.mark_activity(now_ms);
  }
  else
  {
    coding_time.advance(now_ms);
  }
  if (coding_time.total_ms == before)
  {
    // Nothing was credited (the user is idle past the cap), so there is nothing
    // new worth writing out.
    return;
  }
  coding_time_dirty = true;
  flush_coding_time(now_ms, false);
}

void Editor::flush_coding_time(long long now_ms, bool force)
{
  if (!coding_time_dirty && !force)
  {
    return;
  }
  if (!force && coding_time_flushed_ms != 0 && now_ms - coding_time_flushed_ms < kFlushIntervalMs)
  {
    return;
  }
  if (!coding_time_key.empty())
  {
    // The live total, not the committed one: the window since the last tick is
    // real time the user spent here, and a machine that goes down should not
    // lose it.
    coding_time::set_total(coding_time_totals, coding_time_key, coding_time.live_ms(now_ms));
  }
  coding_time::save_totals(coding_time::store_path(), coding_time_totals);
  coding_time_dirty = false;
  coding_time_flushed_ms = now_ms;
}

std::string Editor::status_coding_label(long long now_ms)
{
  if (wakatime_enabled && !wakatime_today_text.empty())
  {
    return wakatime_today_text;
  }
  return status_clock::format_duration(coding_time.live_ms(now_ms));
}
