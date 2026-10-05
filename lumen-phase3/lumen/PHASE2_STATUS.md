# Lumen Phase 2 — Compiler & Runtime Foundation — Status

Tracking against the 16-criterion Phase 2 spec. Updated honestly as work lands —
unchecked items are genuinely not done, not "mostly done."

## Completed

- [x] **Name Resolution** (Milestone 1). `Resolver` class, wired into every entry
  point (`run`, `check`, `--vm`, module loading, REPL). Two-tier model (lexical
  variables: sound+complete; functions/structs/enums: sound-for-rejection-only,
  matching the interpreter's real flat/imperative registration) documented in the
  class docstring and `docs/errors.md` §4.9. 30 dedicated tests
  (`tests/conformance/test_name_resolution.py`).
- [x] **Typed IR** (Milestone 2). `IRProgram`/`IR*` node classes covering variables,
  constants, functions, calls, branches, loops, returns, structs, enums, pattern
  matching, try/catch, modules, capabilities. `IRBuilder` lowers the resolved AST
  into it, reusing `TypeChecker.infer()` so every expression's `.type` is guaranteed
  consistent with what type-checking already verified. `IRValidator` does structural
  sanity checking independent of the builder. 14 dedicated tests
  (`tests/conformance/test_ir.py`).
- [x] **Bytecode compiler now consumes IR, never AST** (part of criterion #4's pass
  condition). `Compiler` matches exclusively on `IR*` node types.
- [x] **Interpreter ↔ VM equivalence suite** (criterion #6, marked CRITICAL).
  `tests/test_equivalence.py`, wired to `lumen test --vm` / `lumen test --interpreter`
  per the spec's exact CLI shape. 22 tests, all passing. This suite caught two real
  spec-violating bugs in the VM before they shipped:
  - Block-scope shadowing was completely unimplemented in the VM (flat variable
    table, no scope stack) — violated the frozen `language.md` §1.11 Rule B. Fixed:
    the VM now has a real scope stack (`PUSH_SCOPE`/`POP_SCOPE`, per-`if`-branch and
    per-loop-iteration, mirroring the interpreter's `Env` chain exactly).
  - `and`/`or` did not short-circuit at all in the VM (both operands always eagerly
    evaluated) — violated `language.md` §1.10. Fixed: real conditional-jump-based
    short-circuit compilation (`DUP`/`JUMP_IF_TRUE`/`JUMP_IF_FALSE`).
  - List/dict indexing was never wired into the VM at all (silent gap, not a
    violation, but a real coverage hole). Fixed: `INDEX_GET` opcode added.
- [x] Also found and fixed, incidentally, while building the above: a genuine
  pre-existing bug in `TypeChecker.infer()`'s arithmetic-operator inference that
  disagreed with the already-documented runtime rules in `types.md` §3.6.1 (e.g.
  `let x: Float = 1 / 2` was incorrectly *rejected* — the checker thought `Int/Int`
  inferred `Int`, when division always produces `Float` at runtime). Fixed and
  `types.md` §3.5 corrected to match.
- [x] `lumen test --interpreter` / `lumen test --vm` CLI, matching the spec's exact
  wording.
- [x] **Functions, calls, and recursion in the VM** (start of Milestone 3, criterion
  #4). Real call-stack management (`CALL`/`RETURN`/`REGISTER_FN` opcodes) — the VM
  uses an explicit `call_stack` list rather than nested Python calls, so Lumen-level
  recursion depth is *not* bounded by Python's own recursion limit (verified:
  `countdown(5000)` runs fine in the VM). Faithfully replicates the interpreter's
  imperative, order-dependent function-registration semantics from Milestone 1 (a
  forward-referenced top-level function still correctly fails identically on both
  backends) rather than being more permissive than the reference interpreter. Nested
  (`fn`-inside-`fn`) definitions are explicitly out of scope for now and rejected
  with a named error rather than silently mishandled. 8 new equivalence tests.
- [x] Also found and fixed, incidentally, while building the above: **the reference
  interpreter itself leaked a raw Python `RecursionError` traceback** on deep Lumen
  recursion (`countdown(5000)` crashed with a Python stack dump, not a Lumen error) —
  this predates Phase 2 entirely and directly violated this project's own established
  "no Python internals leak to the user" standard. Fixed: `call_function` now
  converts `RecursionError` into a normal, catchable runtime failure
  (`"stack overflow: maximum call depth exceeded"`), consistent with every other
  failure in `errors.md`'s uniform model.
- [x] **Structs, enums, pattern matching, and try/catch in the VM** (rest of
  Milestone 3, criterion #4/#5). New opcodes: `MAKE_STRUCT`/`FIELD_GET`/`FIELD_SET`,
  `MAKE_VARIANT`/`IS_VARIANT`/`DESTRUCTURE_VARIANT`/`MATCH_FAIL`,
  `TRY_PUSH`/`TRY_POP` (a real handler stack, not a Python-exception-scoping trick —
  an error raised arbitrarily deep inside a function called from within a `try` body
  still unwinds correctly, snapshot-and-restore of both the scope stack and the call
  stack), `INDEX_SET`, and `CALL_BUILTIN` for `len`/`push`/`keys`/`values`/`fail`.
  `LumenInstance`/`EnumInstance` (the interpreter's own runtime objects) are reused
  directly as the VM's struct/enum representation — one object model, not two (start
  of criterion #7). 34 new equivalence tests, including a composite program combining
  structs + enums + match + try/catch + recursion.
  **This round of equivalence testing caught real bugs again, not hypothetically:**
  - The VM's `MAKE_STRUCT` didn't validate the given fields against the struct's
    *declared* field set at all — `P { x: 1 }` for a two-field struct silently
    succeeded in the VM while the interpreter correctly raised `TypeError`. Fixed.
  - The VM's variant construction had a genuine **stack-underflow bug**: it always
    popped exactly as many values as the variant's *declared* field count, regardless
    of how many were actually pushed — calling a 2-field variant constructor with 1
    argument popped a value that belonged to unrelated earlier code, silently
    corrupting the stack instead of raising a clean arity error. Fixed by having the
    compiler pass the *actual* pushed argument count alongside the declared count, so
    the VM validates before popping.
  - Builtin functions (`len`, `push`, `keys`, `values`, `fail`) were never wired into
    the compiler *at all* — any use of them crashed with an unclean "not supported"
    error even for these low-risk, non-capability-gated builtins. Implemented; the
    genuinely deferred builtins (`read_file`/`write_file`/`http_get`/`wait`/`chan`/
    `send`/`receive`) still fail with a clear, honest, named error, since they need
    capability enforcement and/or concurrency primitives the VM doesn't have yet
    (criteria #11/#12).
  - Also: `IRUnitVariant` and match's `unit_variant` pattern kind didn't exist before
    this round — `IRBuilder` was silently treating every bare-name pattern as a
    catch-all bind, which would have made `match A: A: ... ` (a unit-variant-equality
    test per the already-frozen `errors.md` §4.5) behave like `match A: n: ...`
    instead once the VM tried to compile it. Fixed before it ever shipped as a VM
    behavior, by building the IR-level distinction correctly from the start.

- [x] **Modules and capabilities in the VM** (criteria #9 and #12). Required a real
  architectural addition, not just new opcodes: calling a function that lives in a
  *different* compiled module means the VM needs nested execution contexts. Solved
  by refactoring the per-instruction dispatch loop into a reusable `_exec_one` step
  function (mutating `self.pc` in place), shared by `run()` (top-level execution) and
  the new `call_exported_function()` (a cross-module call reuses the exact same
  instruction semantics — recursion, nested try/catch, further nested cross-module
  calls all just work, nothing was specially handled for them). `use "path" as alias`
  compiles to `USE_MODULE`, which at runtime lexes/parses/resolves/type-checks/lowers/
  compiles the target file into its own `BytecodeProgram` and runs it on a fresh `VM`
  instance — matching the interpreter's real semantics exactly: no caching (`use`
  twice on the same path gives two independent instances), capability isolation (a
  grant in the importer never reaches an imported module and vice versa), and cycle
  detection with the same shared-chain mechanism as the interpreter. `use fs`/`use
  net` compile to `GRANT_CAPABILITY`; `read_file`/`write_file`/`http_get` are now real
  in the VM, gated by `self.capabilities` exactly like `Interpreter.call_capability`.
  17 new equivalence tests (multi-file scenarios via a new `equivalent_multifile`
  harness helper), including cross-module recursion, three-deep import chains, and
  independent-instance-per-`use` semantics.
  **Found two more real bugs via equivalence testing, both fixed:**
  - The cycle-detection error message's chain listing was built from a plain Python
    `set`, whose iteration order is not guaranteed — the interpreter and VM (run as
    separate processes) could report the *same* cycle in a *different* order,
    depending on hash randomization. This was a **pre-existing bug in the reference
    interpreter itself**, not something the VM introduced; only surfaced once
    something compared the two side by side. Fixed in both places: `loading` is now
    an ordered list, so the reported chain always matches the actual import order.
  - `call_exported_function`'s "no such function" error omitted the module's alias
    (`"module has no function 'x'"` vs. the interpreter's `"module 'm' has no
    function 'x'"`). Fixed.

## Not yet started

- [ ] Bytecode compiler coverage for concurrency (`spawn`/`wait`/`chan`/`send`/
  `receive`) — the only remaining language feature not in the VM. Everything else
  from criteria #4/#5's checklist is now covered and equivalence-tested: variables,
  functions/calls/recursion, conditionals, loops, lists, dicts (partial — literals
  and indexing work; no dict-specific opcodes beyond that), structs, enums, pattern
  matching, errors/try-catch, modules, capabilities.
- [ ] Runtime object model consolidation (a single defined representation per value
  kind, rather than "whatever the interpreter already used") — partially done:
  `LumenInstance`/`EnumInstance` are already shared between interpreter and VM, and
  `VMModule` mirrors `LumenModule`'s shape; a `Function`/`Channel`/`Task` runtime
  object doesn't exist yet since concurrency isn't in the VM.
- [ ] Explicit memory-management strategy write-up (currently: leverages the host's
  GC, as the spec allows for a Python-hosted VM — but this hasn't been written down
  as a deliberate policy yet).
- [x] Runtime modules in the VM — done this round (see above); no longer
  interpreter-only.
- [ ] VM error handling with a real stack trace / source-location display (not
  Python-internal leakage) — current VM errors are plain messages, no trace.
- [ ] VM concurrency (`spawn`/`wait`/`chan`/`send`/`receive` in the VM).
- [x] Capability enforcement surviving compilation to bytecode — done this round;
  `read_file`/`write_file`/`http_get` are gated by `self.capabilities` in the VM
  exactly like the interpreter, and a grant never crosses a module boundary.
- [ ] `tests/` directory reorganization into the full
  `lexer/parser/types/ir/compiler/vm/interpreter/conformance/modules/concurrency/security/`
  layout the spec sketches (current layout: `tests/conformance/` one file per
  `docs/` chapter, plus `tests/run_tests.py`, `tests/test_equivalence.py`).
- [ ] VM benchmarks.
- [ ] Basic VM debugger (`lumen debug`).

## Test counts (grows with each milestone, run via `python3 tests/run_all.py`)

| Suite | Count |
|---|---|
| Regression + CLI (`tests/run_tests.py`) | 36 |
| Spec-driven conformance (`tests/run_conformance.py`, 10 files) | 298 |
| Interpreter ↔ VM equivalence (`tests/test_equivalence.py`) | 71 |
| **Total** | **405** |
