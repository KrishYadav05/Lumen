# Lumen Phase 4 — Error Handling & Reliability — Status

Publisher: Krish Kumar

Release: **Lumen 3.1.0-p4** (Language Specification 3.1) — a **pre-release**. Tracking against *"Lumen Phase 4 — Error
Handling & Reliability: Definition of Done and Acceptance Criteria"*. Updated honestly: **unchecked items are genuinely
not done**, and a checked item means a test in this repository exercises it on the interpreter **and** on the VM (and the
VM at `-O2`), not that it is beyond attack.

> **Numbering.** This is the new **Phase 4**. Security is **Phase 5** (`PHASE5_STATUS.md`, unchanged by this release). The
> old "Performance, Security & Production Hardening" checklist that used to be called Phase 4 moved to a later phase; its
> file is now `PERFORMANCE_HARDENING_STATUS.md` so the two are never confused.
>
> **Direction taken (your decisions):** follow the new spec — `Result`/`Option`/`?` for expected failures, `try`/`catch` kept
> for exceptional ones; additive (Specification 3.0 → 3.1); general generic enums/structs, with `Result`/`Option` ordinary
> library enums always in scope; and all four extras: `finally`, stack traces, error chaining, error codes + columns.
>
> **Objective (from the spec):** make error handling predictable, type-safe, consistent and easy to debug.
> **Not claimed:** that Lumen has no error-handling defects, or that the implementation has had an independent review.

## Requirements (ERR-001 … ERR-012)

- [x] **ERR-001 `Result<T, E>` (P0).** A generic enum in scope everywhere; `Ok(T)`/`Err(E)`; both variants checked against
  their declared types to any depth; invalid variants and wrong payloads are compile-time errors; `match` handles both; nested
  results (`Result<Option<Int>, Str>`) are checked and behave as written; equal on both engines. The **required negative test**
  (`let result: Result<Int, Str> = Ok("hello")`) is rejected with `expected: Result<Int, Str>` / `found: Result<Str, _>` and a
  source location. *Limit:* gradual typing — a value that passes through an unannotated function, parameter or variable is `Any`
  and is checked only where a type is written; inside a generic function a `T`-typed value is `Any`.
- [x] **ERR-002 `Option<T>` (P0).** `Some(T)`/`None`; absence kept apart from failure; `match` exhaustiveness is checked when the
  scrutinee's type is known (E0301) and is a **warning** (W0301) when it is not; the interaction with `Result` is defined
  (`ok_or` converts absence into an error explicitly; `?` on the wrong kind is E0403). *Not provided:* a `Result` → `Option`
  conversion helper (use `match`).
- [x] **ERR-003 `?` (P0).** Works on `Result`/`Option` only (a statically known other type is E0401; on `Any`, a runtime
  `TypeError`); the success value is extracted with the right type; the failure is returned **unchanged**; nothing after it runs
  on that path, **nothing to its right in the same expression runs**; enclosing `finally` blocks run first; nested calls
  propagate to the right caller; one shared decision function is used by both engines. *Not allowed:* at the top level or in a
  `test` block (E0402), in a `finally` (E0407), directly after an argument of a paren-free call (syntax error: ambiguous).
- [x] **ERR-004 Error type checking (P0).** The compiler checks that the error type propagated by `?` is the one the function
  declares — **exact matching only** (E0404, with the explicit-conversion help) — and that `?` and the function agree on
  `Result` vs `Option` (E0403). The required negative test for incompatible error types is rejected with a located diagnostic.
  *Not done:* any conversion mechanism beyond `match`/`ok_or` (see "Decisions" #1).
- [x] **ERR-005 Structured errors (P0).** An error value has `message`, `kind`, `code`, `cause` (an `Option`) and `trace`;
  codes are stable and documented (compile-time `E`, warnings `W`, runtime `R0001`–`R0015`, program-chosen via `fail_with`);
  wording can change without the code changing. Compile-time diagnostics and runtime errors are separate representations,
  as the spec allows.
- [x] **ERR-006 Exception handling (P1).** `try`/`catch`/`finally` rules documented and tested: what `catch` binds, that nothing is
  caught by accident (an `Err` is a value, not an exception), the order and scope of cleanup, a failing finalizer, `return`/`?`
  forbidden in `finally`. **Catching a failure grants no capability** (tested: still denied in the handler and in `finally`).
- [x] **ERR-007 Stack traces (P1).** Function, file and line, outermost first, across calls, modules, `spawn`/`wait` and
  `finally`; identical on both engines. *Limits:* statement-granular (no columns); a very long trace is shortened in the middle.
- [x] **ERR-008 Error chaining (P1).** `fail(message, cause)` / `fail_with(code, message, cause)`; `e.cause` is an `Option`;
  a failing finalizer chains the failure it replaced; an uncaught failure prints every cause with its own code and trace.
- [x] **ERR-009 Interpreter/VM parity (P0).** Both engines: the same results, output, messages, codes, traces and exit status,
  compared byte for byte by `tests/test_phase4.py` on the interpreter, the VM and the VM at `-O2`. The decisions are made in
  one shared place, not twice. *Permitted differences (documented):* the depth at which runaway recursion stops (interpreter:
  whatever the host stack allows, about 160 calls in this environment; VM: 10 000) — same failure, same code.
  *Pre-existing, unrelated:* a function and an enum variant with the **same name** resolve differently in the two engines
  (documented in `errors.md` §4.14; a program should not declare both).
- [x] **ERR-010 Diagnostics (P0).** Every static diagnostic has a code, line, **column range**, the offending source underlined,
  `expected`/`found` notes and — where the fix is not obvious — a `help`; warnings are distinguished from errors. *Limits:* a
  **syntax error** still has only a line (no column, no code; `E0001` is reserved); a runtime error has a trace, not a column; a
  parameter/return type annotation is underlined as part of its whole declaration rather than on its own (a `let` annotation is
  underlined exactly).
- [x] **ERR-011 Testing (P0).** `tests/test_phase4.py` (380) covers every row of the spec's table — result construction, generic
  type checking, option handling, propagation, error compatibility, evaluation order, nested calls, pattern matching, exception
  handling, resource cleanup, interpreter/VM parity, regression — and each case runs on three execution paths. `tests/phase4_mutation_check.py`
  breaks one rule at a time in a copy (40 mutants): every one must be killed (result: all 40 killed — see "Verification" below).
- [x] **ERR-012 Documentation (P0).** `docs/errors.md` (3.1 chapter), `types.md`, `language.md`, plus `grammar.md` and
  `standard-library.md`. **Every `lumen run` / `lumen error` example in them (173) is executed** by `tests/test_docs.py` on all
  three paths and must print what the document says (a floor on the number of examples per chapter keeps them from being deleted
  one by one). The changes that can affect a 3.0 program are listed in `errors.md` §4.13 and `CHANGELOG.md`.

## The spec's release gate

**Type system**
- [x] `Result<T, E>` works with generic types.
- [x] `Option<T>` handles absence correctly.
- [x] Error payloads are type-checked.
- [x] Error conversions follow explicit rules (exact matching; explicit conversion by `match` / `ok_or` — nothing implicit).

**Language semantics**
- [x] `?` propagates errors correctly (typing and control flow defined first, then implemented identically on both engines).
- [x] Evaluation order is defined (`language.md` §1.9) and tested.
- [x] Exception behaviour is documented.
- [x] Resource cleanup follows defined rules (`finally`; `errors.md` §4.6) — runs once on normal end, `return`, `?` and failure;
  failing cleanup replaces and chains; no double cleanup. *Lumen has no destructors or `with` blocks; cleanup is explicit.*

**Runtime**
- [x] The interpreter implements the complete supported behaviour.
- [x] The VM implements the same behaviour.
- [x] Errors preserve their useful information (code, message, cause, trace).
- [x] Runtime failures do not corrupt execution state (three VM state-corruption defects that predate this phase were found and
  fixed — see "Defects found" below).
- [x] **No silent loss of errors in supported propagation paths.** A discarded `Result` is a compile error (E0405); a failing
  finalizer chains what it replaced; a spawned task's failure nobody waited for is reported on standard error. *Limits:* see
  "Known limits".

**Diagnostics and testing**
- [x] Compiler diagnostics identify the source of type errors.
- [x] Runtime errors provide useful context.
- [x] Positive and negative tests pass.
- [x] Interpreter/VM conformance tests pass.
- [x] Regression tests cover known defects.

**Documentation**
- [x] Error semantics are documented.
- [x] All examples are verified.
- [x] Unsupported behaviour and limitations are stated (`errors.md` §4.14 and below).

**The spec's own warning applies:** *"A partial Result implementation or a working `?` operator alone is not enough."* The list
above is what is implemented and tested; the sections below are what is **not**.

## Decisions for you (nothing here is hidden; each is a real choice, not a defect)

1. **Error conversion.** I implemented the spec's recommendation — exact error-type matching for `?`, no implicit conversion.
   There is no `From`-style mechanism and no `map_err` (Lumen has no first-class functions to pass). An `IOError` →
   `ConfigError` conversion is a `match` at the call site. Do you want a designed conversion mechanism next?
2. **`?` at the top level.** The spec's example `fn main(): let value = calculate()?` assumes an entry-point function that can
   propagate. Lumen programs have no `main`; top-level code is statements, so `?` there is rejected (E0402) with advice to
   `match`. Option: allow it and treat an `Err` as an uncaught failure (exit 1).
3. **I/O built-ins still raise.** The spec's `fs.read(path)?` presumes `Result`-returning I/O. `read_file`, `http_get`,
   `list_dir` and the `std/*.lum` modules predate `Result` and raise on failure (documented; `errors.md` §4.15 shows the
   wrapper). Migrating them is a **breaking** change; adding `Result`-returning variants under new names is additive. Which?
4. **Exit status of an unwaited failed task.** It is now *reported* (stderr) but the exit status stays 0, so existing scripts
   do not start failing. Should it exit non-zero?
5. **`Result` → `Option`** has no helper (`match` it).

## Known limits (not decisions — these are what the implementation does not do)

- A `Result` bound to a variable and then never looked at is **not** flagged; only a `Result` discarded as an expression
  statement is (E0405).
- A task that **outlives the program** is abandoned (as it always was) and its later failure is not seen; a task still running
  when the program ends gets a 0.1 s grace, then is abandoned.
- Type annotations are not runtime assertions: `Any` can put a wrong-typed value into a typed field or `Result`, and the program
  finds out when something uses it (`types.md` §3.9). Reassigning a name declared with a primitive type is not checked.
- Syntax errors have no column and no code. Runtime traces have lines, not columns.
- The interpreter's recursion depth is the host's (about 160 calls here); the VM stops at 10 000.
- One host: there is no native backend, and this environment is Linux only.
- **No independent review** of the implementation or the tests has been done. The mutation check shows the tests notice the rules
  they were written for being broken; it does not show the rules are complete.

## Defects found and fixed on the way (all predate 3.1; each has a regression test)

- VM: a failure or early `return` in a `for` loop / mid-expression left a stale iterator or operand (wrong values later, or a loop
  that never ended).
- VM: no recursion limit — runaway recursion exhausted memory; now the catchable `stack overflow` (R0009).
- VM: two `spawn`ed tasks calling the same imported function corrupted each other's state (per-thread VM state now).
- Both engines: a task that hit an implementation fault left `wait` blocked forever.
- `%` by zero crashed the interpreter with a host traceback and was not catchable on the VM.
- A module's functions showed the wrong file name in interpreter traces.
- *Found while documenting, closed:* `p.x = "text"` on an `Int` field was not checked; enum/struct dict keys changed meaning
  (structural, disclosed as change #7 in `errors.md` §4.13); a failure in a task nobody waited for vanished.

## Performance (measured, same machine, `fib(25)` and start-up, best of several runs, vs 3.0.0-5c)

| | 3.0.0-5c | 3.1.0-p4 |
|---|---|---|
| interpreter, `fib(25)` (in-process) | ≈ 1.2 s | ≈ 1.2 s (level — see below) |
| VM, `fib(25)` (command line) | ≈ 1.44 s | ≈ 1.49 s (+3–4 %) |
| process start-up (`print 1`) | ≈ 0.10 s | ≈ 0.13 s |

The first cut of this phase made the interpreter ≈ 12–19 % slower on call-heavy code; that was recovered by tracking the current
line on the scope instead of a thread-local read per statement, and by sending plain user-function calls past the chain of
built-in name tests (pinned by a test that the chain's names stay inside the built-in set). The start-up cost is the larger
source file compiled on every launch. These are single-machine, small-sample numbers, not a benchmark.

## Verification (the final tree)

| Suite | Count |
|---|---|
| Regression + CLI (`tests/run_tests.py`) | 37 |
| Spec-driven conformance (`tests/run_conformance.py`) | 735 |
| Interpreter ↔ VM equivalence (`tests/test_equivalence.py`) | 193 |
| Optimizer stability, `-O0`/`-O1`/`-O2` (`tests/test_optimizer.py`) | 28 |
| Adversarial security (`tests/test_security.py`) | 184 |
| Standard library, both backends (`tests/run_stdlib.py`) | 148 × 2 |
| Consent prompt + audit log, through the CLI (`tests/test_consent_cli.py`) | 102 |
| **Phase 4 error handling (`tests/test_phase4.py`)** | **380** |
| **Verified documentation examples (`tests/test_docs.py`)** | **173** |

`python3 tests/run_all.py` runs all of them. The two mutation checks run on demand (they are slow) and were run on the final
tree: **`tests/phase4_mutation_check.py` — 40 mutants, 40 killed, 0 survivors** (a mutant that no longer matches the source is
reported as a failure, not a pass — one did after the performance change and was fixed, not ignored);
**`tests/security_mutation_check.py` — 44 mutants, 44 killed, 0 survivors** (the capability layer was not changed in this
release; this confirms the security tests still notice each check being weakened).
