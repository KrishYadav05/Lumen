# docs/standard-library.md

# Lumen Language Specification — Chapter 8: Standard Library

Status: **Normative.**

## 8.1 There Are No Importable Standard Library Modules

Every name in this chapter is a **host-provided built-in**, permanently present in
every module's global scope (`modules.md` §5.7). None require a `use` statement to
become visible (`use fs`/`use net` gate *calling* three of them, §8.7 — they are
still visible/referenceable as names without those grants; only the call itself is
gated). There is no `.lum`-source standard library and no additional built-in beyond
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
| `read_file(path: Str) -> Str` | Reads the entire file at `path` and returns its contents as a `Str`. Raises a catchable failure (`errors.md` §4.4.8) naming the OS-level reason if the path cannot be opened/read. | `fs` |
| `write_file(path: Str, content: Str) -> Any` | Writes `content`, overwriting the file at `path` in full (not append — there is no append mode in v1.0). Returns `nothing` (Lumen has no distinct `Unit`/`Void` type; a call made purely for effect returns the same `nothing` value described in §8.3). Raises a catchable failure naming the OS-level reason on failure. | `fs` |
| `http_get(url: Str) -> Str` | Performs an HTTP GET and returns the response body as a `Str`. **A non-2xx HTTP status is itself a failure**, not a returned value — there is no way in v1.0 to inspect a status code or response headers; `http_get` either yields the successful body or raises. DNS failure, connection refused, and timeout are all the same failure category, differing only in the free-text reason embedded in the message. | `net` |

Calling any of the three without its capability granted raises `PermissionError`
before any I/O is attempted (`errors.md` §4.4.7); the capability check happens first,
unconditionally.

## 8.5 Collection Functions

| Signature | Behavior |
|---|---|
| `len(x: List \| Dict \| Str) -> Int` | Number of elements (`List`), key/value pairs (`Dict`), or characters (`Str`). Raises `TypeError` for any other argument type — `len` is not defined for `Int`, `Float`, `Bool`, `struct`, or `enum` values. |
| `push(list: List, value: Any) -> Any` | Appends `value` to the end of `list` **in place** (mutates the same `List` every other reference to it observes, per `types.md` §3.3's reference semantics) and returns `nothing`. Raises `TypeError` if the first argument is not a `List`. |
| `keys(d: Dict) -> List` | Returns a new `List` of `d`'s keys, in insertion order. Raises `TypeError` if the argument is not a `Dict`. |
| `values(d: Dict) -> List` | Returns a new `List` of `d`'s values, in insertion order (parallel to `keys`'s ordering for the same `d`). Raises `TypeError` if the argument is not a `Dict`. |

There is no `pop`, `remove`, `slice`, `sort`, `map`, `filter`, `reduce`, `join`,
`split`, or any string-manipulation built-in beyond what indexing/`+`/`len` already
provide in v1.0. This is a deliberately minimal v1.0 surface; additional collection
built-ins are additive and are natural candidates for a `1.x` release
(`modules.md` §5.6).

## 8.6 Explicit Failure

| Signature | Behavior |
|---|---|
| `fail(message: Str) -> Any` (never actually returns) | Immediately raises a runtime failure whose `.message` is exactly `message`, unmodified — no prefix is added (contrast every other built-in failure in `errors.md` §4.4, which all carry a fixed prefix like `TypeError:`). This is the only failure form with a fully programmer-controlled message; intended for signaling domain-specific conditions from library code. |

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
