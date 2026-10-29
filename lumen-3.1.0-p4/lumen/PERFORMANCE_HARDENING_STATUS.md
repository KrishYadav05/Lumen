# Lumen — Performance, Security & Production Hardening — Status

Publisher: Krish Kumar

> **Renamed (Lumen 3.1.0-p4).** This file was `PHASE4_STATUS.md` and called itself "Lumen Phase 4". The
> project's **Phase 4 is now "Error Handling & Reliability"** — see `PHASE4_STATUS.md` (a different file, a
> different scope) — and the security work that used to be listed here is **Phase 5** (`PHASE5_STATUS.md`).
> This checklist (performance, fuzzing, CI, benchmarks, AOT, observability) was moved to a later phase whose
> number has not been assigned; **nothing in it was done or dropped by that move**, and every unchecked item
> below is still genuinely not done. Older entries in `CHANGELOG.md` and the phase files that say "Phase 4" for
> performance or hardening mean *this* file.

> **Update — Lumen 3.0.0-5a (Phase 5).** Statements below about capabilities describe Lumen
> ≤ 2.3.0, where `use fs`/`use net` *granted* access and importing `std/fs.lum`/`std/net.lum` was
> in effect a grant. In Language Specification 3.0 `use` only *declares*; the host grants
> (`--allow-*`), and a library can never exceed its importer — see `docs/capabilities.md`,
> `CHANGELOG.md` (3.0.0-5a) and `PHASE5_STATUS.md`. Test counts below are those of the release
> this phase ended on; the current counts are in `PHASE5_STATUS.md`.

Updated honestly as work lands — unchecked items are genuinely not done, not "mostly
done." This phase took a deliberate mid-course pivot (see below) on the user's own
explicit direction, so this file tracks that pivot openly rather than silently
reordering the record.

## The pivot (read this first)

Phase 4 started against its original checklist (fuzzing infrastructure, a CI
workflow, FFI/compatibility docs, a benchmark suite, an AOT build, observability
tooling). Partway through, the user gave direct, sharp feedback on the *language
itself*, independent of that checklist — verbatim: *"i told u to make an easy to
write and fast to result Language but u made it hard to write due little little
things i have to write to code in that language and the result r fucking slow."*

That was investigated, not argued with: benchmarks and a minimal repro confirmed
both halves of the complaint were real and attributable to a specific root cause
(the "Rule B" scoping bug — see `CHANGELOG.md`'s 2.0 entry), not a vague impression.
Given a direct choice — finish the original Phase 4 checklist, or stop and fix
ergonomics/performance first — the user chose the latter explicitly. That work
**is** this phase's output so far; the original checklist is paused, not abandoned,
and resumes once the user is satisfied with ergonomics/performance or says to
pick it back up.

## Completed

- [x] **The Rule B scoping bug, fixed (breaking change → Language Specification
  2.0).** Bare reassignment (`x = expr`) inside a nested `if`/`for`/`try`/`match`
  block used to silently shadow the outer variable instead of mutating it — every
  accumulator-style loop in the language was affected, not a narrow edge case.
  Root-caused to the parser producing one AST node (`Let`) for both declaration and
  reassignment; fixed with a new, distinct `Assign`/`IRAssign` node threaded through
  every layer of the pipeline (parser → resolver → type checker → interpreter → IR
  builder/validator/optimizer → compiler → VM). Full writeup, before/after examples,
  and version-bump rationale: `CHANGELOG.md`, `language.md` §1.11.1.
- [x] **Native `range()` builtin**, added specifically after an intermediate fix
  attempt (a recursive loop-counting helper) was found to blow the interpreter's
  recursion limit at a few hundred iterations — the same class of bug as Rule B,
  found one layer down by this project's own benchmarking discipline before it
  shipped. `standard-library.md` §8.5.
- [x] **Standard library rewritten, recursion → iteration**, across
  `std/math.lum`, `std/collections.lum`, `std/strings.lum`, `std/io.lum` — every
  function whose recursion existed only to work around Rule B or to manufacture a
  count is now a plain loop. 41 stdlib tests still pass on both backends.
  `docs/stdlib-modules.md` has the full per-module breakdown.
- [x] **IR optimizer** (`-O0`/`-O1`/`-O2` on `lumen run --vm`) — constant folding,
  dead-code elimination after `return`, short-circuit boolean folding, dead-branch
  elimination for constant-condition `if`. 22 stability tests confirm all three
  levels agree on program output.
- [x] **Security/capability-bypass hardening suite** (`tests/test_security.py`,
  23 tests) — adversarial tests aimed at routing around the `fs`/`net` capability
  gates, not just exercising their happy path.
- [x] **Parser robustness fix** — adversarially deep nesting used to leak a raw
  Python `RecursionError` to the user instead of a clean Lumen `SyntaxError`.
  Found via adversarial-input probing, not a reported bug.
- [x] **Low-punctuation syntax, additive (Language Specification 2.1).** Further
  direct feedback — this time specifically about punctuation ceremony (colons,
  commas, parens-on-calls) rather than the scoping/speed issues 2.0 fixed.
  Colons, commas (including trailing), and parens-on-calls are now all
  optional, `<T>` generics can be inferred, and `print` takes multiple
  space-separated values — every new spelling is pure parser sugar producing
  the identical AST a 1.0–2.0 program would, so nothing downstream of the
  parser changed. Fully additive: no 1.0–2.0 program's behavior changes.
  36 new conformance tests + 8 new equivalence tests. Full writeup:
  `CHANGELOG.md`'s 2.1 entry, `language.md` §1.13, `examples/lowpunct_demo.lum`.
- [x] **Interpreter/VM call-dispatch performance (2.1.1).** Directly follows
  up the "dispatch overhead... remains open work" gap below. Profiled with
  `cProfile` on `fib(24)` (not guessed); found both the tree-walking
  Interpreter's `eval()`/`exec_stmt()` isinstance-chains and the bytecode
  VM's `_exec_one()` opcode string-comparison chain had their hottest
  types/opcodes (`Var`/`BinOp`/`Call`; `BINOP`/`JUMP_IF_FALSE`) sitting at the
  worst (deepest, most-expensive) position in a long linear if/elif scan.
  Fixed with a pure reorder — byte-identical branch bodies, only the check
  order changed — verified safe by the full 558-test suite (especially the
  interpreter↔VM byte-for-byte equivalence suite) passing unchanged.
  Measured result on identical hardware, `fib(24)`, median of 7 runs:
  Interpreter 1.216s → 0.860s (1.41x, 41% faster); VM 1.116s → 0.860s (1.30x,
  30% faster). Full writeup, methodology, and the honest "still ~200x slower
  than raw Python" caveat: `CHANGELOG.md`'s 2.1.1 entry.
- [x] **Interpreter dict-dispatch, `eval()`/`exec_stmt()` (2.1.2).** Follow-up
  to 2.1.1, which deliberately deferred this as unproven extra work rather
  than assuming it would help. Measured it instead: replaced the (already
  reordered) isinstance chains with a `type(node)`-keyed dict dispatching
  straight to small per-type handler methods — safe because every AST `Node`
  subclass is a flat leaf with no further subclassing, so `isinstance(node,
  X)` and `type(node) is X` are provably equivalent. Generated mechanically
  (an extraction script, not hand-editing) with an explicit before/after
  node-type-coverage check, specifically to avoid repeating 2.1.1's VM
  opcode-deletion near-miss. Verified against the full 558-test suite
  (unchanged) plus a fresh `fib(24)` benchmark: 0.860s → 0.771s, a further
  1.12x (12%) on top of 2.1.1's reorder — a real, measured win, not assumed.
  VM backend untouched in this release (reorder only, per 2.1.1). Full
  writeup: `CHANGELOG.md`'s 2.1.2 entry.

## Paused, not abandoned (original Phase 4 checklist)

- [ ] **Fuzzing infrastructure** — beyond the one adversarial-nesting probe above,
  no systematic fuzzer (grammar-based or mutation-based) exists yet.
- [ ] **CI workflow** — no hosted CI configuration. `lumen check`/`lumen test`
  already work non-interactively and would compose into one.
- [ ] **FFI / compatibility documentation** — no foreign-function interface exists
  (noted as a deliberate v1.0 scope boundary in `standard-library.md`); no
  dedicated compatibility doc for embedding or interop has been written.
- [ ] **Benchmark suite tooling** (`lumen bench`/`profile` as a first-class CLI
  surface) — the ad-hoc benchmarks in `CHANGELOG.md`'s 2.0 entry were run by hand
  for this release, not through reusable tooling.
- [ ] **AOT build** — the VM's bytecode is in-memory only; there is still no
  distributable compiled-artifact output format to build one from.
- [ ] **Observability tooling** — no structured logging, tracing, or metrics
  surface for running Lumen programs.

## Known, explicitly out-of-scope-for-now gaps found during this phase

- **No `break`/`continue`.** A real ergonomics gap — there is no early-loop-exit
  mechanism — identified while rewriting the stdlib (worked around there with
  boolean "found" flags, which is still correct and still an iteration-over-
  recursion win, just not optimal). Not implemented; not yet put to the user as a
  decision to make. **Still true after 2.2's `while` addition** (`CHANGELOG.md`,
  `PHASE3_STATUS.md`): `while` is a new, independent loop form, not an early-exit
  mechanism for `for` — a `while true:` body still has no way out except a
  `return` that unwinds the entire enclosing function, so `while`'s own dead-loop
  optimizer rule deliberately only folds away a *literal-false* condition, never
  `while true`, since nothing could prove that loop ever terminates without a
  `break` this language doesn't have. A VM-only bug touching the Interpreter/VM's
  shared exception-handling machinery (not the dispatch loop) was found and
  fixed in 2.2 while building `std/json.lum` — see `CHANGELOG.md`'s 2.2 entry —
  a `return` inside a `try` body was leaking a stale handler onto `VM.try_stack`
  that could wrongly catch a later, unrelated exception. Unrelated to the
  dispatch-overhead work below, but the same "test both backends, never assume"
  discipline this phase established is exactly what caught it.
- **Interpreter/VM dispatch overhead for user-written recursive code — improved
  twice (2.1.1, 2.1.2), not eliminated.** The stdlib rewrite (above) removes
  the standard library's *own* recursion; it does not, by itself, make a
  user's own recursive Lumen function (e.g. a hand-written `fib`) faster —
  that took the separate 2.1.1 (dispatch reorder, both backends) and 2.1.2
  (Interpreter dict-dispatch) fixes above. Current numbers: `fib(24)` runs in
  ~0.77s on the Interpreter and ~0.86s on the VM (down from ~1.1–1.2s
  originally), but this is still roughly two orders of magnitude slower than
  the ~0.006s raw Python takes for the same function. Both fixes reduce
  per-step dispatch overhead; neither changes the fundamental cost model —
  every Lumen-level operation still goes through environment lookups and
  Python-level function calls underneath. The VM backend has not yet had an
  equivalent dict-dispatch rewrite (it still uses 2.1.1's reordered opcode
  chain, not a dict); whether that would repeat the Interpreter's ~12% gain
  is open, unmeasured work, not assumed. A fundamentally different approach
  (compiling to Python bytecode directly, or a register-based VM) would be
  needed to close the two-orders-of-magnitude gap with native Python, not
  more dispatch tuning on the current design.

## Test counts (run via `python3 tests/run_all.py`, plus `lumen test` for stdlib)

| Suite | Count |
|---|---|
| Regression + CLI (`tests/run_tests.py`) | 36 |
| Spec-driven conformance (`tests/run_conformance.py`, 12 files) | 428 |
| Interpreter ↔ VM equivalence (`tests/test_equivalence.py`) | 162 |
| Optimizer stability, `-O0`/`-O1`/`-O2` (`tests/test_optimizer.py`) | 28 |
| Security hardening (`tests/test_security.py`) | 31 |
| Standard library, both backends (`lumen test std/tests/`) | 145 (×2 backends) |
| **Total (excluding the ×2 stdlib backend runs)** | **685** |

(Numbers above reflect 2.3.0, the current release. Through 2.2.2 only the
stdlib row had grown since 2.2 (85 → 105 → 113: `std/path.lum`,
`strings.last_index_of`, `std/net.lum` were library-only additions). 2.3.0
adds five builtins, so the conformance, equivalence, security and stdlib suites grew (regression and optimizer did not): conformance 399 → 428,
equivalence 156 → 162, security 23 → 31, stdlib 113 → 145 (`std/fs.lum`,
plus five `strings` cases for the `split`/`replace`/`substring` rewrite).
Earlier phases of this file quoted 558/41 as of 2.1.2 — see `CHANGELOG.md`
for what changed in between.)
