# Lumen Phase 3 — Developer Experience & Ecosystem — Status

Publisher: Krish Kumar

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

## Not yet started

- [x] **Standard library, first modules** (criterion #1, partial) —
  `math`/`strings`/`collections`/`time`/`io` built, 41 `lumen test` tests passing
  on both backends. Two new ungated core builtins (`now`, `read_line`) added to
  make `time`/`io` possible. Found and fixed two real gaps along the way: the VM
  never compiled dict literal *construction* (only indexing), and the lexer had
  no bracket-aware indentation suppression (multi-line literals were a syntax
  error). See `docs/stdlib-modules.md` and `CHANGELOG.md`.
- [ ] `std/fs.lum`, `path`, `json`, `net`, `concurrency` — not yet built.
  `process` deliberately never will be (no process-spawning capability exists).
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
| Spec-driven conformance (`tests/run_conformance.py`, 11 files) | 316 |
| Interpreter ↔ VM equivalence (`tests/test_equivalence.py`) | 76 |
| **Total** | **428** |
