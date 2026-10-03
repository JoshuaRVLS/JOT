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
        show_message=function(m) calls.message=m end,
        popup=function(text,title) calls.popup={text=text,title=title} end,
        picker=function(title,items,cb) calls.picker={title=title,items=items,callback=cb} end,
      },
      keymap={set=function(key,fn,detail) calls.keys=calls.keys or {}; calls.keys[#calls.keys+1]=key end,
              remove=function() end},
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
  REQUIRE(state.run("return jot.leetcode_feature.config.get('endpoint')") == "leetcode.com");
}

TEST_CASE("LeetCode problem list maps upstream status pairs", "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run(R"LUA(
    local rows
    package.loaded['jot_lc.client'].list(0, function(result) rows = result end)
    local body = package.loaded['jot_lc.json'].encode({stat_status_pairs={{
      stat={question_id=1, frontend_question_id='1', question__title='Fixture Problem',
            question__title_slug='fixture-problem', question__hide=false},
      difficulty={level=1}
    }}})
    calls.callback({ok=true, status=200, body=body, error=''})
    return rows and rows[1].title .. '|' .. rows[1].title_slug or 'empty'
  )LUA") == "Fixture Problem|fixture-problem");
}

TEST_CASE("LeetCode authenticated requests keep cookies in request headers", "[leetcode][lua]")
{
  State state;
  REQUIRE(state.run("package.loaded['jot_lc.client'].auth(function() end); "
                    "local h=calls.request.headers; return h[6].name..'|'..h[6].value..'|'..h[7].name..'|'..h[7].value")
          == "Cookie|LEETCODE_SESSION=fake; csrftoken=csrf|x-csrftoken|csrf");
  REQUIRE(state.run("return tostring(store.leetcode_session)") == "nil");
}
