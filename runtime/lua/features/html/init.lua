-- HTML preview - the file being edited opened in a browser, served from its own
-- tree so its relative stylesheets, scripts and images resolve, and reloaded
-- when the buffer changes.
--
-- Commands:
--   :HtmlPreview         start (or re-target) the preview
--   :HtmlPreviewStop     stop it and close the server
--   :HtmlPreviewToggle   toggle
--
-- Lua API (`jot.html_preview`):
--   jot.html_preview.setup{ auto_start = true, root = "dir" }
--   jot.html_preview.start(), stop(), toggle(), refresh()
--   jot.html_preview.is_running(), url(), state()
--
-- Every scalar option is stored under `html_preview_*` in the normal config
-- store (features/html/config.lua), so `:settings`, `config.lua` and setup{}
-- all edit the same values and take effect immediately.
local config = require("jot_html.config")
local preview = require("jot_html.preview")

command("HtmlPreview", function()
  preview.start()
end, "Preview the current HTML file in a browser")

command("HtmlPreviewStop", function()
  preview.stop()
end, "Stop the HTML preview")

command("HtmlPreviewToggle", function()
  preview.toggle()
end, "Toggle the HTML preview")

autocmd("BufChange", function(event)
  preview.on_buffer_change(event)
end)

autocmd("BufSave", function(event)
  preview.on_buffer_save(event)
end)

autocmd("BufOpen", function(event)
  preview.on_buffer_open(event)
end)

autocmd("BufClose", function(event)
  preview.on_buffer_close(event)
end)

-- The Lua-facing namespace, next to jot.md.
jot.html_preview = {
  setup = config.setup,
  start = preview.start,
  stop = preview.stop,
  toggle = preview.toggle,
  refresh = preview.refresh,
  is_running = preview.is_running,
  url = preview.url,
  state = preview.state,
}

-- Status line segment: a marker while a preview is live, so the served file is
-- visible even when the browser window is behind the terminal.
pcall(function()
  jot.status.register("html_preview", {
    side = "right",
    priority = 25,
    text = function()
      if not preview.is_running() then
        return ""
      end
      return " html"
    end,
  })
end)
