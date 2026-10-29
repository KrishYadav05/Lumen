# Lumen Changelog

Publisher: Krish Kumar

## 3.1.0-p4 — error handling & reliability, Phase 4 (Language Specification 3.1) — pre-release

**Phase 4 — "Error Handling & Reliability".** (Numbering: this is the new Phase 4. The old "Performance, Security &
Production Hardening" checklist that used to be called Phase 4 moved to a later phase — its file is now
`PERFORMANCE_HARDENING_STATUS.md`, formerly `PHASE4_STATUS.md`; older entries below that say "Phase 4" for performance or
fuzzing mean that file. Security is Phase 5 and is **unchanged** here: everything in 3.0.0-5c is included.)
The language change is additive: a 3.0 program runs as it did, except for the corners in **Changed** below.

### Added

- **`Result<T, E>` and `Option<T>`**, always in scope, as ordinary generic enums defined in Lumen itself
  (`Ok`/`Err`, `Some`/`None`). The checker follows their type arguments everywhere a type is written, to any depth:
  `let r: Result<Int, Str> = Ok("hello")` is **rejected** (`expected: Result<Int, Str>` / `found: Result<Str, _>`).
  Gradual typing is kept: `Any` fits everything.
- **Generic `enum`s and `struct`s** (`enum Tree<T>`, `struct Pair<A, B>`) and **typed fields / payloads** (`x: Int`,
  `Node(left: Tree<T>, value: T)`); a value put into a typed field or payload — in a literal, a constructor call or a
  field assignment — is checked (E0201).
- **The `?` operator**: unwraps `Ok`/`Some`, or returns the `Err`/`None` from the enclosing function unchanged. Same typing
  and control flow on both engines (one shared decision function); operand evaluated first, nothing to its right runs
  after a propagation; runs the enclosing `finally` blocks. **No implicit error conversion**: an error of another type is
  E0404 with a hint; `?` on an `Option` in a `Result` function (or the reverse) is E0403; not a `Result`/`Option` E0401;
  outside a function E0402. Explicit conversion: `ok_or`, or `match`.
- **`try` / `catch` / `finally`**: the `finally` block runs exactly once however the `try` ends (normal end, `return`,
  `?`, a failure), in the scope of the `try`; a failing finalizer replaces the failure in flight and chains it as its cause;
  `return` / `?` inside a `finally` is rejected (E0407). `finally` is a keyword.
- **Structured error values**: `message`, `kind`, `code`, `cause` (an `Option`), `trace`. Stable codes: compile-time
  `E0xxx`, warnings `W0xxx`, runtime `R0001`–`R0015`; programs choose their own with `fail_with(code, message[, cause])`.
  `fail(message, cause)` chains. New built-ins: `fail_with`, `ok_or`, `unwrap`, `unwrap_or`, `is_ok`, `is_err`, `is_some`,
  `is_none`.
- **Stack traces** (function and `file:line`, outermost first, across modules, tasks and `finally`) and **error chaining**;
  an uncaught failure prints its message line exactly as before, then `code:`, `trace (most recent call last):` and one
  `caused by:` block per cause.
- **Diagnostics with locations**: every static diagnostic carries a code, line, column range, the offending source underlined,
  `expected`/`found` notes and a `help` where the fix is not obvious (`lumen check`/`run`/`test`; `LumenError.code/.line/.col/.end_col/.help/.notes`
  for tools). A type annotation is underlined where it is written.
- **Static checks**: `match` exhaustiveness on a known enum (E0301; W0301 warning when the type is unknown), patterns that
  are not variants / have the wrong arity (E0302, E0303), a variant built with the wrong number of values (E0304), a
  `Result` that is computed and discarded (E0405), a `Result`/`Option` function that can reach its end (E0406), wrong number
  of type arguments / unknown generic type / one type parameter bound to two types (E0202–E0204).
- **No silent loss of a spawned task's failure**: a task that failed and that nothing `wait`ed for is reported on standard
  error when `lumen run` ends (same text on both engines; stdout and exit status unchanged).
- Documentation: `docs/errors.md` rewritten as the 3.1 chapter; `types.md`, `language.md`, `grammar.md`, `standard-library.md`
  updated; every `lumen run` / `lumen error` example in `errors.md`, `types.md`, `language.md` and `grammar.md` (173) is
  executed by `tests/test_docs.py` on the interpreter, the VM and the VM at `-O2`, and must print what the document says.

### Changed (all disclosed in `docs/errors.md` §4.13)

1. `finally` is a keyword. 2. `Option`/`Result`/`Some`/`None`/`Ok`/`Err` are always in scope (a program that declares its own
keeps its own meaning). 3. `match` on a value of known type must be exhaustive (add a `_` arm if you relied on the runtime
`MatchError`). 4. A declared `struct`/`enum` name in a signature is now that type, not an implicit generic placeholder
(`language.md` §1.13.3). 5. The error value has more fields, so `print e` shows more. 6. An uncaught failure prints more
lines after the unchanged message line. 7. **Enum and struct values compare by structure** (`Ok(1) == Ok(1)` was `false`); the
same rule decides when two values are the same `Dict` key. 8. A failed task nobody waited for is reported on stderr.
9. `%` by zero is a catchable failure. 10. `lumen --version` reports specification 3.1.

### Fixed (defects that predate 3.1, found by the new tests)

- **VM**: a failure or an early `return` inside a `for` loop, or in the middle of an expression, left a stale iterator /
  operand behind (a wrong value later, or a loop that never ended).
- **VM**: unbounded recursion had no limit and exhausted memory; it is now the catchable `stack overflow` (R0009) at a depth
  of 10 000 (the interpreter's depth is whatever the host stack allows — documented as engine-specific).
- **VM**: two `spawn`ed tasks calling the same imported function corrupted each other (per-thread VM state); a task that hit an
  implementation fault left `wait` blocked forever (both engines).
- `%` by zero crashed the interpreter with a host traceback and was uncatchable on the VM.
- A function from an imported module appeared in interpreter traces under the wrong file name.

### Performance (measured; see `PHASE4_STATUS.md`)

Call-heavy code: the interpreter is level with 3.0.0-5c (a per-statement thread-local read was replaced by a per-scope
record and plain user-function calls skip the chain of built-in name tests); the VM is a few percent slower; process start-up
is about 30 ms longer (a larger source file to compile on every launch).

### Tests

- New `tests/test_phase4.py` (380): result construction, generic type checking, option handling, propagation, error
  compatibility, evaluation order, nested calls, pattern matching, exception handling, resource cleanup, interpreter/VM
  parity (each case on the interpreter, the VM and the VM at `-O2`, compared byte for byte), regression, CLI, concurrency.
- New `tests/test_docs.py` (173 verified documentation examples).
- New `tests/phase4_mutation_check.py`: breaks one error-handling rule at a time in a copy; every mutant must be killed (40).
- Totals: regression 37, conformance 735, equivalence 193, optimizer 28, adversarial security 184, standard library 148 per
  backend, consent + audit CLI 102, Phase 4 380, documentation examples 173.

### Not done (see `PHASE4_STATUS.md`)

Open decisions: error-conversion mechanism beyond exact matching; `?` at the top level; Result-returning I/O built-ins
(`read_file` & co. still raise); exit status for an unwaited failed task; `Result`→`Option` conversion; a `Result` bound to a
variable and never used is not flagged; syntax errors have no code or column; no independent review.

## 3.0.0-5c — consent prompt and audit log, milestone 5C (partly) (Language Specification 3.0) — pre-release

**Phase 5, milestone 5C, first part:** the two remaining spec requirements that are about *seeing* and *recording*
permissions rather than enforcing them — SEC-005 (CLI permission prompts) and SEC-010 (audit logging). Enforcement
is unchanged: both sit on top of the same `Policy` and the same single `capability_call`. The language and the
policy model (spec 3.0) are unchanged; every program that ran under 5b runs identically.

### Added

- **`lumen run --ask FILE`** — the consent prompt (`docs/capabilities.md` §3.3). It lists what the program declares
  with `use`, asks per capability *which paths / hosts* (or the literal word `everywhere`, or Enter to deny), prints
  the **effective permissions** and asks `Run with these permissions? [y/N]`. It can only **add** capabilities the
  program declared and no flag granted (a flag's scope is never widened), needs a real terminal on **both** stdin and
  stderr (otherwise: exit 2, nothing runs), denies by default everywhere (Enter, Ctrl-D, wrong answers, anything but
  `y`), never runs the program if you decline, and escapes control characters. `--ask` is for `run` only.
  If flags already grant everything the program declares there is no prompt.
- **`--audit-log=FILE`, `--audit-level=all|denied`** — an opt-in JSON-lines record of `run` / `declare` (the program's
  requests) / `request`, `consent`, `confirm` (the prompt) / `access` (every gated operation: allow or deny, capability,
  operation, resource, module, reason). It contains **no** file contents, **no** URL userinfo / path / query, and a hostile
  file name cannot forge a record. An allow is recorded before the operation; a denial however late it arises; **if a record
  cannot be written the operation is not performed** (`AuditError`). The program cannot reach its own log: an unrestricted
  `fs.write`, an `fs.write` scope containing the log, or a symlinked / hard-linked / directory log path is refused before the
  program runs. Works with `run`, `test` and `repl`.
- Embedding API: `AuditLog(path, level="all")`, `Policy(grants, audit=...)`, `Policy.extended(extra)` (adds, never widens),
  `declared_capabilities(path)`, `request_consent(...)`, `permission_lines(...)`.
- `docs/capabilities.md` §3.3–3.4, `docs/security-model.md` §5.8 (what the prompt and the log do **not** do).

### Fixed (found while testing the above; none was a 5b security finding)

- The prompt crashed with a Python traceback when `everywhere` (or a scope reaching the log) was typed for `fs.write`
  next to an audit log — fail-closed, but a crash. Now explained and re-asked.
- A closed stdin, or input that cannot be decoded, cancels the prompt cleanly.

### Tests

- New `tests/conformance/test_audit.py` (92): the log read back from disk — fields, reasons, redaction, forged lines,
  truncation, 8-thread concurrency, refusals, fail-closed behaviour checked on disk, a late race denial, interpreter/VM
  identical logs through a library — and the prompt logic with in-memory streams.
- New `tests/test_consent_cli.py` (102): the prompt on a **real pseudo-terminal**, no-terminal refusals, and, for every
  capability, the permissions *shown* compared with what the program *actually did* (disk, loopback server hit counter);
  the audit log end to end on both backends.
- Mutation check: 19 → 44 mutants (each audit / prompt check weakened in a copy); suites are tried cheapest first.
- Totals: regression 37, conformance 734, equivalence 193, optimizer 28, adversarial 184, standard library 148 per
  backend, consent + audit CLI 102.

### Not done (see `PHASE5_STATUS.md`)

Saved grants (deliberately: every run starts from the empty policy — a decision to confirm), tamper-evident logs,
cross-platform verification (SEC-011), independent security review, hard-link policy, an `env.read` capability.

## 3.0.0-5b — capability-based security, milestone 5B (Language Specification 3.0) — pre-release

**Phase 5, milestone 5B:** scoped file access no longer has a check-then-use race. The language
and the policy model (spec 3.0) are unchanged from 5a; no migration is needed.

### Security finding fixed (present in 3.0.0-5a)

With `--allow-read/-write/-list=DIR`, a directory on the path could be swapped for a symlink
**after** the scope check and **before** the operation. In 5a that let `write_file`/`append_file`
create files outside the scope, `delete_file` delete an outside file, and
`file_exists`/`is_dir`/`list_dir` reveal or list outside paths. (Reads and overwrites of existing
files were already refused by the Linux post-open check.) Found by injecting the swap
deterministically between the check and the use; the new tests fail on the 5a code.

### Changed

- The resolved path now only *selects* the granted directory. Every scoped operation is performed
  **relative to that directory, pinned as an open descriptor**, walking each component with
  `O_NOFOLLOW|O_DIRECTORY` — a symlink swapped in at any depth is refused with the usual structured
  `out-of-scope` denial ("the path changed while it was being opened") instead of being followed.
- The grant follows the *directory*, not its name: if the granted directory is renamed or replaced
  mid-run, access stays with the original directory.
- `file_exists`, `is_dir`, `list_dir` and `delete_file` use the same descriptor-relative primitives
  (previously path-based). `delete_file` removes the link, never its target, as before.
- Single-file and not-yet-existing scopes are anchored through their parent directory.
- Unchanged behaviour for ordinary cases: error texts and results are identical to the unrestricted
  path (a differential test checks this).

### Limits (documented, not fixed)

- Windows has no descriptor-relative open here: it uses the 5a path-based check, with the 5a race limits.
- Single-file scopes are anchored through their parent directory by path.
- Not addressed: hard links, name-based network matching (no DNS-rebinding defence), resource exhaustion.

### Tests

- New `tests/conformance/test_fs_races.py` (37): every operation, an ancestor of the granted
  directory, the Linux `/proc` backstop disabled, a real racing thread, pinning, single-file and
  late-created scopes, the path-based fallback, scoped-vs-unrestricted differential.
- Mutation check: 14 → 19 mutants (descriptor walk following links, disabled, not pinned, not
  recognising `ELOOP`/`ENOTDIR`; `O_NOFOLLOW` dropped).
- Totals: regression 37, conformance 642, equivalence 193, optimizer 28, adversarial 184, standard library 148 per backend.

### Packaging fix

The 3.0.0-5a zip contained a stray one-byte file, `std/tests/should-not-exist.txt`, left over from an
earlier version of an `fs` standard-library test whose "outside the scope" path was in fact inside it. The test
was corrected before 5a was packaged but the file was not removed. It is gone in 5b, and
`tests/run_stdlib.py` now fails if a run creates or deletes any file anywhere in the repository tree.

Still outstanding for Phase 5 (see `PHASE5_STATUS.md`): consent prompts (SEC-005), audit log
(SEC-010), cross-platform verification (SEC-011), independent review.

## 3.0.0-5a — capability-based security, milestone 5A (Language Specification 3.0) — pre-release

**Phase 5, milestone 5A of three** (5A core capabilities → 5B confinement → 5C production
readiness; see `PHASE5_STATUS.md` for exactly what is and is not done). This is a
**breaking Language Specification bump, 2.3 → 3.0**, and it changes what `use fs` means.

**The change in one sentence:** declaring a capability no longer grants it — the *host* grants,
and by default grants nothing.

### Breaking changes (migration)

| 2.3 | 3.0 |
|---|---|
| `use fs` executes and **grants** unrestricted file access | `use fs` (alias for `use fs.read`+`fs.write`+`fs.list`) only **declares** the need; it grants nothing |
| `use net` grants `http_get` to any URL | `use net` (alias for `use net.http`) only declares; the host must grant |
| a program just runs with its `use` lines | run with `lumen run --allow-read[=PATH,...] --allow-write[=PATH,...] --allow-list[=PATH,...] --allow-fs[=PATH,...] --allow-net[=HOST[:PORT],...] FILE`; with no flags **everything is denied** |
| `use fs` could be written anywhere (inside a function, an `if`) and took effect when executed | declarations are static and must be at the **top level** of the file (`ModuleError` otherwise) |
| importing `std/fs.lum` / `std/net.lum` was "in effect a grant" | importing is **not** a grant: the importing program must itself declare (`use fs.read`, `use net`) and the host must grant; a library can never exceed its importer |
| one `fs` capability for read, write, delete | `fs.read`, `fs.write`, `fs.list` are separate; so is `net.http` |
| `lumen test std/tests/` | `lumen test std/tests/ --allow-fs=std/tests/fixtures --allow-net=127.0.0.1` (the new `python3 tests/run_stdlib.py` does this) |
| `use "x" as m` imports any readable path | imports load only **relative paths to files named `*.lum`** (see "Findings") |

To migrate a 2.x program: keep its `use fs`/`use net` lines (they still parse) or narrow them to
`use fs.read` etc., and run it with the matching `--allow-*` flags. `lumen check` needs no flags.

### Added

- **Capabilities** `fs.read` (`read_file`, `file_exists`, `is_dir`), `fs.write` (`write_file`,
  `append_file`, `delete_file`), `fs.list` (`list_dir`), `net.http` (`http_get`). `net.connect`,
  `env.read`, `process.spawn` are **reserved**: declaring one is a `ModuleError` ("reserved but not
  implemented"), because nothing implements them (fail closed).
- **Host policy:** `Policy` (immutable; capability → unrestricted | list of paths | list of
  `HOST[:PORT]`), `Policy.deny_all()`, `Policy.unrestricted()`, `DENY_ALL`; CLI flags `--allow-read`,
  `--allow-write`, `--allow-list`, `--allow-fs`, `--allow-net`, each bare (everywhere) or `=a,b` (scoped), on
  `run`, `test` and `repl`. There is **no `--allow-all`**; an unknown `--allow…` flag, an empty value or an
  empty list entry is a usage error (exit 2). The CLI never prompts; stdin cannot grant anything.
- **One resource-access layer:** `capability_call` in `lumen.py` is used by both the interpreter and the VM
  (the old duplicated VM `GRANT_CAPABILITY` opcode and `_call_capability` are gone). Four checks per
  operation: declared by the executing module; declared by **every importer** (a library cannot hold authority
  its importer lacks); granted by the host; resource inside the granted scope.
- **Filesystem scopes:** paths resolved to real locations (symlinks, `..`) and compared by component
  (`/data-evil` is not inside `/data`); escaping symlinks refused for every operation (file, directory
  component, dangling); `delete_file` removes a link, never its target; out-of-scope paths answer identically
  whether or not they exist; in scoped mode files open with `O_NOFOLLOW`, writes truncate only after
  verification, and on Linux the open descriptor's real path is re-checked.
- **Network scopes:** `HOST` or `HOST:PORT`, matched by name; default ports 80/443; **every redirect hop is
  re-authorised before it is requested**; only `http`/`https`.
- **Structured denials:** `PermissionError` carries `capability`, `operation`, `resource`, `reason`
  (`not-declared`, `not-declared-by-importer`, `not-granted`, `out-of-scope`, `redirect-out-of-scope`); URLs
  are reduced to `scheme://host:port` (no credentials, path or query). Every caught error now also has
  `.kind` (`"PermissionError"`, `"TypeError"`, …).
- `lumen --help` (documents the permission flags) and clean `Error: cannot read '…'` for a missing file or a
  directory — previously a raw Python traceback.
- **Documentation:** new `docs/capabilities.md` (normative), `docs/security-model.md` (threat model, limits),
  `docs/security-testing.md`; rewritten `docs/security.md` §7.1/7.3–7.5; updated `modules.md` §5.4–5.5, `errors.md`
  §4.4.7–4.4.9, `standard-library.md` §8.4, `language.md`, `grammar.md`, `stdlib-modules.md`.
- **Tests:** the security suites were rewritten for the new model (below), plus a **mutation check**
  (`tests/security_mutation_check.py`: weakens each check in a copy and requires a suite to notice), a
  **builtin allowlist** test and a **static single-choke-point scan** of `lumen.py`, and the std library tests are
  now part of `tests/run_all.py` (both backends).

### Findings — three pre-existing gaps in 2.x, found while writing the Phase 5 tests (fixed)

1. **High — `file://` through `http_get`.** A program granted only `net` could read any local file:
   `http_get("file:///etc/passwd")`, also via a redirect to a `file:` URL (the HTTP library opens `file:` URLs).
   Fixed: only `http`/`https`, at the first URL and every redirect.
2. **High — integer arguments reached the OS.** `read_file(0)` read file descriptor 0 (other integers named other
   descriptors); `write_file(path, 5)` crashed with a raw Python exception. Fixed: argument types and counts are
   validated before anything else.
3. **Medium — import as a read primitive.** `use "secret.txt" as s` needed no capability and its parse error echoed
   the file's first token (`NameError: 'TOP' is not defined`); "cannot find" vs "error loading" revealed which
   paths exist. Fixed: only relative `*.lum` paths load (modules.md §5.4 already said there were no absolute paths;
   the runtime now enforces it), and everything else gets the same "cannot find module" error as a missing file.
   *Residual, documented:* `.lum` files can still be probed and loaded (`security-model.md` §5.4).

### Also fixed (not security findings)

- The VM compiler crashed with a raw Python `KeyError` on a call to an undefined function inside a `try` body
  (Name Resolution defers that error to run time inside `try`, and the IR labelled the call "variant" by
  elimination); the interpreter gave a catchable `NameError`. Both backends now raise the same `NameError`
  (equivalence tests added). Present since at least 2.3.0.
- `lumen --help` and a missing file crashed with a Python traceback (see Added).
- New `examples/capabilities_demo.lum` shows declaring vs granting; its output under three grant sets is tested on
  both backends.

### Known limits (not fixed in 5A — see `docs/security-model.md` §5 and `PHASE5_STATUS.md`)

Races with another process rearranging directories inside a granted scope (no descriptor-walk yet; a lost
race can leave an empty file outside the scope); hard links; name-based network matching (DNS rebinding,
SSRF through a granted host); no resource limits; ambient stdin/stdout/clock; an unrestricted `--allow-read`
can read `/proc/self/environ`; Linux only — macOS/Windows semantics unverified; no consent prompts
(SEC-005), no audit log (SEC-010); no independent security review.

### Tests (all passing at this release)

Regression 37; spec conformance 605 (security 105, capabilities 86, modules 18, errors 38, stdlib 85, …);
interpreter↔VM equivalence 193; optimizer 28; adversarial CLI security suite 184 (every case on both
backends); standard library 148 on each backend; mutation check 14/14 killed. A passing suite does not prove
the absence of vulnerabilities.

## 2.3.0 — five `fs` builtins, `std/fs.lum`, and a `strings.split/replace/substring` rewrite (Language Specification 2.3)

**Why this release exists:** continued, self-directed Phase 3 work
(`PHASE3_STATUS.md`). `std/fs.lum` was the next missing stdlib module, but
with only `read_file`/`write_file` as primitives it could not have been more
than a two-function wrapper: it could not check whether a file exists, tell a
file from a directory, list a directory, append, or delete. Rather than ship
a hollow module, this release adds the missing primitives first. That makes
it the first release since 2.2 to add to the **builtin namespace**, so —
unlike 2.2.1 and 2.2.2, which were pure `.lum` library additions — it is a
real **Language Specification bump: `SPEC_VERSION` 2.2 → 2.3** (and
`LUMEN_VERSION` 2.2.2 → 2.3.0). See "Compatibility" below for the one
behavioral consequence of adding builtin names.

**Five new builtins**, all gated by the existing `fs` capability (there is
still no third capability; the capability model is unchanged), all wired
through the whole pipeline — `BUILTIN_FUNCTION_NAMES` (the single source the
Resolver and IRBuilder read), `VM_SUPPORTED_BUILTINS`, the VM's
`CALL_BUILTIN` dispatch and `_call_capability`, and the interpreter's call
path and `call_capability` — and tested on **both** backends:
- `file_exists(path) -> Bool` — true for files *and* directories; never
  raises (a path that can't be examined is simply "not there").
- `is_dir(path) -> Bool` — never raises either. **Added beyond the original
  plan of four:** while designing `std/fs.lum`'s `list_files`/`is_file`, it
  turned out `file_exists` alone cannot tell a file from a directory, so
  nothing in the module could be written without it.
- `append_file(path, text) -> Nothing` — creates the file if absent. Failure:
  `could not append to '<path>': <reason>`.
- `list_dir(path) -> List[Str]` — bare entry names, sorted in code-point
  order (so results are identical across platforms and backends), without
  `.` or `..`. Failure: `could not list directory '<path>': <reason>`.
- `delete_file(path) -> Nothing` — files only; a directory is refused with
  the OS's own reason, never removed. Failure: `could not delete '<path>':
  <reason>`.

`file_exists`/`is_dir` are deliberately non-raising and the other three
raise a catchable Lumen error carrying the OS's reason (`No such file or
directory`, `Not a directory`, `Is a directory`, …) — the same convention
`read_file`/`write_file` already used. Documented in
`docs/standard-library.md` §8.4, `docs/errors.md` §4.4.7–§4.4.8,
`docs/security.md` §7.3–§7.5 and `docs/language.md`.

**`std/fs.lum`** (27 `lumen test` cases): re-exports the seven file builtins
and adds `is_file`, `split_lines`/`read_lines`/`write_lines`/`append_line`,
`file_size`, `copy_file`, `move_file`, `delete_if_exists`, `list_files`/
`list_subdirs`, and `read_json`/`write_json` (through `std/json.lum`).
Design decisions worth recording:
- `split_lines` follows Python's `splitlines` (a trailing newline adds no
  empty final line; `""` is `[]`) and `write_lines` terminates every line, so
  `read_lines(p)` after `write_lines(p, xs)` returns `xs` — including empty
  lines and the empty list.
- `move_file` is copy-then-delete (there is no rename builtin, so it is not
  atomic), and **moving a path onto itself is a no-op** — detected lexically
  via `path.normalize`, so `a`, `./a` and `x/../a` are all caught. A naive
  copy-then-delete would have destroyed the only copy; the test for this
  exists for exactly that reason. Two different names for one file (a
  symlink) are *not* caught: there is no `stat`/`realpath` to tell.
- Stated limitations, in the module header: no mkdir/rmdir/rename/stat/
  timestamps/permissions (no such builtins), text only, whole-file reads,
  and `file_size` counts characters, not bytes.

**Security note (read this before importing `std/fs.lum`):** capability
grants do not propagate between modules (`docs/modules.md` §5.5), so for
`std/fs.lum` to work from a program with no `use fs` of its own, the module
has to declare `use fs` itself. **Importing it therefore gives the importer
the full `fs` capability in effect** — and the same is already true of
`std/net.lum` for `net` (2.2.2). Verified empirically on both backends
rather than assumed: a call through the module fails at the file-system layer
(not with `PermissionError`) when the importer has no grant, while the raw
builtin called directly in that same importer is still denied. This is now
spelled out in `docs/security.md` §7.5 and in both module headers, and the
audit rule stays the same — grep the program *and everything it imports* for
`use fs`/`use net`. No existing check was weakened; the point is that
`use "std/fs.lum"` should be read as a capability grant, like `use fs`.

**Unplanned fix: `strings.split`, `strings.replace` and `strings.substring`.**
While making `read_lines` and `split_lines` handle a 3,000-line file, I found
a defect already in the 2.x standard library, and fixed it at the source
rather than only routing `fs.lum` around it:
- `split` and `replace` were the two functions 2.0's iterative rewrite left
  recursive — one Lumen call per match. Measured against the 2.2.2 module:
  `split` on a string with 300 separators dies on the **interpreter** with
  `stack overflow: maximum call depth exceeded` (after 6.8 s) and takes
  **18.5 s on the VM**; at 3,000 separators the interpreter overflows after
  85 s and the VM had still not finished when I stopped it at 150 s. They are now
  single-pass iterative: 300 separators take 0.15 s on both backends, and
  3,000 separators (a 15,000-character string, `split` and `replace`
  together) take 1.2 s interpreter / 1.8 s VM.
- `substring` walked *every* character of `s` on *every* call, regardless of
  the slice, so `index_of`/`contains`/`starts_with`/`ends_with` — all built
  on it — were quadratic. It now indexes directly (`s[i]` is O(1)) and costs
  O(slice length). `index_of`/`contains` on a 5,000-character string:
  **92 s → 0.2 s on the interpreter, 211 s → 0.4 s on the VM.**
- Semantics are preserved, including the edge cases: `substring` clamps to
  the string and lets a negative `start` eat into `length`
  (`substring("hello", -2, 5)` is `"hel"`); `split` on an empty separator
  still fails with `split: separator must not be empty` (checked against the
  old implementation — it did already); `replace` with an empty needle
  returns the string unchanged; matches are non-overlapping, left to right,
  and replacement text is never rescanned. **Verified by differential
  fuzzing:** 56,635 generated cases against Python's own `str.split`/
  `str.replace`/slicing as the oracle, on both backends — 0 mismatches, and
  the old implementation was run over the same cases to confirm the rewrite
  changed speed and stack behaviour, not answers.
- Five new `strings_test.lum` cases (substring clamping, split edge cases,
  empty-separator error, replace edge cases, thousands of matches).

**Compatibility:** the five names (`file_exists`, `append_file`, `list_dir`,
`delete_file`, `is_dir`) are now builtin names, and a bare call to a name
that is also a builtin resolves to the builtin (`docs/language.md` §1.7).
Checked on both backends: a program that defines its own top-level
`fn file_exists(p)` and calls it now gets the *builtin* (so, without a
`use fs`, `PermissionError: file_exists() needs the 'fs' capability`), not
its own function — a silent behavior change for any such program. That is
inherent to adding a builtin name, and it is the reason this is a spec bump
rather than a library-only release. Nothing in this repository defined any of the five
names (searched all `.lum`, `.md` and `.py` files); the one deliberate reuse
is `std/fs.lum`, whose same-named wrappers call the builtin and do not
recurse (verified on both backends).

**Verification (final numbers, this repository, after the changes above):**
core suites — regression **36**, spec-driven conformance **428** (12 files,
up from 399 at 2.2.2; the stdlib conformance file alone is 85, up from 62,
and the security conformance file gains 6), covering the exact
failure messages of the three raising builtins, sorted-order and `.`/`..`
exclusion in `list_dir`, directory refusal in `delete_file`, and that
`write_file` still overwrites), interpreter↔VM equivalence **162** (6 new),
optimizer stability **28**, security/capability-bypass **31** (8 new:
denial of each new builtin without a grant, that a `net` grant does not
unlock them, a multi-file leak test for `delete_file`, and that one `fs`
grant covers all seven fs-gated builtins) — **685 core tests, 0 failures**.
Standard library: **145** `lumen test` cases (up from 113), identical on the
interpreter and on `--vm`. `std/tests/fixtures/scratch/` is left holding only
its committed `README.txt` after a full run. `lumen test` must be run from
the repository root for `fs_test.lum`, because the `fs` builtins resolve
paths against the working directory (unlike `use "..."`, which resolves
relative to the importing file). The VS Code extension is brought to 2.3.0 /
spec 2.3 with the five names added to its syntax highlighting and
autocomplete.

## 2.2.2 — `std/net.lum` (implementation-only, no spec change)

**Why this release exists:** continued, self-directed Phase 3 work
(`PHASE3_STATUS.md`) — with `json` (2.2) and `path` (2.2.1) both done, `net`
was the next explicitly-listed missing stdlib module, chosen over `fs`
specifically because it pairs naturally with the just-built `json.lum`
(a `get_json(url)` one-liner) and needs no new capability or language
feature. Purely additive at the library level: `LUMEN_VERSION` moves to
2.2.2, `SPEC_VERSION` stays **2.2**.

**`std/net.lum`:** a thin wrapper over the `http_get` builtin — the only
network primitive this language exposes (no POST, no custom headers, no
status codes) — plus URL query-string construction, since building a
parameterized GET request is the one thing `http_get` alone can't help
with:
- `get(url)` / `get_json(url)` (fetch and, for the latter, parse the body
  as JSON via `std/json.lum` in one call).
- `url_encode(s)`, `build_query(params: Dict)`, `with_query(url, params)` —
  RFC 3986 percent-encoding (`%20` for spaces, not form-encoding's `+`,
  since there's no POST body here for that convention to apply to) and
  query-string assembly.
- `fetch(url, params)` / `fetch_json(url, params)` — `get`/`get_json` with
  query parameters attached in one call.

**A capability-model subtlety, verified empirically before relying on it:**
capability grants don't propagate between modules (`docs/modules.md`
§5.5) — each module's `capabilities` set belongs to its own interpreter/VM
instance. That means a program using `net.get(url)` does **not** need its
own `use net` statement: `std/net.lum` grants itself the capability its
own `http_get` calls need, independent of what the importing program
declares. This wasn't assumed from reading the docs alone — confirmed by
running a module with its own `use net`, imported from a program with none
of its own, and observing the call fail at the *network* layer (connection
refused) rather than at the *capability check* (`PermissionError`), on
both backends, before writing it into the module's header comment as a
documented behavior.

**`url_encode`'s stated scope:** single-byte (ASCII/Latin-1, code points
0-255) input only — it `fail()`s cleanly on anything wider rather than
silently mis-encoding it. Lumen has no Bytes type and no UTF-8 encode
primitive (`types.md` has exactly Int/Float/Str/Bool/List/Dict/Any), so
there's no way to produce the UTF-8 byte sequence a real URL percent-encoder
needs for a wider Unicode code point — the same kind of honestly-stated gap
as `path.lum`'s POSIX-only scope, not a silently wrong result.

**Verification:** all pure-logic functions (`url_encode`, `build_query`,
`with_query`) were hand-computed and smoke-tested on both backends first.
The capability-self-grant claim was verified with a real network attempt
(not just read in the docs) before being written down as fact, and
`get_json`'s full `http_get` → `json_decode` pipeline was smoke-tested
end-to-end against a real local HTTP server returning real JSON (both
backends agreed byte-for-byte) before being trusted — that server was
throwaway verification tooling only, not part of the shipped module or test
suite, since `std/tests/` stays self-contained and offline, matching every
other stdlib test file's convention: the 8 new `net_test.lum` cases test
the deterministic parts (query encoding) directly and confirm the
capability-gate/error-propagation behavior via a loopback address nothing
listens on (fails fast with a connection error, not a `PermissionError`,
deterministically and without any live network dependency). 113 `std/`
stdlib tests total (up from 105), passing identically on both backends.
Full existing suite (642 tests) re-run and unchanged — nothing here
touches the Lexer/Parser/Resolver/TypeChecker/Interpreter/Compiler/VM,
this is `.lum` source code only. `LUMEN_VERSION` bumped to `2.2.2`.

## 2.2.1 — `std/path.lum`, `strings.last_index_of` (implementation-only, no spec change)

**Why this release exists:** continued, self-directed Phase 3 work
(`PHASE3_STATUS.md`) — `std/path.lum` was the next explicitly-listed
missing stdlib module after `json` (2.2), and unlike `net`/`concurrency` it
needs no new capability and no language-level feature (no first-class
functions required), so it was buildable immediately. Purely additive at
the library level: `LUMEN_VERSION` moves to 2.2.1, but `SPEC_VERSION` stays
**2.2** — nothing here touches the grammar, a keyword, or a core builtin,
so there is nothing for the Language Specification itself to version.

**`strings.last_index_of(s, sub)`:** a small, genuine gap found while
designing `path.lum`: `dirname`/`basename`/`extension` all need the
*rightmost* occurrence of `/` or `.`, but `std/strings.lum` only had
leftmost `index_of`. Added via the `reverse_str`+`index_of` trick (find the
leftmost match in both strings reversed, then translate the offset back:
`len(s) - len(sub) - idx`), rather than a new hand-rolled scan — matches
Python's `str.rfind` convention for the empty-needle case too
(`last_index_of(s, "")` is `len(s)`). 7 new assertions in
`std/tests/strings_test.lum`.

**`std/path.lum`:** POSIX-style (forward-slash only — no Windows backslash
handling, stated explicitly) path manipulation, pure string logic with no
filesystem access and no `fs` capability requirement:
- `is_absolute(p)`, `join(a, b)` / `join_all(parts)`, `dirname(p)` /
  `basename(p)` / `split(p)`, `extension(p)` / `stem(p)`, and `normalize(p)`.
- `join` and `normalize` deliberately match well-known, documented
  conventions instead of inventing new ones — Python's `os.path.join(a, b)`
  (an absolute second argument discards the first entirely, matching a
  shell `cd`) and `os.path.normpath` (lexical `.`/`..`/repeated-`/`
  resolution, no filesystem/symlink awareness) respectively — and the
  module's header comment states this explicitly, since path-joining rules
  genuinely vary by language and leaving it unstated would be ambiguous.
- `normalize`'s segment-stack algorithm needs to drop the last collected
  segment when it sees a `..` — ordinarily a list "pop." **This language has
  no `pop`/remove-last List builtin, only `push`** (confirmed by grepping
  `BUILTIN_FUNCTION_NAMES`/`VM_SUPPORTED_BUILTINS`), the same kind of
  concrete gap that drove 2.2's `chr`/`ord`/`type_of`/`while` additions.
  This time, rather than adding a new core builtin for one stdlib module's
  sake, `normalize` was redesigned around `collections.substring_list(list,
  0, len(list) - 1)` (rebuild the list one element shorter) — a genuinely
  pop-free algorithm, not a workaround with a stated limitation. Whether a
  real `pop` builtin is worth adding later (it would need the full
  `chr`/`ord`/`type_of`-style pipeline treatment: `BUILTIN_FUNCTION_NAMES`,
  `VM_SUPPORTED_BUILTINS`, Interpreter + VM branches, documentation, tests)
  is left open for whenever a future module actually needs mutation, not
  just avoidable list-rebuilding.

**A real bug in my own first draft, caught before it shipped:** the first
version of `dirname`/`basename` was a plain "split at the last `/`," which
does not match any documented path-splitting convention at the root:
`dirname("/a")` came out `""` instead of `"/"`, and `dirname("/")` likewise.
Rewritten to match Python's `os.path.split` algorithm exactly, including
its one genuine special case — a head consisting entirely of `/` characters
(the root, or a run of leading slashes) is never stripped down to `""` —
and re-verified by hand against `/a`, `/`, `a/b/`, and `a/b/c.txt` (each
matching Python's actual `os.path.dirname`/`basename`/`split` output)
*before* writing the formal test suite, so the regression tests wouldn't
simply encode the same mistake they were meant to catch.

**Verification:** all 40 hand-computed smoke-test values (covering every
function, including the root-path edge cases above) matched on both the
Interpreter and the VM before any formal test was written. 19 new
dedicated `lumen test` cases (`std/tests/path_test.lum`) plus the 7
`last_index_of` assertions — 105 `std/` stdlib tests total (up from 85),
passing identically on both backends. The full existing suite — 36
regression, 399 spec-driven conformance, 156 interpreter↔VM equivalence,
28 optimizer-stability, 23 security — 642 total, re-run and unchanged,
since nothing here touches the Lexer/Parser/Resolver/TypeChecker/
Interpreter/Compiler/VM at all; this is `.lum` source code only.
`LUMEN_VERSION` bumped to `2.2.1`.

## 2.2 (Language Specification 2.2) — `while`, `chr`/`ord`/`type_of`, and `std/json.lum`

**Why this release exists:** continued work on Phase 3 (Developer Experience
& Ecosystem, `PHASE3_STATUS.md`), scoped as building `std/json.lum` — one of
the explicitly-listed missing stdlib modules. Writing a real JSON encoder and
decoder surfaced, in order, three genuine language gaps rather than being a
pure library exercise:

1. **No way to produce a literal `"` character.** `STRING` token syntax
   (`language.md` §1.8) has no escape mechanism, documented as a frozen v1.0
   lexical limitation — and a JSON encoder fundamentally cannot produce valid
   output without the ability to emit a double-quote. Rather than reopen the
   frozen lexer grammar, the fix is library-level: **`chr(code: Int) -> Str`**
   and **`ord(c: Str) -> Int`** builtins (`standard-library.md` §8.5.1),
   giving `chr(34)` as a working, if indirect, way to build a quote
   character at runtime. `docs/language.md` §1.8 is annotated to note this
   closes the practical gap even though the lexer itself is intentionally
   unchanged.
2. **No safe way to tell a `Bool` from an `Int` at runtime.** A generic JSON
   encoder has to dispatch on a value's runtime kind, and naive equality
   against `true`/`false` is actively wrong in Lumen: `Bool` is a Python
   `int` subclass internally, so `0 == false` and `1 == true` both hold,
   meaning duck-typed Bool detection silently misclassifies the plain
   integers 0 and 1. **`type_of(v: Any) -> Str`** (§8.5.1) returns the exact
   type-name string already used throughout the spec (`"Int"`, `"Float"`,
   `"Bool"`, `"Str"`, `"List"`, `"Dict"`, a struct/enum's own declared name,
   `"Task"`, `"Channel"`, `"Module"`, or `"Nothing"`), checking `Bool` before
   `Int` internally so it never makes that mistake.
3. **No `while` loop.** Lumen had only `for x in <collection>`; a JSON
   *decoder* needs a cursor that advances through a string by an arbitrary
   stopping condition, which `for` cannot express and which — combined with
   the already-documented absence of `break`/`continue` — would otherwise
   force either an awkward recursive-descent-via-recursion decoder (exactly
   the unbounded-recursion failure mode the 2.0 stdlib rewrite moved away
   from) or no clean decoder at all. **`while condition: body`**
   (`grammar.md` §2.2, `language.md` §1.14) is a new statement, wired through
   every pipeline layer — Lexer keyword, AST node, Parser rule, Resolver,
   TypeChecker, a new `IRWhile` IR node, IRBuilder, IRValidator, a dead-loop
   optimizer rule (only folds away a *literal-false* condition at `-O2`;
   deliberately never folds `while true`, since there is no `break` that
   would make assuming termination safe), and Compiler+VM codegen — reusing
   the VM's existing `JUMP_IF_FALSE`/`JUMP`/`PUSH_SCOPE`/`POP_SCOPE` opcodes,
   so no new VM opcode was needed. Each iteration's body runs in a fresh
   scope, exactly like `for`'s (`language.md` §1.11 Rule A). Both `while`
   itself and `-O2`'s dead-loop fold are supported on **both** backends,
   confirmed by dedicated interpreter↔VM equivalence tests, not assumed from
   interpreter-only testing.

**`std/json.lum`:** a complete `json_encode`/`json_decode` pair (plus `null`/
`is_null` helpers) built from the three additions above and the existing
`std/strings.lum`/`std/io.lum`/`std/math.lum` helpers (`substring`,
`parse_int`, `parse_float`, `pow`), documented in `docs/stdlib-modules.md`
and covered by 44 dedicated `lumen test` cases (`std/tests/json_test.lum`),
passing identically on both backends. Supports the full JSON grammar,
including `\uXXXX` escapes and exponents (`1e10`, `2.5e-3`); encodes Int,
Float, Bool, Str, List, Dict, and Nothing (as `null`). Struct, enum, Task,
Channel, and Module values have no natural JSON shape — Lumen has no
generic reflection to enumerate a struct's fields — so encoding one fails
cleanly rather than guessing; Dict keys must be Str, matching JSON itself.

**A real VM-only bug, found and fixed in the process (not a json.lum
workaround — a core interpreter/VM fix):** `std/json.lum`'s
`hex_digit_value(c)` uses the ordinary, idiomatic `try: return EXPR catch e:
...` pattern — the entire try body is a single `return`. On the VM backend
only, a `return` executed directly inside a `try` body jumps straight out
of the function via the `RETURN` opcode, bypassing the `TRY_POP` that the
compiler only emits on the path where the try body completes by falling
through normally (see `compile_stmt`'s `IRTryCatch` case). This left the
matching `TRY_PUSH`'s handler entry on `VM.try_stack` forever — stale, still
bound to a call frame that no longer existed — where it could wrongly catch
a **later, completely unrelated exception** and report its own stale catch
message instead of the real one. The bug was invisible in an ordinary
top-level script (a later, correctly-nested `try/catch` in the same script
sits on top of the stale entry and is popped first, LIFO) and only became
externally visible when the stale entry was left on a *separate, reused* VM
instance — concretely, a module's `sub_vm` (`use "..." as alias`, then two
separate calls into it across two top-level statements), which is exactly
the shape `std/json.lum` hits every time `json_decode` is called more than
once. (A spawned task is immune: `spawn` allocates a brand-new VM instance
*per call*, never reused — confirmed by testing, not assumed.) **Fix:** the
`RETURN` opcode now prunes any `try_stack` entry whose recorded call depth
is deeper than the depth it has just returned to, exactly as if that
entry's `TRY_POP` had executed. Covered by 4 new dedicated regression cases
in `tests/test_equivalence.py` (`try_return_equivalence()`) — verified, by
actually disabling the fix and re-running the suite before writing this
down, that each one fails without it and passes with it, rather than
assuming the tests are meaningful. This fix is purely internal (no spec or
behavior change for any program that was already working correctly) but is
part of this release because `std/json.lum` is what surfaced it.

**Verification:** every addition follows this project's full construct-wiring
discipline — nothing special-cased, nothing partially implemented — and
every claim here was checked on both the Interpreter and VM backends before
being written down, per standing project policy. Test totals: 36 regression,
399 spec-driven conformance (up from 358), 156 interpreter↔VM equivalence
(up from 119), 28 optimizer-stability, 23 security — 642 total, all passing,
plus 85 `std/` stdlib tests (up from 41) on both backends. `LUMEN_VERSION`/
`SPEC_VERSION`: `2.1.2`/`2.1` → **`2.2.0`/`2.2`** — a minor bump: additions
to the documented global namespace (two builtins) and a new keyword/grammar
form (`while`), same precedent as `2.0`/`2.1`'s bumps for `now`/`read_line`/
`range`/`as`/`returns`/`is`.

## 2.1.2 — Interpreter Dict-Dispatch (`eval()`/`exec_stmt()`)

**Why this release exists:** direct continuation of 2.1.1's dispatch work, on
the same "keep making it faster" direction. 2.1.1 deliberately chose the
lower-risk option first (reorder the existing isinstance chain by measured
hotness) over a full dispatch-table rewrite, explicitly leaving the rewrite
as later, optional work *if it actually measured faster* — reordering alone
had already moved the hottest types to the front of a short chain, so a
further rewrite wasn't guaranteed to help and needed to be measured, not
assumed. It was measured, and it does help.

**What changed:** the `Interpreter`'s `eval()` and `exec_stmt()` — previously
a reordered-but-still-linear `if isinstance(node, X): ... elif ...` chain —
are now a single `type(node)` dict lookup (`self._EVAL_DISPATCH.get(type(node))`)
that calls directly into a small per-node-type handler method (`_eval_Num`,
`_eval_BinOp`, `_eval_Call`, …; `_exec_If`, `_exec_Return`, …). This is safe
specifically *because* every Lumen AST `Node` subclass is a flat leaf class
with no further subclassing (verified by reading every class definition) —
which makes `isinstance(node, X)` and `type(node) is X` provably equivalent,
so this is a pure restructuring with no behavior change. Every handler
method's body is byte-for-byte identical to its old `if`/`elif` branch; only
the dispatch mechanism changed. Generated mechanically with an extraction
script (not hand-edited line-by-line) that verified the exact same 15
expression node types and 17 statement node types were present before and
after, specifically to rule out the kind of silent branch loss a manual edit
risks (see 2.1.1's VM fix, which hit exactly that bug and caught it via the
test suite).

**Measured result** (`fib(24)`, median of 9 runs, Interpreter backend,
identical hardware — this is *on top of* 2.1.1's reorder, i.e. reordered
chain vs. dict dispatch, not vs. the original unordered chain):

| Version | Median | vs. previous |
|---|---|---|
| 2.1.1 (reordered isinstance chain) | 0.860s | — |
| 2.1.2 (type-keyed dict dispatch) | 0.771s | **1.12x (12% faster)** |

Confirmed by `cProfile`: `eval()`'s own internal time dropped from 1.834s to
0.832s for the same profiled run, with the per-type logic now cleanly
separated into its own small function rather than inline chain branches.

**Combined effect across both 2.1.1 and 2.1.2** (vs. the original, un-reordered
baseline reported in `PHASE4_STATUS.md`): Interpreter `fib(24)` went from
~1.2s to ~0.77s, roughly **1.56x faster** overall. The VM backend only
received 2.1.1's reorder (not a dict-dispatch rewrite — out of scope for
this release) and stays at ~0.86s.

**Honest limitation, unchanged from 2.1.1:** still a real, bounded win, not a
transformation. Raw Python computes `fib(24)` in ~0.006s; Lumen's
tree-walking interpreter is still roughly two orders of magnitude slower,
because the fundamental cost model — environment lookups, Python-level
function calls per Lumen-level operation — is unchanged. Further gains would
need a different approach entirely (e.g. compiling to Python bytecode
directly, or a register-based VM), not more dispatch tuning.

## 2.1.1 — Interpreter/VM Call-Dispatch Performance

**Why this release exists:** continued, explicit direction from the user to
keep making the language faster — on top of 2.0's scoping fix and 2.1's
punctuation ergonomics, this release targets the dispatch-loop overhead that
`PHASE4_STATUS.md` had flagged as open work: *"Interpreter/VM dispatch
overhead for user-written recursive code is unchanged... Profiling and
speeding up the interpreter/VM's own call-dispatch hot path remains open
work."* This release is that work. **Purely internal — zero spec or behavior
change.** No new syntax, no new builtin, no change to what any existing
program outputs; every one of the 558 existing tests (conformance,
interpreter↔VM equivalence, optimizer stability, security) still passes
unchanged, which is exactly how this was verified.

**Root cause, found by profiling (`cProfile` on `fib(24)`), not guessed:**

- The tree-walking `Interpreter`'s `eval()`/`exec_stmt()` are long
  `if isinstance(node, X): ... elif isinstance(node, Y): ...` chains. Because
  this is a linear scan, whichever node type is checked *first* is
  effectively free and whichever is checked *last* pays for every check
  before it. The chain order was arbitrary (roughly the order features were
  added), not ordered by how often each node type is actually evaluated — so
  `Var`, `BinOp`, and `Call` (the hottest types for ordinary arithmetic and
  recursive calls) sat deep in a 15- and 17-way chain while rare types like
  `SpawnExpr` and `SpawnExpr`-adjacent sat near the front.
- The bytecode `VM`'s `_exec_one()` has the same shape: a 39-way `if op ==
  "HALT": ... elif op == "LOAD": ...` chain of **string comparisons**. Here
  too, `BINOP` and `JUMP_IF_FALSE` — needed by every arithmetic expression
  and every `if`/loop condition — sat at the very end of the chain, the
  worst possible position for the hottest opcodes.

**The fix, in both cases: pure reordering, nothing else.** Every branch's
code is byte-for-byte identical to before; only the order of the `if`/`elif`
checks changed, moving the measured-hottest node types/opcodes to the front.
This was deliberately chosen over a full `type(node)`-keyed dict-dispatch
rewrite (a larger, higher-risk change) because a reorder is trivially safe to
verify — the interpreter↔VM equivalence suite does byte-for-byte output
comparison, not just "it still parses" — and, as the numbers below show,
recovers most of the available win on its own. The reordering was done with
a small extraction script (not hand-edited line-by-line) that verified the
exact same set of node types/opcodes was present before and after, to rule
out the kind of silent drop a manual edit could introduce.

**Measured results** (`fib(24)`, median of 7 runs, before vs. after,
identical hardware, identical opcode/branch bodies — only the order moved):

| Backend | Before (median) | After (median) | Speedup |
|---|---|---|---|
| Interpreter | 1.216s | 0.860s | **1.41x (41% faster)** |
| VM (bytecode) | 1.116s | 0.860s | **1.30x (30% faster)** |

Mechanism confirmed independently: `cProfile` showed the Interpreter's total
`isinstance()` call count drop from 10,878,944 to 3,376,511 (a 69% reduction)
for the exact same program and the exact same output — fewer checks run
before each `eval()`/`exec_stmt()` call finds its match, which is precisely
what reordering by hotness predicts.

**Honest limitation, stated plainly:** this is a real, measured win, not a
transformation. `fib(24)` on raw Python still runs in ~0.006s — Lumen's
tree-walking interpreter and bytecode VM remain roughly two orders of
magnitude slower than native Python for this workload, because dispatch
reordering reduces overhead per step but doesn't change the fundamental cost
model (every Lumen-level operation still goes through environment lookups,
Python-level function calls, and now a shorter but still-linear dispatch
check). The `type(node)`-keyed dict-dispatch rewrite that would eliminate
the linear scan entirely remains open, lower-priority work — the reorder
was chosen first specifically because it was the lower-risk, immediately
verifiable option, consistent with how the VM side of this exact problem
was approached.

## 2.1 (Language Specification 2.1) — Low-Punctuation Syntax

**Why this release exists:** more direct user feedback, this time specifically
about ceremony: too much small, low-value punctuation (colons, commas, parens)
relative to what the code is actually saying, making it hard to just write down
what's already in your head. Unlike 2.0, this is **entirely additive, not
breaking** — every 1.0–2.0 program keeps parsing and means exactly what it
always did; this release only legalizes additional, equivalent spellings of the
same programs. Full rationale, examples, and the few deliberate exceptions are
in `language.md` §1.13; short version:

- **Colons are optional.** `if`/`else`/`for`/`fn`/`match`/each `case`/`try`/
  `catch`/`struct`/`enum`/`test` headers may end with `:` or nothing at all —
  indentation alone opens the block either way.
- **Commas are optional**, including a trailing one, in list literals, call
  arguments, `fn` parameter lists, and dict/struct literals. (A trailing comma
  was a documented 1.0 syntax error; relaxing that is additive, since it
  tightens nothing that used to be valid.)
- **Two new keywords, `as` and `returns`, plus `is`**, as alternative spellings:
  `x as Int` for a type annotation (same as `x: Int`), `returns Int` for a
  function's return type (same as `-> Int`), and `"key" is value` for a dict/
  struct-literal entry (same as `"key": value`). Reserving two new words is a
  narrow, disclosed exception to full backward compatibility (breaks only a
  program that used `returns`/`is` as an identifier) — the same kind of
  exception `test` was in 1.1, not a major-version break.
- **Function calls no longer need parentheses**: `add 2 3` instead of
  `add(2, 3)`, read exactly like ML/Haskell application — each argument is a
  single atom, and application binds tighter than every operator, so
  `add 2 3 + 1` means `(add 2 3) + 1`. A compound-expression argument keeps its
  own parens (`add 2 (3 + 1)`), which is now parens' only remaining job. The
  same applies to enum-variant construction, and enum variant fields/match
  patterns can drop their parens too (`Some value` / `Some v`).
- **`fn`'s `<T>` generic-parameter list is optional** — any param/return type
  name that isn't a built-in concrete type is automatically treated as generic
  if no explicit list is given.
- **`print` accepts multiple space-separated values** (`print "total:" n`),
  pure sugar for the existing `+` string-coercion rule — not a new display
  mechanism.
- **One documented exception, stated plainly rather than silently mis-parsed:**
  a bare multi-argument call used as one of several items in a comma-optional
  list (a list literal, call arguments, dict/struct values) needs parens —
  `[add 2 3]` is fine (the call is the list's only item), `[add 2 3 10]` is a
  clean `SyntaxError` naming the fix (`[(add 2 3) 10]`), not a silent
  misparse. Without this one exception, a comma-free list containing a bare
  call would have no way to mark where the call's arguments end and the next
  list item begins.

**Implementation note:** every new spelling is pure parser sugar — it produces
the *exact same* AST as the corresponding 1.0–2.0 form, so nothing downstream
of the parser (resolver, type checker, interpreter, IR builder, compiler, VM)
needed to change at all. This was verified directly, not assumed: 8 new
interpreter↔VM equivalence tests and 36 new conformance tests
(`tests/conformance/test_lowpunct.py`) cover every form in `language.md` §1.13,
on both backends (119 equivalence tests total, up from 111; 358 conformance
tests, up from 322). `LUMEN_VERSION`/`SPEC_VERSION`: `2.0.0`/`2.0` →
**`2.1.0`/`2.1`** — a minor bump, consistent with this being additive.

Two incidental, pre-existing-feature bugs were found and fixed while finishing
this: enum variant *declarations* (`Some value`, no parens) weren't parsed at
all before this release closed the gap (only the 1.0 `Some(value)` form and the
*pattern*-matching side's paren-free form existed); and list literals/call
arguments had not actually been migrated to accept optional commas despite
comments suggesting they had — `[1 2 3]` raised a syntax error until fixed.
Both are covered by the new tests above.

## 2.0 (Language Specification 2.0) — Ergonomics & Performance

**Why this release exists:** direct user feedback on 1.1.1 — that the language
was awkward to write idiomatic-looking code in, and slow to run it — was
investigated rather than argued with. It led to one real, user-facing bug and
one real, missing core feature; both are fixed here, and the entire standard
library was rewritten to take advantage of the fix. Phase 4's originally
planned checklist (fuzzing infra, CI workflow, FFI docs, AOT build) is paused,
not abandoned, pending further ergonomics/performance follow-up.

**Fixed — breaking change — the "Rule B" scoping bug:**
`let x = expr` (declare) and bare `x = expr` (reassign) used to parse to the
same AST node, and both always wrote to the innermost scope. The effect: any
bare reassignment inside a nested `if`/`for`/`try`/`match` block silently
**shadowed** the outer variable in that inner scope instead of mutating it —
not a narrow for-loop quirk as 1.x's docs claimed, but any nested block at all.
This is exactly the pattern every accumulator loop needs:
```
let total = 0
for n in [1, 2, 3, 4, 5]:
    total = total + n   # used to silently do nothing to the outer `total`
print total              # used to print 0, not 15
```
**Fix:** a new `Assign` AST node (and `IRAssign` IR node), distinct from
`Let`/`IRLet`, threaded through every layer — parser, static resolver, type
checker, tree-walking interpreter, IR builder/validator/optimizer, bytecode
compiler (new `ASSIGN` opcode), and VM. `Env.assign()`/`VM.assign()` walk the
scope chain outward and mutate the nearest scope that already declared the
name; `let` is unchanged and always declares fresh in the innermost scope.
Assigning to a name never declared anywhere is a clean, compile-time
`NameError`, not a silent no-op. **This is a genuine breaking change**: a 1.x
program that depended on the old shadowing behavior will now behave
differently (the common case — using the result afterward — now gives the
*correct* answer instead of the old buggy one). Full before/after examples:
`language.md` §1.11.1. Per this project's own stated semver policy
(`standard-library.md`'s frozen-spec section), a breaking change requires a
major version bump: `1.1.1` → **`2.0.0`**, `SPEC_VERSION` `"1.1"` → `"2.0"`.
Three conformance tests that had encoded the old bug as "correct" behavior
(`language.md` §1.11, `modules.md` §5.5, `security.md` §7.4) were rewritten to
assert the corrected behavior instead.

**Added — native `range()` builtin:** `range(n)` → `[0, 1, ..., n-1]`,
`range(start, end)` → `[start, ..., end-1]`. Implemented directly in Python on
both backends (not as Lumen-level recursion), specifically so a counted loop
can never hit the interpreter's recursion-depth limit. This was found to be
necessary, not optional: an intermediate fix used a private recursive helper
to manufacture `[0..n-1]` for `for` to iterate (Lumen's `for` has no numeric-
range form), and it blew the Python recursion limit at a few hundred
iterations — the same class of bug as Rule B, just rediscovered one layer
down. The native builtin has no such ceiling. See `standard-library.md` §8.5
for the full signature and error semantics.

**Changed — standard library rewritten from recursion to iteration:** every
counting/accumulating helper across `std/math.lum`, `std/collections.lum`,
`std/strings.lum`, and `std/io.lum` that used to recurse specifically to carry
state across loop iterations (working around Rule B, or manufacturing a count
with no `range()`) is now a plain iterative loop. No `.lum` source file content
changed in a way that altered its public API — only how each function gets its
answer. Concretely verified, not assumed:
- A hand-reconstructed "old-style" recursive `sqrt` driven by a recursive
  counting loop crashes with a stack overflow somewhere between 200 and 250
  iterations on the interpreter backend. The 2.0 iterative version (same
  `math.sqrt`, driven by `range()`) handles 20,000 iterations in ~2.3s with no
  crash risk at any count — this is a correctness fix (it didn't work at all
  past a few hundred items), not only a speed one.
- At the largest common size the old style could still run (200 iterations),
  the new iterative version was also modestly faster (~0.08s vs ~0.11s on this
  machine) — expected, since it trades N recursive call frames for N loop
  iterations.
- `fib(24)` (a user-written recursive function, not stdlib) is **unchanged** at
  ~1.0–1.1s on both backends regardless of `-O0`/`-O1`/`-O2` (raw Python:
  ~0.006s). This is expected and reported honestly: the stdlib rewrite removes
  the stdlib's *own* recursion, it does not speed up a user's own recursive
  Lumen code, and the IR optimizer's constant-folding/dead-code passes don't
  help a function whose cost is dominated by per-call interpretation overhead
  rather than redundant arithmetic. **Interpreter/VM dispatch performance for
  recursive user code remains open work**, not yet addressed by this release.
- All 41 stdlib `lumen test` tests still pass on both backends after the
  rewrite, plus 13 new interpreter↔VM equivalence tests covering `range()`'s
  error semantics and the corrected assignment behavior (111 equivalence tests
  total, up from 98, 0 failing).

**Also in this release (Phase 4 work done before the ergonomics/performance
pivot, kept rather than discarded):**
- **IR optimizer** (`-O0`/`-O1`/`-O2` flags on `lumen run --vm`): `-O1` adds
  constant folding of `IRBinOp`/`IRNot` over literal operands plus dead-code
  elimination after a `return`; `-O2` adds short-circuit folding of boolean
  operators and dead-branch elimination for an `if` with a constant condition.
  `-O0` (default) is a pure identity pass — no existing test's behavior
  changes unless a flag opts in. Only affects `--vm` runs (the tree-walking
  interpreter doesn't build this IR). 22 new optimizer-stability tests verify
  `-O0`/`-O1`/`-O2` all produce byte-identical program output.
- **Security/capability-bypass hardening suite** (`tests/test_security.py`,
  23 tests) — adversarial tests specifically trying to route around the
  capability model (`fs`/`net` gating) rather than just exercising its happy
  path.
- **Parser robustness fix:** deeply/adversarially nested expressions or
  statements used to leak a raw, uncaught Python `RecursionError` straight to
  the user. `Parser.parse_program()` now catches it and raises a clean Lumen
  `SyntaxError("expression or statement nested too deeply to parse")` instead
  — found via adversarial-input probing, not a reported bug.

**Test counts:** 322 spec conformance (up from 299) + 111 interpreter↔VM
equivalence (up from 98) + 22 optimizer-stability (new) + 23 security-hardening
(new) + 36 regression/CLI + 41 stdlib `lumen test` tests on both backends —
all passing. `tests/run_tests.py`'s hardcoded `"Language specification: 1.1"`
assertion updated to `"2.0"`.

**Not addressed in this release** (explicitly, so this isn't mistaken for done):
`break`/`continue` don't exist — identified as a related ergonomics gap (no
early-loop-exit), worked around in the stdlib rewrite with boolean "found"
flags, but not implemented. Interpreter/VM dispatch overhead for recursive
user-written code (as opposed to stdlib code) is unchanged. Phase 4's fuzzing
infrastructure, CI workflow, FFI/compatibility docs, benchmark suite, AOT
build, and observability items are started but paused, per explicit user
direction to prioritize ergonomics/performance first.

## 1.1.1 — VM concurrency (implementation-only, no spec change)

**Fixed:** the bytecode VM previously rejected `spawn`/`wait`/`chan`/`send`/
`receive` with a clean "not yet supported" error — the single remaining
language feature documented in `concurrency.md` but not runnable on the VM
backend (tracked in `PHASE2_STATUS.md`). Implemented:
- A new `SPAWN` opcode, compiled from the existing `IRSpawn` IR node.
- `VM.spawn()`: runs the spawned call on a background thread via a fresh `VM`
  instance that shares the global scope, registered functions, and granted
  capabilities by reference with the spawning VM — the same sharing pattern
  already used for `use`-imported modules, applied here to threads instead so
  that the two VM instances' own control-flow state (stack, call stack,
  program counter) never collide across threads.
- `wait`/`chan`/`send`/`receive` added to the VM's builtin dispatch, reusing
  the interpreter's existing `TaskHandle`/`LumenChannel` runtime classes
  directly rather than parallel VM-specific types.

No syntax, semantics, or documented behavior changed — `docs/concurrency.md`
required no edits. This is a conformance fix (the VM now implements what the
spec already described) rather than a specification change, hence a patch
version bump (1.1.0 → 1.1.1) with `SPEC_VERSION` unchanged at "1.1". 18 new
interpreter↔VM equivalence tests cover it; full suite (36 regression + 318
conformance + 98 equivalence + 41 stdlib tests on both backends) passes.

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
words, so they carry no such caveat. **Correction (2.0):** this entry originally
said these only shadow a program's own same-named function; that's backwards. A
bare call to a builtin's name always resolves to the builtin, never to a
same-named user function, regardless of definition order (`language.md` §1.7) —
verified directly (`fn len(x): return 999; print len([1,2,3])` prints the real
length, not `999`). A module-qualified call (`mymod.len(...)`) is unaffected
either way, since that resolves through the module's own function table, not
through builtin precedence.

**Not changed:** every 1.0 semantic — scoping, type inference, error model,
capability model, module resolution, concurrency — is untouched. All 299 spec
conformance tests, 36 regression tests, and 76 interpreter↔VM equivalence tests
from 1.0 still pass unmodified.

## 1.0 (Language Specification 1.0) — Phase 1 & 2

Initial frozen specification and reference implementation (interpreter + bytecode
VM). See `docs/standard-library.md`'s closing section and `PHASE2_STATUS.md` for
the full history of what was built, tested, and fixed along the way.
