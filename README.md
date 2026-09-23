# jot

**VS Code, but in your terminal.**
<img width="2533" height="1372" alt="image" src="https://github.com/user-attachments/assets/b77080c2-8370-42ab-89f5-dac1c1de6e3c" />
Just try it. **Still in development**.

## Install

```bash
./install.sh          # user-local install to ~/.local
```

Or build with CMake:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
cmake --install build --prefix "$HOME/.local"
```

## Run

```bash
jot                 # resume last workspace, or home menu
jot file.cpp        # open a file
jot path/to/project # open a folder as the workspace
add --gui flag to use the GUI.
```

## Docs

- [Features, keybindings & commands](docs/FEATURES.md) - what jot can do and how to drive it
- [Configuration](docs/FEATURES.md#configuration) - Lua-first settings
- [Themes](docs/THEMES.md) - authoring colorschemes
- [Architecture](docs/ARCHITECTURE.md) - module layout for contributors
- [Lua API](docs/LUA_API.md) - plugins and scripting
- [Plugins](docs/PLUGINS.md)
- [Debugger](docs/DEBUGGER.md)
- [Tree-sitter](docs/TREE_SITTER.md)

## Platform support

Linux (x86_64/arm64) and macOS (Intel/Apple Silicon) are supported. Windows
10/11 + MSVC + Windows Terminal is experimental.

## License

MIT
