// ---------------------------------------------------------------------------
// C++ definition checks
// ---------------------------------------------------------------------------
//
// The workspace scan behind `:cppcheck` and the Problems list: every header and
// source in the workspace is parsed for function signatures, and a declaration
// in a header with no body anywhere -- or a signature with more than one body --
// becomes a diagnostic (see features/cpp_definitions.h for the rules).
//
// The scan runs on the worker thread, once per workspace (on open, on save, and
// when asked); its result lands as one diagnostics slice per file, which the LSP
// merge and every diagnostics surface then read exactly like a server's.
//
// A fragment of the Editor class body, included by src/jot/editor.h. It is not
// a standalone header: no include guard, no includes, and the members sit in
// class scope exactly as if they were written in editor.h.
private:
  // Queues a workspace scan. A request that arrives while one is in flight only
  // marks another as wanted: the scan that lands runs it, so rapid saves do not
  // stack up scans. `announce` is the `:cppcheck` path -- the result is shown
  // (the Problems list opens on its findings, the summary arrives as a toast,
  // and the caret lands on the next finding) rather than only published.
  void request_cpp_definitions_scan(bool announce = false);
  // Landed a scan's result: rebuild the per-file diagnostics and refresh every
  // file whose diagnostics changed, open buffer or not.
  void apply_cpp_definitions(CppDefinitions::ScanResult result);
  // Drops every definition diagnostic (the `cpp_definitions` setting went off).
  void clear_cpp_definitions();
  // The status line's one-line summary of the last scan.
  std::string cpp_definitions_summary() const;
