// Markdown preview server: a tiny HTTP/1.1 server that serves the rendered
// markdown page to a browser, streams live updates over Server-Sent Events and
// receives scroll positions back from the page (browser -> editor sync).
//
// It is driven by the editor's existing libuv loop (EventLoop), so it adds no
// threads and no sockets of its own: every handle lives on the main loop and
// every callback runs on the main thread, which is also where Lua executes.
//
// Routes:
//   GET  /               the rendered page shell
//   GET  /events         text/event-stream (live updates + scroll sync)
//   POST /sync           body {"line":N} — the browser's top visible line
//   GET  /sync?line=N    same, for clients that cannot POST
//   GET  /image?path=..  a local image referenced by the document
//   GET  /favicon.ico    204
//
// and, when a file root is set (set_file_root), the whole tree is served by
// path instead -- see the file-root section below.
#ifndef JOT_MARKDOWN_PREVIEW_SERVER_H
#define JOT_MARKDOWN_PREVIEW_SERVER_H

#include <memory>
#include <string>

class EventLoop;
struct PreviewServerImpl;

class PreviewServer
{
public:
  explicit PreviewServer(EventLoop *loop);
  ~PreviewServer();

  PreviewServer(const PreviewServer &) = delete;
  PreviewServer &operator=(const PreviewServer &) = delete;

  // Binds and listens. `port` 0 asks the OS for a free port; the chosen port is
  // returned through `out_port`. Returns false and fills `error` on failure.
  bool start(const std::string &host, int port, int *out_port, std::string *error);
  void stop();

  bool running() const;
  int port() const;
  int client_count() const;

  // The page served at "/". Stored until replaced.
  void set_page(std::string html);
  const std::string &page() const;

  // The rendered document body. This is what the EventSource pushes as the
  // `content` event, so the page can swap its inner HTML without a reload.
  void set_content(std::string body);
  const std::string &content() const;

  // ── file root ─────────────────────────────────────────────────────────────
  //
  // Serving a whole directory instead of one stored page. This is what an HTML
  // preview needs and a rendered document does not: the page's own relative
  // references -- a sibling `.css`, an `../img/logo.png`, a module import -- have
  // to resolve against the same root they would if the file were opened
  // directly, which is only true if the server serves the tree the file lives
  // in.
  //
  // Every `GET /<path>` is then a file under that root (a request that tries to
  // climb out of it is refused), and a response whose content type is HTML gets
  // the live-reload client appended before its `</body>`, so the page reloads
  // when `notify("reload", ...)` fires instead of needing the reader to hit
  // refresh.
  void set_file_root(std::string dir);
  const std::string &file_root() const;

  // The document served in place of the file on disk, for the one path being
  // edited: the buffer's text, unsaved changes and all. Every other path under
  // the root still comes from the file system, which is what lets the preview
  // show an unsaved edit without the page's stylesheet disappearing.
  void set_document(std::string relative_path, std::string text);
  void clear_document();
  const std::string &document_path() const;

  // Broadcasts a Server-Sent Event to every connected page.
  void notify(const std::string &event, const std::string &data);

  // Editor -> preview scroll sync: broadcast the editor's top line to every
  // page, which scrolls to the matching `data-line` anchor.
  void sync_clients(int line);

  // Preview -> editor scroll sync. `push_scroll` records the newest line the
  // browser reported; `take_scroll` drains it (returns -1 when nothing new),
  // so the Lua side can apply it on a timer without a callback hop.
  void push_scroll(int line);
  int take_scroll();

private:
  std::unique_ptr<PreviewServerImpl> impl_;
};

#endif // JOT_MARKDOWN_PREVIEW_SERVER_H
