# docs/standard-library.md

# Lumen Language Specification — Chapter 8: Standard Library

Status: **Normative.**

## 8.1 There Are No Importable Standard Library Modules

Every name in this chapter is a **host-provided built-in**, permanently present in
every module's global scope (`modules.md` §5.7). None require a `use` statement to
become visible (eight of them are *capability-gated*, §8.4: calling one needs its capability declared with
`use` and granted by the host — they are still visible/referenceable as names without
that; only the call itself is gated). There is no `.lum`-source standard library and no additional built-in beyond
what is listed below in v1.0.

Built-in names are ordinary global bindings, not reserved keywords (`language.md`
§1.7): a user `let len = 5` or `fn push(...)` shadows the built-in for the rest of that
scope. This is legal and un-flagged by the checker.

## 8.2 `print` (statement, not a function)

```ebnf
print_stmt = 'print' , expression ;
```

Evaluates the expression, converts it to display form (§8.3), writes it followed by a
single `\n` to standard output. `print` is a statement, not a callable value — it
cannot be passed as an argument, assigned, or used inside an expression.

## 8.3 Display Conversion Rules (used by `print` and by `+`'s string-coercion rule)

| Value | Display form |
|---|---|
| `Int` | decimal digits, no separators |
| `Float` | host's default float-to-string conversion |
| `Str` | the string's own content, unquoted |
| `Bool` | `true` or `false` |
| `List` | `[e1, e2, ...]`, each element display-converted recursively, comma-space separated |
| `Dict` | `{k1: v1, k2: v2, ...}`, insertion order, each key/value display-converted recursively |
| `struct` instance | `StructName { field1: v1, field2: v2 }`, declared field order |
| `enum` instance, payload variant | `Variant(v1, v2)` |
| `enum` instance, unit variant | `Variant` (bare name, no parens) |
| a value returned by nothing (a function whose only path falls off the end without `return`, or whose `return`-less path is taken) | `nothing` |
| task handle (`spawn` result) | `<task>` |
| channel (`chan()` result) | `<channel>` |
| module namespace (`use "..." as x` result) | `<module {alias}>` |

There is no dedicated `to_string`/`str()` conversion function separate from this table
in v1.0; the only way to obtain a `Str` from a non-`Str` value is `+`'s coercion rule
(`types.md` §3.6.1), e.g. `"" + 5` yields `"5"`.

## 8.4 I/O Functions

| Signature | Behavior | Capability required |
|---|---|---|
| `read_file(path: Str) -> Str` | Reads the entire file at `path` and returns its contents as a `Str`. Raises a catchable failure (`errors.md` §4.4.8) naming the OS-level reason if the path cannot be opened/read. | `fs.read` |
| `write_file(path: Str, content: Str) -> Any` | Writes `content`, overwriting the file at `path` in full (`write_file` itself never appends — see `append_file` below, added in 2.3). Returns `nothing` (Lumen has no distinct `Unit`/`Void` type; a call made purely for effect returns the same `nothing` value described in §8.3). Raises a catchable failure naming the OS-level reason on failure. | `fs.write` |
| `append_file(path: Str, content: Str) -> Any` | *(2.3)* Appends `content` to the end of the file at `path`, first creating the file (empty) if it does not exist; existing contents are left untouched. Returns `nothing`. Raises a catchable failure `could not append to '{path}': {os_reason}` if the file cannot be opened for appending. | `fs.write` |
| `file_exists(path: Str) -> Bool` | *(2.3)* `true` if `path` names an existing file **or directory**, `false` otherwise. It never raises (beyond the capability check): a path that cannot be inspected for any OS-level reason is simply reported as `false`. | `fs.read` |
| `is_dir(path: Str) -> Bool` | *(2.3)* `true` only if `path` names an existing directory; `false` for a regular file and for a path that does not exist. Like `file_exists`, it never raises beyond the capability check. Together the two distinguish a file (`file_exists(p) and not is_dir(p)`) from a directory from nothing. | `fs.read` |
| `list_dir(path: Str) -> List` | *(2.3)* The entries directly inside the directory at `path`, as a `List` of `Str` **names** (not paths — join them to `path` yourself), sorted ascending in code-point order so the result is deterministic. Never includes `.` or `..`; an empty directory gives `[]`. Entries are not distinguished by kind: files and subdirectories are listed together (use `is_dir` to tell them apart). Raises a catchable failure `could not list directory '{path}': {os_reason}` if `path` does not exist, is not a directory, or cannot be read. | `fs.list` |
| `delete_file(path: Str) -> Any` | *(2.3)* Deletes the file at `path`. Returns `nothing`. **Files only:** pointing it at a directory fails (there is deliberately no way to delete a directory, recursively or otherwise). Raises a catchable failure `could not delete '{path}': {os_reason}` if the file does not exist, is a directory, or cannot be removed. | `fs.write` |
| `http_get(url: Str) -> Str` | Performs an HTTP GET and returns the response body as a `Str`. **A non-2xx HTTP status is itself a failure**, not a returned value — there is no way in v1.0 to inspect a status code or response headers; `http_get` either yields the successful body or raises. DNS failure, connection refused, and timeout are all the same failure category, differing only in the free-text reason embedded in the message. | `net.http` |

Each of these needs its capability **declared** (`use fs.read` …, at the top level of the file
and of every file that imports it) **and granted by the host** (`--allow-read`, `--allow-write`,
`--allow-list`, `--allow-net`; optionally restricted to directories or hosts) — declaring never
grants (`capabilities.md`). Otherwise the call raises `PermissionError` before any I/O is
attempted (`errors.md` §4.4.7); the checks happen first, unconditionally. Every argument must
be a `Str` (a wrong type is a `TypeError`: `read_file(0)` is *not* a read of file descriptor
0), and a path or URL may not contain a NUL character. The capabilities are independent: reading,
writing/deleting and listing are three separate permissions (so are `fs` and `net`), and
`use fs` / `use net` remain as declaration shorthands for all three `fs.*` / for `net.http`.
`http_get` accepts only `http://` and `https://` URLs, applies the granted host scope to the
initial URL **and every redirect**, and never reads a local file. Not provided, and not
expressible with these primitives: creating or removing a directory, renaming, file metadata
(size in bytes, timestamps, permissions), binary I/O, and streaming (every read and write is
whole-file).

## 8.5 Collection Functions

| Signature | Behavior |
|---|---|
| `len(x: List \| Dict \| Str) -> Int` | Number of elements (`List`), key/value pairs (`Dict`), or characters (`Str`). Raises `TypeError` for any other argument type — `len` is not defined for `Int`, `Float`, `Bool`, `struct`, or `enum` values. |
| `push(list: List, value: Any) -> Any` | Appends `value` to the end of `list` **in place** (mutates the same `List` every other reference to it observes, per `types.md` §3.3's reference semantics) and returns `nothing`. Raises `TypeError` if the first argument is not a `List`. |
| `keys(d: Dict) -> List` | Returns a new `List` of `d`'s keys, in insertion order. Raises `TypeError` if the argument is not a `Dict`. |
| `values(d: Dict) -> List` | Returns a new `List` of `d`'s values, in insertion order (parallel to `keys`'s ordering for the same `d`). Raises `TypeError` if the argument is not a `Dict`. |
| `range(n: Int) -> List` | **New in 2.0.** Returns `[0, 1, ..., n-1]`. Returns `[]` (not an error) if `n <= 0`. Raises `TypeError` if `n` is not an `Int`. Implemented natively (no Lumen-level recursion), so it is safe to drive counted loops of any size without risking the interpreter's call-depth limit — this is the standard idiom for `for i in range(n):`. |
| `range(start: Int, end: Int) -> List` | **New in 2.0.** Two-argument form. Returns `[start, start+1, ..., end-1]`. Returns `[]` (not an error) if `end <= start`. Raises `TypeError` if either argument is not an `Int`, or if called with any other argument count. |

There is no `pop`, `remove`, `slice`, `sort`, `map`, `filter`, `reduce`, `join`,
`split`, or any string-manipulation built-in beyond what indexing/`+`/`len`/`range`
already provide in core v2.0 — those operations are covered by the standard
library modules instead (`std/collections.lum`, `std/strings.lum`; see
`stdlib-modules.md`). Additional core built-ins remain additive and are natural
candidates for a future release (`modules.md` §5.6).

## 8.5.1 Character Code Functions (added post-1.0, Phase 3)

| Signature | Behavior |
|---|---|
| `chr(code: Int) -> Str` | Returns the length-1 `Str` whose single character has Unicode code point `code`. Raises `TypeError` if `code` is not an `Int`, or if it is outside `0..=1114111` (`0x10FFFF`, the full Unicode range). |
| `ord(c: Str) -> Int` | Returns the Unicode code point of `c`, a length-1 `Str`. Raises `TypeError` if `c` is not a `Str` of exactly length 1. |
| `type_of(v: Any) -> Str` | Returns `v`'s runtime type as one of the exact type-name strings used elsewhere in this spec: `"Int"`, `"Float"`, `"Bool"`, `"Str"`, `"List"`, `"Dict"`; a `struct`/`enum` value's own declared type name (e.g. `"P"`, `"Opt"` — not the variant name for an enum); `"Task"` (a `spawn` handle), `"Channel"` (`chan()`), `"Module"` (a `use "path" as name` import); or `"Nothing"` for the value documented in §8.3 as returned by a function that falls off its end without `return`. Never raises — every Lumen value has exactly one of these. |

**Why these exist:** `language.md` §1.8 documents, as a frozen v1.0 lexical
limitation, that there is no string-escape syntax and therefore no way to
write a `"` character directly inside a source string literal (the token
grammar is `"[^"]*"` — the first `"` always ends the literal). Before `chr`/
`ord`, this meant there was no way *at all*, anywhere in the language, for a
program to construct a string value containing a `"` character — which
blocks anything that needs to emit quoted output (`std/json.lum`'s encoder,
for one). `chr(34)` is the fix: a library-level workaround for a lexical gap,
rather than a lexer/grammar change (consistent with §1.8's own note that a
post-1.0 proposal introducing escape sequences remains a *separate*, still-
open option). More generally, `chr`/`ord` are the only character-by-code-point
operations in the language — useful for anything working with character
classes or codes beyond the dict-lookup-table idiom `std/strings.lum` uses
for case conversion.

`type_of` fills a parallel gap: there was no `is_list`/`is_dict`/`is_int`-
style predicate anywhere, and the obvious workaround — comparing a value
against `true`/`false` to detect `Bool` — is actively wrong in Lumen,
because `Bool` is implemented as an `Int` subtype at the runtime level
(`0 == false` and `1 == true` are both `true`), so that comparison silently
misclassifies ordinary integers. `type_of` is the one safe, general way to
branch on a value's runtime shape — needed by `std/json.lum`'s encoder,
and by any other code that has to handle several possible value shapes.

## 8.6 Explicit Failure

| Signature | Behavior |
|---|---|
| `fail(message: Str) -> Any` (never actually returns) | Immediately raises a runtime failure whose `.message` is exactly `message`, unmodified — no prefix is added (contrast every other built-in failure in `errors.md` §4.4, which all carry a fixed prefix like `TypeError:`). This is the only failure form with a fully programmer-controlled message; intended for signaling domain-specific conditions from library code. |
| `fail(message: Str, cause: Error) -> Any` *(3.1)* | As above, with the error a `catch` bound as the new failure's cause (`errors.md` §4.10). |
| `fail_with(code: Str, message: Str) -> Any` and `fail_with(code, message, cause)` *(3.1)* | As `fail`, with a program-chosen code that a handler can test instead of parsing the message (`errors.md` §4.4.11). |

### 8.6.1 `Result` and `Option` helpers *(3.1)*

| Signature | Behavior |
|---|---|
| `ok_or(option: Option<T>, error: E) -> Result<T, E>` | `Some(v)` → `Ok(v)`; `None` → `Err(error)`. |
| `unwrap(x: Result<T, E> or Option<T>) -> T` | The payload of `Ok`/`Some`; on `Err`/`None` a failure with code R0011. |
| `unwrap_or(x: Result<T, E> or Option<T>, default: T) -> T` | The payload, or `default` for `Err`/`None`. |
| `is_ok(r)`, `is_err(r)` / `is_some(o)`, `is_none(o)` `-> Bool` | Test which variant a `Result` / `Option` is. |

The wrong kind of argument is a `TypeError`. Full semantics and verified examples: `errors.md` §4.4.11–§4.4.12.
`Some`, `None`, `Ok` and `Err` are the variants of the library enums `Option<T>` and `Result<T, E>`
(`errors.md` §4.2.2); they are constructors, not functions in this table.

## 8.7 Concurrency

Full semantics in `concurrency.md` Chapter 6; signatures repeated here for completeness:

| Signature | Behavior |
|---|---|
| `wait(t: <task>) -> Any` | Blocks until the task `t` (produced by `spawn`) completes; returns its result or re-raises its failure. `TypeError` if `t` is not a task handle. |
| `chan() -> <channel>` | Creates a new unbounded, thread-safe FIFO queue. |
| `send(ch: <channel>, value: Any) -> Any` | Enqueues `value`; never blocks. `TypeError` if `ch` is not a channel. |
| `receive(ch: <channel>) -> Any` | Dequeues and returns the oldest value, blocking if empty. `TypeError` if `ch` is not a channel. |

`spawn` itself is `grammar.md` §2.5/§2.2 syntax (`spawn f(args)`), not a callable
built-in value — it cannot be referenced as a name.

## 8.8 CLI (not part of the language proper, but part of the standard toolchain)

These are `lumen <subcommand>` shell invocations, not Lumen-language constructs:

| Command | Behavior |
|---|---|
| `lumen run [--vm] <file.lum>` | Type-checks then executes `<file.lum>`. `--vm` runs on the bytecode-VM backend (a supported subset only — unsupported constructs fail with a named error rather than silently falling back, per the VM's documented scope). |
| `lumen check <file.lum>` | Runs the parser and static type checker only; never executes the program. Prints `No errors found in <file>` or the first error, with the same messages `run` would produce for the same static failure. |
| `lumen repl` | Interactive line-at-a-time evaluation against one persistent `Interpreter` instance. Each line is independently lexed/parsed/checked/executed; multi-line block statements (`if`, `fn`, etc.) are not supported across multiple REPL prompts in v1.0 — a block header and its body must be enterable as the parser's single-statement grammar allows, which in practice limits the REPL to single-line statements. |
| `lumen new <name>` | Scaffolds a directory `<name>/` containing `main.lum` (a one-line `print` program) and `lumen.toml` (with an empty `[dependencies]` table). |
| `lumen install` | Reads `[dependencies]` from `./lumen.toml`; for each listed package, reports whether it is already present under `./lumen_packages/<name>/` or reports it as not found (`modules.md` §5.4). **Does not fetch anything from a network registry** — Lumen 1.0 has no public package registry; this command is a manifest-vs-local-disk consistency check and reporting tool only, never claims to have installed something it did not actually place on disk. |

---

# Lumen Language Specification 1.0 — Design Frozen

This concludes the eight-chapter Lumen 1.0 specification (`language.md`,
`grammar.md`, `types.md`, `errors.md`, `modules.md`, `concurrency.md`,
`security.md`, `standard-library.md`), backed by a 247-test spec-driven conformance
suite (`tests/conformance/`, one file per chapter, run via `tests/run_conformance.py`)
plus a 34-test regression/CLI suite (`tests/run_tests.py`) — 281 automated tests in
total, run together via `tests/run_all.py`.

Every semantic claim in this suite was checked directly against the reference
implementation's actual runtime behavior while writing it, not asserted from memory.
That process found and fixed four genuine implementation defects before they could be
codified as "design": unbounded-recursion crashes on circular module imports
(`modules.md` §5.6); uncontrolled host-level crashes from `push`/`keys`/`values`/`len`/
`send`/`receive` on wrong-typed arguments (`standard-library.md` §8.5, §8.7); and
silent acceptance of structurally inconsistent indentation that should have been a
syntax error (`language.md` §1.5). It also corrected two claims that were written
before being tested and turned out to describe intended-but-not-actual behavior
rather than the reverse: `Bool`'s arithmetic/equality behavior and `List`/`Str`
repetition via `*` (`types.md` §3.6.1), and the discovery that functions are not
first-class values at all (`types.md` §3.10, a real and significant semantic that had
no documentation prior to being found by a failing test). Each fix was verified with a
regression test before being documented as frozen behavior, so this specification
describes correct, intended 1.0 behavior rather than codifying bugs as design.

Every other statement of "known limitation" in this suite (no string escapes, no
`const`, no visibility control, no `finally`, no locks, no FFI, non-exhaustive `match`
checking, generics not checked inside function bodies, no module caching, no
first-class functions) is a deliberate, explicit v1.0 scope boundary, not an
unexamined gap, and each has a stated path for post-1.0, additive, backward-compatible
extension.

No further breaking changes to the constructs specified in this suite will be made
under the `1.x` line; only additive extensions are permitted without a major version
increment, per the evolution process described in `modules.md` §5.6.

**Lumen Language Specification 1.0 — Design Frozen.**

---

# Addendum: Language Specification 2.0

The `1.x` line's "no further breaking changes" commitment above was itself broken
once, deliberately, in 2.0: bare reassignment (`x = expr`, as opposed to `let x =
expr`) previously parsed to the same AST node as a fresh `let` declaration, so
writing to an outer variable from inside a nested `if`/`for`/`try`/`match` block
silently shadowed it in that inner scope instead of mutating it — e.g. a
`total = total + n` accumulator inside a `for` loop body silently did nothing to
the outer `total`. This was a real implementation defect, not an intended design,
caught from direct user-facing feedback that idiomatic-looking loop code produced
wrong answers. §1.11.1 of `language.md` has the corrected rule and full examples;
`CHANGELOG.md`'s 2.0 entry has the complete before/after. Every conformance test
that had encoded the old, buggy behavior as "correct" (`language.md` §1.11,
`modules.md` §5.5, `security.md` §7.4) was identified and rewritten to assert the
corrected behavior instead, rather than left in place or deleted.

2.0 also adds one new core built-in, `range()` (§8.5 above), implemented natively
rather than in Lumen itself specifically so that counted loops cannot hit the
interpreter's recursion-depth limit. With both of these in place, the standard
library modules (`std/collections.lum`, `std/math.lum`, `std/strings.lum`,
`std/io.lum`) were rewritten from recursive helpers to plain iterative loops —
shorter to read, and no longer carrying the recursion-depth risk the 1.x versions
had for large inputs.

**Lumen Language Specification 2.0 — Design Frozen.** The same "additive-only"
commitment above now applies from this baseline forward.
