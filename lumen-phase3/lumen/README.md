# Lumen 1.1.0

Publisher: Krish Kumar

A small, readable, gradually-typed, capability-secured general-purpose language.
Core language design is frozen at Specification 1.0, with a disclosed, narrow
extension in 1.1 (the `test` keyword — see `CHANGELOG.md`). See `docs/` for the
complete, normative spec. This README is a practical quick-start, not the spec itself.

## Install / Run
```
python3 lumen.py --version          # Lumen 1.1.0 / Language specification: 1.1 / Runtime: interpreter
python3 lumen.py run file.lum       # run a program
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

fn square<T>(x: T) -> T:
    return x * x
print square(4)

struct Point:
    x
    y
let p = Point { x: 1, y: 2 }
print p.x + p.y

enum Option:
    Some(value)
    None

fn half(n):
    if n % 2 == 0:
        return Some(n / 2)
    else:
        return None

match half(10):
    Some(v):
        print "got " + v
    None:
        print "nothing"

try:
    let r = 10 / 0
catch err:
    print "caught: " + err.message

use fs                              # capability required for file access
write_file("out.txt", "hi")
print read_file("out.txt")

use "utils.lum" as u                # import another Lumen file
print u.helper(5)

fn slow_task(n):
    return n * n
let t = spawn slow_task(5)          # concurrency: OS-thread tasks + channels
print wait(t)
```

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
- No file/network access by default — `use fs` / `use net` must be declared explicitly
  (`docs/security.md`).
- Every runtime failure — expected or programmer-error alike — is uniformly catchable
  with `try`/`catch` (`docs/errors.md`).
- Functions are **not** first-class values in 1.0 — only callable by name
  (`docs/types.md` §3.10).

## Specification (docs/)
| File | Covers |
|---|---|
| `language.md` | lexical structure, keywords, precedence, scoping, mutability |
| `grammar.md` | formal EBNF |
| `types.md` | type system, inference, coercion, generics |
| `errors.md` | the uniform runtime-failure model, full message catalogue |
| `modules.md` | imports, visibility, cyclic-dependency handling |
| `concurrency.md` | `spawn`/`wait`/channels, race-condition guarantees |
| `security.md` | capability model, memory-safety guarantee |
| `standard-library.md` | every built-in's exact signature and behavior |
| `testing.md` | *(1.1)* `test` blocks, `assert`/`assert_eq`/`assert_ne`, `lumen test` |

Also: `PHASE2_STATUS.md` (compiler/VM build-out) and `PHASE3_STATUS.md` (developer
experience / ecosystem) track progress honestly against their respective goals —
what's done, what isn't, and what this environment genuinely can't verify (a hosted
package registry, real Windows/macOS/ARM machines).

## Tests
```
python3 tests/run_all.py         # everything (regression + full conformance)
python3 tests/run_tests.py       # 36 regression/CLI tests
python3 tests/run_conformance.py # 316 spec-driven conformance tests, one file per docs/ chapter
python3 tests/test_equivalence.py # 76 interpreter <-> VM equivalence tests
```
Every normative claim in `docs/` has at least one corresponding test in
`tests/conformance/`. Run `run_all.py` before any change to the interpreter or the spec.

## Editor support
`lumen-vscode/` — a self-contained VS Code extension (syntax highlighting, live
diagnostics, autocomplete) targeting Language Specification 1.0. See its own README
for install steps.
