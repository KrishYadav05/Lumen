# Lumen Phase 3 — Developer Experience & Ecosystem — Status

Publisher: Krish Kumar

> **Update — Lumen 3.0.0-5a (Phase 5).** Statements below about capabilities describe Lumen
> ≤ 2.3.0, where `use fs`/`use net` *granted* access and importing `std/fs.lum`/`std/net.lum` was
> in effect a grant. In Language Specification 3.0 `use` only *declares*; the host grants
> (`--allow-*`), and a library can never exceed its importer — see `docs/capabilities.md`,
> `CHANGELOG.md` (3.0.0-5a) and `PHASE5_STATUS.md`. Test counts below are those of the release
> this phase ended on; the current counts are in `PHASE5_STATUS.md`.

Tracking against the 18-item Phase 3 spec. Updated honestly as work lands — unchecked
items are genuinely not done, not "mostly done." Some items in the original spec
require real infrastructure this environment cannot provide or verify (a hosted
package registry, actual Windows/macOS/ARM machines) — flagged explicitly below
rather than silently skipped.

## Completed

- [x] **First-class testing framework** (`test "name": <block>` + `assert`/
  `assert_eq`/`assert_ne`). Wired through the *entire* pipeline exactly like every
  other language construct in this project — Lexer → Parser → AST → Resolver →
  TypeChecker → Interpreter → IRBuilder → Compiler/VM — not a special-cased add-on.
  Test bodies are collected, not executed inline (matching real test-framework
  convention); `lumen test` is the only thing that runs them. `docs/testing.md`
  is the normative spec for this addition; `CHANGELOG.md` documents it honestly as
  Language Specification **1.1**, not a silent patch to 1.0 — `test` is a newly
  reserved word, which is a disclosed, narrow exception to full backward
  compatibility (breaks only a program that used `test` as an identifier).
  17 dedicated conformance tests (`tests/conformance/test_testing.py`) plus 5 new
  interpreter↔VM equivalence tests. **Found and fixed a real bug in the process**:
  a builtin-name list was duplicated across three places (`Resolver.BUILTIN_NAMES`,
  `IRBuilder._call_kind`, `Compiler`'s allow-list); adding `assert_eq` to only two
  of them crashed the compiler (`KeyError` — it got misrouted to "enum variant"
  handling). Consolidated into a single shared `BUILTIN_FUNCTION_NAMES` constant so
  this class of bug can't recur.
- [x] **Project scaffolding** (criterion #9) matches the spec's exact structure:
  `lumen new my_project` now produces `lumen.toml`, `src/main.lum`,
  `tests/main_test.lum`, and `README.md`. `cd my_project && lumen run` works with
  **zero arguments** (reads `[project].entry` from `lumen.toml`); same for
  `lumen check` and `lumen test`. This was previously a flat `main.lum` requiring
  an explicit path — a real gap the spec's own pass condition would have caught.
- [x] `lumen test [path] [--vm] [--filter SUBSTR]` — discovery (defaults to
  `./tests/`), per-file execution, pass/fail reporting with assertion messages,
  correct exit codes, and a `--vm` mode that runs the same tests through the
  bytecode VM with verified-identical results. The pre-existing Phase 2
  `lumen test --interpreter`/`--vm` (which ran *this implementation's own*
  regression suite, a completely different audience) was renamed to
  `lumen test --self-test [--vm]` to remove the naming collision rather than
  silently overload one flag with two incompatible meanings.
- [x] **`while` loop, `chr`/`ord`/`type_of` builtins** (Language Specification
  2.2) — found while building `std/json.lum` (see below), not planned up
  front. A JSON encoder needs a way to emit a literal `"` (the lexer has no
  string-escape mechanism at all, a documented frozen-1.0 limitation) and a
  way to tell `Bool` from `Int` at runtime without being fooled by `0==false`/
  `1==true`; a JSON *decoder* needs a loop driven by an arbitrary stopping
  condition, which `for` cannot express. Fixed with `chr(code) -> Str`,
  `ord(c) -> Int`, `type_of(v) -> Str`, and a full `while condition: body`
  statement — wired through every pipeline layer (Lexer, Parser, AST,
  Resolver, TypeChecker, IR, Optimizer, Compiler, VM, Interpreter), exactly
  like every other construct in this project, not special-cased. See
  `CHANGELOG.md`'s **2.2** entry for the full rationale and `docs/language.md`
  §1.14 / `docs/standard-library.md` §8.5.1 for the normative spec. Verified
  on both the Interpreter and VM backends, per standing project policy —
  never claimed working from interpreter-only testing.
- [x] **`std/json.lum`** — `json_encode`/`json_decode`/`null`/`is_null`,
  completing one of the four stdlib modules previously listed as "not yet
  built" below. Full JSON grammar including `\uXXXX` escapes and exponents;
  44 dedicated `lumen test` cases, passing identically on both backends. See
  `docs/stdlib-modules.md` and `CHANGELOG.md`'s **2.2** entry for scope and
  stated limitations (no struct/enum reflection, Str-only Dict keys).
  **Found and fixed a real, previously-undiscovered VM-only bug in the
  process** (not a workaround in json.lum — a core interpreter/VM fix): a
  `return` executed directly inside a `try` body bypassed the VM's `TRY_POP`,
  leaking a stale exception handler onto `VM.try_stack` that could wrongly
  catch a later, unrelated exception — externally visible only through a
  reused module `sub_vm` (the exact shape `json_decode`, called more than
  once, hits). The Interpreter was never affected (its try/catch is ordinary
  Python try/except). Fixed in the `RETURN` opcode; 4 new dedicated
  regression cases in `tests/test_equivalence.py`, each verified (by
  disabling the fix and re-running) to actually fail without it. Full
  account in `CHANGELOG.md`'s **2.2** entry.
- [x] **`std/path.lum`** (implementation release 2.2.1, library-only — no
  grammar/builtin change, so `SPEC_VERSION` stays 2.2) — POSIX-style path
  manipulation: `is_absolute`, `join`/`join_all`, `dirname`/`basename`/
  `split`, `extension`/`stem`, and `normalize` (lexical `.`/`..`/repeated-`/`
  resolution, matching Python's `os.path.normpath` convention). Completes
  one more of the four stdlib modules previously listed as "not yet built"
  below. Pure string logic — no filesystem access, no `fs` capability.
  `normalize`'s segment-stack algorithm needed to drop the last collected
  segment on a `..`, which would ordinarily be a list "pop" — discovered
  along the way that **this language has no `pop`/remove-last List
  builtin, only `push`** (confirmed by grepping `BUILTIN_FUNCTION_NAMES`).
  Rather than adding a new core builtin for one stdlib module's sake, used
  `collections.substring_list(list, 0, len(list) - 1)` to rebuild the list
  one element shorter — a pop-free design, not a stated limitation. Also
  added `strings.last_index_of(s, sub)` (`dirname`/`basename`/`extension`
  all need the *rightmost* separator, and `strings.lum` only had leftmost
  `index_of`) via the `reverse_str`+`index_of` trick, with its own
  dedicated test coverage. **Caught and fixed a real bug in my own first
  draft before shipping it**: an initial `dirname`/`basename` written as a
  simple "split at the last `/`" did not match any documented convention at
  the root — `dirname("/a")` came out `""` instead of `"/"`. Rewritten to
  match Python's `os.path.split` algorithm exactly (including its one
  genuine special case: a head of pure `/` characters is never stripped
  down to empty), re-verified by hand against `/a`, `/`, `a/b/`, and
  `a/b/c.txt` before writing the formal test suite. 19 new `lumen test`
  cases (`std/tests/path_test.lum`) plus 7 new `strings_test.lum` assertions
  for `last_index_of`, all passing identically on both backends — 105
  `std/` tests total (up from 85). See `docs/stdlib-modules.md` and
  `CHANGELOG.md`'s **2.2.1** entry.
- [x] **`std/net.lum`** (implementation release 2.2.2, library-only — no
  grammar/builtin change, so `SPEC_VERSION` stays 2.2) — `get`/`get_json`
  (the latter piping `http_get`'s response straight through
  `json.json_decode` — a direct payoff from having just built `json.lum`),
  `url_encode`/`build_query`/`with_query` (RFC 3986 percent-encoding and
  query-string assembly, since `http_get` has no POST/params support of its
  own), and `fetch`/`fetch_json` (get/get_json with query parameters
  attached). Completes the third of the four stdlib modules previously
  listed as "not yet built" below — chosen over `fs` specifically because
  it pairs with `json.lum` and needed no new capability or language
  feature. **Verified, not assumed, a capability-model subtlety before
  documenting it as fact**: capability grants don't propagate between
  modules (`docs/modules.md` §5.5), so a program calling `net.get(url)`
  needs no `use net` of its own — `net.lum` grants itself what its own
  `http_get` calls need. Confirmed by running a module with its own
  `use net`, imported from a program with none, and observing the call
  fail at the network layer (not a `PermissionError`) on both backends.
  `url_encode` is explicitly scoped to single-byte (0-255) input and
  `fail()`s on anything wider — Lumen has no Bytes type or UTF-8 encode
  primitive to do it correctly for a wider code point, stated plainly
  rather than silently mis-encoded. `get_json`'s full pipeline was
  smoke-tested end-to-end against a real local HTTP server returning real
  JSON (throwaway verification tooling, not part of the shipped module or
  test suite) before being trusted; the shipped 8 `net_test.lum` cases
  stay self-contained and offline like every other stdlib test file,
  testing the deterministic query-encoding logic directly and the
  capability/error-propagation behavior via a loopback address nothing
  listens on. 113 `std/` tests total (up from 105). See
  `docs/stdlib-modules.md` and `CHANGELOG.md`'s **2.2.2** entry.
- [x] **`std/fs.lum` + five new `fs`-gated builtins** (implementation release
  2.3.0, **Language Specification 2.3** — a real spec bump, because unlike
  `path`/`net` this adds names to the builtin namespace). A `fs.lum` over only
  `read_file`/`write_file` would have been hollow, so the missing primitives
  came first: `file_exists`, `is_dir`, `append_file`, `list_dir` (sorted, no
  `.`/`..`) and `delete_file` (files only). `is_dir` was not in the original
  plan of four — it turned out `file_exists` alone cannot tell a file from a
  directory, so `is_file`/`list_files` could not be written without it. All
  five are wired through the whole pipeline (`BUILTIN_FUNCTION_NAMES`,
  `VM_SUPPORTED_BUILTINS`, both backends' dispatch) and gated by the existing
  `fs` capability — no new capability. The module re-exports the seven file
  builtins and adds `is_file`, `split_lines`/`read_lines`/`write_lines`/
  `append_line`, `file_size`, `copy_file`, `move_file` (a move onto itself,
  however spelled, is a no-op rather than destroying the only copy),
  `delete_if_exists`, `list_files`/`list_subdirs`, `read_json`/`write_json`.
  27 `lumen test` cases plus 43 new core-suite cases (29
  conformance, 6 equivalence, 8 security). **Security note, documented in
  `docs/security.md` §7.5:** like `net.lum`, importing `fs.lum` is in effect a
  grant of `fs` to the importer (grants don't propagate between modules, so
  the module must hold its own). **Stated limitations, not papered over:** no
  mkdir/rmdir/rename/stat (no such builtins), text only, whole-file reads,
  `file_size` is characters not bytes, `move_file` is copy-then-delete (not
  atomic) and cannot detect two names for one file (symlinks).
  **Also found and fixed, unplanned:** `strings.split`/`replace` were still
  recursive (one call per match) — `split` overflowed the interpreter's stack
  at ~300 separators and took 18.5 s on the VM — and `strings.substring`
  walked the whole string on every call, making `index_of`/`contains`
  quadratic (92 s → 0.2 s interpreter, 211 s → 0.4 s VM for a 5,000-character
  string after the fix). Rewritten iteratively and verified by differential
  fuzzing against Python (56,635 cases, both backends, 0 mismatches, old
  implementation confirmed to give the same answers). 145 `std/` tests total
  (up from 113). See `docs/stdlib-modules.md` and `CHANGELOG.md`'s **2.3.0**
  entry.

## Not yet started

- [x] **Standard library, first modules** (criterion #1, partial) —
  `math`/`strings`/`collections`/`time`/`io`/`json`/`path`/`net`/`fs` built, 145
  `lumen test` tests passing on both backends (up from 41 — see the
  `json.lum`/`path.lum`/`net.lum`/`fs.lum` bullets above). Two new ungated core
  builtins (`now`, `read_line`) added to make `time`/`io` possible. Found
  and fixed two real gaps along the way: the VM never compiled dict literal
  *construction* (only indexing), and the lexer had no bracket-aware
  indentation suppression (multi-line literals were a syntax error). See
  `docs/stdlib-modules.md` and `CHANGELOG.md`.
- [ ] `concurrency` — not yet built (`json`, `path`, `net` and `fs` are now
  done, see above). `process` deliberately never will be (no
  process-spawning capability exists). `concurrency` is specifically
  blocked on a real language gap: Lumen has no first-class functions (a
  bare function name used as a value is a `NameError`, and `spawn` only
  accepts direct call syntax), which rules out anything like a generic
  `parallel_map(list, fn)` — this would need to be a language addition, not
  just a library one, and hasn't been put to the user as a decision yet.
- [ ] **Package manager** (#2) beyond what Phase 1 already built
  (`lumen.toml`, `lumen install` as a manifest-vs-disk check, local
  `lumen_packages/` resolution). No `lumen add`/`remove`/`update`/`build`, no
  `lumen.lock`, no real dependency resolution — there's nothing to resolve without
  a registry (see below).
- [ ] **Formatter** (`lumen fmt`, #3).
- [ ] **Linter** (`lumen lint`/expanded `lumen check`, #4) — unused variable/import,
  unreachable code, shadowing warnings, etc. Current `lumen check` only does the
  Phase 1/2 static passes (parse, resolve, type-check), not lint-style warnings.
- [ ] **LSP feature expansion** (#5) — hover, go-to-definition, find references,
  rename, signature help, semantic highlighting, formatting, code actions, import
  completion. Current LSP (from earlier work) has diagnostics and basic
  keyword/builtin/symbol completion only. The one constraint already satisfied:
  it imports `Parser`/`Resolver`/`TypeChecker` directly from `lumen.py` — there is
  no second, parallel Lumen implementation inside the LSP, and there won't be one.
- [ ] **Debugger** (`lumen debug`, #6) — breakpoints, step, stack, locals, watch.
  Not started; the VM's explicit call-stack/scope-stack model built in Phase 2
  should make this more tractable than it would be against a tree-walking
  interpreter alone, but nothing exists yet.
- [ ] **Documentation generator** (`lumen doc`, part of #8) and **tutorials**
  (`docs/tutorial/01-hello-world` through `10-packages`, part of #8). The language
  reference (`docs/language.md` etc.) exists; a beginner tutorial sequence and an
  API-doc generator do not.
- [ ] **Build system** (`lumen build`/`--release`/`clean`, #10) beyond `run`/`check`.
  There is no compiled-artifact output format to build *to* yet (the VM's bytecode
  is in-memory only, not a distributable file format) — this is prerequisite work,
  not just a missing CLI command.
- [ ] **Cross-platform support** (#11) — genuinely cannot be verified in this
  environment, which is a single Linux container. The implementation avoids
  OS-specific code paths (pure Python + `os.path`, no shell-outs, no
  platform-conditional logic) so it *should* run on Windows/macOS, but "should"
  is not "tested," and this status file will not claim otherwise.
- [ ] **Capability/security tooling** (`lumen permissions`, `lumen audit`, #12).
  The capability *model* is real and enforced (Phase 1 §7, Phase 2 VM); a CLI
  surface for inspecting it is not built.
- [ ] **Benchmark/profiling tooling** (`lumen bench`/`profile`, #13).
- [ ] **CI-friendly workflow** (#14) — the individual commands (`lumen check`,
  `lumen test`) work non-interactively today and would compose into a CI script,
  but no formatter (#3) means `lumen fmt --check` doesn't exist yet, and there's no
  actual CI configuration file or hosted runner to verify against.
- [ ] **Package registry** (`lumen publish`, `lumen add package_name` fetching from
  a real registry, #15) — **cannot be built here at all**: this requires hosting a
  server with persistent storage, a public URL, and account/publish authentication,
  none of which exist in this sandbox. Explicitly flagging this as infrastructure
  this environment cannot provide, not a deferred-but-buildable item.
- [ ] **First-party packages** (`lumen-json`, `lumen-http`, `lumen-cli`,
  `lumen-testing`, `lumen-date`, #17) — blocked on both the standard library (some
  of this is arguably std, not a package) and a registry to publish to.
- [ ] **Package quality standards / `lumen package check`** (#18) — nothing to
  validate against without a package ecosystem yet.
- [ ] **Dogfooding — real projects built in Lumen** (#16). None built yet. This is
  arguably the most valuable remaining item precisely because it's the one that
  surfaces missing-convenience problems no amount of compiler testing would catch
  — worth prioritizing once the standard library has *something* to build with.

## Test counts (grows with each milestone, run via `python3 tests/run_all.py`)

| Suite | Count |
|---|---|
| Regression + CLI (`tests/run_tests.py`) | 36 |
| Spec-driven conformance (`tests/run_conformance.py`, 12 files) | 428 |
| Interpreter ↔ VM equivalence (`tests/test_equivalence.py`) | 162 |
| Optimizer stability, -O0/-O1/-O2 (`tests/test_optimizer.py`) | 28 |
| Security / capability-bypass (`tests/test_security.py`) | 31 |
| **Total** | **685** |

Plus 145 `std/` stdlib tests (`lumen test std/tests/`, both backends, up
from 85 — see the `path.lum`/`net.lum`/`fs.lum` bullets above; run from the
repository root, because `fs_test.lum`'s fixture paths are cwd-relative) — run separately via
`lumen test`, not `tests/run_all.py`, since they exercise the CLI's own
test runner rather than the implementation's internal test suite.
