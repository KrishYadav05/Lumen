# docs/testing.md

# Lumen Language Specification 1.1 — Testing Framework

Status: **Normative**, added in Language Specification 1.1 (see `CHANGELOG.md`).

## T.1 `test` declarations

```lumen
test "addition works":
    assert_eq(2 + 3, 5)
```

`test STRING : <block>` declares a named test. Unlike every other statement in the
language, **a test's body does not execute as part of ordinary program flow.**
Running the file with `lumen run` (or `lumen check`, or importing it via `use`)
registers the test but never executes its body — matching real test-framework
convention, where test code doesn't run unless a test runner explicitly asks for it.
Only `lumen test` (§T.3) actually executes test bodies.

**Scoping** (`language.md` §1.11): a test body is isolated exactly like a function
body (Rule C) — it sees the module's true top-level globals (including ones declared
*after* the test in source order, since the test doesn't run inline; by the time
`lumen test` runs it, the whole file's top-level code has already executed) and
nothing else. A test has no parameters and cannot be called from ordinary Lumen code
via `name(...)` call syntax — the only way to run one is `lumen test`.

Two tests may share the same name (there is no uniqueness requirement); `lumen test`
runs each registered test independently.

## T.2 `assert`, `assert_eq`, `assert_ne`

Three ordinary built-in functions (not new syntax — `standard-library.md`'s existing
built-in surface, extended):

| Signature | Behavior |
|---|---|
| `assert(cond) -> Any` | Raises a runtime failure with message `"assertion failed"` if `cond` is falsy (`types.md` §3.7's truthiness rule — `assert([1,2,3])` passes, `assert(0)` fails). Returns `nothing` otherwise. |
| `assert_eq(a, b) -> Any` | Raises `"assertion failed: {a} != {b}"` (using the same display conversion as `print`, `standard-library.md` §8.3) if `a != b`. Returns `nothing` otherwise. |
| `assert_ne(a, b) -> Any` | Raises `"assertion failed: {a} == {b}"` if `a == b`. Returns `nothing` otherwise. |

These are **not** a new failure category — they raise through the exact same
uniform runtime-failure protocol as everything in `errors.md` §4.2.5. A `try`/`catch`
around an `assert` works exactly like it would around `fail(...)`, and `lumen test`
itself works by calling a test body and catching whatever it raises (if anything).

## T.3 `lumen test` CLI

```
lumen test [path] [--vm] [--filter SUBSTR]
```

- **Discovery:** with no `path`, looks for `./tests/` and recursively runs every
  `.lum` file found there; falls back to `[project].entry` from `./lumen.toml` if no
  `tests/` directory exists. A specific file or directory may be given explicitly.
- For each discovered file: runs its top-level code (registering any `test`
  declarations in it — including ones reached via `use "..." as alias`, since a
  test file commonly imports the code under test), then runs every registered test
  in that file and reports pass/fail.
- **`--filter SUBSTR`**: only runs tests whose name contains `SUBSTR` (plain
  substring match, not a pattern language).
- **`--vm`**: runs the discovered tests through the bytecode VM backend instead of
  the reference interpreter. A test file using a construct the VM doesn't yet
  compile fails with the VM's ordinary named "not yet supported" error for that
  file, reported per-file — never a silent skip.
- **Exit code:** `0` if every discovered test passed and no file failed to even
  load; `1` otherwise.
- **Output shape:**
  ```
  tests/main_test.lum: running 3 test(s)
    test "addition works" ... ok
    test "addition is commutative" ... ok
    test "this one fails" ... FAILED
      assertion failed: 4 != 5

  test result: FAILED. 2 passed; 1 failed
  ```

### T.3.1 `lumen test --self-test`

A separate, developer-facing command, deliberately kept under its own explicit flag
so it can never be confused with "run my project's tests": runs *this Lumen
implementation's own* regression/conformance/equivalence suite
(`tests/run_all.py`, or `tests/test_equivalence.py` with `--vm`). Not relevant to
an ordinary Lumen project; documented here only so the flag's existence isn't a
surprise.

---
