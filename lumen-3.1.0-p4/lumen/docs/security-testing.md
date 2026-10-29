# docs/security-testing.md

# Testing the Security Model

Status: **Informative.** Version: Lumen 3.0.0-5c. Publisher: Krish Kumar.

A passing test suite is necessary but does not prove the absence of vulnerabilities. This
document explains what the security tests check, what they deliberately do *not* rely on, how
to run them, and how to add a test for a new capability or a newly found bypass.

## 1. The rule that shapes every security test

> **Verify actual resource access — not merely that the program printed an error message.**

A test that only checks `PermissionError` appeared can pass while the operation still happened
(the error was raised *after* the write) or while the wrong thing was denied. So:

| The test claims… | …and verifies it by |
|---|---|
| a write was denied | looking at the **disk** afterwards: the file does not exist / still has its old contents |
| a read was denied | confirming the **secret never reaches stdout** (and is not in the error message) |
| a network request was denied | counting the requests a **real local HTTP server** received (zero for the denied target) |
| a delete was denied or removed only a link | checking the target file **still exists** |
| a redirect was refused | the redirect **target server saw no request** |
| an operation is allowed | the program **printed the real content**, so a test cannot pass by both sides failing |

Positive controls matter as much as denials: every denial test has a sibling that shows the same
operation succeeding when permitted, so a broken harness cannot make everything look "denied".

## 2. The suites

| Suite | File | What it covers |
|---|---|---|
| Conformance: chapter 7 | `tests/conformance/test_security.py` | the four checks as a matrix (8 operations × declared/granted combinations), independence of capabilities, declarations (aliases, dotted, unknown, reserved, top-level only), structured error fields, "catching a denial yields nothing", argument validation, URL scheme refusal, module chains, memory-safety carry-overs, **the builtin allowlist** and the **static choke-point scan** (§4) |
| Conformance: capabilities | `tests/conformance/test_capabilities.py` | `Policy` API and validation, the `--allow-*` parser, filesystem scopes (traversal, prefix siblings, symlinks of every shape, single-file scopes, existence oracle, per-operation disk effects, the race-simulation tests for the open layer), network scopes against real servers with hit counters (host, port, host-only, name vs address, redirects in and out of scope, `file:` redirects, credential redaction, default ports) |
| Conformance: races | `tests/conformance/test_fs_races.py` | check-then-use races on scoped file access: the attacker's move (rename a directory, put a symlink to outside in its place) is injected **deterministically between the check and the use** for every operation, also for an **ancestor** of the granted directory, also with the Linux `/proc` backstop disabled; a real racing thread (3 s, 5 workers); pinning of the granted directory; single-file and late-created scopes; the path-based fallback; a differential test that scoped and unrestricted access give identical results and error texts for ordinary cases. Each test inspects the real file system for what happened *outside* the scope |
| Conformance: audit log and prompt logic | `tests/conformance/test_audit.py` | the audit log read back **from disk**: every allow/deny with the right fields and reasons, no contents / URL userinfo / path / query / forged lines, truncation, concurrency (8 threads), the log refusing a write grant that could reach it (unrestricted, containing scope, symlinked scope, hard link, symlink path), a log that cannot be written stops the operation (**checked on disk**), a late race denial recorded, interpreter and VM writing identical records through a library; and the prompt logic (`request_consent`) with in-memory streams: only declared and un-granted capabilities are asked, every default denies, `everywhere` must be exact, a flag grant is never widened, the display equals the effective permissions |
| Conformance: modules/errors/stdlib | `test_modules.py`, `test_errors.py`, `test_stdlib.py` | importer ceiling, import-as-read regression, `PermissionError` wording, `kind` field |
| Adversarial, through the CLI | `tests/test_security.py` | the same attacks, run as `lumen run [--vm] FLAGS FILE` subprocesses on **both backends**, each checking real access (§1): default-deny on disk and on the network, read/write/list separation, path restrictions with symlink and traversal variants, network restrictions with redirects, regressions for every bypass found so far, module-boundary attacks (including a module written and imported at run time), structured errors, argument validation, CLI behaviour (grant, deny, non-interactive, misuse flags, `lumen test`), absence of escape-hatch builtins |
| Consent prompt + audit log, through the CLI | `tests/test_consent_cli.py` | `lumen run --ask` on a **real pseudo-terminal** (typed answers, Ctrl-D, Enter), refusal when stdin is a pipe or only stdin is a terminal, `--ask` on other commands, and for each capability the effective permissions **shown** compared with what the program **actually did** (disk and a loopback server's hit counter); `--audit-log` end to end on both backends (identical logs, no secrets, tamper attempts refused, unsafe flag combinations refused before the program runs); prompt + log together |
| Equivalence | `tests/test_equivalence.py` | interpreter and VM produce identical output **and identical denial messages** for every protected scenario |
| Standard library | `tests/run_stdlib.py` | `std/tests/*.lum` on both backends with `--allow-fs=std/tests/fixtures --allow-net=127.0.0.1`; the fs and net tests also assert the restriction holds |
| Mutation check | `tests/security_mutation_check.py` | deliberately weakens each enforcement check in a copy and requires that a suite fails (§5) |

## 3. Running them

```
python3 tests/run_all.py                        # everything above except the mutation check (~1 min)
python3 tests/test_security.py                  # adversarial CLI suite (~20 s)
python3 tests/conformance/test_fs_races.py      # the check-then-use race suite (~5 s)
python3 tests/conformance/test_audit.py         # audit log + prompt logic (~5 s)
python3 tests/test_consent_cli.py               # the prompt on a pseudo-terminal + audit log through the CLI (~1 min)
python3 tests/conformance/test_capabilities.py  # one conformance file
python3 tests/run_conformance.py                # all conformance files
python3 tests/security_mutation_check.py        # the mutation check (44 mutants; several minutes)
python3 tests/run_stdlib.py                     # std library tests, both backends
```

The tests need no network access: the "network" is loopback servers started by the tests, and
`NO_PROXY` is set so a configured proxy cannot intercept them. They create and delete their own
temporary directories; the std tests write only in `std/tests/fixtures/scratch/` and the runner checks
it ends clean.

## 4. Structural checks

Two conformance tests protect the *shape* of the implementation rather than one behaviour:

- **Every builtin has a capability decision.** Each builtin is either in `OPERATIONS` (gated, with a
  capability) or on a reviewed list of non-gated builtins in `test_security.py`. Adding a builtin
  that is neither fails the test.
- **One choke point.** The test parses `lumen.py` and lists every use of a primitive that can touch the
  file system, network, environment, subprocesses or dynamic code (`open`, `os.*` other than
  `os.path`, `urllib`, `subprocess`, `socket`, `eval`, `exec`, …) together with the function it is
  in. Each must be on a reviewed list with a reason (the resource-access layer; loading `.lum` source;
  the standard streams; host-side command-line code). A new use anywhere else fails until reviewed.

These catch *additions*. They do not prove the reviewed lists are right — that takes a human reading
`capability_call`, `_fs_target`, `_beneath`, `_open_checked`, `_authorize_url` and `_locate_module`. That review is the main thing still outstanding (`PHASE5_STATUS.md`).

## 5. The mutation check

`tests/security_mutation_check.py` copies the repository, applies one weakening at a time to the copy's
`lumen.py`, and runs the security suites against it (audit/prompt conformance, the three older conformance files, the
consent/audit CLI suite and the adversarial suite). Each mutant must
be **killed** (some suite fails). Current mutants (44): scope check always true; path containment by text
prefix; network port ignored; network scope always true; redirects not re-authorised; `file:` URLs
allowed; importer ceiling dropped; declaration check dropped; grant check dropped; non-`Str` arguments
accepted; NUL check dropped; imports of non-`.lum` files allowed; an unmediated `open()` added elsewhere
in `lumen.py`; the descriptor walk following directory symlinks, being disabled, not pinning the granted
directory, or not recognising a swapped-in link (`ELOOP`, and Linux's `ENOTDIR`); `O_NOFOLLOW` dropped on the final open;
and (5C) the audit log: an allow or a denial not recorded, a URL or redirect hop logged unredacted, no length cap,
declarations not recorded, `--audit-level=denied` still recording allows, a failed log write not stopping the
operation, each of the three "the program cannot reach its log" refusals removed, a symlinked log path accepted, the
log lost when a policy is extended; the prompt: no terminal check, only stdin checked, undeclared capabilities offered,
`everywhere` case-insensitive, the final question defaulting to yes, end of input counting as yes, a flag's grant
replaceable by the prompt, control characters sent to the terminal, effective permissions ignoring the declaration
ceiling, a refused scope crashing the prompt, undecodable input not cancelling it, refusal text reaching the terminal
unescaped. The suites are tried cheapest first and the first one that fails kills the mutant.
A surviving mutant means a check exists that no test would notice losing — add a test, don't delete the mutant.
Mutants match exact source text: after refactoring enforcement code a mutant reports `pattern-miss` and must be
updated (that is a failure, not a pass).

Two things this tool and the race suite taught: a negative test must be seen to fail first (the race tests run
against the 3.0.0-5a code fail — files created and deleted outside the scope — and pass on 5b); and the first run of the
mutation check found that removing `O_NOFOLLOW` went unnoticed, because the Linux `/proc` re-check covers for it, so
the open layer is now also tested directly with that check disabled (simulating other platforms).

## 6. Writing a security test

For a new behaviour or a newly found bypass:

1. **Write the attack first and watch it succeed** on the unfixed code — a regression test you have
   never seen fail may be vacuous.
2. Put the *attack* in `tests/test_security.py` (subprocess, both backends, verify real access: a
   `World()` gives you an inside/outside/lookalike directory tree with a secret; `Server()` is a
   loopback HTTP server that records hits). Use `blocked_on_both` / `equivalent_on_both` /
   `expect_on_both` for the common shapes.
3. Put the *specified behaviour* in the matching conformance file with its `docs/*.md` reference.
4. Add the same scenario to `tests/test_equivalence.py` if both backends take different code paths.
5. If it is a new enforcement check, add a mutant to `tests/security_mutation_check.py` and confirm
   it is killed.
6. Add a line to the "Findings fixed" list in `docs/security-model.md` §7 and to `CHANGELOG.md`.

For a **new capability**: add it to `CAPABILITIES`, `OPERATIONS` (and `_GRANT_FLAG_HINT`,
`GRANT_FLAGS`); the builtin-allowlist test and the default-deny matrix in
`tests/conformance/test_security.py` then require a row for it. Document it in `docs/capabilities.md`.

## 7. Release gate

All of the following must hold, each checked by a command above, not by assertion:

| Gate | Evidence |
|---|---|
| Default-deny | conformance matrix; adversarial default-deny cases check disk and server |
| Explicit declarations | declaration tests (aliases, unknown, reserved, top-level only, per-file) |
| Runtime enforcement | all four checks, mutation check |
| Filesystem restrictions | scope, traversal, prefix-sibling, symlink, delete-link, single-file cases; race suite |
| Structured errors | field-level tests; per-reason tests |
| Interpreter/VM parity | `tests/test_equivalence.py` and every adversarial case on both backends |
| Regression tests for fixed bypasses | `file://`, fd/arguments, import-as-read, `--allow-all`, … in `tests/test_security.py` |
| No known unresolved critical/high bypass | the findings are listed in `docs/security-model.md` §7; remaining limits in §5 |
| Docs | this set of documents |
| Cross-platform | **not met** (Linux only); see `PHASE5_STATUS.md` |
| Independent review | **not done** |
