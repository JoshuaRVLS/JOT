local config = require("jot_lc.config")
local ui = require("jot_lc.ui")

local function key(chord, callback, detail)
  jot.keymap.set(chord, callback, detail)
end

local commands = {
  {"Leet", ui.actions, "Open the LeetCode workspace"},
  {"LeetList", ui.list, "Browse LeetCode problems"},
  {"LeetDaily", ui.daily, "Open today's LeetCode problem"},
  {"LeetRandom", ui.random, "Open a random LeetCode problem"},
  {"LeetProfile", ui.profile, "Show LeetCode profile statistics"},
  {"LeetCookie", ui.open_cookie_prompt, "Store a session in the OS credential store"},
  {"LeetSignOut", ui.sign_out, "Remove the LeetCode session"},
  {"LeetRun", function() ui.run("run") end, "Run the selected example"},
  {"LeetTest", function() ui.next_case() end, "Select the next example"},
  {"LeetSubmit", function() ui.run("submit") end, "Submit the active solution"},
  {"LeetReset", ui.reset, "Restore the platform solution template"},
  {"LeetLang", ui.change_language, "Change language for the active problem"},
  {"LeetDesc", ui.description, "Show the active problem statement"},
  {"LeetLatest", ui.restore_latest, "Restore the latest accepted submission"},
  {"LeetOpen", ui.open_browser, "Open the active problem in a browser"},
  {"LeetStatus", ui.status, "Show LeetCode workspace status"},
  {"LeetActions", ui.actions, "Open LeetCode actions"},
  {"LeetCacheClear", function() ui.notify("Removed " .. require("jot_lc.cache").clear() .. " cached question(s)") end, "Clear cached public problem data"},
  {"LeetSetup", function(value)
    local name, setting = tostring(value or ""):match("^([%w_]+)=(.*)$")
    if not name then ui.notify("Use :LeetSetup name=value"); return end
    local parsed = tonumber(setting) or setting
    ui.setup({[name]=parsed})
    ui.notify("Set " .. name)
  end, "Configure one LeetCode option as name=value"},
}

for _, entry in ipairs(commands) do
  jot.command(entry[1], entry[2], entry[3])
end

key("Alt+Shift+L", "", "LeetCode")
key("Alt+Shift+L L", ui.list, "LeetCode: problem list")
key("Alt+Shift+L D", ui.daily, "LeetCode: daily problem")
key("Alt+Shift+L R", ui.random, "LeetCode: random problem")
key("Alt+Shift+L A", ui.actions, "LeetCode: actions")
key("Alt+Shift+L T", function() ui.next_case() end, "LeetCode: next example")
key("Alt+Shift+L S", function() ui.run("submit") end, "LeetCode: submit")

jot.leetcode_feature = {
  setup = ui.setup,
  actions = ui.actions,
  list = ui.list,
  daily = ui.daily,
  random = ui.random,
  status = ui.status,
  active_question = function() return ui.current end,
  client = require("jot_lc.client"),
  cache = require("jot_lc.cache"),
  config = config,
}

return true
