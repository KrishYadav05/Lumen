# Lumen Changelog

Publisher: Krish Kumar

## 1.1 (Language Specification 1.1) — Phase 3

**Added:**
- `std/math.lum`, `std/strings.lum`, `std/collections.lum`, `std/time.lum`,
  `std/io.lum` — 41 functions, 41 passing `lumen test` tests on both backends.
  Two new ungated core builtins to support them: `now()` and `read_line()`
  (`read_line()` raises a normal catchable `"end of input"` at EOF).
- **Bracket-aware indentation/`NEWLINE` suppression** (`language.md` §1.5.1):
  a multi-line list/dict/struct literal or call argument list is now legal,
  exactly like Python. Purely additive — found while writing `strings.lum`'s
  lookup tables, which needed to span lines to stay readable.
- Fixed: **the VM never compiled dict literal *construction*** (only list
  literals and dict *indexing* were wired up) — found the same way, by
  actually writing a module that constructs one. `MAKE_DICT` added.
- `test "name": <block>` — a named test declaration. The body does not execute as
  part of ordinary program flow (`lumen run`); it is collected and only runs under
  `lumen test`, matching real test-framework convention. See `docs/language.md`
  §1.7 keyword index and the new testing framework docs.
- `assert(cond)`, `assert_eq(a, b)`, `assert_ne(a, b)` — built-in functions that
  raise a normal, catchable runtime failure (`"assertion failed"`, or
  `"assertion failed: {a} != {b}"` / `"== {b}"`) when the condition doesn't hold.
  They participate in the exact same uniform failure model as every other runtime
  failure (`errors.md` §4.2) — nothing new was invented for them.
- `lumen test [path] [--vm] [--filter SUBSTR]` — discovers `.lum` files (default:
  everything under `./tests/`), runs each file's top-level code, then runs every
  registered test and reports pass/fail with assertion messages for failures.
- `lumen new` now scaffolds `src/main.lum`, `tests/main_test.lum`, and `README.md`
  alongside `lumen.toml` (previously just a single `main.lum`). `lumen run` and
  `lumen check` now work with **no file argument** inside a project directory,
  reading `[project].entry` from `./lumen.toml`.

**Why this is 1.1, not a silent patch to 1.0:** `test` is a newly reserved keyword.
Every other 1.0 program is unaffected, with exactly one exception: a program that
used the bare identifier `test` as a variable, function, struct, or enum name would
now fail to parse. Phase 1's frozen-spec commitment
(`docs/standard-library.md`'s closing section) explicitly reserved minor-version
bumps for additive changes and said breaking changes require a version increment —
a new reserved word is exactly that: additive for the overwhelming majority of
programs, breaking in one narrow, disclosed case, and therefore a minor version
bump rather than either a silent patch or a major version bump. `assert`/
`assert_eq`/`assert_ne`/`fail`-style additions are ordinary builtins, not reserved
words, so they carry no such caveat — they only shadow if a program already defined
its own function of that exact name, same as any other builtin (`language.md` §1.7).

**Not changed:** every 1.0 semantic — scoping, type inference, error model,
capability model, module resolution, concurrency — is untouched. All 299 spec
conformance tests, 36 regression tests, and 76 interpreter↔VM equivalence tests
from 1.0 still pass unmodified.

## 1.0 (Language Specification 1.0) — Phase 1 & 2

Initial frozen specification and reference implementation (interpreter + bytecode
VM). See `docs/standard-library.md`'s closing section and `PHASE2_STATUS.md` for
the full history of what was built, tested, and fixed along the way.
