# Lumen 3.1.0-p4

Publisher: Krish Kumar

A small, readable, gradually-typed, **secure-by-default** general-purpose language.

**3.1.0-p4 (Language Specification 3.1) is Phase 4 — error handling & reliability, and it is additive.**
Failures the caller is expected to handle are now *values*: `Result<T, E>` and `Option<T>` are always in scope,
the static checker follows their types (`let r: Result<Int, Str> = Ok("hello")` is rejected, with
`expected` / `found` shown), and the postfix `?` propagates a failure up the call chain with the same typing and
control flow on both engines — with **no implicit error conversion** (an error of another type is a compile error
with a hint, not a silent cast). Exceptional failures stay `try`/`catch`, now with `finally` cleanup that runs exactly
once however the block ends, error values that carry a **code**, a **stack trace** and a **cause**, and compiler
diagnostics with the line **and column**, the offending code underlined, and an explanation. Generic `enum`s and
`struct`s with typed fields are part of it. A program written for 3.0 behaves the same except for the corners listed in
`docs/errors.md` §4.13 (e.g. `finally` is a keyword; enum/struct values compare by structure). See `docs/errors.md`
(every example in it is executed by `tests/test_docs.py`), `CHANGELOG.md`'s 3.1.0-p4 entry and `PHASE4_STATUS.md`
(what is and is **not** done). The security work below is Phase 5 and is unchanged.

**3.0.0-5c (Language Specification 3.0) is capability-based security, milestones 5A–5C (5C partly) —
a pre-release, and a breaking change.** A Lumen program can do nothing outside itself
(no file, no directory listing, no network) unless the person running it grants it:
`use fs.read` in a file only *declares* a need, and `lumen run --allow-read=./data file.lum`
*grants* it. With no flags, everything is denied. Read, write, list and network are
separate permissions, file access can be limited to directories and network access to hosts,
a library can never hold more authority than the program importing it, and denials are
structured errors. `lumen run --ask` shows what a program declares and asks before running it (default
deny, interactive terminal only), and `--audit-log=FILE` records every permission decision without contents or
credentials. See `docs/capabilities.md` (the rules), `docs/security-model.md` (the
threat model — and what is **not** protected), `docs/security-testing.md`, `CHANGELOG.md`'s
3.0.0-5a/5b/5c entries (migration table; the bypasses found and fixed) and
`PHASE5_STATUS.md` (what is and is not done: Linux only, no independent review, no saved grants). Lumen does not claim to be "completely secure".

(2.3.0 added five `fs` builtins, `std/fs.lum`, and a fix for `strings.split`/`replace`/
`substring`; 2.2.1 and 2.2.2 were library-only follow-ups.)
Core language design was frozen at Specification 1.0, extended additively in 1.1
(the `test` keyword), corrected with one deliberate breaking change in 2.0 (a real
scoping bug meant `x = x + 1`-style reassignment inside a nested block used to
silently do nothing to the outer variable — now fixed to work the way you'd expect
from Python/JS), and extended additively again in 2.1 with an optional
low-punctuation syntax: colons, commas, parens-on-calls, and `<T>` generics are
all now *optional* — `if x` works exactly like `if x:`, `add 2 3` exactly like
`add(2, 3)` — so either style, or a mix, is valid everywhere. 2.1.1 and 2.1.2
are pure internal performance fixes (no spec or behavior change — see
`CHANGELOG.md`'s 2.1.1/2.1.2 entries) that make call-dispatch noticeably
faster on both backends. 2.2 is additive again: a `while condition: body` loop
(§1.14), `chr`/`ord`/`type_of` builtins (§8.5.1), and a new `std/json.lum`
module built from them. See `CHANGELOG.md`'s 2.2, 2.1.2, 2.1.1, 2.1, and 2.0
entries for the full story and `docs/` for the complete, normative spec.
This README is a practical quick-start, not the spec itself.

## Install / Run
```
python3 lumen.py --version          # Lumen 3.1.0-p4 / Language specification: 3.1 / Runtime: interpreter
python3 lumen.py run file.lum       # run a program (denied all file/network access; see below)
python3 lumen.py run --allow-read=./data --allow-net=api.example.com:443 file.lum   # grant, scoped
python3 lumen.py --help             # usage, including the permission flags
python3 lumen.py run --vm file.lum  # run on the bytecode-VM backend (supported subset)
python3 lumen.py check file.lum     # type-check only, no execution
python3 lumen.py repl               # interactive shell
python3 lumen.py new myapp          # scaffold a project (lumen.toml, src/, tests/, README.md)
python3 lumen.py install            # check lumen.toml dependencies against lumen_packages/
python3 lumen.py test               # discover and run `test "name": ...` blocks under tests/
```
Inside a scaffolded project (`cd myapp`), `run`/`check`/`test` all work with **no
file argument** — they read `[project].entry` from `lumen.toml`.

## Language tour
```lumen
let name = "Ada"          # inferred type
let age: Int = 30         # optional annotation, checked before running

if age >= 18:
    print "adult"
else:
    print "minor"

for n in [1, 2, 3]:
    print n

let total = 0              # reassignment inside a block now mutates the
for n in [1, 2, 3, 4, 5]:  # outer variable, like Python/JS (fixed in 2.0 —
    total = total + n      # see CHANGELOG.md; `let total = ...` would shadow
print total                 # instead — that's a different statement)

for i in range(5):         # new in 2.0: a native, non-recursive range()
    print i

let i = 0                  # new in 2.2: `while` for conditions `for` can't
while i < 5:                # express (no fixed collection to iterate over)
    print i
    i = i + 1

fn square<T>(x: T) -> T:
    return x * x
print square(4)

struct Point:
    x
    y
let p = Point { x: 1, y: 2 }
print p.x + p.y

# 3.1 — expected failures are values (Result / Option), propagated with `?`;
# exceptional failures are exceptions (try / catch / finally)
fn parse_age(text: Str) -> Result<Int, Str>:
    if text == "":
        return Err("empty")
    return Ok(len(text))

fn describe(text: Str) -> Result<Str, Str>:
    let age = parse_age(text)?          # `?` unwraps Ok, or returns the Err from `describe`
    return Ok("age " + age)

match describe("42"):
    Ok(msg):
        print msg
    Err(why):
        print "failed: " + why
match describe(""):
    Ok(msg):
        print msg
    Err(why):
        print "failed: " + why

fn cleanup_demo():
    try:
        fail_with("E_DEMO", "something broke")
    finally:
        print "cleanup runs either way"

try:
    cleanup_demo()
catch err:
    print err.code + ": " + err.message

try:
    let r = 10 / 0
catch err:
    print "caught: " + err.message

use fs.read                         # DECLARES that this file reads files — grants nothing;
use fs.write                        # run with:  lumen run --allow-read --allow-write file.lum
write_file("out.txt", "hi")         #   (or scoped: --allow-fs=./work)
print read_file("out.txt")

use "utils.lum" as u                # import another Lumen file
print u.helper(5)

fn slow_task(n):
    return n * n
let t = spawn slow_task(5)          # concurrency: OS-thread tasks + channels
print wait(t)
```

## Low-punctuation syntax (2.1)

Everything above still works unchanged — nothing here is required. But colons,
commas, parens-on-calls, and `<T>` generics are all optional (`docs/language.md`
§1.13; see also `examples/lowpunct_demo.lum`):

```lumen
fn add a b                       # same as fn add(a, b):
    return a + b

fn clamp x as Int lo as Int hi as Int returns Int   # 'as'/'returns' instead of ':'/'->'
    if x < lo
        return lo
    return x

let nums = [1 2 3 4 5]            # no commas needed
print add 2 3                     # paren-free call: add(2, 3)
print add 2 3 + 1                 # application binds tighter than '+': (add 2 3) + 1
print add 2 (3 + 1)               # a compound argument keeps its own parens

let p = Point { x is 1 y is 2 }   # 'is' instead of ':'
print "total:" p.x + p.y          # print takes multiple space-separated values
```

One documented exception: a bare multi-argument call used as one of several
items in a comma-free list needs parens (`[add 2 3]` is fine; `[add 2 3 10]` is
a clean `SyntaxError` naming the fix) — there's no way to otherwise tell where
the call's own arguments end and the next list item begins.

## Testing (1.1)
```lumen
fn add(a, b):
    return a + b

test "addition works":
    assert_eq(add(2, 3), 5)

test "addition is commutative":
    assert_eq(add(2, 3), add(3, 2))
```
`test` bodies don't run under `lumen run` — only `lumen test` executes them.
```
lumen test               # discovers tests/, runs everything, reports pass/fail
lumen test --filter add  # only run tests whose name contains "add"
lumen test --vm          # run the same tests through the bytecode VM
```

## Core rules (full detail in `docs/`)
- Indentation defines blocks (spaces only — `docs/language.md` §1.5).
- Types are optional and gradual; when given, checked *before* the program runs
  (`docs/types.md`).
- `let x = expr` always declares a fresh binding (shadowing any outer `x`); bare
  `x = expr` mutates the nearest enclosing scope where `x` is already declared, or
  is a compile-time `NameError` if it never was — these are two different
  statements (`docs/language.md` §1.11.1, corrected/breaking in 2.0).
- No file/network access by default: a file *declares* what it needs (`use fs.read`, `use net`)
  and the *host* grants it (`--allow-read[=DIR]`, `--allow-write`, `--allow-list`, `--allow-net[=HOST[:PORT]]`);
  declaring never grants, and nothing is prompted for unless you pass `--ask` (`docs/capabilities.md`,
  `docs/security.md`).
- Failures you expect are values — `Result<T, E>` / `Option<T>`, propagated with `?`, checked by the compiler,
  never converted implicitly; failures that mean something is broken are exceptions — raised by the runtime or
  `fail` / `fail_with`, caught with `try`/`catch`, cleaned up with `finally`, and uncaught they print a code and a
  stack trace (`docs/errors.md`). A discarded `Result` is a compile error: a failure cannot vanish silently.
- Functions are **not** first-class values — only callable by name
  (`docs/types.md` §3.10).
- No `break`/`continue` yet — a known, open ergonomics gap, not an oversight in
  this README (`PERFORMANCE_HARDENING_STATUS.md`, formerly `PHASE4_STATUS.md`).

## Specification (docs/)
| File | Covers |
|---|---|
| `language.md` | lexical structure, keywords, precedence, scoping, mutability |
| `grammar.md` | formal EBNF |
| `types.md` | type system, inference, coercion, generics |
| `errors.md` | error handling: `Result`/`Option`/`?`, `try`/`catch`/`finally`, error codes, stack traces and chaining, diagnostics, the full failure catalogue |
| `modules.md` | imports, visibility, cyclic-dependency handling |
| `concurrency.md` | `spawn`/`wait`/channels, race-condition guarantees |
| `security.md` | security model (chapter 7), memory-safety guarantee |
| `capabilities.md` | *(3.0, normative)* every capability, declaring vs granting, scopes, CLI flags, denial errors |
| `security-model.md` | *(3.0)* threat model, trust boundaries, **what is not protected**, requirement status |
| `security-testing.md` | *(3.0)* how the security guarantees are tested, mutation check, release gate |
| `standard-library.md` | every built-in's exact signature and behavior |
| `testing.md` | *(1.1)* `test` blocks, `assert`/`assert_eq`/`assert_ne`, `lumen test` |

Also: `PHASE2_STATUS.md` (compiler/VM build-out), `PHASE3_STATUS.md` (developer
experience / ecosystem), and `PERFORMANCE_HARDENING_STATUS.md` (performance/hardening, formerly Phase 4,
and the mid-phase pivot to fixing ergonomics and performance first) track progress
honestly against their respective goals — what's done, what isn't, and what this
environment genuinely can't verify (a hosted package registry, real
Windows/macOS/ARM machines).

## Tests
```
python3 tests/run_all.py          # everything below except the mutation checks (a few minutes)
python3 tests/run_tests.py        # 37 regression/CLI tests
python3 tests/run_conformance.py  # 735 spec-driven conformance tests, one file per docs/ chapter
python3 tests/test_equivalence.py # 193 interpreter <-> VM equivalence tests
python3 tests/test_optimizer.py   # 28 -O0/-O1/-O2 optimizer-stability tests
python3 tests/test_security.py    # 184 adversarial capability-bypass tests (CLI, both backends, verifying real access)
python3 tests/test_consent_cli.py # 102 tests of `--ask` (on a real pseudo-terminal) and `--audit-log`, both backends
python3 tests/test_phase4.py       # 348 error-handling tests (Result, Option, `?`, finally, traces, codes, diagnostics), each run on the
                                  #   interpreter, the VM and the VM at -O2 and required to agree
python3 tests/test_docs.py        # 167 documentation examples in docs/ — run on all three, output must match the doc
python3 tests/run_stdlib.py       # 148 standard-library tests on EACH backend, with the grants they need:
                                  #   lumen test [--vm] std/tests/ --allow-fs=std/tests/fixtures --allow-net=127.0.0.1
python3 tests/security_mutation_check.py   # weakens each of 44 security checks in a copy; every mutant must be caught
python3 tests/phase4_mutation_check.py     # breaks each of 40 error-handling rules in a copy; every mutant must be caught
```
Every normative claim in `docs/` has at least one corresponding test in
`tests/conformance/`. Run `run_all.py` before any change to the interpreter or the spec.

## Editor support
`lumen-vscode/` — a self-contained VS Code extension (syntax highlighting, live
diagnostics, autocomplete) targeting Language Specification 3.1. See its own README
for install steps.
