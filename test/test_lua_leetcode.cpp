#include <catch2/catch_test_macros.hpp>
#include <string>

extern "C"
{
#include <lauxlib.h>
#include <lua.h>
#include <lualib.h>
}

namespace
{
  const char *kPrelude = R"LUA(
    local dir = ...
    store, files, calls = {}, {}, {}
    float_lines, float_callbacks, float_spans = {}, {}, nil
    jot = {
      config = {
        get = function(k, d) local v=store[k]; if v==nil then return d end return v end,
        get_number = function(k, d) local v=store[k]; if v==nil then return d end return tonumber(v) end,
        set = function(k,v) store[k]=v end,
      },
      file = {
        read = function(path) return files[path] end,
        write = function(path,text) files[path]=text; return true end,
        list = function() return {} end,
        remove = function() return true end,
        open = function(path) calls.open=path end,
      },
      leetcode = {
        credential_get = function() return {ok=true, available=true, value="LEETCODE_SESSION=fake; csrftoken=csrf"} end,
        credential_set = function(value) calls.cookie=value; return {ok=true, available=true} end,
        credential_delete = function() return {ok=true, available=true} end,
        request = function(req, cb) calls.request=req; calls.callback=cb; return true end,
      },
      ui = {
        show_message=function(m) calls.message=m; calls.messages=calls.messages or {}; calls.messages[#calls.messages+1]=m end,
        popup=function(text,title) calls.popup={text=text,title=title} end,
        picker=function(title,items,cb) calls.picker={title=title,items=items,callback=cb} end,
        buffer={create=function() return 1 end, set_lines=function(_,_,_,_,lines) float_lines=lines end},
        float={open=function(buf, config)
                 -- Mirror the real binding's luaL_checktype: the float options
                 -- are argument #2 and the Neovim (buffer, enter, config)
                 -- shape must keep failing here instead of only in the editor.
                 if type(config) ~= "table" then error("jot.ui.float.open expects (buffer, config)") end
                 calls.float_open={buffer=buf, config=config}
                 return 1
               end,
               is_valid=function() return false end,
               configure=function(_,config) calls.float_configure=config end,
               on_key=function(_,fn) float_callbacks.key=fn end,
               on_paste=function(_,fn) float_callbacks.paste=fn end,
               set_lines=function(_,lines) float_lines=lines end,
               set_spans=function(_,line,spans) float_spans={line=line, spans=spans} end,
               close=function() end},
      },
      keymap={set=function(key,fn,detail) calls.keys=calls.keys or {}; calls.keys[#calls.keys+1]=key end,
              remove=function() end},
      status={register=function(name, spec) calls.status={name=name, spec=spec} end,
              unregister=function() end},
      clipboard={set=function(text) calls.clipboard=text end},
      theme={palette=function() return {} end},
      command=function(name,fn,detail) calls.commands=calls.commands or {}; calls.commands[name]=fn end,
      timer={set_timeout=function(ms,fn) calls.timer=fn; return 1 end, clear=function() end},
      shell_quote=function(value) return "'"..value.."'" end,
      viewport={info=function() return {window={width=120,height=40}} end},
    }
    package.loaded["jot_lc.json"] = assert(loadfile(dir .. "json.lua"))()
    package.loaded["jot_lc.config"] = assert(loadfile(dir .. "config.lua"))()
    package.loaded["jot_lc.cache"] = assert(loadfile(dir .. "cache.lua"))()
    package.loaded["jot_lc.client"] = assert(loadfile(dir .. "client.lua"))()
    package.loaded["jot_lc.solution"] = assert(loadfile(dir .. "solution.lua"))()
    package.loaded["jot_lc.ui"] = assert(loadfile(dir .. "ui.lua"))()
    package.loaded["jot_lc.init"] = assert(loadfile(dir .. "init.lua"))()
    feature = package.loaded["jot_lc.init"]
  )LUA";

  struct State
  {
    lua_State *L = luaL_newstate();
    State()
    {
      REQUIRE(L != nullptr);
      luaL_openlibs(L);
      const std::string dir = std::string(JOT_LUA_SOURCE_DIR) + "/features/leetcode/";
      REQUIRE(luaL_loadstring(L, kPrelude) == LUA_OK);
      lua_pushlstring(L, dir.data(), dir.size());
      if (lua_pcall(L, 1, 0, 0) != LUA_OK)
      {
        const std::string error = lua_tostring(L, -1) ? lua_tostring(L, -1) : "unknown";
        FAIL("LeetCode Lua prelude failed: " << error);
      }
    }
    ~State() { lua_close(L); }
    std::string run(const std::string &source)
    {
      REQUIRE(luaL_loadstring(L, source.c_str()) == LUA_OK);
      if (lua_pcall(L, 0, 1, 0) != LUA_OK)
      {
        const std::string error = lua_tostring(L, -1) ? lua_tostring(L, -1) : "unknown";
        FAIL("LeetCode Lua chunk failed: " << error);
      }
      std::string value = lua_isstring(L, -1) ? lua_tostring(L, -1) : "";
      lua_pop(L, 1);
      return value;
    }
  };
}

TEST_CASE("LeetCode JSON parser decodes data without evaluating it", "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run("local v=package.loaded['jot_lc.json'].decode('{\\\"data\\\":{\\\"ok\\\":true}}'); return tostring(v.data.ok)") == "true");
  REQUIRE(state.run("local v,e=package.loaded['jot_lc.json'].decode('{oops'); return tostring(v)..'|'..tostring(e)").find("invalid") != std::string::npos);
  REQUIRE(state.run("local v=package.loaded['jot_lc.json'].decode('{\\\"x\\\":1} os.execute(\\\"touch /tmp/jot-lc-json-executed\\\")'); return tostring(v)") == "nil");
}

TEST_CASE("LeetCode runtime registers a complete command family", "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run("return type(calls.commands.Leet)..'|'..type(calls.commands.LeetRun)..'|'..type(calls.commands.LeetSubmit)") == "function|function|function");
  REQUIRE(state.run("return "
                    "type(calls.commands.LeetConsole)..'|'..type(calls.commands.LeetTabs)..'|'.."
                    "type(calls.commands.LeetYank)..'|'..type(calls.commands.LeetInfo)")
          == "function|function|function|function");
  REQUIRE(state.run("return jot.leetcode_feature.config.get('endpoint')") == "leetcode.com");
  REQUIRE(state.run("return calls.status.name..'|'..calls.status.spec.side") == "leetcode|right");
}

TEST_CASE("LeetCode list pages send filters and normalize status", "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run(R"LUA(
    local ui = package.loaded['jot_lc.ui']
    local json = package.loaded['jot_lc.json']
    local body = json.encode({data={problemsetQuestionList={total=2, questions={
      {frontend_id='1', title='Easy One', title_slug='easy-one', difficulty='Easy', status='ac'},
      {frontend_id='2', title='Hard One', title_slug='hard-one', difficulty='Hard'},
    }}}})
    ui.list('difficulty=easy, status=solved')
    local live = calls.status.spec.text()
    local sent = json.decode(calls.request.body).variables
    calls.callback({ok=true, status=200, body=body, error=''})
    local items = calls.picker.items()
    return table.concat({
      tostring(live:find('loading list') ~= nil),
      tostring(sent.categorySlug == 'all-code-essentials' and sent.skip == 0 and sent.limit == 100),
      tostring(sent.filters.difficulty == 'EASY' and sent.filters.status == 'AC'),
      tostring(items[1] and items[1].value == 'easy-one'),
      tostring(items[1] and items[1].detail:find('solved') ~= nil),
      tostring(items[2] and items[2].detail:find('todo') ~= nil),
      tostring(tostring(calls.message):find('Loaded 2 of 2') ~= nil),
    }, '|')
  )LUA") == "true|true|true|true|true|true|true");
}

TEST_CASE("LeetCode cookie prompt opens masked and strips pasted line breaks", "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run(R"LUA(
    package.loaded['jot_lc.ui'].open_cookie_prompt()
    local opened = calls.float_open
    local intro = float_lines[2]
    float_callbacks.paste("LEETCODE_SESSION=pasted-cookie; " .. string.char(13, 10) .. "csrftoken=csrf")
    local visible = table.concat(float_lines, string.char(10))
    float_callbacks.key({key=13})
    return tostring(opened ~= nil) .. "|" .. tostring(opened.config.title == "LeetCode sign-in") .. "|" ..
           tostring(opened.config.focusable == true) .. "|" .. tostring(intro) .. "|" ..
           tostring(visible:find("pasted%-cookie") == nil) .. "|" ..
           tostring(visible:find("%*%*%*%*%*%*%*%*%*%*%*%*", 1) ~= nil) .. "|" ..
           tostring(calls.cookie == "LEETCODE_SESSION=pasted-cookie; csrftoken=csrf") .. "|" ..
           tostring(store.leetcode_session == nil) .. "|" ..
           tostring(opened.config.border == "single") .. "|" ..
           tostring(float_spans ~= nil and float_spans.line == 5)
  )LUA") == "true|true|true|Open LeetCode in your browser.|true|true|true|true|true|true");
}

TEST_CASE("LeetCode tabs, yank and judge console keep the opened question usable",
          "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run(R"LUA(
    local ui = package.loaded['jot_lc.ui']
    local json = package.loaded['jot_lc.json']
    local body = json.encode({data={problemsetQuestionList={total=1, questions={
      {frontend_id='1', title='Easy One', title_slug='easy-one', difficulty='Easy', status='ac'},
    }}}})
    ui.list('')
    -- The resolver requires the filters variable even when empty; omitting it
    -- fails the request with "resolve_question_list() missing ... 'filters'".
    local unfiltered = json.decode(calls.request.body).variables
    calls.callback({ok=true, status=200, body=body, error=''})
    calls.picker.callback('easy-one')
    local question = json.encode({data={question={
      id='1', frontend_id='1', title='Easy One', title_slug='easy-one', difficulty='Easy',
      content='<p>Add them.</p>', testcase_list={'[1,2]', '[3,4]'},
      code_snippets={{lang='C++', lang_slug='cpp', code='int twoSum() { return 0; }'}},
    }}})
    calls.callback({ok=true, status=200, body=question, error=''})
    calls.picker.callback('cpp')
    local wrote_template = calls.open ~= nil and files[calls.open] ~= nil
                           and files[calls.open]:find('int twoSum') ~= nil
    ui.yank()
    ui.open_tabs()
    local tabs = calls.picker.items()
    ui.run('run')
    local running = table.concat(float_lines, string.char(10))
    calls.callback({ok=true, status=200, body=json.encode({interpret_id=42}), error=''})
    calls.callback({ok=true, status=200, body=json.encode({
      status_code=10, status_msg='Accepted',
      total_correct=1, total_testcases=1,
      runtime=4, memory='10.2 MB',
      code_answer={'[0,1]'},
      expected_code_answer={'[0,1]'},
    }), error=''})
    local finished = table.concat(float_lines, string.char(10))
    local console_win = calls.float_open
    return table.concat({
      tostring(unfiltered.filters ~= nil),
      tostring(calls.clipboard ~= nil and calls.clipboard:find('twoSum') ~= nil),
      tostring(tabs[1] and tabs[1].value == 1),
      tostring(wrote_template),
      tostring(running:find('Running example 1') ~= nil),
      tostring(finished:find('Status: Accepted') ~= nil and finished:find('Output:') ~= nil),
      tostring(finished:find('Correct cases: 1 / 1') ~= nil),
      tostring(console_win ~= nil and console_win.config.title == 'LeetCode judge · example 1'),
    }, '|')
  )LUA") == "true|true|true|true|true|true|true|true");
}

TEST_CASE("LeetCode list load-more accumulates pages instead of stopping at one", "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run(R"LUA(
    local ui = package.loaded['jot_lc.ui']
    local json = package.loaded['jot_lc.json']
    local function page(rows, total)
      return json.encode({data={problemsetQuestionList={total=total, questions=rows}}})
    end
    ui.list('difficulty=easy')
    calls.callback({ok=true, status=200, body=page({
      {frontend_id='1', title='One', title_slug='one', difficulty='Easy'},
      {frontend_id='2', title='Two', title_slug='two', difficulty='Easy'},
    }, 3), error=''})
    local first = calls.picker.items()
    local more = first[#first]
    calls.picker.callback('__more__')
    local sent = json.decode(calls.request.body).variables
    calls.callback({ok=true, status=200, body=page({
      {frontend_id='3', title='Three', title_slug='three', difficulty='Easy'},
    }, 3), error=''})
    local second = calls.picker.items()
    return table.concat({
      tostring(more.value == '__more__' and more.detail:find('2 of 3') ~= nil),
      tostring(sent.skip == 100 and sent.limit == 100),
      tostring(#second == 3 and second[3].value == 'three'),
      tostring(second[#second].value ~= '__more__'),
      tostring(tostring(calls.message):find('Loaded 3 of 3') ~= nil),
    }, '|')
  )LUA") == "true|true|true|true|true");
}

TEST_CASE("LeetCode status filter maps to the API enum", "[leetcode][lua]")
{
  // solved maps to AC, the enum the live API accepts; an unsupported status
  // (attempted has no server-side filter) is dropped with a message instead
  // of being sent as SOLVED/ATTEMPTED, which the API rejects with an error.
  State state;
  REQUIRE(state.run(R"LUA(
    local ui = package.loaded['jot_lc.ui']
    local json = package.loaded['jot_lc.json']
    ui.list('status=solved')
    local solved = json.decode(calls.request.body).variables.filters
    calls.callback({ok=true, status=200, body=json.encode({data={problemsetQuestionList={total=1, questions={
      {frontend_id='1', title='One', title_slug='one', difficulty='Easy'},
    }}}}), error=''})
    ui.list('status=attempted')
    local attempted = json.decode(calls.request.body).variables.filters
    local warned = table.concat(calls.messages or {}, '|')
    return tostring(solved.status == 'AC' and attempted.status == nil)
           .. '|' .. tostring(warned:find('has no LeetCode filter') ~= nil)
  )LUA") == "true|true");
}

TEST_CASE("LeetCode testcase list accepts the live array shape", "[leetcode][lua]")
{
  // The live API returns exampleTestcaseList as an array of case strings; the
  // old string-only parser concatenated it and threw inside the question
  // callback, so the language picker never opened after the loading toast.
  State state;
  REQUIRE(state.run(R"LUA(
    local solution = package.loaded['jot_lc.solution']
    local live = solution.testcases({testcase_list={'[2,7,11,15]' .. string.char(10) .. '9', '[3,3]' .. string.char(10) .. '6'}})
    local legacy = solution.testcases({testcase_list='[1,2]' .. string.char(10) .. string.char(10) .. '[3,4]'})
    local missing = solution.testcases({})
    local empty = solution.testcases({testcase_list={}})
    return table.concat({
      tostring(#live == 2 and live[1]:find('2,7,11,15') ~= nil),
      tostring(#legacy == 2 and legacy[2] == '[3,4]'),
      tostring(#missing == 0 and #empty == 0),
    }, '|')
  )LUA") == "true|true|true");
}

TEST_CASE("LeetCode authenticated requests keep cookies in request headers", "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run("package.loaded['jot_lc.client'].auth(function() end); "
                    "local h=calls.request.headers; return h[6].name..'|'..h[6].value..'|'..h[7].name..'|'..h[7].value")
          == "Cookie|LEETCODE_SESSION=fake; csrftoken=csrf|x-csrftoken|csrf");
  REQUIRE(state.run("return tostring(store.leetcode_session)") == "nil");
}
