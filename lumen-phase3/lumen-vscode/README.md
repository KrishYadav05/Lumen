# Lumen VS Code Extension (v0.1.0)

Syntax highlighting + `.lum` file association for the Lumen language.

## Full setup (highlighting + live errors + autocomplete)
1. Copy this whole folder to your VS Code extensions directory:
   - macOS/Linux: `~/.vscode/extensions/lumen-lang`
   - Windows: `%USERPROFILE%\.vscode\extensions\lumen-lang`
2. Open a terminal in that folder and run:
   ```
   npm install
   ```
3. Restart VS Code. Open a `.lum` file — you now get highlighting, red
   squiggles on real errors (from `lumen check`'s own checker), and
   Ctrl+Space autocomplete for keywords/types/built-ins/your own names.
4. Requires `python3` on your PATH (the extension spawns `lsp_server.py`).

## What it covers
- Keywords (`let`, `fn`, `if/else`, `for/in`, `try/catch`, `match`, `spawn`, `struct`, `enum`, `use`, `and/or/not`)
- Types (`Int`, `Str`, `Bool`, `Float`, `List`, `Dict`, `Any`)
- Strings, numbers, comments (`#`)
- Function calls and built-ins (`print`, `len`, `wait`, `send`, `receive`, …)
- Auto-closing brackets/quotes and `#` comment toggling (Ctrl+/)

## Not included yet (next step)
Real-time error checking and autocomplete require a Language Server — Lumen's
`lumen check` command already produces line-numbered errors, so wrapping it in
an LSP server is the natural next step to get red squiggles in the editor.

## Publishing
To put this on the VS Code Marketplace: install `vsce` (`npm i -g vsce`), add
a `LICENSE` and icon, then run `vsce package` and `vsce publish` from this folder.
