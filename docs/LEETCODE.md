# LeetCode workspace

Jot includes a bundled, jot-native LeetCode workflow inspired by the feature set of [leetcode.nvim](https://github.com/kawre/leetcode.nvim). It uses Jot's pickers, popups, commands, keymaps and language servers. It does not embed Neovim or its NUI/plenary dependencies.

## Commands

| Command | Action |
| --- | --- |
| `:Leet` / `:LeetActions` | Open the action picker |
| `:LeetList` | Fetch and browse the algorithms problem list |
| `:LeetDaily` | Fetch today's problem |
| `:LeetRandom` | Fetch a random algorithms problem |
| `:LeetProfile` | Show solved counts for the signed-in account |
| `:LeetCookie` | Enter a session cookie in a masked prompt and store it in the OS credential store |
| `:LeetSignOut` | Remove the saved session |
| `:LeetRun` | Send the current solution and selected example to the judge |
| `:LeetTest` | Select the next example |
| `:LeetSubmit` | Submit the current solution and poll the judge |
| `:LeetReset` | Restore the selected language's platform template |
| `:LeetLang` | Choose a language and open its solution path |
| `:LeetDesc` | Show the current problem statement, topics and example count |
| `:LeetLatest` | Fetch and restore the latest submission for the active problem |
| `:LeetOpen` | Open the active problem in the configured browser or report its URL |
| `:LeetCacheClear` | Remove cached public question data |
| `:LeetStatus` | Show active question, endpoint, language and solution path |
| `:LeetSetup name=value` | Change a supported option for the current session |

Keymap family: `Alt+Shift+L`, followed by `L` for the list, `D` for daily, `R` for random, `A` for actions, `T` for the next example, or `S` to submit. The family uses Jot's which-key display.

## Configuration

```lua
jot.leetcode_feature.setup({
  endpoint = "leetcode.com",
  language = "cpp",
  solution_dir = "/path/to/solutions",
  cache_ttl = 604800,
  timeout = 20,
  list_limit = 50,
  api_base = "",
})
```

The China host can be selected with `endpoint = "leetcode.cn"`. `api_base` is intended for local fixtures and API-compatible endpoints. Session-bearing requests are restricted to the official LeetCode HTTPS domains; a custom API base never receives a saved session cookie.

Problem metadata is cached under the OS cache directory (`XDG_CACHE_HOME` on POSIX, `LOCALAPPDATA` on Windows). Cookies are not written to config, cache, request body files, shell commands or logs.

## Credential storage

`:LeetCookie` accepts a cookie header value in a masked float. The feature stores it under a Jot-specific key in the platform credential service:

- Windows Credential Manager
- Apple Keychain
- Linux Secret Service through `secret-tool` with a live D-Bus session

When the operating-system store is unavailable, sign-in reports that state and does not fall back to plaintext storage. The cookie may be either the complete cookie header string or the `LEETCODE_SESSION` value. If a `csrftoken` cookie is present in the stored header, Jot sends the matching CSRF header on authenticated requests.

## Current scope and limits

The local PTY fixture verifies the problem list and daily-question fetch against a local API fixture. Unit tests cover the JSON decoder, command registration, HTTP validation and credential-store availability. Question display, browser opening, profile requests, judge polling, cache helpers and solution-buffer setup are present, but the full online account/judge workflow has not been validated against LeetCode. In the current Linux environment the Secret Service prompt is skipped when there is no D-Bus session. The language picker opens a `two-sum.cpp` buffer, but the PTY probe has not confirmed the corresponding file exists on disk. Treat run/submit and LSP-backed solution editing as incomplete until file persistence and the judge flow pass the real-binary probe. Endpoint schemas are undocumented by LeetCode and may change.

MIT License. This feature is a separate implementation using the public API shape as reference. No leetcode.nvim source is bundled.
