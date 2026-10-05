# docs/errors.md

# Lumen Language Specification — Chapter 4: Error-Handling Model

Status: **Normative.**

## 4.1 Two-Phase Failure Model

Lumen has exactly two, non-overlapping phases in which a program can fail. They use
different mechanisms and are not to be confused:

1. **Static-phase failure** (before execution starts): a `SyntaxError` from the parser,
   or a `TypeError` from the static type-checking pass (`types.md` §3.6). If either
   occurs, **the program does not execute at all** — not one statement runs. This
   phase has no recovery mechanism; `try`/`catch` is a runtime construct and cannot
   intercept a static-phase failure, because no code is executing yet when it occurs.
2. **Runtime-phase failure**: anything raised while statements are executing. This is
   the domain of `try`/`catch`, detailed below.

There is no third phase; Lumen 1.0 has no separate "linking" or "loading" failure
class distinct from module resolution errors, which are themselves runtime-phase
(§4.7).

## 4.2 Runtime Failures Are Values, Uniformly — There Is No Exception Hierarchy

Lumen does **not** distinguish "recoverable" vs. "unrecoverable" runtime failures at
the language level, and does **not** expose a typed exception hierarchy. There is
exactly one runtime failure protocol:

- A failure is raised at the point of occurrence, carrying a single, plain-text
  message (§4.3–§4.8 enumerate every message form the reference implementation
  produces).
- It propagates up through the call stack — unwinding function calls, `if`/`for`
  bodies, and nested `try` blocks that are not the failure's own — exactly like the
  host runtime's native exception propagation, because that is the mechanism the
  reference implementation is built on.
- The **first enclosing `try`/`catch`** on the stack, if any, intercepts it: execution
  jumps to that `catch`'s body, with `catch NAME` binding `NAME` to a value that
  exposes exactly one field, `.message` (a `Str`). No other structured data (an error
  "kind"/"code" field, a stack trace object, a cause chain) is exposed to Lumen code in
  v1.0.
- If no enclosing `try`/`catch` exists anywhere up the call stack, the failure reaches
  the top level, the runtime prints its message to standard output prefixed as shown
  by the specific failure kind (§4.3–§4.8), and the process exits with a non-zero
  status. **This applies uniformly** — a `NameError` from a typo and a caught-by-design
  `"division by zero"` both terminate the program the same way if nothing catches
  them, and both are equally catchable if something does. There is no failure kind in
  Lumen 1.0 that is uncatchable-in-principle.

This is a deliberate design choice (`language.md` §1.1, goal 3): rather than force
programmers to pre-declare which failures are "expected" (would need a `Result`/`Option`
return-type convention) vs. "exceptional" (would need a typed exception hierarchy with
catch-by-type), Lumen has one mechanism and lets `try`/`catch` be reached for whenever
the programmer judges it useful, including around what other languages would call
"programmer errors."

## 4.3 Panic Conditions Requiring No `try`/`catch` to Reach Safely

The following are **not** failures that terminate the process outright when uncaught in
some unrecoverable way beyond normal propagation — they follow the exact rule of §4.2
(catchable if wrapped, terminating with a printed message if not). Lumen 1.0 has **no**
failure category that skips normal `try`/`catch` interception (no `abort()`, no
segfault-equivalent, no unrecoverable panic distinct from an ordinary uncaught
failure), because Lumen has no manually-managed memory or pointers for such a
distinct category to exist over (`security.md` §6.1).

## 4.4 Runtime Failure Catalogue

Every message the reference implementation can produce, grouped by cause. `Str`
interpolations are shown in `{braces}`.

### 4.4.1 Arithmetic and comparison

| Trigger | Message |
|---|---|
| `/` with a zero divisor | `division by zero` |
| Incompatible operand types for `+ - * / % == != < > <= >=` | `TypeError: cannot apply '{op}' to {type1} and {type2}` |

### 4.4.2 Name resolution

| Trigger | Message |
|---|---|
| Reading an unbound variable | `NameError: '{name}' is not defined` |
| Calling an unbound function name | `NameError: function '{name}' is not defined` |
| `spawn`-ing an unbound function name | `NameError: function '{name}' is not defined (line {n})` |
| Reading a nonexistent field on a module namespace | `NameError: module '{alias}' has no value '{field}'` |
| Calling a nonexistent function on a module namespace | `NameError: module '{alias}' has no function '{name}'` |
| Reading a nonexistent field on a struct instance | `NameError: {StructName} has no field '{field}'` |

### 4.4.3 Function and constructor arity/shape

| Trigger | Message |
|---|---|
| Wrong argument count to a user function | `TypeError: {name}() expects {n} arguments, got {m}` |
| Wrong payload-value count to an `enum` variant constructor | `TypeError: {Variant}() expects {n} value(s), got {m}` |
| `struct` literal with missing and/or extra fields | `TypeError: {StructName} {{ }} missing [...], unexpected [...]` (only the applicable one of "missing"/"unexpected" is included) |

### 4.4.4 Collections and indexing

| Trigger | Message |
|---|---|
| `list[i]` with `i` out of range | `index {i} is out of range (length {n})` |
| `dict[k]` with `k` absent | `key {k!r} not found` |
| Indexing a value that supports neither list- nor dict-style indexing | `TypeError: cannot index into this value with {k!r}` |
| Assigning through an index on a non-indexable value | `TypeError: cannot assign index on this value ({detail})` |

### 4.4.5 Field access/mutation on non-structs

| Trigger | Message |
|---|---|
| `.field` read on a value that is not a struct or module | `TypeError: cannot read field '{field}' on non-struct value` |
| `.field = ...` write on a value that is not a struct | `TypeError: cannot set field '{field}' on non-struct value` |
| Calling `.method(...)` on a value that is not a module namespace | `TypeError: '{name}' is not callable on this value` |

### 4.4.6 `match` exhaustiveness

| Trigger | Message |
|---|---|
| No `match_case` pattern matches the scrutinee, and control falls off the end of the `match` | `MatchError at line {n}: no pattern matched {value}` |

**`match` is not statically checked for exhaustiveness in v1.0.** A non-exhaustive
`match` is legal to write and only fails, as a normal catchable runtime failure, if
execution actually reaches it with an unmatched value. This is a known limitation
(exhaustiveness checking requires whole-`enum` static knowledge that the current
checker, scoped as described in `types.md` §3.6, does not perform); it is a candidate
for a post-1.0 additive tightening, not a 1.0 guarantee.

### 4.4.7 Capabilities (see `security.md` for the full model)

| Trigger | Message |
|---|---|
| Calling `read_file`/`write_file` without `use fs`, or `http_get` without `use net` | `PermissionError: {fn}() needs the '{cap}' capability — add 'use {cap}' at the top of your program to allow it` |

### 4.4.8 I/O failures (only reachable once the relevant capability has been granted)

| Trigger | Message |
|---|---|
| `read_file` on a path that cannot be opened/read | `could not read '{path}': {os_reason}` |
| `write_file` on a path that cannot be opened/written | `could not write '{path}': {os_reason}` |
| `http_get` failure of any kind — DNS resolution, timeout, connection refused, or the server responding with a non-2xx status (a 404, 500, etc. is treated as a failure, not a returned value — see `standard-library.md` §8.4) | `http_get failed: {reason}` |

### 4.4.9 Modules

| Trigger | Message |
|---|---|
| `use "path" as name` where `path` cannot be found (searched next to the importing file, then in `lumen_packages/` — `modules.md` §5.4) | `ModuleError: cannot find module '{path}' at line {n} (looked next to this file and in lumen_packages/)` |
| A static-phase (parse or type-check) failure *inside* an imported module, discovered while loading it | `ModuleError: error loading '{path}': {inner message}` |
| `use x` where `x` is not one of the two recognized capability names (`fs`, `net`) | `ModuleError: unknown module '{name}' at line {n}` |

### 4.4.10 Concurrency

`wait(t)` where `t` is not a value produced by `spawn`: `TypeError: wait() expects a
task returned by spawn`. All other concurrency failure modes are described in
`concurrency.md` §6.4 (`wait` re-raising the spawned call's own failure is not a new
message form — it re-raises the *original* failure, of whatever kind above, exactly as
it would have surfaced had the call not been spawned).

### 4.4.11 Explicit programmer-raised failures

`fail(message)` — a built-in (`standard-library.md` §8.6) — raises a runtime failure
whose `.message` is exactly the `Str` passed to it (no prefix is added). This is the
only failure form in Lumen 1.0 with a fully programmer-controlled message, intended for
library authors to signal domain-specific failures without a language-level way to
define new named error kinds (there is no `error` or exception-subclassing declaration
form in v1.0).

## 4.5 `match` Pattern Resolution Order (an explicitly resolved ambiguity)

A bare-name pattern (`NAME` with no parentheses, `grammar.md` §2.3) is ambiguous on its
face — it could mean "match only this specific unit variant" or "match anything and
bind it to this name." Lumen resolves this **at match time**, not parse time, by a
fixed rule: if `NAME` is the name of a declared **unit** variant (an `enum` variant
with no payload fields) reachable in the current scope, it is a **variant-equality
pattern** — it matches only an instance of that exact variant and binds nothing. In
every other case (an ordinary identifier, or the name of a *payload-carrying* variant
used without parentheses), it is a **catch-all binding pattern** — it always matches,
and binds the entire scrutinee value to that name in the case's block scope. There is
no way in v1.0 to write a binding pattern whose chosen name happens to coincide with a
unit variant's name; that combination is always resolved as the variant-equality
reading. `_` is reserved specifically as the unconditional wildcard (matches anything,
binds nothing, and is never treated as a variant name even if a variant is
(illegally, since `_` cannot be declared as a variant name — it collides with no
keyword restriction here but is conventionally reserved) named `_`).

## 4.6 Cleanup / `finally`

**Lumen 1.0 has no `finally` clause, no destructor mechanism, and no
resource-cleanup-on-unwind guarantee of any kind.** If a runtime failure propagates
past a block that opened a resource (e.g. a capability-gated `write_file` call that
partially wrote data before a later statement in the same `try` body failed), no
language-level mechanism runs cleanup code for that resource. This is an explicit,
frozen v1.0 gap — not a `TBD` — recorded here so it is not silently assumed to exist.
Programmers needing deterministic cleanup must structure code so the risky operation is
the last statement in its `try` body, or perform cleanup explicitly in both the
success path and the `catch` block.

## 4.7 Stack Unwinding Semantics

Because runtime failures propagate via the host runtime's native exception mechanism,
unwinding a Lumen call stack has the following precise effects, all of which follow
directly from `language.md` §1.11's scoping rules:

- Every block-local scope created between the failure site and the intercepting
  `catch` (or the top level) is discarded with the unwind; none of its bindings are
  observable afterward.
- Because function calls create a scope chained to the **global** scope (not the
  caller's local scope — `language.md` §1.11 Rule C), an unwind through a function call
  cannot corrupt or partially-mutate the *caller's local bindings* through the act of
  unwinding itself. It CAN leave global bindings, and the contents of any `List`/
  `Dict`/`struct` reachable from the call, in a partially-mutated state — reference
  types are not rolled back on unwind (§4.6 applies here too: there is no transactional
  rollback of mutations).
- A `spawn`ed task that fails is **not** unwound onto the spawning thread's stack at
  the point of failure. It is captured on the task handle and only re-raised, on the
  spawning thread, at the point where that thread calls `wait()` on the handle
  (`concurrency.md` §6.4). A spawned task whose failure is never `wait`ed on is
  silently discarded when the process exits — there is no "unhandled task failure"
  reporting in v1.0.

## 4.9 Phase 2 Addendum: Static Name Resolution and §4.1's Boundary

Phase 2 introduces a static Name Resolution pass (see the language implementation's
`Resolver`) that runs between parsing and type-checking. It moves a *subset* of what
was, under Phase 1's interpreter alone, a purely runtime `NameError` into the
static-failure phase (§4.1) instead: specifically, a reference to a name that does not
exist anywhere in the compilation unit, and that is not lexically inside a `try`
block's own body. This is deliberately scoped, not a blanket change:

- **§4.2's uniform-catchability guarantee is fully preserved, unchanged.** Any name
  reference lexically inside a `try` body — including one that turns out to be
  undefined everywhere — is *not* statically rejected; it is deferred to the exact
  same runtime check Phase 1 always had, and remains catchable exactly as documented
  above and exactly as tested by the existing conformance suite. `try: print nope
  catch e: ...` still works precisely as shown earlier in this chapter.
- **What does change:** a reference to an undefined name that is *not* inside any
  `try` now causes the failure to move from "some prior statements' side effects may
  have already happened, then the program fails" to "static-phase failure, §4.1: the
  program does not execute at all, not even earlier statements." Concretely,
  `print "before"\nprint nope` printed `before` under the Phase 1 interpreter alone;
  under Phase 2's full pipeline it prints nothing and fails before either statement
  runs. No test in the frozen Phase 1 conformance suite relied on the older timing
  (only `SyntaxError`/static-`TypeError` cases, which already worked this way, were
  tested), so this is an addition, not a violation, of the Phase 1 freeze — but it is
  a real, observable difference and is recorded here rather than left implicit.

---
