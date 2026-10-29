# Lumen VS Code Extension (v3.1.0-p4)

Publisher: Krish Kumar

Syntax highlighting, live diagnostics, and autocomplete for the Lumen
language (Language Specification 3.1) via `.lum` file association.

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

## What changed in 3.1 (Phase 4 — error handling)
- **Exact diagnostics.** A squiggle now covers exactly the offending code (the compiler's line *and column range*),
  carries the compiler's **code** (`E0201`, `E0404`, …) and shows the `expected` / `found` notes and the `help` line, the same
  text `lumen check` prints. A warning (`W0301`, a `match` that may not cover every variant) is a *warning* squiggle, not an
  error. A syntax error still marks its whole line (the compiler gives a line, not a column, for syntax errors).
- **`Result` / `Option` / `?` / `finally`.** `Ok`, `Err`, `Some`, `None`, `Result`, `Option`, the `?` operator and the `finally`
  keyword are highlighted and offered by autocomplete, as are the new built-ins `fail_with`, `ok_or`, `unwrap`, `unwrap_or`,
  `is_ok`, `is_err`, `is_some`, `is_none`.
- The language server now runs the compiler's single shared front end (`analyze`), so it cannot disagree with `lumen check` —
  and, importantly, it knows about the always-in-scope `Result`/`Option` (an older server would have reported `Ok(...)` as an
  undefined name).

## What it covers
- Keywords (`let`, `fn`, `if/else`, `for/in`, `while`, `try/catch/finally`, `match`, `spawn`, `struct`, `enum`, `use`, `and/or/not`,
  plus the 2.1 low-punctuation alternatives `as`/`returns`/`is` — see this project's main README's
  "Low-punctuation syntax" section) — note there is still no `break`/`continue`; that's
  a known, open language gap, not a missing highlight. `while` was added in 2.2 (see
  this project's main README and `docs/language.md` §1.14) for loops driven by an
  arbitrary condition rather than a fixed collection.
- Capability declarations (`use fs.read`, `use fs.write`, `use fs.list`, `use net.http`, and the `use fs` /
  `use net` shorthands) are highlighted, and the checker's errors for an unknown or reserved capability or a
  declaration that is not at the top level of the file appear as squiggles. **Declaring never grants:** what a
  run may do is decided by `lumen run --allow-read=… --allow-net=…`, or interactively by `lumen run --ask` (which lists
  these declarations and asks before running), with `--audit-log=FILE` to record the decisions (this project's
  `docs/capabilities.md`); the extension only checks and highlights, it does not run or grant anything.
- Types (`Int`, `Str`, `Bool`, `Float`, `List`, `Dict`, `Any`, `Result`, `Option`)
- Strings, numbers, comments (`#`)
- Function calls and built-ins (`print`, `len`, `push`, `keys`, `values`,
  `range`, `now`, `read_line`, `wait`, `chan`, `send`, `receive`, `fail_with`, `ok_or`, `unwrap`,
  `chr`, `ord`, `type_of` (added in 2.2), and the file builtins
  `file_exists`, `is_dir`, `append_file`, `list_dir`, `delete_file`
  (added in 2.3), …) — highlighting and autocomplete use one list, checked
  against the interpreter's own builtin set by `test_lsp.py`
- Auto-closing brackets/quotes and `#` comment toggling (Ctrl+/)
- Live diagnostics and autocomplete via `lsp_server.py`, which imports
  `Parser`/`Resolver`/`TypeChecker` directly from the bundled `lumen.py` —
  there is no second, separate implementation of the language living in this
  extension.

## Publishing
To put this on the VS Code Marketplace: install `vsce` (`npm i -g vsce`), add
a `LICENSE` and icon, then run `vsce package` and `vsce publish` from this folder.
